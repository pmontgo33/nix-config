#!/usr/bin/env python3
"""Explicit Hermes-side apply orchestrator for owner-scoped Pi-hole policy.

This is intentionally separate from ``live_dry_run.py``. It resolves the
inventory locally, sends no password over SSH, and asks the Pi-hole-local
wrapper to execute the exact guarded live_reconcile apply transaction.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pihole import live_dry_run as dry

APPLY_CONFIRMATION = "APPLY_SHARED_PIHOLE_POLICY"
REMOTE_DEFAULT_PATH = "/etc/pihole/live-policy-apply"


class OrchestratorError(dry.OrchestratorError):
    """Raised when the guarded policy apply cannot be completed."""


def _require_apply_confirmation(value: str) -> str:
    if value != APPLY_CONFIRMATION:
        raise OrchestratorError("exact apply confirmation is required")
    return value


def _apply_payload(*, inventory: dict[str, Any], origin: str, password_path: str) -> dict[str, Any]:
    return {
        "inventory": inventory,
        "origin": origin,
        "password_path": password_path,
        "apply": True,
        "confirmation": APPLY_CONFIRMATION,
    }


def _build_apply_inventory(rendered_inventory: dict[str, Any], identities: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Construct the live_reconcile apply inventory directly from a resolved
    inventory + identities.

    Unlike ``live_adapter.adapt``, which redacts MACs into
    ``client:<sha256(MAC)>`` opaque keys for dry-run plans, this preserves
    the raw MAC so Pi-hole's ``POST /api/clients`` can identify the device.
    """
    policy = rendered_inventory.get("policy", {})
    raw_base = policy.get("base", {}) if isinstance(policy, dict) else {}
    groups_in = policy.get("groups", {}) if isinstance(policy, dict) else {}
    output_clients: list[dict[str, Any]] = []
    clients_in = rendered_inventory.get("piholeClients")
    if not isinstance(clients_in, list) or not clients_in:
        raise OrchestratorError("inventory has no resolved Pi-hole clients")
    for client in clients_in:
        if not isinstance(client, dict):
            raise OrchestratorError("inventory client entry is not an object")
        ref = client.get("clientRef")
        if not isinstance(ref, str):
            raise OrchestratorError("inventory client is missing clientRef")
        # clientRef is `identityRef:<name>`; the identities map keys are the
        # bare name without the `identityRef:` prefix.
        if ref.startswith("identityRef:"):
            name = ref[len("identityRef:"):]
        else:
            name = ref
        entry = identities.get(name)
        if not isinstance(entry, dict):
            raise OrchestratorError(f"no MAC mapping for identity ref {ref}")
        mac = entry.get("mac")
        if not isinstance(mac, str) or not mac:
            raise OrchestratorError(f"identity ref {ref} has no MAC")
        group = client.get("group")
        if not isinstance(group, str) or not group:
            raise OrchestratorError(f"inventory client {ref} has no group")
        # The live reconciler accepts only {identifier, group} per client.
        # Hostname lives in Pi-hole's own bookkeeping, not in the policy
        # inventory, so we drop it here.
        payload = {"identifier": mac, "group": group}
        output_clients.append(payload)

    # Translate the policy.groups map to the live_reconcile list-of-dicts
    # shape; the live reconciler accepts only a list.
    output_groups: list[dict[str, Any]] = []
    for name, body in sorted(groups_in.items()):
        if not isinstance(body, dict):
            continue
        description = body.get("description", "")
        output_groups.append({
            "name": name,
            "description": description if isinstance(description, str) else "",
            "enabled": True,
        })
    # The live reconciler validates against its pinned BASELINE_BASE
    # ({"dns": {...}, "database": {...}}). We must include every required
    # key, even if the offline inventory has nothing to say about it.
    from scripts.pihole.live_reconcile import BASELINE_BASE as _LIVE_BASE
    base = dict(_LIVE_BASE)
    raw_base = policy.get("base", {}) if isinstance(policy, dict) else {}
    if isinstance(raw_base, dict):
        upstreams = raw_base.get("upstreams")
        if isinstance(upstreams, list) and upstreams:
            base["dns"] = dict(base["dns"], upstreams=list(upstreams))
        elif isinstance(upstreams, str) and upstreams:
            base["dns"] = dict(base["dns"], upstreams=[upstreams])
        retention = raw_base.get("retention")
        if isinstance(retention, (int, float)):
            base["database"] = dict(base["database"], maxDBdays=int(retention))
    return {
        "base": base,
        "groups": output_groups,
        "adlists": [],
        "clients": output_clients,
        "localDns": [],
        "rules": {"allow": [], "block": []},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=sorted(dry._TARGETS))
    parser.add_argument("--apply-confirmation", required=True)
    # Pi-hole's webserver/API listens on the declared `webPort`, which the
    # module currently fixes to 8080. Default to the loopback origin so the
    # hermes-side orchestrator talks to the local FTL over the runtime
    # SOPS-rendered API password file.
    parser.add_argument("--origin", default="http://127.0.0.1:8080")
    parser.add_argument("--ssh-host", default=None)
    parser.add_argument("--remote-path", default=REMOTE_DEFAULT_PATH)
    parser.add_argument("--inventory-nix", type=Path, default=dry.REPO_ROOT / "inventory" / "default.nix")
    parser.add_argument("--secrets", type=Path, default=dry.REPO_ROOT / "secrets" / "pihole-identities.yaml")
    parser.add_argument("--age-key-file", type=Path, default=Path("/var/lib/hermes/.config/sops/age/pihole-identities.txt"))
    parser.add_argument("--password-path", default=dry._PASSWORD_PATH)
    parser.add_argument("--lock-path", default="/var/lib/pihole/.pihole-policy.lock")
    return parser


def _ssh_apply(ssh_host: str, remote_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run the Pi-hole-local apply wrapper over SSH."""
    body = json.dumps(payload, indent=2, sort_keys=True)
    remote_command = f"{remote_path}"
    command = ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", ssh_host, remote_command]
    result = subprocess.run(command, input=body, capture_output=True, text=True, check=False)
    dry._require(result.returncode == 0, f"remote apply wrapper exited {result.returncode}: {dry._sanitize(result.stderr)}")
    dry._require(bool(result.stdout), "remote apply wrapper produced no output")
    parsed = json.loads(result.stdout)
    dry._require(type(parsed) is dict, "remote apply wrapper output must be an object")
    return parsed


_SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def _resolve_secrets_path(ssh_host: str, name: str) -> str:
    """Find the sops-nix-managed SOPS secret path on the remote host.

    sops-nix bumps the numeric subdirectory under ``/run/secrets.d/`` on
    every system activation. The orchestrator resolves the live per-activation
    path before sending the payload so the wrapper reads from the right
    location. We avoid ``bash -c`` because the Pi-hole default shell is fish.
    """
    dry._require(
        bool(_SAFE_SECRET_NAME.fullmatch(name)),
        f"unsafe SOPS secret name: {name!r}",
    )
    ssh_cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        ssh_host,
        "set target (readlink /run/secrets 2>/dev/null); "
        "if test -n \"$target\" -a -e \"$target/" + name + "\"; "
        "echo \"$target/" + name + "\"; exit 0; "
        "end; "
        "set found (find /run/secrets.d -maxdepth 2 -name '" + name + "' -print -quit); "
        "if test -n \"$found\"; echo \"$found\"; exit 0; end; "
        "exit 1",
    ]
    result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=False)
    dry._require(
        result.returncode == 0,
        f"could not resolve SOPS path on {ssh_host}: {dry._sanitize(result.stderr)}",
    )
    path = result.stdout.strip()
    dry._require(bool(path), f"no SOPS secret {name!r} found on {ssh_host}")
    return path


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        _require_apply_confirmation(args.apply_confirmation)
        origin, _ = dry._validate_origin(args.origin)
        target = args.target
        ssh_host = dry._validate_ssh_host(args.ssh_host or f"root@{target}", target=target)
        remote_path = dry._validate_remote_path(args.remote_path)
        # Resolve the active sops-nix secret path on the remote host; the
        # numeric subdirectory under /run/secrets.d/ is per-activation.
        password_basename = Path(args.password_path).name
        password_path = _resolve_secrets_path(ssh_host, password_basename)
        rendered_inventory = dry.inventory_renderer.render(dry.inventory_renderer.load_source(args.inventory_nix, None))
        identities = dry._decrypt_identities(args.secrets, args.age_key_file)
        apply_inventory = _build_apply_inventory(rendered_inventory, identities)
        dry._safe_keys(apply_inventory)

        payload = _apply_payload(
            inventory=apply_inventory, origin=origin, password_path=password_path,
        )
        payload["lock_path"] = args.lock_path
        dry._safe_keys(payload)
        result = _ssh_apply(ssh_host, remote_path, payload)
        if result.get("apply") is not True or result.get("verified") is not True:
            raise OrchestratorError("remote policy apply did not verify convergence")
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0
    except OrchestratorError as exc:
        print(f"Pi-hole live apply failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
