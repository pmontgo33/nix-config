#!/usr/bin/env python3
"""Pi-hole deployment script.

Deploys NixOS configuration to one or both Pi-hole instances,
then runs the policy reconciler.
Usage: python3 deploy.py [pihole1|pihole2|all]
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/var/lib/hermes/workspace/nix-config")
HOSTS = {
    "pihole1": "192.168.86.101",
    "pihole2": "192.168.86.102",
}
HEALTH_URL = "http://{ip}:8080/api/auth"
REBUILD_TIMEOUT = 600
CURL_TIMEOUT = 10
HEALTH_RETRIES = 10
HEALTH_SLEEP = 3
RECONCILER = REPO / "scripts" / "pihole" / "live_reconcile.py"


def log(host, msg):
    """Print a log line with host prefix."""
    print(f"[{host}] {msg}")


def deploy(host):
    """Build, deploy, and health-check a single host. Returns True on success."""
    ip = HOSTS[host]
    log(host, f"--- Deploying {host} ({ip}) ---")

    # Build and switch
    log(host, "Running nixos-rebuild switch...")
    result = subprocess.run(
        ["nixos-rebuild", "switch",
         "--flake", f"{REPO}#{host}",
         "--target-host", f"root@{host}"],
        cwd=REPO,
        capture_output=True, text=True, timeout=REBUILD_TIMEOUT,
    )
    if result.returncode != 0:
        log(host, f"BUILD FAILED (exit {result.returncode})")
        if result.stderr:
            log(host, f"stderr: {result.stderr[-300:]}")
        return False
    log(host, "Build OK")

    # Health check
    url = HEALTH_URL.format(ip=ip)
    log(host, f"Health check (polling {url}, {HEALTH_RETRIES} attempts)...")
    for attempt in range(1, HEALTH_RETRIES + 1):
        time.sleep(HEALTH_SLEEP)
        try:
            result = subprocess.run(
                ["curl", "-sS", "--max-time", "5", url],
                capture_output=True, text=True, timeout=CURL_TIMEOUT,
            )
            data = json.loads(result.stdout)
            if "session" in data:
                log(host, "Health OK — Pi-hole API responding")
                return True
            log(host, f"Attempt {attempt}: unexpected response: {result.stdout[:120]}")
        except subprocess.TimeoutExpired:
            log(host, f"Attempt {attempt}: curl timed out")
        except json.JSONDecodeError:
            log(host, f"Attempt {attempt}: bad JSON: {result.stdout[:120]}")
        except Exception as exc:
            log(host, f"Attempt {attempt}: {exc}")

    log(host, "HEALTH CHECK FAILED after all retries")
    return False


def run_reconciler():
    """Run the policy reconciler. Non-fatal — logs errors but never blocks."""
    print("\n=== Running policy reconciler ===")
    try:
        result = subprocess.run(
            ["python3", str(RECONCILER)],
            cwd=REPO,
            capture_output=True, text=True, timeout=120,
        )
        if result.stdout:
            print(result.stdout.rstrip())
        if result.returncode != 0:
            print(f"Reconciler exited with code {result.returncode}")
            if result.stderr:
                print(f"stderr: {result.stderr[-300:]}")
            print("Policy sync failed (non-critical, deployment continues).")
        else:
            print("Policy sync OK.")
    except Exception as exc:
        print(f"Reconciler error: {exc}")


def main():
    # --- Parse args ---
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all":
        hosts = list(HOSTS.keys())
    elif target in HOSTS:
        hosts = [target]
    else:
        print(f"Unknown host: {target}. Options: {', '.join(HOSTS.keys())}, all")
        sys.exit(1)

    # --- Deploy each host in sequence (abort on first failure) ---
    results = {}
    try:
        for host in hosts:
            ok = deploy(host)
            results[host] = ok
            if not ok:
                print(f"\n!!! {host} FAILED — aborting. Remaining hosts skipped.")
                break
    except KeyboardInterrupt:
        print("\n\nInterrupted (Ctrl+C). Cleaning up...")
        sys.exit(130)

    # --- Summary table ---
    print("\n=== Deployment Summary ===")
    print(f"  {'Host':<12} {'IP':<17} {'Status'}")
    print(f"  {'-'*12} {'-'*17} {'-'*10}")
    for host in hosts:
        ip = HOSTS[host]
        if host not in results:
            status = "SKIPPED"
        else:
            status = "OK" if results[host] else "FAILED"
        print(f"  {host:<12} {ip:<17} {status}")

    all_ok = all(results.values())
    if all_ok:
        # Only run reconciler if every host succeeded
        try:
            run_reconciler()
        except KeyboardInterrupt:
            print("\n\nInterrupted during reconciler. Deployment was successful.")
            sys.exit(130)
    else:
        print("\nSkipping policy reconciler — one or more hosts failed.")

    overall = "SUCCESS" if all_ok else "FAILED"
    print(f"\nOverall: {overall}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
