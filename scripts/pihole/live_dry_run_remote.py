#!/usr/bin/env python3
"""Pi-hole-side remote wrapper for the offline live dry-run CLI.

This script runs **on the Pi-hole itself**, invoked by the Hermes-side
``scripts/pihole/live_dry_run.py`` orchestrator over SSH.  It does not open a
real network socket: it imports the already-approved ``live_reconcile`` module
and runs ``reconcile_live(apply=False)`` against the local Pi-hole v6 API.

The runtime Pi-hole API password is read from
``/run/secrets.d/1/pihole-api-password`` and used only inside this process.
It is never echoed, never logged, never written to disk, and never passed
through any transport.

Inputs (stdin): JSON document with two keys:

    {
        "inventory":   <live_reconcile._desired shape>,
        "password_path": <absolute path to SOPS tmpfile with the API password>,
        "origin":       <Pi-hole API origin URL, e.g. "http://127.0.0.1:80">,
    }

Outputs (stdout): JSON document with the ``live_reconcile.reconcile_live``
return value, which is the deterministic dry-run plan.

Failure modes:
    - missing or unreadable password file  -> non-zero exit + sanitized JSON
    - ``live_reconcile.LivePolicyError``   -> non-zero exit + sanitized JSON
    - any unexpected exception            -> non-zero exit + sanitized JSON

In every failure case, the sanitized JSON contains only ``{"error": "..."}``
without leaking the password or any MAC identifier.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pihole import live_reconcile as live


_LEGAL_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_KEY = re.compile(r"^[a-zA-Z0-9._:/-]+$")
_DEFAULT_LOCK_PATH = "/var/lib/pihole/.pihole-policy.lock"


def _acquire_remote_lock(lock_path: str) -> int:
    """Acquire the shared Pi-hole policy lock exclusively on the remote host.

    Uses fcntl.flock semantics via a Python wrapper so both `live_apply`
    (here) and the setup service's `pihole-ftl-setup` script share the same
    lockfile. The lockfile is intentionally left in place after release so
    the inode persists across writer invocations; only the flock is dropped.
    """
    import fcntl
    import grp
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open with explicit mode 0660 + group pihole so the setup service
    # (which runs as the pihole user) can take the same flock. We use
    # fchmod/fchown on the fd to bypass umask and any pre-existing mode
    # left by a previous root-owned create. If the pihole group is not
    # present (e.g. hermes-side test environments) we still set the mode
    # so the file is world-readable-but-not-writable, which is enough
    # for the current apply transaction and lets the setup service fix
    # the group later.
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o660)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(fd)
        raise RuntimeError(f"Pi-hole policy lock is held by another actor: {lock_path}")
    try:
        os.fchmod(fd, 0o660)
        pihole_gid = grp.getgrnam("pihole").gr_gid
    except KeyError:
        pihole_gid = None
    if pihole_gid is not None:
        try:
            os.fchown(fd, -1, pihole_gid)
        except (OSError, PermissionError):
            pass
    return fd


def _release_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        return


def _sanitize_error(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(message)).strip()
    return cleaned[:200] if cleaned else "unspecified error"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read()
    _require(bool(raw), "empty stdin payload")
    payload = json.loads(raw.decode("utf-8"))
    _require(type(payload) is dict, "payload must be an object")
    return payload


def _read_password(path_text: str) -> str:
    path = Path(path_text)
    _require(path.is_absolute(), "password path must be absolute")
    _require(path.exists(), "password file does not exist")
    text = path.read_text(encoding="utf-8")
    password = text.strip()
    _require(bool(password), "password file is empty")
    return password


def _validate_origin(origin_text: str) -> tuple[str, bool]:
    _require(type(origin_text) is str and bool(origin_text), "origin must be a non-empty string")
    _require("://" in origin_text, "origin must include a scheme")
    scheme, _, rest = origin_text.partition("://")
    scheme_lower = scheme.lower()
    _require(scheme_lower in {"http", "https"}, "origin scheme must be http or https")
    _require("/" not in rest.split("?", 1)[0], "origin must not include a path")
    host, _, port_text = rest.partition(":")
    host = host.strip("[]")
    allow_private = False
    if scheme_lower == "http":
        _require(host in _LEGAL_ORIGIN_HOSTS, f"http origin host must be one of {sorted(_LEGAL_ORIGIN_HOSTS)}")
        allow_private = True
    else:
        _require(bool(host), "https origin must include a hostname")
    if port_text:
        _require(port_text.isdigit(), "origin port must be numeric")
    return f"{scheme}://{rest}", allow_private


def _check_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key in value:
            _require(type(key) is str, "object keys must be strings")
            _require(_SAFE_KEY.fullmatch(key) is not None, f"unsafe key: {key!r}")
            _check_keys(value[key])
    elif isinstance(value, list):
        for item in value:
            _check_keys(item)


def main() -> int:
    lock_fd = None
    try:
        payload = _read_payload()
        inventory = payload["inventory"]
        password_path = payload["password_path"]
        origin = payload["origin"]
        apply = payload.get("apply", False)
        confirmation = payload.get("confirmation", "")
        lock_path = payload.get("lock_path", _DEFAULT_LOCK_PATH)

        _require(type(inventory) is dict, "inventory must be an object")
        _check_keys(inventory)
        _require(type(lock_path) is str and bool(lock_path), "lock path must be a non-empty string")

        validated_origin, allow_private = _validate_origin(origin)
        password = _read_password(password_path)
        transport = live.UrllibTransport(validated_origin, allow_private_http=allow_private)

        def _credential_callback() -> str:
            return password

        # Acquire the shared Pi-hole policy lock around the apply transaction
        # so the setup service cannot run concurrently. Lockfile persists
        # across releases; only the flock is dropped on exit.
        lock_fd = _acquire_remote_lock(lock_path)

        plan = live.reconcile_live(
            inventory,
            credential_callback=_credential_callback,
            transport=transport,
            apply=bool(apply),
            confirmation=confirmation if apply else None,
        )
    except live.LivePolicyError as exc:
        _release_lock(lock_fd)
        sys.stdout.write(json.dumps({"error": _sanitize_error(str(exc))}, sort_keys=True) + "\n")
        return 2
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        _release_lock(lock_fd)
        sys.stdout.write(json.dumps({"error": _sanitize_error(str(exc))}, sort_keys=True) + "\n")
        return 2
    except Exception:  # noqa: BLE001 - sanitized re-raise
        _release_lock(lock_fd)
        sys.stdout.write(json.dumps({"error": "unexpected Pi-hole dry-run failure"}, sort_keys=True) + "\n")
        return 2

    _release_lock(lock_fd)
    sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
