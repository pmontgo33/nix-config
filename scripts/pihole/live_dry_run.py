#!/usr/bin/env python3
"""Hermes-side orchestrator for the offline Pi-hole v6 live dry-run.

This CLI is the only caller of the Pi-hole-side wrapper
``scripts/pihole/live_dry_run_remote.py``.  It performs four operations:

1. Render the offline inventory (``scripts/dns_migration/render_inventory.py``).
2. Decrypt ``secrets/pihole-identities.yaml`` using the dedicated Hermes age
   key (``SOPS_AGE_KEY_FILE``) and resolve every client reference.
3. Render the offline policy (``scripts/pihole/policy_reconcile.py``) and
   adapt it to the ``live_reconcile._desired`` shape
   (``scripts/pihole/live_adapter.py``).
4. SSH the adapted inventory plus the runtime SOPS tmpfile path of the
   Pi-hole API password into the remote wrapper.  The wrapper never logs or
   echoes the password; the orchestrator never reads it.

The orchestrator is offline and dry-run only: ``live_reconcile.reconcile_live``
is invoked with ``apply=False``.  No mutation is performed.

Usage:

    scripts/pihole/live_dry_run.py \
        --target pihole1 \
        [--origin http://127.0.0.1:80] \
        [--ssh-host root@pihole1] \
        [--remote-path /var/lib/pihole/live_dry_run_remote.py] \
        [--inventory-nix inventory/default.nix] \
        [--secrets secrets/pihole-identities.yaml] \
        [--age-key-file /var/lib/hermes/.config/sops/age/pihole-identities.txt]

The output is the structured dry-run plan as JSON on stdout.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.dns_migration import render_inventory as inventory_renderer
from scripts.pihole import live_adapter as adapter
from scripts.pihole import policy_reconcile as policy


_SAFE_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_KEY = re.compile(r"^[a-zA-Z0-9._:/-]+$")
_VALID_HOST_LABEL = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_TARGETS = frozenset({"pihole1", "pihole2"})
_REMOTE_DEFAULT_PATH = "/etc/pihole/live-policy-apply"
# Dry-run still expects this default, but the apply orchestrator resolves
# the live per-activation path itself before sending the payload.
_PASSWORD_PATH = "/run/secrets.d/pihole-api-password"
_SAFE_PATH_CHARS = re.compile(r"^[a-zA-Z0-9._/+-]+$")
_SOPS_GLOB = "/nix/store/*-sops-*/bin/sops"


def _resolve_sops() -> str:
    sops = shutil.which("sops")
    if sops is not None:
        return sops
    import glob
    matches = sorted(glob.glob(_SOPS_GLOB))
    if matches:
        return matches[-1]
    raise OrchestratorError("sops binary not found on PATH")


class OrchestratorError(Exception):
    """Raised for any user-facing failure in this orchestrator."""


def _require(condition: object, message: str) -> None:
    if condition is not True:
        raise OrchestratorError(message)


def _safe_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key in value:
            _require(type(key) is str, "object keys must be strings")
            _require(_SAFE_KEY.fullmatch(key) is not None, f"unsafe key: {key!r}")
            _safe_keys(value[key])
    elif isinstance(value, list):
        for item in value:
            _safe_keys(item)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=sorted(_TARGETS), help="Pi-hole target to dry-run against")
    parser.add_argument("--origin", default="http://127.0.0.1:80", help="Pi-hole API origin reachable from inside the Pi-hole")
    parser.add_argument("--ssh-host", default=None, help="ssh host (default: root@<target>)")
    parser.add_argument("--remote-path", default=_REMOTE_DEFAULT_PATH, help="Absolute path where the remote wrapper is installed on the Pi-hole")
    parser.add_argument("--inventory-nix", type=Path, default=REPO_ROOT / "inventory" / "default.nix")
    parser.add_argument("--secrets", type=Path, default=REPO_ROOT / "secrets" / "pihole-identities.yaml")
    parser.add_argument("--age-key-file", type=Path, default=Path("/var/lib/hermes/.config/sops/age/pihole-identities.txt"))
    parser.add_argument("--password-path", default=_PASSWORD_PATH, help="Runtime SOPS tmpfile path on the Pi-hole containing the API password")
    return parser


def _validate_origin(origin: str) -> tuple[str, bool]:
    _require(type(origin) is str and bool(origin), "origin must be a non-empty string")
    _require("://" in origin, "origin must include a scheme")
    scheme, _, rest = origin.partition("://")
    scheme_lower = scheme.lower()
    _require(scheme_lower in {"http", "https"}, "origin scheme must be http or https")
    _require("/" not in rest.split("?", 1)[0], "origin must not include a path")
    host, _, port_text = rest.partition(":")
    host = host.strip("[]")
    allow_private = False
    if scheme_lower == "http":
        _require(host in _SAFE_ORIGIN_HOSTS, f"http origin host must be one of {sorted(_SAFE_ORIGIN_HOSTS)}")
        allow_private = True
    else:
        _require(bool(host), "https origin must include a hostname")
    if port_text:
        _require(port_text.isdigit(), "origin port must be numeric")
    return f"{scheme}://{rest}", allow_private


def _validate_ssh_host(host: str, *, target: str) -> str:
    _require(type(host) is str and bool(host), "ssh host must be a non-empty string")
    _require(" " not in host and "\t" not in host, "ssh host must not contain whitespace")
    _require(host.count("@") <= 1, "ssh host must contain at most one @")
    if "@" in host:
        user, _, hostname = host.partition("@")
        _require(bool(user) and bool(hostname), "ssh host user and hostname must be non-empty")
    else:
        hostname = host
    _require(bool(hostname), "ssh host hostname must be non-empty")
    _require(hostname == target, f"ssh host hostname must match target: {target}")
    _require(_VALID_HOST_LABEL.fullmatch(target) is not None, "ssh target must be a valid DNS label")
    return host


def _validate_remote_path(path_text: str) -> str:
    _require(type(path_text) is str and bool(path_text), "remote path must be a non-empty string")
    _require(path_text.startswith("/"), "remote path must be absolute")
    _require(".." not in path_text.split("/"), "remote path must not contain ..")
    _require(_SAFE_PATH_CHARS.fullmatch(path_text) is not None, "remote path contains unsafe characters")
    return path_text


def _validate_password_path(path_text: str) -> str:
    _require(type(path_text) is str and bool(path_text), "password path must be a non-empty string")
    _require(path_text.startswith("/"), "password path must be absolute")
    return path_text


def _decrypt_identities(secret_path: Path, age_key_file: Path) -> dict[str, dict[str, str]]:
    _require(secret_path.exists(), f"secrets file does not exist: {secret_path}")
    _require(age_key_file.exists(), f"age key file does not exist: {age_key_file}")
    sops = _resolve_sops()
    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(age_key_file)
    result = subprocess.run(
        [sops, "--decrypt", str(secret_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    _require(result.returncode == 0, f"sops decrypt failed: {_sanitize(result.stderr)}")
    identities = _parse_identity_yaml(result.stdout)
    _require(type(identities) is dict and len(identities) > 0, "identity mapping is empty")
    return identities


def _parse_identity_yaml(text: str) -> dict[str, dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        stripped = line.strip()
        if stripped.startswith("identityRef:") and line.endswith(":"):
            current = stripped[:-1].split(":", 1)[1]
            identities[current] = {}
            continue
        if current is not None and stripped.startswith("mac:"):
            identities[current]["mac"] = stripped.split(":", 1)[1].strip()
            continue
        if line.startswith("identities:"):
            continue
    if not identities:
        raise OrchestratorError("identity mapping is empty")
    return identities


def _sanitize(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:200] if cleaned else "unspecified error"


def _ssh_dry_run(ssh_host: str, remote_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, indent=2, sort_keys=True)
    # ``remote_path`` is either the sealed ``/etc/pihole/live-policy-apply``
    # shell wrapper or, for tests, a path the hermes-side can resolve.
    # We never invoke ``python3`` against it: the wrapper is a script that
    # reads JSON on stdin and writes JSON on stdout.
    command = ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", ssh_host, remote_path]
    result = subprocess.run(command, input=body, capture_output=True, text=True, check=False)
    _require(result.returncode == 0, f"remote wrapper exited {result.returncode}: {_sanitize(result.stderr)}")
    _require(bool(result.stdout), "remote wrapper produced no output")
    parsed = json.loads(result.stdout)
    _require(type(parsed) is dict, "remote wrapper output must be an object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        target = args.target
        origin, _ = _validate_origin(args.origin)
        ssh_host = _validate_ssh_host(args.ssh_host or f"root@{target}", target=target)
        remote_path = _validate_remote_path(args.remote_path)
        password_path = _validate_password_path(args.password_path)
        _safe_keys({"target": target})

        rendered_inventory = inventory_renderer.render(inventory_renderer.load_source(args.inventory_nix, None))
        _safe_keys(rendered_inventory)

        identities = _decrypt_identities(args.secrets, args.age_key_file)
        _safe_keys(identities)

        resolved = adapter.resolve_identities(rendered_inventory, identities)
        _safe_keys(resolved)

        policy_doc = policy.render_policy(resolved, target)
        adapted = adapter.adapt(policy_doc)
        _safe_keys(adapted)

        payload = {"inventory": adapted, "password_path": password_path, "origin": origin}
        _safe_keys(payload)

        remote_plan = _ssh_dry_run(ssh_host, remote_path, payload)

        envelope = {
            "target": target,
            "sshHost": ssh_host,
            "origin": origin,
            "inventoryFingerprint": policy_doc["policyRevision"],
            "managedObjectFingerprint": policy_doc["managedObjectFingerprint"],
            "plan": remote_plan,
        }
        _safe_keys(envelope)
        sys.stdout.write(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
        return 0
    except OrchestratorError as exc:
        print(f"Pi-hole live dry-run failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
