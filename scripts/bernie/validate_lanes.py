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
    # Strict one-line response contract: weaker models (e.g. MiniMax-M2.7 at
    # reasoning=low) paraphrase or decorate loose "include this marker"
    # prompts, which fails exact-match validation. Requiring the entire
    # response to be exactly the marker removes that failure mode.
    goal = (
        f"Your entire response must be exactly one line containing only this "
        f"marker and nothing else: {marker}. Do not use tools, edit files, "
        "inspect secrets, or make external changes."
    )
    context = (
        "This is an automated lane-validation probe. The only success criterion "
        f"is that your response is exactly: {marker}"
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
            # One retry when the marker is missing: a clean exit without the
            # exact marker is usually a transient model-copy slip or provider
            # hiccup, not a broken route. Bounded to a single re-probe so a
            # persistently broken lane costs at most two launches per cycle.
            if not _marker_returned(result, marker):
                retry = _probe_lane(worker, lane_name)
                if _marker_returned(retry["result"], marker):
                    # The retry is now the authoritative probe result; keep
                    # retry diagnostics so the summary and evidence show the
                    # first attempt missed.
                    result = dict(retry["result"])
                    result["retry_run_id"] = retry["result"].get("run_id")
                    result["retry_ok"] = True
                else:
                    # Both attempts missed the marker: keep the first failure
                    # as primary evidence but record retry outcome so the
                    # summary shows both runs were tried.
                    result = dict(result)
                    result["retry_run_id"] = retry["result"].get("run_id")
                    result["retry_ok"] = bool(retry["result"].get("ok"))
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
        if result.get("retry_run_id"):
            entry["retry_run_id"] = result.get("retry_run_id")
            entry["retry_ok"] = result.get("retry_ok")
        if _marker_returned(result, marker):
            evidence = f"validator probe run {result.get('run_id')} receipt {receipt}"
            if result.get("retry_run_id"):
                # First attempt missed the marker and the bounded retry
                # passed; keep both run ids in the persisted evidence.
                evidence += f" (passed on retry {result.get('retry_run_id')})"
            records[lane_name] = {
                "status": "validated",
                "validated_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "evidence": evidence,
                "registry_fingerprint": fingerprints[lane_name],
            }
            entry["ok"] = True
        else:
            # Both attempts failed: mark pending so dispatch fails closed
            # until a future cycle passes. Evidence carries both run ids.
            evidence = f"validator probe failed run {result.get('run_id')}: {result.get('error')}"
            if result.get("retry_run_id"):
                evidence += f" (retry {result.get('retry_run_id')} also failed)"
            records[lane_name] = {
                "status": "pending",
                "validated_at": None,
                "expires_at": None,
                "evidence": evidence,
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
