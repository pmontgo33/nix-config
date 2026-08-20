"""Adapter between the offline Pi-hole policy renderer and the v6 live reconciler.

This module is intentionally offline.  It never contacts a Pi-hole, opens a
socket, decrypts SOPS, or shells out.  It only transforms in-memory data:

  render_policy(...)["desired"]  ->  live_reconcile._desired shape

The offline policy renderer (``scripts.pihole.policy_reconcile``) produces a
logical ``desired`` view of managed resources.  The live reconciler
(``scripts.pihole.live_reconcile``) consumes a Pi-hole v6 API-shaped view of
the same data.  Bridging the two without leaking identifiers is the adapter's
sole responsibility.

The shape contract for ``live_reconcile._desired`` is:

    {
        "base":    {"dns": {"upstreams": [...], "interface": ..., "queryLogging": bool},
                    "database": {"maxDBdays": int}},
        "groups":  [{"name", "description", "enabled"}],
        "adlists": [{"address", "description", "type", "groups", "enabled"}],
        "clients": [{"identifier", "group"}],
        "localDns": [],
        "rules":   {"allow": [], "block": []},
    }

The shape contract for ``policy_reconcile.render_policy(...)["desired"]`` is:

    {
        "base":    {"upstreams": [...], "listeningInterfaces": [...],
                    "queryLogging": bool, "retention": int},
        "groups":  [{"key", "managed", "owner", "name", "description", "enabled"}],
        "adlists": [{"key", "managed", "owner", "address", "description",
                     "enabled", "type"}],
        "clients": [{"key", "managed", "owner", "clientRef", "device",
                     "group"}],
        "localDns": [...],
        "rules":   {"allow": [...], "block": [...]},
    }

Only the public ``adapt`` entry point should be called externally; every other
helper is private to the module.
"""

from __future__ import annotations

from typing import Any


class LiveAdapterError(Exception):
    """Raised when the offline policy cannot be adapted to the live shape."""


_ADLIST_TYPES = frozenset({"block", "allow"})


def _require(condition: object, message: str) -> None:
    if condition is not True:
        raise LiveAdapterError(message)


def _text(value: Any, field: str) -> str:
    _require(type(value) is str and bool(value) is True, f"{field} must be a non-empty string")
    return value


def _bool(value: Any, field: str) -> bool:
    _require(type(value) is bool, f"{field} must be a boolean")
    return value


def _list_of(value: Any, field: str, *, allow_empty: bool = True) -> list[Any]:
    _require(type(value) is list, f"{field} must be a list")
    if not allow_empty and not value:
        raise LiveAdapterError(f"{field} must not be empty")
    return list(value)


def adapt(rendered: dict[str, Any]) -> dict[str, Any]:
    """Convert ``render_policy`` output into the ``live_reconcile`` input shape.

    Parameters
    ----------
    rendered:
        The full output of ``policy.render_policy``.  Only the ``desired``
        field is consumed; the rest is ignored.  Accepting the full envelope
        keeps the call site simple.

    Returns
    -------
    dict
        A fresh ``live_reconcile._desired``-compatible dictionary.  The
        returned dictionary is detached from the input so the caller may
        serialise it without further defensive copying.
    """
    _require(type(rendered) is dict, "rendered policy must be an object")
    desired = rendered.get("desired")
    _require(type(desired) is dict, "rendered policy must include a desired object")
    base = _adapt_base(desired.get("base"))
    groups = _adapt_groups(desired.get("groups"))
    adlists = _adapt_adlists(desired.get("adlists"), group_names={group["name"] for group in groups})
    clients = _adapt_clients(desired.get("clients"))
    local_dns = _adapt_local_dns(desired.get("localDns"))
    rules = _adapt_rules(desired.get("rules"))
    return {
        "base": base,
        "groups": groups,
        "adlists": adlists,
        "clients": clients,
        "localDns": local_dns,
        "rules": rules,
    }


def _adapt_base(value: Any) -> dict[str, Any]:
    _require(type(value) is dict, "desired.base must be an object")
    upstreams = _list_of(value.get("upstreams"), "desired.base.upstreams")
    for upstream in upstreams:
        _text(upstream, "desired.base.upstreams entry")
    interfaces = _list_of(value.get("listeningInterfaces"), "desired.base.listeningInterfaces")
    for interface in interfaces:
        _text(interface, "desired.base.listeningInterfaces entry")
    query_logging = _bool(value.get("queryLogging"), "desired.base.queryLogging")
    retention = value.get("retention")
    _require(type(retention) is int, "desired.base.retention must be an integer")
    return {
        "dns": {"upstreams": upstreams, "interface": interfaces[0], "queryLogging": query_logging},
        "database": {"maxDBdays": retention},
    }


def _adapt_groups(value: Any) -> list[dict[str, Any]]:
    _require(type(value) is list, "desired.groups must be a list")
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for entry in value:
        _require(type(entry) is dict, "desired.groups entry must be an object")
        name = _text(entry.get("name"), "desired.groups.name")
        _require(name not in seen, f"duplicate desired group: {name}")
        seen.add(name)
        description = entry.get("description")
        _require(type(description) is str, "desired.groups.description must be a string")
        enabled = _bool(entry.get("enabled", True), "desired.groups.enabled")
        output.append({"name": name, "description": description, "enabled": enabled})
    return sorted(output, key=lambda item: item["name"])


def _adapt_adlists(value: Any, *, group_names: set[str]) -> list[dict[str, Any]]:
    _require(type(value) is list, "desired.adlists must be a list")
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    for entry in value:
        _require(type(entry) is dict, "desired.adlists entry must be an object")
        address = _text(entry.get("address"), "desired.adlists.address")
        list_type_value = entry.get("type")
        kind_value = entry.get("kind")
        _require(list_type_value is None or (type(list_type_value) is str and bool(list_type_value) is True), "desired.adlists.type must be a non-empty string when provided")
        _require(kind_value is None or (type(kind_value) is str and bool(kind_value) is True), "desired.adlists.kind must be a non-empty string when provided")
        if list_type_value is None and kind_value is None:
            list_type = "block"
        elif list_type_value is not None:
            list_type = list_type_value
        else:
            _require(kind_value in {"standard", "kids"}, "desired.adlists.kind must be standard or kids")
            list_type = "block" if kind_value == "standard" else "allow"
        _require(list_type in _ADLIST_TYPES, f"desired.adlists.type must be one of {sorted(_ADLIST_TYPES)}")
        key = (address, list_type)
        _require(key not in seen, f"duplicate desired adlist: {address} ({list_type})")
        seen.add(key)
        description = entry.get("description")
        _require(type(description) is str, "desired.adlists.description must be a string")
        groups_value = _list_of(entry.get("groups", []), "desired.adlists.groups")
        resolved_groups: list[str] = []
        for name in groups_value:
            _text(name, "desired.adlists.groups entry")
            _require(name in group_names, f"desired.adlists references unknown group: {name}")
            resolved_groups.append(name)
        enabled = _bool(entry.get("enabled"), "desired.adlists.enabled")
        output.append({
            "address": address,
            "description": description,
            "type": list_type,
            "groups": resolved_groups,
            "enabled": enabled,
        })
    return sorted(output, key=lambda item: (item["type"], item["address"]))


def _adapt_clients(value: Any) -> list[dict[str, Any]]:
    _require(type(value) is list, "desired.clients must be a list")
    seen_keys: set[str] = set()
    output: list[dict[str, Any]] = []
    for entry in value:
        _require(type(entry) is dict, "desired.clients entry must be an object")
        opaque_key = _text(entry.get("key"), "desired.clients.key")
        _require(opaque_key not in seen_keys, "duplicate desired client opaque key")
        seen_keys.add(opaque_key)
        group = entry.get("group")
        _require(type(group) is str and bool(group) is True, "desired.clients.group must be a non-empty string")
        output.append({"identifier": opaque_key, "group": group})
    return sorted(output, key=lambda item: item["identifier"])


def _adapt_local_dns(value: Any) -> list[dict[str, Any]]:
    _require(type(value) is list, "desired.localDns must be a list")
    if value:
        for entry in value:
            _require(type(entry) is dict, "desired.localDns entry must be an object")
        raise LiveAdapterError("desired.localDns must be empty for the baseline live policy")
    return []


def _adapt_rules(value: Any) -> dict[str, list[Any]]:
    _require(type(value) in (dict, list), "desired.rules must be an object or list")
    allow: list[Any] = []
    block: list[Any] = []
    if type(value) is list:
        for entry in value:
            _require(type(entry) is dict, "desired.rules entry must be an object")
            kind = entry.get("kind")
            _require(kind in {"allow", "block"}, "desired.rules.kind must be allow or block")
            (allow if kind == "allow" else block).append(entry.get("value"))
    else:
        allow = _list_of(value.get("allow", []), "desired.rules.allow")
        block = _list_of(value.get("block", []), "desired.rules.block")
    return {"allow": allow, "block": block}


def resolve_identities(rendered_inventory: dict[str, Any], identities: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Replace ``identifier`` placeholders with the resolved MAC from SOPS.

    Parameters
    ----------
    rendered_inventory:
        The output of ``render_inventory.render``.  Its ``piholeClients``
        entries are expected to carry ``status: pending-encrypted-identity-resolution``
        with ``identifier: None``.
    identities:
        Mapping of identity ref (without ``identityRef:`` prefix) to a
        payload that contains a ``mac`` field.  The caller is responsible
        for decrypting and sanitising this mapping; this function only
        consumes the bytes it needs.

    Returns
    -------
    dict
        A fresh inventory dictionary with ``identityResolutionRequired``,
        ``unresolvedIdentityRefs`` and ``piholeClients`` rewritten so the
        offline policy renderer accepts the result.
    """
    _require(type(rendered_inventory) is dict, "rendered inventory must be an object")
    _require(type(identities) is dict, "identities must be an object")
    clients = rendered_inventory.get("piholeClients")
    _require(type(clients) is list, "rendered inventory.piholeClients must be a list")

    unresolved_refs = _list_of(rendered_inventory.get("unresolvedIdentityRefs", []), "rendered inventory.unresolvedIdentityRefs")
    expected_refs = {ref for ref in unresolved_refs}
    available_refs = {ref for ref in identities}
    _require(expected_refs == available_refs, "identity mapping does not cover every unresolved identity reference")

    resolved_clients: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    seen_mac: set[str] = set()
    for client in clients:
        _require(type(client) is dict, "piholeClients entry must be an object")
        ref = client.get("clientRef")
        _require(type(ref) is str and ref.startswith("identityRef:"), "piholeClients entry must carry a clientRef")
        identity_ref = ref[len("identityRef:"):]
        _require(identity_ref not in seen_refs, f"duplicate resolved client ref: {identity_ref}")
        seen_refs.add(identity_ref)
        _require(client.get("status") == "pending-encrypted-identity-resolution", "piholeClients entry must be pending resolution")
        payload = identities.get(identity_ref)
        _require(type(payload) is dict and type(payload.get("mac")) is str, f"identity mapping missing for {identity_ref}")
        mac = payload["mac"]
        _require(mac not in seen_mac, f"duplicate resolved client identifier: {mac}")
        seen_mac.add(mac)
        rebuilt = dict(client)
        rebuilt["identifier"] = mac
        rebuilt["status"] = "resolved"
        resolved_clients.append(rebuilt)

    updated = dict(rendered_inventory)
    updated["piholeClients"] = resolved_clients
    updated["unresolvedIdentityRefs"] = []
    updated["identityResolutionRequired"] = False

    assignments: dict[str, str] = {}
    for client in resolved_clients:
        ref = client.get("clientRef")
        group = client.get("group")
        if type(ref) is str and type(group) is str:
            assignments[ref] = group
    policy_section = updated.get("policy")
    if type(policy_section) is dict:
        new_policy = dict(policy_section)
        new_policy["groupAssignments"] = dict(sorted(assignments.items()))
        updated["policy"] = new_policy
    return updated
