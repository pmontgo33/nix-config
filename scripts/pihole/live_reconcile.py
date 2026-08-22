#!/usr/bin/env python3
"""Guarded, owner-scoped Pi-hole v6 policy rollout.

Accepts resolved inventory, a runtime credential callback, and an injected
transport. Secret-store and SSH integration are intentionally out of scope.
"""
from __future__ import annotations

import ipaddress
import hashlib
import hmac
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

OWNER = "shared-pihole-policy"
OWNER_MARKER = "[owner=shared-pihole-policy]"
APPLY_CONFIRMATION = "APPLY_SHARED_PIHOLE_POLICY"
BASELINE_BASE = {"dns": {"upstreams": ["192.168.86.1"], "interface": "eth0", "queryLogging": True}, "database": {"maxDBdays": 91}}
_DESIRED_FIELDS = {
    "groups": frozenset({"name", "description", "enabled"}),
    "clients": frozenset({"identifier", "group"}),
}
_OBSERVED_FIELDS = {
    "groups": frozenset({"id", "name", "comment", "enabled", "date_added", "date_modified"}),
    "clients": frozenset({"id", "client", "comment", "groups", "name", "date_added", "date_modified"}),
    "domains": frozenset({"id", "domain", "comment", "type", "enabled", "groups", "date_added", "date_modified"}),
}
_READ_ENDPOINTS = (("config", "/api/config"), ("groups", "/api/groups"), ("domains", "/api/domains"), ("clients", "/api/clients"), ("version", "/api/info/version"))
_READ_PATHS = frozenset(path for _, path in _READ_ENDPOINTS)
_COLLECTIONS = frozenset({"groups", "domains", "clients"})
_CONFIG_FIELDS = frozenset({"dns", "dhcp", "ntp", "resolver", "database", "webserver", "files", "misc", "debug"})
_VERSION_FIELDS = frozenset({"core", "web", "ftl", "docker"})
_CONFIG_NESTED_FIELDS = {
    "dns": frozenset({"upstreams", "CNAMEdeepInspect", "blockESNI", "EDNS0ECS", "ignoreLocalhost", "showDNSSEC", "analyzeOnlyAandAAAA", "piholePTR", "replyWhenBusy", "blockTTL", "hosts", "domainNeeded", "expandHosts", "domain", "bogusPriv", "dnssec", "interface", "hostRecord", "listeningMode", "queryLogging", "cnameRecords", "port", "localise", "cache", "revServers", "blocking", "specialDomains", "reply", "rateLimit"}),
    "database": frozenset({"DBimport", "maxDBdays", "DBinterval", "useWAL", "forceDisk", "network"}),
    "dhcp": frozenset({"active", "start", "end", "router", "netmask", "leaseTime", "ipv6", "rapidCommit", "multiDNS", "logging", "ignoreUnknownClients", "hosts"}),
    "webserver": frozenset({"domain", "acl", "port", "threads", "headers", "serve_all", "advancedOpts", "session", "tls", "paths", "interface", "api"}),
    "ntp": frozenset({"ipv4", "ipv6", "sync"}),
    "resolver": frozenset({"resolveIPv4", "resolveIPv6", "macNames", "networkNames", "refreshNames"}),
    "files": frozenset({"pid", "database", "tmp_db", "gravity", "gravity_tmp", "macvendor", "pcap", "log"}),
    "misc": frozenset({"nice", "delay_startup", "addr2line", "etc_dnsmasq_d", "privacylevel", "dnsmasq_lines", "extraLogging", "readOnly", "normalizeCPU", "hide_dnsmasq_warn", "hide_connection_error", "check"}),
    "debug": frozenset({"database", "networking", "locks", "queries", "flags", "shmem", "gc", "arp", "regex", "api", "tls", "overtime", "status", "caps", "dnssec", "vectors", "resolver", "edns0", "clients", "aliasclients", "events", "helper", "config", "inotify", "webserver", "extra", "reserved", "ntp", "netlink", "timing", "performance", "all"}),
}
_VERSION_COMPONENT_FIELDS = frozenset({"local", "remote"})
_VERSION_LOCAL_FIELDS = frozenset({"branch", "version", "hash", "date"})
_VERSION_REMOTE_FIELDS = frozenset({"version", "hash"})
_SAFE_ID = re.compile(r"^[0-9]+$")
_SAFE_SID = re.compile(r"^[A-Za-z0-9+/=._~-]{1,256}$")
_OWNER_RE = re.compile(r"(?:^|\s)\[owner=shared-pihole-policy\](?:$|\s)")


class LivePolicyError(ValueError):
    """Raised when a live rollout cannot be proven safe."""


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _error(message: str) -> LivePolicyError:
    error = LivePolicyError(message)
    error.__cause__ = None
    error.__context__ = None
    return error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _error(message)


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise _error("non-finite JSON value")
    if isinstance(value, dict):
        for key, child in value.items():
            _require(isinstance(key, str), "JSON object keys must be strings")
            _finite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _finite(child)


def _typed_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_typed_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def _text(value: Any, message: str, *, empty: bool = False) -> str:
    _require(isinstance(value, str), message)
    _require(empty or bool(value), message)
    _require(not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value), message)
    return value


def _safe_id(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and _SAFE_ID.fullmatch(value):
        return int(value)
    raise _error("malformed Pi-hole state")


def _owner_comment(owner_token: str | None = None) -> str:
    return OWNER_MARKER if owner_token is None else f"{OWNER_MARKER} {owner_token}"


def _managed(comment: Any, owner_token: str | None = None) -> bool:
    return owner_token is not None and isinstance(comment, str) and comment == _owner_comment(owner_token)


def _comment(description: Any = "", owner_token: str | None = None) -> str:
    _text(description, "malformed policy", empty=True)
    return _owner_comment(owner_token)


def _ownership_token(password: str) -> str:
    return hmac.new(password.encode(), f"{OWNER}:v1".encode(), hashlib.sha256).hexdigest()


def _desired(inventory: Any) -> dict[str, Any]:
    if isinstance(inventory, dict) and set(inventory) == {"desired"}:
        inventory = inventory["desired"]
    _require(isinstance(inventory, dict), "malformed policy inventory")
    _finite(inventory)
    expected = {"base", "groups", "adlists", "clients", "localDns", "rules"}
    _require(set(inventory) == expected, "malformed policy inventory")
    _require(_typed_equal(inventory["base"], BASELINE_BASE), "unsupported base configuration")
    _require(isinstance(inventory["groups"], list) and isinstance(inventory["adlists"], list) and isinstance(inventory["clients"], list), "malformed policy inventory")
    _require(inventory["localDns"] == [] and inventory["rules"] == {"allow": [], "block": []}, "unsupported live policy resources")
    groups: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in inventory["groups"]:
        _require(isinstance(raw, dict), "malformed policy inventory")
        _require(set(raw) <= _DESIRED_FIELDS["groups"], "malformed policy inventory")
        name = _text(raw.get("name"), "malformed policy")
        _require(name not in names, "ambiguous policy inventory")
        names.add(name)
        enabled = raw.get("enabled", True)
        _require(isinstance(enabled, bool), "malformed policy inventory")
        groups.append({"name": name, "description": _text(raw.get("description", ""), "malformed policy", empty=True), "enabled": enabled})
    _require(inventory["adlists"] == [], "adlists are owned by services.pihole-ftl.lists")
    adlists: list[dict[str, Any]] = []
    clients: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for raw in inventory["clients"]:
        _require(isinstance(raw, dict), "malformed policy inventory")
        _require(set(raw) <= _DESIRED_FIELDS["clients"], "malformed policy inventory")
        identifier = _text(raw.get("identifier"), "malformed policy")
        _require(identifier not in identifiers, "ambiguous policy inventory")
        identifiers.add(identifier)
        group = _text(raw.get("group"), "malformed policy")
        _require(group in names, "malformed policy inventory")
        clients.append({"identifier": identifier, "group": group})
    return {"base": dict(BASELINE_BASE), "groups": sorted(groups, key=lambda x: x["name"]), "adlists": sorted(adlists, key=lambda x: x["address"]), "clients": sorted(clients, key=lambda x: (x["group"], x["identifier"]))}


def _validate_took(value: Any) -> None:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), "malformed Pi-hole state")


def _validate_version(version: dict[str, Any]) -> None:
    _require(set(version) <= _VERSION_FIELDS and isinstance(version.get("core"), dict), "malformed Pi-hole state")
    for component in ("core", "web", "ftl"):
        value = version.get(component)
        if value is None:
            continue
        _require(isinstance(value, dict) and set(value) <= _VERSION_COMPONENT_FIELDS, "malformed Pi-hole state")
        for side, fields in (("local", _VERSION_LOCAL_FIELDS), ("remote", _VERSION_REMOTE_FIELDS)):
            side_value = value.get(side)
            if side_value is not None:
                _require(isinstance(side_value, dict) and set(side_value) <= fields, "malformed Pi-hole state")
                for field_value in side_value.values():
                    _require(field_value is None or isinstance(field_value, str), "malformed Pi-hole state")
    docker = version.get("docker")
    if docker is not None:
        _require(isinstance(docker, dict) and set(docker) <= {"local", "remote"}, "malformed Pi-hole state")
        _require(all(value is None or isinstance(value, str) for value in docker.values()), "malformed Pi-hole state")


def _validate_config(config: dict[str, Any]) -> None:
    _require(set(config) <= _CONFIG_FIELDS, "malformed Pi-hole state")
    for section, fields in _CONFIG_NESTED_FIELDS.items():
        value = config.get(section)
        if value is not None:
            _require(isinstance(value, dict) and set(value) <= fields, "malformed Pi-hole state")
    def check(value: Any, schema: dict[str, Any]) -> None:
        _require(isinstance(value, dict), "malformed Pi-hole state")
        for key, expected in schema.items():
            if key not in value:
                continue
            actual = value[key]
            if isinstance(expected, dict):
                _require(isinstance(actual, dict) and set(actual) <= set(expected), "malformed Pi-hole state")
                check(actual, expected)
            elif expected is list:
                _require(isinstance(actual, list), "malformed Pi-hole state")
            else:
                _require(type(actual) is expected, "malformed Pi-hole state")

    schemas = {
        "dns": {"port": int, "queryLogging": bool, "upstreams": list, "interface": str},
        "database": {"DBimport": bool, "maxDBdays": int, "DBinterval": int, "useWAL": bool, "forceDisk": bool, "network": {"parseARPcache": bool, "expire": int}},
        "ntp": {"ipv4": {"active": bool, "address": str}, "ipv6": {"active": bool, "address": str}, "sync": {"active": bool, "server": str, "interval": int, "count": int, "rtc": {"set": bool, "device": str, "utc": bool}}},
        "resolver": {"resolveIPv4": bool, "resolveIPv6": bool, "macNames": bool, "networkNames": bool, "refreshNames": str},
        "files": {"pid": str, "database": str, "tmp_db": str, "gravity": str, "gravity_tmp": str, "macvendor": str, "pcap": str, "log": {"ftl": str, "dnsmasq": str, "webserver": str}},
        "misc": {"nice": int, "delay_startup": int, "addr2line": bool, "etc_dnsmasq_d": bool, "privacylevel": int, "dnsmasq_lines": list, "extraLogging": bool, "readOnly": bool, "normalizeCPU": bool, "hide_dnsmasq_warn": bool, "hide_connection_error": bool, "check": {"load": bool, "shmem": int, "disk": int}},
        "debug": {key: bool for key in _CONFIG_NESTED_FIELDS["debug"]},
    }
    for section, schema in schemas.items():
        if section in config:
            check(config[section], schema)
    dhcp = config.get("dhcp")
    if dhcp is not None:
        expected_types = {"active": bool, "start": str, "end": str, "router": str, "netmask": str, "leaseTime": str, "ipv6": bool, "rapidCommit": bool, "multiDNS": bool, "logging": bool, "ignoreUnknownClients": bool, "hosts": list}
        for key, value in dhcp.items():
            expected = expected_types[key]
            _require(isinstance(value, expected) and (expected is not bool or isinstance(value, bool)), "malformed Pi-hole state")
    webserver = config.get("webserver")
    if webserver is not None:
        nested = {
            "session": frozenset({"timeout", "restore"}),
            "tls": frozenset({"cert", "validity"}),
            "paths": frozenset({"webroot", "webhome", "prefix"}),
            "interface": frozenset({"boxed", "theme"}),
            "api": frozenset({"max_sessions", "prettyJSON", "password", "pwhash", "totp_secret", "app_pwhash", "app_sudo", "cli_pw", "excludeClients", "excludeDomains", "maxHistory", "maxClients", "client_history_global_max", "allow_destructive", "temp"}),
        }
        for key, fields in nested.items():
            if key in webserver:
                _require(isinstance(webserver[key], dict) and set(webserver[key]) <= fields, "malformed Pi-hole state")
        api = webserver.get("api")
        if isinstance(api, dict):
            for key in ("max_sessions", "maxHistory", "maxClients"):
                if key in api:
                    _require(isinstance(api[key], int) and not isinstance(api[key], bool), "malformed Pi-hole state")
            for key in ("prettyJSON", "app_sudo", "cli_pw", "client_history_global_max", "allow_destructive"):
                if key in api:
                    _require(isinstance(api[key], bool), "malformed Pi-hole state")
            for key in ("excludeClients", "excludeDomains"):
                if key in api:
                    _require(isinstance(api[key], list) and all(isinstance(item, str) for item in api[key]), "malformed Pi-hole state")
            if "temp" in api:
                _require(isinstance(api["temp"], dict) and set(api["temp"]) <= {"limit", "unit"}, "malformed Pi-hole state")


def _validate_processed(value: Any, message: str) -> None:
    _require(value is None or (isinstance(value, dict) and set(value) <= {"success", "errors"}), message)
    if isinstance(value, dict):
        _require(all(isinstance(value.get(key), list) for key in value), message)
        for item in value.get("success", []):
            _require(isinstance(item, dict) and set(item) <= {"item"} and isinstance(item.get("item"), str), message)
        for item in value.get("errors", []):
            _require(isinstance(item, dict) and set(item) <= {"item", "error"} and isinstance(item.get("item"), str) and isinstance(item.get("error"), str), message)


def _validate_item(item: dict[str, Any], name: str, *, complete: bool) -> None:
    _require(set(item) <= _OBSERVED_FIELDS[name], "malformed Pi-hole state")
    _require("id" in item, "malformed Pi-hole state")
    _safe_id(item["id"])
    if complete:
        _require("comment" in item and (item["comment"] is None or isinstance(item["comment"], str)), "malformed Pi-hole state")
        if name in {"groups", "lists"}:
            _require("enabled" in item and isinstance(item["enabled"], bool), "malformed Pi-hole state")
    if "comment" in item:
        _require(item["comment"] is None or isinstance(item["comment"], str), "malformed Pi-hole state")
        if isinstance(item["comment"], str):
            _text(item["comment"], "malformed Pi-hole state", empty=True)
    if "enabled" in item:
        _require(isinstance(item["enabled"], bool), "malformed Pi-hole state")
    for field in ("date_added", "date_modified", "date_updated", "number", "invalid_domains", "abp_entries", "status"):
        if field in item:
            _require(isinstance(item[field], int) and not isinstance(item[field], bool) and item[field] >= 0, "malformed Pi-hole state")
    if name == "groups" and "name" in item:
        _text(item["name"], "malformed Pi-hole state")
    if name == "clients" and "client" in item:
        _text(item["client"], "malformed Pi-hole state")
    if "groups" in item:
        _require(isinstance(item["groups"], list), "malformed Pi-hole state")
        for value in item["groups"]:
            _safe_id(value)


def _collection(payload: Any, name: str) -> list[dict[str, Any]]:
    value = payload
    if isinstance(payload, dict):
        _require(name in payload and set(payload) <= {name, "took", "processed"}, "malformed Pi-hole state")
        if "took" in payload:
            _validate_took(payload["took"])
        if "processed" in payload:
            _validate_processed(payload["processed"], "malformed Pi-hole state")
        value = payload[name]
    _require(isinstance(value, list), "malformed Pi-hole state")
    output: list[dict[str, Any]] = []
    ids: set[int] = set()
    for item in value:
        _require(isinstance(item, dict), "malformed Pi-hole state")
        _validate_item(item, name, complete=True)
        item_id = _safe_id(item.get("id"))
        _require(item_id not in ids, "malformed Pi-hole state")
        ids.add(item_id)
        normalized_item = dict(item)
        if name in {"lists", "clients"}:
            _require("groups" in item and isinstance(item["groups"], list), "malformed Pi-hole state")
            group_values = item["groups"]
            normalized_item["groups"] = [_safe_id(value) for value in group_values]
        output.append(normalized_item)
    return output


def _state(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "malformed Pi-hole state")
    _finite(raw)
    allowed = frozenset({"config", "groups", "domains", "clients", "version"})
    _require(frozenset(raw) in {allowed, allowed | {"lists"}}, "malformed Pi-hole state")
    if "lists" not in raw:
        raw = {**raw, "lists": []}
    config = raw["config"]
    if isinstance(config, dict) and "config" in config:
        _require(set(config) <= {"config", "took"} and isinstance(config["config"], dict), "malformed Pi-hole state")
        if "took" in config:
            _validate_took(config["took"])
        config = config["config"]
    version = raw["version"]
    if isinstance(version, dict) and "version" in version:
        _require(set(version) <= {"version", "took"} and isinstance(version["version"], dict), "malformed Pi-hole state")
        if "took" in version:
            _validate_took(version["took"])
        version = version["version"]
    _require(isinstance(config, dict) and isinstance(version, dict), "malformed Pi-hole state")
    _validate_config(config)
    _validate_version(version)
    local_core = version["core"].get("local")
    _require(isinstance(local_core, dict) and isinstance(local_core.get("version"), str) and local_core["version"].startswith("v6."), "unsupported Pi-hole version")
    _finite(config)
    _finite(version)
    result: dict[str, Any] = {"config": dict(config), "version": dict(version)}
    for family in _COLLECTIONS:
        result[family] = _collection(raw[family], family)
    _require(result["domains"] == [], "unsupported live domain state")
    return result


def _validate_observed_base(config: dict[str, Any], desired_base: dict[str, Any]) -> None:
    _require(isinstance(config, dict) and bool(config), "malformed Pi-hole base configuration")
    for key, expected in desired_base.items():
        _require(key in config, "unsupported observed base configuration")
        actual = config[key]
        if isinstance(expected, dict):
            _require(isinstance(actual, dict), "unsupported observed base configuration")
            _validate_observed_base(actual, expected)
        else:
            _require(_typed_equal(actual, expected), "unsupported observed base configuration")


def _group_ids(current: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in current["groups"]:
        name = _text(item.get("name"), "malformed Pi-hole state")
        _require(name not in result, "malformed Pi-hole state")
        result[name] = _safe_id(item["id"])
    return result


def _same_group(wanted: dict[str, Any], current: dict[str, Any], owner_token: str | None = None) -> bool:
    return current.get("name") == wanted["name"] and current.get("comment") == _comment(wanted["description"], owner_token) and current.get("enabled") == wanted["enabled"]


def _same_client(wanted: dict[str, Any], current: dict[str, Any], ids: dict[str, int], owner_token: str | None = None) -> bool:
    expected = ids.get(wanted["group"])
    groups = current.get("groups", [])
    try:
        normalized = [_safe_id(value) for value in groups]
        expected_id = _safe_id(expected)
    except LivePolicyError:
        return False
    return current.get("comment") == _owner_comment(owner_token) and normalized == [expected_id]


def _operations(inventory: Any, observed: Any, owner_token: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    desired = _desired(inventory)
    current = _state(observed)
    _validate_observed_base(current["config"], desired["base"])
    group_ids = _group_ids(current)
    operations: list[dict[str, Any]] = []
    groups_by_name: dict[str, dict[str, Any]] = {}
    for item in current["groups"]:
        name = _text(item.get("name"), "malformed Pi-hole state")
        _require(name not in groups_by_name, "malformed Pi-hole state")
        groups_by_name[name] = item
    desired_group_names = {item["name"] for item in desired["groups"]}
    for wanted in desired["groups"]:
        item = groups_by_name.get(wanted["name"])
        if item is None:
            operations.append({"action": "create", "family": "groups", "desired": wanted})
        elif not _managed(item.get("comment"), owner_token):
            raise _error("unmanaged object conflicts with desired policy")
        elif not _same_group(wanted, item, owner_token):
            _require(_safe_id(item["id"]) != 0, "reserved Pi-hole group cannot be mutated")
            operations.append({"action": "update", "family": "groups", "desired": wanted, "current": item})
    for item in current["groups"]:
        if item["name"] not in desired_group_names and _managed(item.get("comment"), owner_token):
            stale_id = _safe_id(item["id"])
            _require(stale_id != 0, "reserved Pi-hole group cannot be deleted")
            for family in ("clients",):
                for reference in current[family]:
                    if not _managed(reference.get("comment")) and any(_safe_id(value) == stale_id for value in reference.get("groups", [])):
                        raise _error("managed group is referenced by an unmanaged object")
            operations.append({"action": "delete", "family": "groups", "current": item})
    clients_by_identifier: dict[str, dict[str, Any]] = {}
    for item in current["clients"]:
        identifier = _text(item.get("client"), "malformed Pi-hole state")
        _require(identifier not in clients_by_identifier, "malformed Pi-hole state")
        clients_by_identifier[identifier] = item
    desired_identifiers = {item["identifier"] for item in desired["clients"]}
    for wanted in desired["clients"]:
        item = clients_by_identifier.get(wanted["identifier"])
        if item is None:
            operations.append({"action": "create", "family": "clients", "desired": wanted})
        elif not _managed(item.get("comment"), owner_token):
            raise _error("unmanaged object conflicts with desired policy")
        elif not _same_client(wanted, item, group_ids, owner_token):
            operations.append({"action": "update", "family": "clients", "desired": wanted, "current": item})
    for item in current["clients"]:
        if item["client"] not in desired_identifiers and _managed(item.get("comment"), owner_token):
            operations.append({"action": "delete", "family": "clients", "current": item})
    operations.sort(key=lambda x: ({"groups": 0, "clients": 1}[x["family"]], {"create": 0, "update": 1, "delete": 2}[x["action"]], x.get("desired", {}).get("name", x.get("desired", {}).get("address", x["family"]))))
    return current, operations


def _safe_plan(operations: list[dict[str, Any]], current: dict[str, Any], owner_token: str | None = None) -> dict[str, Any]:
    actions = [{"action": x["action"], "family": x["family"], "managed": True} for x in operations]
    preserved: list[dict[str, Any]] = []
    for family in ("groups", "clients"):
        for item in current[family]:
            if not _managed(item.get("comment"), owner_token):
                preserved.append({"family": family, "managed": False, "reason": "unmanaged-object"})
    preserved.sort(key=lambda x: x["family"])
    return {"apply": False, "owner": OWNER, "actions": actions, "preserved": preserved}


def build_plan(inventory: Any, observed: Any, *, owner_token: str | None = None) -> dict[str, Any]:
    current, operations = _operations(inventory, observed, owner_token)
    return _safe_plan(operations, current, owner_token)



_TRANSPORT_FAILURE = object()



def _invoke(function: Callable[[], Any]) -> Any:
    try:
        return function()
    except Exception:
        return _TRANSPORT_FAILURE


def _allowed(method: str, path: str) -> bool:
    if method == "GET":
        return path in _READ_PATHS
    if method == "POST":
        return path in {"/api/auth", "/api/groups", "/api/clients"}
    if method in {"PUT", "DELETE"}:
        return bool(
            re.fullmatch(r"/api/groups/[^/?#]+", path)
            or re.fullmatch(r"/api/clients/[^/?#]+", path)
        )
    return False


def _request(transport: Any, method: str, path: str, *, payload: Any = None, headers: Mapping[str, str] | None = None, guarded: bool = False, origin: str | None = None) -> Any:
    _require(_allowed(method, path), "unsupported Pi-hole API operation")
    _require(getattr(type(transport), "safety_validated", False) is True, "unvalidated Pi-hole transport")
    _require(method == "GET" or (method == "POST" and path == "/api/auth"), "policy mutations require reconcile_live apply gate")
    if guarded and type(transport) is UrllibTransport:
        result = _invoke(lambda: UrllibTransport._request_json(transport, method, path, payload=payload, headers=headers, origin=origin))
        if result is _TRANSPORT_FAILURE:
            raise _error("Pi-hole API request failed")
        return result
    request = getattr(transport, "request", None)
    _require(callable(request), "invalid Pi-hole transport")
    result = _invoke(lambda: request(method, path, payload=payload, headers=dict(headers or {})))
    if result is _TRANSPORT_FAILURE:
        raise _error("Pi-hole API request failed")
    return result


def _credential(credential_callback: Callable[[], str]) -> str:
    _require(callable(credential_callback), "invalid runtime credential callback")
    password = _invoke(credential_callback)
    if password is _TRANSPORT_FAILURE or not isinstance(password, str) or not password or any(ord(char) < 0x20 for char in password):
        raise _error("invalid runtime credential")
    return password


def _authenticate_password(transport: Any, password: str, *, guarded: bool = False, origin: str | None = None) -> str:
    response = _request(transport, "POST", "/api/auth", payload={"password": password}, guarded=guarded, origin=origin)
    if not isinstance(response, dict) or "session" not in response or set(response) - {"session", "took"} or not isinstance(response.get("session"), dict):
        raise _error("malformed Pi-hole authentication response")
    if "took" in response and (not isinstance(response["took"], (int, float)) or isinstance(response["took"], bool) or not math.isfinite(response["took"])):
        raise _error("malformed Pi-hole authentication response")
    session = response["session"]
    if set(session) - {"valid", "totp", "sid", "csrf", "validity", "message"}:
        raise _error("malformed Pi-hole authentication response")
    if session.get("valid") is not True:
        raise _error("malformed Pi-hole authentication response")
    sid = session.get("sid")
    if not isinstance(sid, str) or _SAFE_SID.fullmatch(sid) is None:
        raise _error("malformed Pi-hole authentication response")
    return sid


def _authenticate(transport: Any, credential_callback: Callable[[], str]) -> str:
    return _authenticate_password(transport, _credential(credential_callback))


def _read_current(transport: Any, sid: str, *, guarded: bool = False, origin: str | None = None) -> dict[str, Any]:
    headers = {"X-FTL-SID": sid, "Cookie": f"sid={sid}"}
    raw = {family: _request(transport, "GET", path, headers=headers, guarded=guarded, origin=origin) for family, path in _READ_ENDPOINTS}
    return _state({"config": raw["config"], "groups": _collection(raw["groups"], "groups"), "lists": [], "domains": _collection(raw["domains"], "domains"), "clients": _collection(raw["clients"], "clients"), "version": raw["version"]})


def _created_id(response: Any, family: str) -> int:
    _require(isinstance(response, dict) and family in response and set(response) <= {family, "took", "processed"}, "malformed Pi-hole mutation response")
    if "took" in response:
        _validate_took(response["took"])
    if "processed" in response:
        _validate_processed(response["processed"], "malformed Pi-hole mutation response")
        _require(not response["processed"].get("errors"), "Pi-hole mutation reported errors")
    collection = response[family]
    _require(isinstance(collection, list) and len(collection) == 1 and isinstance(collection[0], dict), "malformed Pi-hole mutation response")
    _validate_item(collection[0], family, complete=False)
    identifier = _safe_id(collection[0]["id"])
    _require(identifier != 0 or family != "groups", "reserved Pi-hole group ID")
    return identifier


def _validate_write_response(response: Any, family: str) -> None:
    if response is None:
        return
    _require(isinstance(response, dict) and set(response) <= {family, "took", "processed"}, "malformed Pi-hole mutation response")
    collection = response[family]
    _require(isinstance(collection, list) and len(collection) == 1 and isinstance(collection[0], dict), "malformed Pi-hole mutation response")
    _validate_item(collection[0], family, complete=False)
    if "took" in response:
        _validate_took(response["took"])
    if "processed" in response:
        _validate_processed(response["processed"], "malformed Pi-hole mutation response")
        _require(not response["processed"].get("errors"), "Pi-hole mutation reported errors")


def reconcile_live(inventory: Any, *, credential_callback: Callable[[], str], transport: Any, apply: bool = False, confirmation: str | None = None) -> dict[str, Any]:
    if apply and (type(confirmation) is not str or confirmation != APPLY_CONFIRMATION):
        raise _error("exact apply confirmation is required")
    if apply:
        if type(transport) is not UrllibTransport:
            raise _error("live apply requires validated Pi-hole transport")
        validated_origin = validate_origin(transport.origin, allow_private_http=getattr(transport, "_allow_private_http", False))
    else:
        validated_origin = None
    _desired(inventory)
    password = _credential(credential_callback)
    sid = _authenticate_password(transport, password, guarded=apply, origin=validated_origin)
    owner_token = _ownership_token(password)
    current = _read_current(transport, sid, guarded=apply, origin=validated_origin)
    current, operations = _operations(inventory, current, owner_token)
    plan = _safe_plan(operations, current, owner_token)
    if not apply:
        plan["verified"] = True
        return plan

    def write_request(method: str, path: str, payload: Any, headers: Mapping[str, str]) -> Any:
        _require(_allowed(method, path), "unsupported Pi-hole API operation")
        request_headers = dict(headers)
        if type(transport) is UrllibTransport:
            body = None
            request_headers = {"Accept": "application/json", **request_headers}
            if payload is not None:
                serialization_failed = False
                try:
                    body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
                except (TypeError, ValueError):
                    serialization_failed = True
                if serialization_failed:
                    raise _error("invalid Pi-hole API request")
                request_headers["Content-Type"] = "application/json"
            request = Request(cast(str, validated_origin) + path, data=body, headers=request_headers, method=method)
            result = _invoke(lambda: build_opener(_NoRedirectHandler).open(request, timeout=transport.timeout))
            if result is _TRANSPORT_FAILURE:
                raise _error("Pi-hole API request failed")
            reader = cast(Callable[[], Any], getattr(result, "read", None))
            _require(callable(reader), "malformed Pi-hole API response")
            raw = _invoke(lambda: reader())
            if raw is _TRANSPORT_FAILURE:
                raise _error("malformed Pi-hole API response")
            if method == "DELETE" and raw in (b"", ""):
                return None
            parsed = _invoke(lambda: json.loads(raw.decode(), parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys))
            if parsed is _TRANSPORT_FAILURE:
                raise _error("malformed Pi-hole API response")
            return parsed
        request = getattr(transport, "request", None)
        _require(callable(request), "invalid Pi-hole transport")
        result = _invoke(lambda: request(method, path, payload=payload, headers=request_headers))
        if result is _TRANSPORT_FAILURE:
            raise _error("Pi-hole API request failed")
        return result

    def apply_operations() -> None:
        created_groups: dict[str, int] = {}
        group_ids = _group_ids(current)
        apply_order = {("groups", "create"): 0, ("groups", "update"): 1, ("clients", "create"): 2, ("clients", "update"): 3, ("clients", "delete"): 4, ("groups", "delete"): 5}
        for op in sorted(operations, key=lambda item: apply_order[(item["family"], item["action"])]):
            family, action = op["family"], op["action"]
            wanted = cast(dict[str, Any], op.get("desired"))
            existing = cast(dict[str, Any], op.get("current"))
            def write(method: str, path: str, payload: Any = None) -> Any:
                return write_request(method, path, payload, {"X-FTL-SID": sid, "Cookie": f"sid={sid}"})
            if family == "groups":
                if action == "create":
                    response = write("POST", "/api/groups", {"name": wanted["name"], "comment": _comment(wanted["description"], owner_token), "enabled": wanted["enabled"]})
                    created_groups[wanted["name"]] = _created_id(response, "groups")
                    group_ids = {**group_ids, **created_groups}
                elif action == "update":
                    _validate_write_response(write("PUT", f"/api/groups/{quote(wanted['name'], safe='')}", {"name": wanted["name"], "comment": _comment(wanted["description"], owner_token), "enabled": wanted["enabled"]}), "groups")
                else:
                    _validate_write_response(write("DELETE", f"/api/groups/{quote(existing['name'], safe='')}") , "groups")
            elif family == "clients":
                if action in {"create", "update"}:
                    payload = {"comment": _owner_comment(owner_token), "groups": [group_ids[wanted["group"]]]}
                    if action == "create":
                        response = write("POST", "/api/clients", {"client": wanted["identifier"], **payload})
                        _created_id(response, "clients")
                    else:
                        _validate_write_response(write("PUT", f"/api/clients/{quote(existing['client'], safe='')}", payload), "clients")
                else:
                    _validate_write_response(write("DELETE", f"/api/clients/{quote(existing['client'], safe='')}") , "clients")

    if operations:
        apply_operations()
        reread = _read_current(transport, sid, guarded=True, origin=validated_origin)
        reread, remaining = _operations(inventory, reread, owner_token)
        if remaining:
            raise _error("post-write verification failed")
        plan = _safe_plan(remaining, reread, owner_token)
    plan["apply"] = True
    plan["verified"] = True
    return plan


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: Request, *args: Any) -> None:
        return None


def validate_origin(origin: str, *, allow_private_http: bool = False) -> str:
    if not isinstance(origin, str) or any(ord(char) < 0x20 or char.isspace() or ord(char) == 0x7F for char in origin):
        raise _error("invalid Pi-hole API origin")
    if "?" in origin or "#" in origin or "\\" in origin or "%" in origin:
        raise _error("invalid Pi-hole API origin")
    parsed: Any = None
    parse_failed = False
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except (TypeError, ValueError):
        parse_failed = True
    if parse_failed:
        raise _error("invalid Pi-hole API origin")
    if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment or not parsed.hostname or parsed.netloc.endswith(":"):
        raise _error("invalid Pi-hole API origin")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        host_for_labels = host[:-1] if host.endswith(".") else host
        labels = host_for_labels.split(".")
        if not host_for_labels or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) for label in labels):
            raise _error("invalid Pi-hole API origin")
    if parsed.scheme.lower() == "https":
        return origin.rstrip("/")
    if parsed.scheme.lower() != "http" or not allow_private_http:
        raise _error("invalid Pi-hole API origin")
    private = parsed.hostname == "localhost"
    private = private or (address is not None and (address.is_private or address.is_loopback))
    _require(private, "invalid Pi-hole API origin")
    return origin.rstrip("/")


class UrllibTransport:
    """Fixed-surface JSON transport with redirects disabled."""
    __slots__ = ("_origin", "timeout", "_frozen", "_allow_private_http")
    safety_validated = True

    def __init__(self, origin: str, *, allow_private_http: bool = False, timeout: int = 15):
        validated = validate_origin(origin, allow_private_http=allow_private_http)
        object.__setattr__(self, "_origin", validated)
        object.__setattr__(self, "_allow_private_http", allow_private_http)
        self.timeout = timeout
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, "_frozen") and name in {"_origin", "timeout", "_frozen", "_allow_private_http"}:
            raise AttributeError("transport is immutable")
        object.__setattr__(self, name, value)

    @property
    def origin(self) -> str:
        return self._origin

    def request(self, method: str, path: str, *, payload: Any = None, headers: Mapping[str, str] | None = None) -> Any:
        _require(_allowed(method, path), "unsupported Pi-hole API operation")
        _require(method == "GET" or (method == "POST" and path == "/api/auth"), "policy mutations require reconcile_live apply gate")
        return self._request_json(method, path, payload=payload, headers=headers)

    def _request_json(self, method: str, path: str, *, payload: Any = None, headers: Mapping[str, str] | None = None, origin: str | None = None) -> Any:
        _require(_allowed(method, path), "unsupported Pi-hole API operation")
        _require(method == "GET" or (method == "POST" and path == "/api/auth"), "policy mutations require reconcile_live apply gate")
        body = None
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        if payload is not None:
            serialization_failed = False
            try:
                body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
            except (TypeError, ValueError):
                serialization_failed = True
            if serialization_failed:
                raise _error("invalid Pi-hole API request")
            request_headers["Content-Type"] = "application/json"
        request_origin = self._origin if origin is None else origin
        _require(request_origin == self._origin, "invalid Pi-hole API origin")
        request = Request(cast(str, request_origin) + path, data=body, headers=request_headers, method=method)
        result = _invoke(lambda: build_opener(_NoRedirectHandler).open(request, timeout=self.timeout))
        if result is _TRANSPORT_FAILURE:
            raise _error("Pi-hole API request failed")
        raw = _invoke(lambda: result.read())
        if raw is _TRANSPORT_FAILURE:
            raise _error("malformed Pi-hole API response")
        parsed = _invoke(lambda: json.loads(raw.decode(), parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys))
        if parsed is _TRANSPORT_FAILURE:
            raise _error("malformed Pi-hole API response")
        return parsed
