#!/usr/bin/env python3
"""Hermes-side orchestrator for the offline Pi-hole v6 live dry-run.

This CLI is the only caller of the Pi-hole-side wrapper
``scripts/pihole/live_dry_run_remote.py``.  It performs four operations:

1. Render the offline inventory (``scripts/dns_migration/render_inventory.py``).
2. Resolve the rendered identity mapping on the target host and resolve every
   client reference.
3. Render the offline policy (``scripts/pihole/policy_reconcile.py``) and
   adapt it to the ``live_reconcile._desired`` shape
   (``scripts/pihole/live_adapter.py``).
4. Resolve the per-activation sops-nix identity and API-password files on the
   target host, read only the identity mapping into memory, and SSH the adapted
   inventory plus the API-password path into the remote wrapper. The wrapper
   never logs or echoes the password; the orchestrator never reads it.

The orchestrator is offline and dry-run only: ``live_reconcile.reconcile_live``
is invoked with ``apply=False``.  No mutation is performed.

Usage:

    scripts/pihole/live_dry_run.py \
        --target pihole1 \
        [--origin http://127.0.0.1:8080] \
        [--ssh-host root@pihole1] \
        [--remote-path /etc/pihole/live-policy-apply] \
        [--inventory-nix inventory/default.nix]

The output is the structured dry-run plan as JSON on stdout.
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

from scripts.dns_migration import render_inventory as inventory_renderer
from scripts.pihole import live_adapter as adapter
from scripts.pihole import policy_reconcile as policy


_SAFE_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_KEY = re.compile(r"^[a-zA-Z0-9._:/-]+$")
_VALID_HOST_LABEL = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_TARGETS = frozenset({"pihole1", "pihole2"})
_REMOTE_DEFAULT_PATH = "/etc/pihole/live-policy-apply"
# The live per-activation path is resolved on the target before the payload is
# sent; this value supplies only the secret basename for that lookup.
_PASSWORD_PATH = "/run/secrets.d/pihole-api-password"
_SAFE_PATH_CHARS = re.compile(r"^[a-zA-Z0-9._/+-]+$")
_IDENTITY_REF_LINE = re.compile(r"^ {4}identityRef:([A-Za-z0-9][A-Za-z0-9._+-]{0,63}):$")
_MAC_LINE = re.compile(r"^ {8}mac:\s*([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})$")


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
    parser.add_argument("--origin", default="http://127.0.0.1:8080", help="Pi-hole API origin reachable from inside the Pi-hole")
    parser.add_argument("--ssh-host", default=None, help="ssh host (default: root@<target>)")
    parser.add_argument("--remote-path", default=_REMOTE_DEFAULT_PATH, help="Absolute path where the remote wrapper is installed on the Pi-hole")
    parser.add_argument("--inventory-nix", type=Path, default=REPO_ROOT / "inventory" / "default.nix")
    parser.add_argument("--password-path", default=_PASSWORD_PATH, help="Rendered API-password path or basename on the Pi-hole")
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


def _require_same_secret_generation(password_path: str, identities_path: str) -> None:
    _require(
        Path(password_path).parent == Path(identities_path).parent,
        "rendered secret generation changed during resolution",
    )


def _parse_identity_yaml(text: str) -> dict[str, dict[str, str]]:
    _require(type(text) is str, "identity mapping must be text")
    identities: dict[str, dict[str, str]] = {}
    current: str | None = None
    saw_root = False
    in_sops_metadata = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if in_sops_metadata:
            continue
        if line == "sops:":
            _require(saw_root and bool(identities), "identity mapping has an invalid sops section")
            in_sops_metadata = True
            current = None
            continue
        if not saw_root:
            _require(line == "identities:", "identity mapping root must be identities")
            saw_root = True
            continue
        identity_match = _IDENTITY_REF_LINE.fullmatch(line)
        if identity_match:
            identity_name = identity_match.group(1)
            current = identity_name
            if identity_name in identities:
                raise OrchestratorError("identity mapping contains a duplicate identity ref")
            identities[identity_name] = {}
            continue
        mac_match = _MAC_LINE.fullmatch(line)
        if mac_match and current is not None:
            if "mac" in identities[current]:
                raise OrchestratorError("identity mapping contains a duplicate MAC entry")
            identities[current]["mac"] = mac_match.group(1)
            continue
        raise OrchestratorError("identity mapping contains an invalid entry")
    if not saw_root or not identities:
        raise OrchestratorError("identity mapping is empty")
    if any(not entry.get("mac") for entry in identities.values()):
        raise OrchestratorError("identity mapping contains an incomplete entry")
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
        _validate_password_path(args.password_path)
        _safe_keys({"target": target})

        rendered_inventory = inventory_renderer.render(inventory_renderer.load_source(args.inventory_nix, None))
        _safe_keys(rendered_inventory)

        # Keep the dry-run CLI on the same remote-rendered secret boundary as
        # live_apply without importing the apply module during module import.
        from scripts.pihole import live_apply
        try:
            password_path = live_apply._resolve_secrets_path(ssh_host, Path(args.password_path).name)
            identities_path = live_apply._resolve_secrets_path(ssh_host, "pihole-identities")
            _require_same_secret_generation(password_path, identities_path)
            identities_text = live_apply._read_remote_secret(ssh_host, identities_path)
        except (live_apply.OrchestratorError, live_apply.dry.OrchestratorError) as exc:
            # When this file is launched directly, live_apply imports the
            # package copy of this module. Normalize its subclass back to the
            # active CLI's error type so failures remain sanitized and
            # traceback-free.
            raise OrchestratorError(str(exc)) from None
        identities = _parse_identity_yaml(identities_text)
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
