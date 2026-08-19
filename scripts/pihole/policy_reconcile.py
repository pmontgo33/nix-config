#!/usr/bin/env python3
"""Render a shared Pi-hole policy and a no-apply reconciliation plan.

This module is deliberately offline.  It consumes the already-rendered DNS
migration inventory and an optional sanitized API-shaped state fixture.  It
never authenticates, opens a socket, edits SQLite, or mutates a Pi-hole.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, cast

OWNER = "shared-pihole-policy"
TARGETS = frozenset({"pihole1", "pihole2"})
SCHEMA_VERSION = 1

_POLICY_KEYS = {"base", "adlists", "groups", "groupAssignments", "localDns", "rules"}
_BASE_KEYS = {"upstreams", "listeningInterfaces", "queryLogging", "retention"}
_ADLIST_KINDS = ("standard", "kids")
_ADLIST_KEYS = {"address", "enabled", "description"}
_RULE_KINDS = ("allow", "block")
_STATE_FAMILIES = frozenset({"base", "adlists", "groups", "clients", "localDns", "rules"})
_STATE_COLLECTIONS = _STATE_FAMILIES - {"base"}
_STATE_COMMON = {"managed", "owner", "id"}
_STATE_KEYS = {
    "groups": _STATE_COMMON | {"name", "description", "enabled"},
    "adlists": _STATE_COMMON | {"address", "description", "enabled", "type"},
    "clients": _STATE_COMMON | {"identifier", "group", "comment"},
    "localDns": _STATE_COMMON | {"recordRef", "aliasRef", "hostname", "domain", "rr", "server", "target", "targetRr", "ttl", "addptr", "enabled", "description"},
    "rules": _STATE_COMMON | {"kind", "value", "domain", "enabled"},
}
_MANAGED_REQUIRED = {
    "groups": {"name", "description"},
    "adlists": {"address", "description", "enabled"},
    "clients": {"identifier", "group"},
    "localDns": {"hostname", "domain"},
    "rules": {"kind", "value"},
}
_IDENTITY_REF = re.compile(r"^identityRef:[a-z0-9][a-z0-9-]{1,62}$")
_BASELINE_BASE = {
    "upstreams": ["192.168.86.1#5353"],
    "listeningInterfaces": ["eth0"],
    "queryLogging": True,
    "retention": 91,
}
_BASELINE_ADLIST = {
    "address": "file:///var/lib/pihole/baseline.hosts",
    "enabled": True,
    "description": "Shared Pi-hole baseline adlist",
}
_BASELINE_GROUPS = {
    "normal": {"description": "Normal clients"},
    "kids": {"description": "Kids clients"},
}


class PolicyError(ValueError):
    """Raised when policy or observed state is unsafe or incomplete."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise PolicyError("non-finite JSON number is not allowed")
    if isinstance(value, dict):
        for key, child in value.items():
            _require(isinstance(key, str), "JSON object keys must be strings")
            _finite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _finite(child)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


_READ_ERROR = object()


def _read_text(path: Path) -> str | object:
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return _READ_ERROR


def load_json(path: Path) -> Any:
    text = _read_text(path)
    if text is _READ_ERROR:
        raise PolicyError("unable to read JSON policy input")
    _require(isinstance(text, str), "unable to read JSON policy input")
    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, ValueError):
        value = _READ_ERROR
    if value is _READ_ERROR:
        raise PolicyError("unable to read JSON policy input")
    _finite(value)
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), f"{field} must be a string")
    _require(allow_empty or bool(value), f"{field} must not be empty")
    _require(not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value), f"{field} contains control characters")
    return value


def _opaque_client_key(identifier: str) -> str:
    return f"client:{hashlib.sha256(identifier.encode()).hexdigest()}"


def _validate_target(target: Any) -> str:
    _require(isinstance(target, str), "target must be pihole1 or pihole2")
    _require(target in TARGETS, "target must be pihole1 or pihole2")
    return target


def _normalize_base(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "policy.base must be an object")
    _require(set(value) == _BASE_KEYS, "policy.base must contain the complete baseline settings")
    _require(value == _BASELINE_BASE, "policy.base does not match the frozen baseline settings")
    _require(isinstance(value["upstreams"], list) and all(isinstance(item, str) and item for item in value["upstreams"]), "policy.base.upstreams must be a string list")
    _require(isinstance(value["listeningInterfaces"], list) and all(isinstance(item, str) and item for item in value["listeningInterfaces"]), "policy.base.listeningInterfaces must be a string list")
    _require(isinstance(value["queryLogging"], bool), "policy.base.queryLogging must be a boolean")
    _require(isinstance(value["retention"], int) and not isinstance(value["retention"], bool), "policy.base.retention must be an integer")
    return {key: value[key] for key in sorted(value)}


def _normalize_adlists(value: Any) -> list[dict[str, Any]]:
    _require(isinstance(value, dict), "policy.adlists must be an object")
    _require(set(value) == set(_ADLIST_KINDS), "policy.adlists must contain standard and kids lists only")
    _require(value["kids"] == [], "policy.adlists.kids must be empty for the baseline policy")
    _require(isinstance(value["standard"], list) and len(value["standard"]) == 1, "policy.adlists.standard must contain the baseline list only")
    output: list[dict[str, Any]] = []
    entry = value["standard"][0]
    _require(isinstance(entry, dict), "policy.adlists.standard entry must be an object")
    _require(set(entry) == _ADLIST_KEYS, "baseline adlist must contain its complete supported fields")
    _require(entry == _BASELINE_ADLIST, "policy adlist must match the existing shared baseline list")
    _require(isinstance(entry["enabled"], bool), "adlist enabled must be a boolean")
    _text(entry["address"], "adlist address")
    _text(entry["description"], "adlist description")
    output.append({"kind": "standard", **{key: entry[key] for key in sorted(entry)}})
    return output


def _normalize_groups(value: Any) -> dict[str, dict[str, Any]]:
    _require(isinstance(value, dict), "policy.groups must be an object")
    _require(set(value) == set(_BASELINE_GROUPS), "policy.groups must contain normal and kids only")
    output: dict[str, dict[str, Any]] = {}
    for name in sorted(_BASELINE_GROUPS):
        raw = value[name]
        _require(isinstance(raw, dict), "policy group must be an object")
        _require(set(raw) == {"description"}, "policy group must contain only its baseline description")
        _require(raw == _BASELINE_GROUPS[name], "policy group does not match the frozen baseline")
        output[name] = {"name": name, "description": _text(raw["description"], "group description")}
    return output


def _normalize_local_dns(value: Any) -> list[dict[str, Any]]:
    _require(isinstance(value, list), "policy.localDns must be a list")
    _require(value == [], "policy.localDns must be empty for the baseline policy")
    return []


def _normalize_rules(value: Any) -> dict[str, list[Any]]:
    _require(isinstance(value, dict), "policy.rules must be an object")
    _require(set(value) == set(_RULE_KINDS), "policy.rules must contain allow and block only")
    _require(value["allow"] == [] and value["block"] == [], "policy.rules must be empty for the baseline policy")
    return {kind: [] for kind in _RULE_KINDS}


def _normalize_clients(data: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    clients = data.get("piholeClients")
    _require(isinstance(clients, list), "piholeClients must be a list")
    output: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    seen_keys: set[str] = set()
    groups: set[str] = set()
    for client in clients:
        _require(isinstance(client, dict), "Pi-hole client entry must be an object")
        _require(set(client) == {"clientRef", "identifier", "status", "device", "hostname", "address", "group"}, "Pi-hole client entry is incomplete or contains unsupported fields")
        ref = client.get("clientRef")
        _require(isinstance(ref, str) and _IDENTITY_REF.fullmatch(ref) is not None, "Pi-hole client reference is invalid")
        _require(ref not in seen_refs, "duplicate Pi-hole client reference")
        _require(client.get("status") == "resolved", "Pi-hole client identity is not resolved")
        identifier = _text(client.get("identifier"), "Pi-hole client identity")
        key = _opaque_client_key(identifier)
        _require(key not in seen_keys, "ambiguous Pi-hole client identity")
        group = client.get("group")
        _require(group in {"normal", "kids"}, "Pi-hole client group must be normal or kids")
        groups.add(group)
        _text(client.get("device"), "Pi-hole client device")
        _text(client.get("address"), "Pi-hole client address")
        payload = {
            "key": key,
            "clientRef": ref,
            "device": client["device"],
            "group": group,
        }
        if client.get("hostname") is not None:
            payload["hostname"] = _text(client["hostname"], "Pi-hole client hostname")
        output.append(payload)
        seen_refs.add(ref)
        seen_keys.add(key)
    return sorted(output, key=lambda item: item["key"]), groups


def _normalize_policy(data: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(data, dict), "policy input must be an object")
    _finite(data)
    _require(data.get("schemaVersion") == SCHEMA_VERSION, "policy input schemaVersion must be 1")
    _require(data.get("apply") is False, "policy input must be marked apply=false")
    _require(data.get("identityResolutionRequired") is False, "unresolved identity references prevent policy reconciliation")
    _require(isinstance(data.get("unresolvedIdentityRefs"), list) and data["unresolvedIdentityRefs"] == [], "unresolved identity references prevent client reconciliation")
    policy_data = data.get("policy")
    _require(isinstance(policy_data, dict), "policy section is required")
    policy_data = cast(dict[str, Any], policy_data)
    _require(set(policy_data) == _POLICY_KEYS, "policy must contain the complete baseline sections")
    clients, _client_groups = _normalize_clients(data)
    groups = _normalize_groups(policy_data["groups"])
    assignments = policy_data["groupAssignments"]
    _require(isinstance(assignments, dict), "policy.groupAssignments must be an object")
    resolved_refs = {client["clientRef"] for client in clients}
    _require(set(assignments) == resolved_refs, "policy.groupAssignments must match the resolved clients exactly")
    for ref, group in assignments.items():
        _require(isinstance(ref, str) and _IDENTITY_REF.fullmatch(ref) is not None and isinstance(group, str) and group in {"normal", "kids"}, "invalid group assignment")
    for client in clients:
        assigned = assignments[client["clientRef"]]
        _require(assigned == client["group"], "group assignment disagrees with client inventory")
    return {
        "base": _normalize_base(policy_data["base"]),
        "adlists": _normalize_adlists(policy_data["adlists"]),
        "groups": list(groups.values()),
        "clients": clients,
        "localDns": _normalize_local_dns(policy_data["localDns"]),
        "rules": _normalize_rules(policy_data["rules"]),
    }


def _managed(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "managed": True, "owner": OWNER, **payload}


def _desired_objects(normalized: dict[str, Any]) -> dict[str, Any]:
    adlists = [_managed(f"adlist:{_digest(item['address'])}", item) for item in normalized["adlists"]]
    groups = [_managed(f"group:{item['name']}", item) for item in normalized["groups"]]
    clients = [_managed(item["key"], item) for item in normalized["clients"]]
    local_dns = [_managed(f"local-dns:{_digest(item['key'])}", item) for item in normalized["localDns"]]
    rules: list[dict[str, Any]] = []
    for kind in _RULE_KINDS:
        for rule in normalized["rules"][kind]:
            payload = {"kind": kind, "value": rule}
            rules.append(_managed(f"rule:{kind}:{_digest(rule)}", payload))
    return {
        "base": normalized["base"],
        "adlists": adlists,
        "groups": groups,
        "clients": clients,
        "localDns": local_dns,
        "rules": sorted(rules, key=lambda item: item["key"]),
    }


def _validate_base_state(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "observed Pi-hole base settings must be an object")
    _require(set(value) == _BASE_KEYS, "observed Pi-hole base settings are incomplete or unsupported")
    _require(isinstance(value["upstreams"], list) and all(isinstance(item, str) and item for item in value["upstreams"]), "observed upstream settings are invalid")
    _require(isinstance(value["listeningInterfaces"], list) and all(isinstance(item, str) and item for item in value["listeningInterfaces"]), "observed listening interface settings are invalid")
    _require(isinstance(value["queryLogging"], bool), "observed query logging setting is invalid")
    _require(isinstance(value["retention"], int) and not isinstance(value["retention"], bool) and value["retention"] >= 0, "observed retention setting is invalid")
    return {key: value[key] for key in sorted(value)}


def _validate_state(state: Any) -> dict[str, Any]:
    _require(isinstance(state, dict), "observed Pi-hole state must be an object")
    _finite(state)
    _require(set(state) == _STATE_FAMILIES, "observed Pi-hole state must contain exactly the supported collections")
    result: dict[str, Any] = {"base": _validate_base_state(state["base"])}
    for family in sorted(_STATE_COLLECTIONS):
        entries = state[family]
        _require(isinstance(entries, list), "observed Pi-hole collection must be a list")
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            _require(isinstance(entry, dict), "observed Pi-hole collection member must be an object")
            _require(not (set(entry) - _STATE_KEYS[family]), "observed Pi-hole object contains unsupported fields")
            _require("managed" in entry and isinstance(entry["managed"], bool), "observed object must contain a boolean managed marker")
            managed = entry["managed"]
            if managed:
                _require(_MANAGED_REQUIRED[family] <= set(entry), "managed observed object is missing effective fields")
            if family == "groups":
                _text(entry.get("name"), "observed group name")
                if "description" in entry:
                    _text(entry["description"], "observed group description", allow_empty=True)
                if "enabled" in entry:
                    _require(isinstance(entry["enabled"], bool), "observed group enabled setting is invalid")
            elif family == "adlists":
                _text(entry.get("address"), "observed adlist address")
                if "description" in entry:
                    _text(entry["description"], "observed adlist description", allow_empty=True)
                if "type" in entry:
                    _text(entry["type"], "observed adlist type", allow_empty=True)
                if "enabled" in entry:
                    _require(isinstance(entry["enabled"], bool), "observed adlist enabled setting is invalid")
            elif family == "clients":
                _text(entry.get("identifier"), "observed client identity")
                _require(entry.get("group") in {"normal", "kids"}, "observed client group is invalid")
                if "comment" in entry:
                    _text(entry["comment"], "observed client comment", allow_empty=True)
            elif family == "localDns":
                _text(entry.get("hostname"), "observed local DNS hostname")
                _text(entry.get("domain"), "observed local DNS domain")
                for field in ("recordRef", "aliasRef", "rr", "server", "target", "targetRr", "description"):
                    if field in entry:
                        _text(entry[field], f"observed local DNS {field}", allow_empty=True)
                if "ttl" in entry:
                    _require(isinstance(entry["ttl"], int) and not isinstance(entry["ttl"], bool) and entry["ttl"] >= 0, "observed local DNS ttl is invalid")
                for field in ("addptr", "enabled"):
                    if field in entry:
                        _require(isinstance(entry[field], bool), f"observed local DNS {field} setting is invalid")
            else:
                _require(isinstance(entry.get("kind"), str) and entry["kind"] in _RULE_KINDS, "observed rule kind is invalid")
                _require("value" in entry and isinstance(entry["value"], str), "observed rule value is invalid")
                _text(entry["value"], "observed rule value")
                if "domain" in entry:
                    _text(entry["domain"], "observed rule domain", allow_empty=True)
                if "enabled" in entry:
                    _require(isinstance(entry["enabled"], bool), "observed rule enabled setting is invalid")
            if family == "groups":
                key = f"group:{_text(entry.get('name'), 'observed group name')}"
            elif family == "adlists":
                key = f"adlist:{_digest(_text(entry.get('address'), 'observed adlist address'))}"
            elif family == "clients":
                key = _opaque_client_key(_text(entry.get("identifier"), "observed client identity"))
            elif family == "localDns":
                raw_key = entry.get("recordRef") or entry.get("aliasRef")
                if raw_key is None:
                    raw_key = f"{entry.get('domain', '')}/{entry.get('hostname', '')}/{entry.get('rr', entry.get('target', 'alias'))}"
                key = f"local-dns:{_digest(_text(raw_key, 'observed local DNS key'))}"
            else:
                key = f"rule:{_text(entry['kind'], 'observed rule kind')}:{_digest(entry['value'])}"
            owner = entry.get("owner")
            if owner is not None:
                owner = _text(owner, "observed owner")
            _require(not managed or owner == OWNER, "managed observed object has an invalid owner")
            _require(owner != OWNER or managed, "observed owner marker is ambiguous")
            _require(key not in seen, "ambiguous duplicate observed Pi-hole object")
            seen.add(key)
            copy_entry = dict(entry)
            copy_entry["_key"] = key
            copy_entry["_managed"] = managed
            output.append(copy_entry)
        result[family] = output
    return result


def _state_semantic(family: str, entry: dict[str, Any], declared: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = tuple(declared) if declared is not None else {
        "groups": ("name", "description", "enabled"),
        "adlists": ("address", "type", "description", "enabled"),
        "clients": ("group", "comment"),
        "localDns": tuple(sorted(_STATE_KEYS["localDns"] - _STATE_COMMON)),
        "rules": ("kind", "value", "domain", "enabled"),
    }[family]
    return {field: entry[field] for field in fields if field in entry}


def _desired_semantic(family: str, entry: dict[str, Any]) -> dict[str, Any]:
    if family == "clients":
        return {field: entry[field] for field in ("group",) if field in entry}
    if family == "adlists":
        return {field: entry[field] for field in ("address", "description", "enabled", "type") if field in entry}
    if family == "groups":
        return {field: entry[field] for field in ("name", "description", "enabled") if field in entry}
    if family == "localDns":
        return {key: value for key, value in entry.items() if key not in {"key", "managed", "owner"}}
    return {field: entry[field] for field in ("kind", "value") if field in entry}


def _reconciliation(desired: dict[str, Any], observed: Any) -> dict[str, list[dict[str, Any]]]:
    if observed is None:
        observed_by_family: dict[str, Any] = {"base": None, **{family: [] for family in _STATE_COLLECTIONS}}
    else:
        observed_by_family = _validate_state(observed)
    plan = {"create": [], "update": [], "delete": [], "preserveUnmanaged": []}
    if observed_by_family["base"] is None:
        plan["create"].append({"family": "base", "key": "base", "managed": True})
    elif observed_by_family["base"] != desired["base"]:
        plan["update"].append({"family": "base", "key": "base", "managed": True})
    for family in sorted(_STATE_COLLECTIONS):
        desired_by_key = {item["key"]: item for item in desired[family]}
        observed_by_key = {item["_key"]: item for item in observed_by_family[family]}
        for key in sorted(desired_by_key):
            wanted = desired_by_key[key]
            current = observed_by_key.get(key)
            if current is None:
                plan["create"].append({"family": family, "key": key, "managed": True})
            elif not current["_managed"]:
                plan["preserveUnmanaged"].append({"family": family, "key": key, "managed": False, "reason": "unmanaged-object"})
            else:
                declared = _desired_semantic(family, wanted)
                if _state_semantic(family, current, declared) != declared:
                    plan["update"].append({"family": family, "key": key, "managed": True})
        for key in sorted(observed_by_key):
            current = observed_by_key[key]
            if not current["_managed"]:
                if key not in desired_by_key:
                    plan["preserveUnmanaged"].append({"family": family, "key": key, "managed": False, "reason": "unmanaged-object"})
            elif key not in desired_by_key:
                plan["delete"].append({"family": family, "key": key, "managed": True, "reason": "stale-managed-object"})
    return plan


def render_policy(data: dict[str, Any], target: str, observed: Any = None) -> dict[str, Any]:
    """Return a deterministic, offline policy artifact and dry-run plan."""
    target = _validate_target(target)
    normalized = _normalize_policy(data)
    desired = _desired_objects(normalized)
    revision_input = {"schemaVersion": SCHEMA_VERSION, "policy": normalized}
    managed_fingerprint_input = {family: desired[family] for family in ("base", "adlists", "groups", "clients", "localDns", "rules")}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "apply": False,
        "target": target,
        "owner": OWNER,
        "policyRevision": _digest(revision_input),
        "managedObjectFingerprint": _digest(managed_fingerprint_input),
        "desired": desired,
        "reconciliation": _reconciliation(desired, observed),
        "notes": [
            "Dry-run only; no Pi-hole API or SQLite operation is performed.",
            "Only objects marked with the shared policy owner are deletion candidates.",
            "Client identities are represented by opaque fingerprints, never raw identifiers.",
        ],
    }


def render(data: dict[str, Any], target: str, observed: Any = None) -> dict[str, Any]:
    return render_policy(data, target, observed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-json", type=Path, required=True)
    parser.add_argument("--state-json", type=Path)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", default=True, help="render the no-apply plan (the default)")
    parser.add_argument("--apply", action="store_true", help="rejected: this first slice is dry-run only")
    return parser


def _write_output(path: Path | None, output: str) -> bool:
    try:
        if path:
            path.write_text(output)
        else:
            sys.stdout.write(output)
    except (OSError, UnicodeError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply:
        print("policy reconciliation is dry-run only", file=sys.stderr)
        return 2
    try:
        data = load_json(args.inventory_json)
        _require(isinstance(data, dict), "policy input must be an object")
        observed = load_json(args.state_json) if args.state_json else None
        plan = render_policy(data, args.target, observed)
        output = json.dumps(plan, indent=2, sort_keys=True, allow_nan=False) + "\n"
        _require(_write_output(args.output, output), "unable to write JSON policy output")
        return 0
    except PolicyError as exc:
        print(f"Pi-hole policy render failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
