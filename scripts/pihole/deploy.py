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

# Allow `python3 scripts/pihole/deploy.py ...` to find the scripts package
# without requiring PYTHONPATH to be set. The scripts/ directory layout
# treats it as a package; tests use `from scripts.pihole import ...`.
_SCRIPTS_PARENT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_PARENT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_PARENT))

from scripts.pihole import deploy_transport  # noqa: E402

REPO = Path("/var/lib/hermes/workspace/nix-config")
HOSTS = {
    "pihole1": "192.168.86.101",
    "pihole2": "192.168.86.102",
}
HEALTH_URL = "http://{ip}:8080/api/auth"
REBUILD_TIMEOUT = 600
CURL_TIMEOUT = 10
HEALTH_RETRIES = 10
SETUP_TIMEOUT = 300
POLICY_TIMEOUT = 300
HEALTH_SLEEP = 3
APPLY_CONFIRMATION = "APPLY_SHARED_PIHOLE_POLICY"
APPLY_RUNNER = REPO / "scripts" / "pihole" / "live_apply.py"
PIHOLE_API_ORIGIN = "http://127.0.0.1:8080"
POLICY_LOCK_PATH = "/var/lib/pihole/.pihole-policy.lock"
# Bounded retries for the `nixos-rebuild switch --target-host` step.
# Tailscale restarts mid-build can drop the SSH transport AFTER
# activation has landed; the recovery path verifies /run/current-system
# instead of treating exit 255 as a hard failure.
REBUILD_MAX_ATTEMPTS = 3


def log(host, msg):
    """Print a log line with host prefix."""
    print(f"[{host}] {msg}")


def deploy(host):
    """Build, deploy, and health-check a single host. Returns True on success."""
    ip = HOSTS[host]
    log(host, f"--- Deploying {host} ({ip}) ---")

    # Build and switch. The transport-recovery wrapper handles the
    # Tailscale-restart case where exit 255 fires after a successful
    # activation: it extracts the requested generation from rebuild
    # output and verifies /run/current-system over a fresh SSH
    # connection. Genuine failures are surfaced unchanged.
    log(host, "Running nixos-rebuild switch...")

    def _rebuild_cmd():
        return [
            "nixos-rebuild", "switch",
            "--flake", f"{REPO}#{host}",
            "--target-host", f"root@{host}",
        ]

    ok, recovered_link = deploy_transport.run_rebuild_with_recovery(
        host,
        rebuild_cmd_factory=_rebuild_cmd,
        max_attempts=REBUILD_MAX_ATTEMPTS,
        cwd=REPO,
        timeout=REBUILD_TIMEOUT,
    )
    if not ok:
        log(host, "BUILD FAILED (transport recovery exhausted or real failure)")
        return False
    if recovered_link is not None:
        log(host, f"Build OK (recovered via /run/current-system == {recovered_link})")
    else:
        log(host, "Build OK")

    # A fresh host must expose a responsive local API before the owner-scoped
    # policy apply can create its groups and clients.
    if not api_healthy(host):
        return False
    if not apply_policy(host):
        log(host, "Policy reconciliation failed; skipping list setup")
        return False

    # Run the declarative Pi-hole setup reconciler explicitly. NixOS does not
    # reliably rerun an inactive oneshot unit when only its generated script
    # changes, so the deployment wrapper must invoke it and verify completion.
    log(host, "Reconciling Pi-hole lists and gravity...")
    ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    target = f"root@{host}"
    stop_cmd = ["ssh", *ssh_opts, target, "systemctl", "stop",
                "pihole-ftl-setup.service"]
    reset_cmd = ["ssh", *ssh_opts, target, "systemctl", "reset-failed",
                 "pihole-ftl-setup.service"]
    restart_cmd = ["ssh", *ssh_opts, target, "systemctl", "restart",
                   "pihole-ftl-setup.service"]
    # Always stop and reset the unit first so a stale remote job cannot
    # block a fresh restart, and so a timeout can clean up safely.
    def _bounded(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=SETUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            log(host, f"Timed out while running: {' '.join(cmd[-3:])}")
            return None
        except Exception as exc:
            log(host, f"Failed to run {' '.join(cmd[-3:])}: {exc}")
            return None

    _bounded(stop_cmd)
    _bounded(reset_cmd)
    try:
        result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=SETUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(host, "Pi-hole setup reconciliation timed out; stopping the unit and failing deployment")
        # Catch every failure so a stuck remote unit does not block cleanup.
        _bounded(stop_cmd)
        _bounded(reset_cmd)
        _bounded(["ssh", *ssh_opts, target, "systemctl", "kill", "--signal=TERM",
                  "pihole-ftl-setup.service"])
        _bounded(["ssh", *ssh_opts, target, "systemctl", "kill", "--signal=KILL",
                  "pihole-ftl-setup.service"])
        return False
    if result.returncode != 0:
        log(host, f"Pi-hole setup reconciliation FAILED (exit {result.returncode})")
        if result.stderr:
            log(host, f"stderr: {result.stderr[-300:]}")
        # Reset the failed state so a future deploy attempt is not blocked.
        _bounded(reset_cmd)
        return False
    log(host, "Pi-hole setup reconciliation OK")

    return api_healthy(host)


def apply_policy(host):
    """Run the explicit, guarded group/client policy apply for one host."""
    log(host, "Reconciling Pi-hole policy groups and clients...")
    try:
        result = subprocess.run(
            ["python3", str(APPLY_RUNNER), "--target", host,
             "--apply-confirmation", APPLY_CONFIRMATION,
             "--origin", PIHOLE_API_ORIGIN,
             "--lock-path", POLICY_LOCK_PATH],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=POLICY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log(host, "Pi-hole policy reconciliation timed out")
        return False
    except Exception as exc:
        log(host, f"Pi-hole policy reconciliation could not start: {exc}")
        return False
    if result.returncode != 0:
        log(host, f"Pi-hole policy reconciliation FAILED (exit {result.returncode})")
        if result.stderr:
            log(host, f"stderr: {result.stderr[-300:]}")
        return False
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        log(host, "Pi-hole policy reconciliation returned invalid JSON")
        return False
    if response.get("apply") is not True or response.get("verified") is not True:
        log(host, "Pi-hole policy reconciliation did not verify convergence")
        return False
    log(host, "Pi-hole policy reconciliation OK")
    return True


def api_healthy(host):
    """Poll the unauthenticated API session endpoint without exposing secrets."""
    ip = HOSTS[host]
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
    overall = "SUCCESS" if all_ok else "FAILED"
    print(f"\nOverall: {overall}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
