#!/usr/bin/env python3
"""Refresh runtime lane validation for Bernie's read-only worker registry.

For every enabled, dispatchable, read-only hermes_chat lane in the
authoritative Nix registry this tool runs one bounded probe launch through
the existing runner (same sandbox, budgets, redaction, receipts, cleanup).
A returned marker refreshes the lane's validation in the runtime state file;
any failure leaves the lane stale so dispatch stays fail-closed.

The runtime state can never grant capabilities: only lanes already present
in the Nix registry with dispatchable=true and empty toolsets are probed,
and provider/model/reasoning/limits remain Nix-authoritative.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

RUNNER_PATH = Path(__file__).resolve().parent / "model_worker.py"
STATE_DIR = Path("/var/lib/hermes/.local/state/bernie-delegation")
VALIDATION_PATH = STATE_DIR / "lane-validation.json"
PROBE_MARKER_PREFIX = "BERNIE_LANE_PROBE_"
PROBE_TTL_HOURS = 24


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("model_worker", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner module: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_eligible_lanes(registry: dict[str, Any]) -> list[str]:
    """Lanes the validator may probe. Structural checks only; the runner
    re-validates everything at dispatch time."""
    eligible: list[str] = []
    for name, lane in sorted(registry.get("lanes", {}).items()):
        if not isinstance(lane, dict):
            continue
        if lane.get("role") != "worker" or lane.get("dispatchable") is not True:
            continue
        if lane.get("runner") != "hermes_chat":
            continue
        if not lane.get("enabled", False):
            continue
        if lane.get("toolsets") != []:
            continue
        if lane.get("side_effect_mode") != "read-only":
            continue
        eligible.append(name)
    return eligible


def _probe_lane(worker, lane_name: str) -> dict[str, Any]:
    marker = f"{PROBE_MARKER_PREFIX}{lane_name.upper().replace('-', '_')}_OK"
    goal = (
        f"Return a concise response containing the exact marker {marker}. "
        "Do not use tools, edit files, inspect secrets, or make external changes."
    )
    context = (
        "This is an automated lane-validation probe. The only success criterion "
        f"is that the worker returns the exact marker {marker}."
    )
    result = worker.run_worker(
        lane_name=lane_name,
        goal=goal,
        context=context,
        repo=Path("/var/lib/hermes/workspace/nix-config"),
        probe_mode=True,
    )
    return {"marker": marker, "result": result}


def _marker_returned(result: dict[str, Any], marker: str) -> bool:
    stdout_path = result.get("stdout_path")
    if not isinstance(stdout_path, str):
        return False
    try:
        content = Path(stdout_path).read_text(encoding="utf-8")
    except OSError:
        return False
    return marker in content and bool(result.get("ok"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report eligible lanes without probing")
    args = parser.parse_args(argv)

    worker = _load_runner_module()
    state_dir = worker._state_dir()
    worker._private_directory(state_dir)

    registry = worker.load_authoritative_registry()

    records: dict[str, dict[str, Any]] = {}
    # Carry forward only records for lanes still eligible and still matching
    # their Nix route fingerprint; everything else is dropped so a changed
    # provider/model tuple cannot inherit stale validation.
    existing = worker.load_runtime_lane_validation(VALIDATION_PATH)
    eligible_lanes = probe_eligible_lanes(registry)
    fingerprints = {
        name: worker._lane_fingerprint(registry["lanes"].get(name, {}))
        for name in eligible_lanes
    }
    for name, record in (existing or {}).items():
        if name in fingerprints and isinstance(record, dict) and record.get("registry_fingerprint") == fingerprints[name]:
            records[name] = record

    if os.environ.get("BERNIE_WORKERS_DISABLED") == "1" or (state_dir / "kill-switch").exists():
        print(json.dumps({"ok": False, "error": "worker dispatch disabled; probes skipped", "probed": []}))
        return 2

    lanes = [name for name in eligible_lanes]
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "eligible_lanes": lanes}, sort_keys=True))
        return 0

    now = worker._utc_now()
    expires = now + timedelta(hours=PROBE_TTL_HOURS)
    summary: list[dict[str, Any]] = []
    for lane_name in lanes:
        # Per-lane try/except: one broken lane (bad route, launch refusal)
        # must not abort the whole refresh cycle; it just marks that lane
        # pending and the cycle continues with the remaining lanes.
        try:
            probe = _probe_lane(worker, lane_name)
            result = probe["result"]
            marker = probe["marker"]
        except worker.WorkerError as exc:
            records[lane_name] = {
                "status": "pending",
                "validated_at": None,
                "expires_at": None,
                "evidence": f"validator probe refused for {lane_name}: {exc}",
                "registry_fingerprint": fingerprints[lane_name],
            }
            summary.append({"lane": lane_name, "ok": False, "receipt": None, "error": str(exc)})
            continue
        receipt = result.get("receipt_path")
        entry: dict[str, Any] = {
            "lane": lane_name,
            "ok": False,
            "receipt": receipt,
            "error": result.get("error"),
        }
        if _marker_returned(result, marker):
            records[lane_name] = {
                "status": "validated",
                "validated_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "evidence": f"validator probe run {result.get('run_id')} receipt {receipt}",
                "registry_fingerprint": fingerprints[lane_name],
            }
            entry["ok"] = True
        else:
            # A failed probe on a previously valid lane means the route may be
            # broken (429, model gone). Mark pending so dispatch fails closed
            # until a probe passes.
            records[lane_name] = {
                "status": "pending",
                "validated_at": None,
                "expires_at": None,
                "evidence": f"validator probe failed run {result.get('run_id')}: {result.get('error')}",
                "registry_fingerprint": fingerprints[lane_name],
            }
        summary.append(entry)

    payload = {
        "schema_version": 1,
        "updated_at": now.isoformat(),
        "ttl_hours": PROBE_TTL_HOURS,
        "lanes": records,
    }
    worker._write_json(VALIDATION_PATH, json.loads(json.dumps(payload)))
    print(json.dumps({"ok": all(entry["ok"] for entry in summary), "results": summary}, sort_keys=True))
    return 0 if summary and all(entry["ok"] for entry in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
