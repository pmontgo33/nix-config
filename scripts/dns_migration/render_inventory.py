#!/usr/bin/env python3
"""Validate and render the shared homelab inventory.

This gate is deliberately offline by default. It evaluates the allowlisted
inventory/default.nix with offline Nix evaluation, validates the resulting data, and emits
deterministic consumer views. It never contacts OPNsense, Pi-hole, Proxmox, or
Tailscale.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

IDENTITY_REF = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
ALLOWED_INTERFACES = {"lan", "iot", "guest"}
ALLOWED_GROUPS = {"normal", "kids"}
FORBIDDEN_KEYS = {
    "mac", "macaddress", "clientid", "clientidentifier", "password", "secret",
    "token", "apikey", "accesstoken",
}
TOP_LEVEL_KEYS = {"schemaVersion", "identitySource", "ownership", "networkProfiles", "staticGuests", "devices", "localDns", "policy"}
OWNERSHIP_KEYS = {"dhcpv4", "dhcpv6", "routerAdvertisements", "rdnss", "dnsDuringPhase1", "localDns"}
PROFILE_KEYS = {"interface", "subnet", "gateway", "dhcpRange", "dnsDuringPhase1"}
RANGE_KEYS = {"from", "to"}
DEVICE_KEYS = {"network", "placement", "services"}
NETWORK_KEYS = {"hostname", "address", "identityRef", "interface", "piholeGroup"}
STATIC_GUEST_KEYS = {"network", "placement"}
STATIC_GUEST_NETWORK_KEYS = {"hostname", "address", "interface"}
SERVICE_KEYS = {"hostname", "protocol", "upstreamPort", "proxy"}
LOCAL_DNS_KEYS = {"zones"}
LOCAL_DNS_ZONE_KEYS = {"hostOverrides", "aliases"}
HOST_OVERRIDE_KEYS = {"hostname", "rr", "server", "ttl", "addptr", "enabled", "description"}
HOST_ALIAS_KEYS = {"hostname", "target", "targetRr", "enabled", "description"}
POLICY_KEYS = {"base", "adlists", "groups", "groupAssignments", "localDns", "rules"}
POLICY_BASE_KEYS = {"upstreams", "listeningInterfaces", "queryLogging", "retention"}
POLICY_ADLIST_KEYS = {"address", "enabled", "description", "type"}
POLICY_GROUP_KEYS = {"description", "enabled"}
POLICY_RULE_KINDS = {"allow", "block"}
POLICY_ADLIST_ADDRESS = "file:///var/lib/pihole/baseline.hosts"
POLICY_ADLIST_DESCRIPTION = "Shared Pi-hole baseline adlist"
DNS_ZONE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
DNS_HOST_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")


class InventoryError(ValueError):
    """Raised when inventory data is unsafe or structurally invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def _ensure_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    _require(not unknown, f"unknown fields at {path}: {', '.join(unknown)}")


def _normal_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _load_nix(path: Path) -> dict[str, Any]:
    approved = Path(__file__).resolve().parents[2] / "inventory" / "default.nix"
    _require(path.resolve() == approved.resolve(), f"Nix source is not the allowlisted inventory: {path}")
    source = path.read_text()
    _require(
        not re.search(r"(?m)\b(import|builtins|fetch(?:url|Tarball|Git)|readFile)\b", source),
        "inventory Nix source contains an evaluation/import primitive",
    )
    try:
        result = subprocess.run(
            [
                "nix", "eval", "--json", "--impure", "--offline",
                "--no-write-lock-file", "--option", "allow-import-from-derivation", "false",
                "--expr", "import ./inventory/default.nix",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=approved.parents[1],
        )
    except FileNotFoundError as exc:
        raise InventoryError("nix is required to evaluate the Nix inventory") from exc
    except subprocess.CalledProcessError as exc:
        raise InventoryError(f"restricted nix evaluation failed: {exc.stderr.strip()}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InventoryError("nix evaluation did not return JSON") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"unable to read JSON inventory {path}") from exc


def load_source(nix_path: Path | None, json_path: Path | None) -> dict[str, Any]:
    _require(bool(nix_path) ^ bool(json_path), "provide exactly one of --inventory-nix or --inventory-json")
    data = _load_nix(nix_path) if nix_path else _load_json(json_path)  # type: ignore[arg-type]
    if "inventory" in data:
        data = data["inventory"]
    _require(isinstance(data, dict), "inventory must be an attribute set/object")
    return data


def _scan_for_forbidden(value: Any, path: str = "inventory") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _require(_normal_key(key) not in FORBIDDEN_KEYS, f"secret/client identifier key is forbidden: {path}.{key}")
            _scan_for_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_forbidden(child, f"{path}[{index}]")


def _ipv4(value: Any, path: str) -> ipaddress.IPv4Address:
    _require(isinstance(value, str), f"{path} must be a string")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise InventoryError(f"invalid IPv4 address at {path}: {value}") from exc
    _require(address.version == 4, f"IPv6 is not valid in DHCPv4 inventory at {path}")
    return ipaddress.IPv4Address(str(address))


def _dns_zone(value: Any, path: str) -> str:
    _require(isinstance(value, str), f"{path} must be a string")
    _require(not value.endswith(".."), f"invalid DNS zone at {path}")
    normalized = value[:-1] if value.endswith(".") else value
    normalized = normalized.lower()
    _require(bool(DNS_ZONE_RE.fullmatch(normalized)), f"invalid DNS zone at {path}")
    _require(len(normalized) <= 253, f"DNS zone is too long at {path}")
    return normalized


def _dns_hostname(value: Any, path: str) -> str:
    _require(isinstance(value, str), f"{path} must be a string")
    _require(not value.endswith(".."), f"invalid DNS hostname at {path}")
    normalized = value[:-1] if value.endswith(".") else value
    normalized = normalized.lower()
    _require(bool(DNS_HOST_RE.fullmatch(normalized)), f"invalid DNS hostname at {path}")
    return normalized


def _bool(value: Any, path: str) -> bool:
    _require(isinstance(value, bool), f"{path} must be a boolean")
    return value


def _description(value: Any, path: str) -> str:
    _require(isinstance(value, str), f"{path} must be a string")
    _require(len(value) <= 255, f"{path} is too long")
    return value


def _ttl(value: Any, path: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{path} must be an integer")
    _require(0 <= value <= 2147483647, f"{path} is outside the supported range")
    return value


def _validate_local_dns(inventory: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    local_dns = inventory.get("localDns", {"zones": {}})
    _require(isinstance(local_dns, dict), "localDns must be an object")
    _ensure_keys(local_dns, LOCAL_DNS_KEYS, "inventory.localDns")
    zones = local_dns.get("zones")
    _require(isinstance(zones, dict), "inventory.localDns.zones must be an object")

    overrides: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    override_refs: dict[str, dict[str, Any]] = {}
    alias_refs: set[str] = set()
    ptr_addresses: set[str] = set()
    seen_zones: set[str] = set()

    for zone_name, zone_value in sorted(zones.items()):
        zone = _dns_zone(zone_name, f"inventory.localDns.zones.{zone_name}")
        _require(zone not in seen_zones, f"duplicate normalized local DNS zone: {zone}")
        seen_zones.add(zone)
        _require(isinstance(zone_value, dict), f"local DNS zone {zone} must be an object")
        _ensure_keys(zone_value, LOCAL_DNS_ZONE_KEYS, f"inventory.localDns.zones.{zone}")
        host_values = zone_value.get("hostOverrides", [])
        alias_values = zone_value.get("aliases", [])
        _require(isinstance(host_values, list), f"hostOverrides must be a list at {zone}")
        _require(isinstance(alias_values, list), f"aliases must be a list at {zone}")

        for index, host_value in enumerate(host_values):
            path = f"inventory.localDns.zones.{zone}.hostOverrides[{index}]"
            _require(isinstance(host_value, dict), f"{path} must be an object")
            _ensure_keys(host_value, HOST_OVERRIDE_KEYS, path)
            hostname = _dns_hostname(host_value.get("hostname"), f"{path}.hostname")
            rr = host_value.get("rr")
            _require(isinstance(rr, str) and rr in {"A", "AAAA"}, f"{path}.rr must be A or AAAA")
            server = host_value.get("server")
            _require(isinstance(server, str), f"{path}.server must be an IP address")
            try:
                address = ipaddress.ip_address(server)
            except ValueError as exc:
                raise InventoryError(f"{path}.server must be an IP address") from exc
            _require(address.version == (4 if rr == "A" else 6), f"{path}.server does not match {rr}")
            ttl = _ttl(host_value.get("ttl"), f"{path}.ttl")
            addptr = _bool(host_value.get("addptr"), f"{path}.addptr")
            enabled = _bool(host_value.get("enabled"), f"{path}.enabled")
            if enabled and addptr:
                address_text = str(address)
                _require(address_text not in ptr_addresses, f"duplicate enabled PTR owner: {address_text}")
                ptr_addresses.add(address_text)
            description = _description(host_value.get("description"), f"{path}.description")
            record_ref = f"{zone}/{hostname}/{rr}"
            _require(record_ref not in override_refs, f"duplicate local DNS host override: {record_ref}")
            override = {
                "recordRef": record_ref,
                "hostname": hostname,
                "domain": zone,
                "rr": rr,
                "server": str(address),
                "ttl": ttl,
                "addptr": addptr,
                "enabled": enabled,
                "description": description,
            }
            override_refs[record_ref] = override
            overrides.append(override)

        for index, alias_value in enumerate(alias_values):
            path = f"inventory.localDns.zones.{zone}.aliases[{index}]"
            _require(isinstance(alias_value, dict), f"{path} must be an object")
            _ensure_keys(alias_value, HOST_ALIAS_KEYS, path)
            hostname = _dns_hostname(alias_value.get("hostname"), f"{path}.hostname")
            target = _dns_hostname(alias_value.get("target"), f"{path}.target")
            target_rr = alias_value.get("targetRr")
            if target_rr is not None:
                _require(isinstance(target_rr, str) and target_rr in {"A", "AAAA"}, f"{path}.targetRr must be A or AAAA")
            enabled = _bool(alias_value.get("enabled"), f"{path}.enabled")
            description = _description(alias_value.get("description"), f"{path}.description")
            alias_ref = f"{zone}/{hostname}"
            _require(
                not any(record["domain"] == zone and record["hostname"] == hostname for record in overrides),
                f"local DNS alias conflicts with host override: {alias_ref}",
            )
            _require(alias_ref not in alias_refs, f"duplicate local DNS alias: {alias_ref}")
            matching = [
                record for record in overrides
                if record["domain"] == zone and record["hostname"] == target
                and (target_rr is None or record["rr"] == target_rr)
            ]
            _require(bool(matching), f"local DNS alias target does not exist: {zone}/{target}")
            _require(len(matching) == 1, f"local DNS alias target is ambiguous: {zone}/{target}")
            if enabled:
                _require(matching[0]["enabled"], f"enabled local DNS alias target is disabled: {zone}/{target}")
            aliases.append({
                "aliasRef": alias_ref,
                "hostname": hostname,
                "domain": zone,
                "targetRef": matching[0]["recordRef"],
                "enabled": enabled,
                "description": description,
            })
            alias_refs.add(alias_ref)

    return sorted(overrides, key=lambda item: item["recordRef"]), sorted(aliases, key=lambda item: item["aliasRef"])


def _policy_text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), f"{path} must be a string")
    _require(allow_empty or bool(value), f"{path} must not be empty")
    _require(not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value), f"{path} contains control characters")
    _require(len(value) <= 2048, f"{path} is too long")
    return value


def _validate_policy(inventory: dict[str, Any]) -> dict[str, Any]:
    _require("policy" in inventory, "inventory.policy is required for the complete baseline policy")
    policy = inventory["policy"]
    _require(isinstance(policy, dict), "policy must be an object")
    _ensure_keys(policy, POLICY_KEYS, "inventory.policy")
    _require(set(policy) == POLICY_KEYS, "inventory.policy must contain the complete baseline policy")

    base = policy["base"]
    _require(isinstance(base, dict), "policy.base must be an object")
    _ensure_keys(base, POLICY_BASE_KEYS, "inventory.policy.base")
    _require(set(base) == POLICY_BASE_KEYS, "policy.base must contain the complete baseline settings")
    _require(base["upstreams"] == ["192.168.86.1"], "policy.base.upstreams must contain the current upstream only")
    _require(base["listeningInterfaces"] == ["eth0"], "policy.base.listeningInterfaces must contain eth0 only")
    _require(base["queryLogging"] is True, "policy.base.queryLogging must be true")
    _require(base["retention"] == 91, "policy.base.retention must be 91 days")
    _policy_text(base["upstreams"][0], "policy.base.upstreams[0]")
    _policy_text(base["listeningInterfaces"][0], "policy.base.listeningInterfaces[0]")
    _require(isinstance(base["queryLogging"], bool), "policy.base.queryLogging must be a boolean")
    _require(isinstance(base["retention"], int) and not isinstance(base["retention"], bool), "policy.base.retention must be an integer")

    adlists = policy["adlists"]
    _require(isinstance(adlists, dict), "policy.adlists must be an object")
    _require(set(adlists) == {"standard", "kids"}, "policy.adlists must contain standard and kids lists only")
    standard = adlists["standard"]
    kids = adlists["kids"]
    _require(isinstance(standard, list) and isinstance(kids, list), "policy adlists must be lists")
    _require(kids == [], "policy.adlists.kids must be empty")
    _require(standard == [] or len(standard) == 1, "policy.adlists.standard must be empty or contain the legacy baseline list")
    # The Pi-hole Nix module owns all adlists. Discard the legacy baseline
    # entry from the policy inventory so live reconciliation never sees
    # an authoritative overlap with services.pihole-ftl.lists. When the
    # inventory still carries the legacy baseline entry, validate it is
    # exactly the canonical one and emit an empty list; otherwise reject
    # it so a non-canonical adlist cannot silently be dropped.
    if standard:
        adlist = standard[0]
        _require(isinstance(adlist, dict), "policy.adlists.standard entry must be an object")
        _ensure_keys(adlist, POLICY_ADLIST_KEYS, "inventory.policy.adlists.standard[0]")
        _require(set(adlist) == {"address", "enabled", "description"}, "baseline adlist has unsupported or missing fields")
        _require(adlist["address"] == POLICY_ADLIST_ADDRESS, "policy adlist must use the existing shared baseline list")
        _require(adlist["enabled"] is True, "baseline adlist must be enabled")
        _require(adlist["description"] == POLICY_ADLIST_DESCRIPTION, "baseline adlist description does not match the shared baseline")
        _policy_text(adlist["address"], "policy.adlists.standard[0].address")
        _policy_text(adlist["description"], "policy.adlists.standard[0].description")
    normalized_adlists: list[dict[str, Any]] = []

    groups = policy["groups"]
    _require(isinstance(groups, dict) and set(groups) == {"normal", "kids"}, "policy.groups must contain normal and kids only")
    normalized_groups: dict[str, dict[str, str]] = {}
    for name in ("normal", "kids"):
        group = groups[name]
        _require(isinstance(group, dict), f"policy.groups.{name} must be an object")
        _ensure_keys(group, POLICY_GROUP_KEYS, f"inventory.policy.groups.{name}")
        _require(set(group) == {"description"}, f"policy.groups.{name} must contain only its description")
        description = _policy_text(group["description"], f"policy.groups.{name}.description")
        normalized_groups[name] = {"description": description}
    _require(normalized_groups == {
        "normal": {"description": "Normal clients"},
        "kids": {"description": "Kids clients"},
    }, "policy group descriptions do not match the baseline policy")

    assignments = policy["groupAssignments"]
    _require(assignments == {}, "policy.groupAssignments must be empty until identities are resolved")
    _require(policy["localDns"] == [], "policy.localDns must be empty for the baseline policy")
    rules = policy["rules"]
    _require(isinstance(rules, dict) and set(rules) == POLICY_RULE_KINDS, "policy.rules must contain allow and block only")
    _require(rules["allow"] == [] and rules["block"] == [], "policy.rules must be empty for the baseline policy")

    return {
        "base": {
            "upstreams": list(base["upstreams"]),
            "listeningInterfaces": list(base["listeningInterfaces"]),
            "queryLogging": base["queryLogging"],
            "retention": base["retention"],
        },
        "adlists": {"standard": normalized_adlists, "kids": []},
        "groups": normalized_groups,
        "groupAssignments": {},
        "localDns": [],
        "rules": {"allow": [], "block": []},
    }


def validate_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    _scan_for_forbidden(inventory)
    _ensure_keys(inventory, TOP_LEVEL_KEYS, "inventory")
    _require(inventory.get("schemaVersion") == 1, "schemaVersion must be 1")
    _require(inventory.get("identitySource") == "sops-runtime", "identitySource must be sops-runtime")

    ownership = inventory.get("ownership")
    _require(isinstance(ownership, dict), "ownership must be an object")
    _ensure_keys(ownership, OWNERSHIP_KEYS, "inventory.ownership")
    _require(ownership == {
        "dhcpv4": "opnsense-dnsmasq",
        "dhcpv6": "opnsense",
        "routerAdvertisements": "opnsense",
        "rdnss": "opnsense",
        "dnsDuringPhase1": "adguard",
        "localDns": "opnsense-unbound",
    }, "Gate 1A ownership must keep DHCPv6/RA/RDNSS/local DNS on OPNsense and DNS on AdGuard")

    policy = _validate_policy(inventory)

    profiles = inventory.get("networkProfiles")
    static_guests = inventory.get("staticGuests", {})
    devices = inventory.get("devices")
    _require(bool(isinstance(profiles, dict) and profiles), "networkProfiles must be non-empty")
    _require(isinstance(static_guests, dict), "staticGuests must be an object")
    _require(bool(isinstance(devices, dict) and devices), "devices must be non-empty")
    profiles = profiles if isinstance(profiles, dict) else {}
    devices = devices if isinstance(devices, dict) else {}

    profile_networks: dict[str, ipaddress.IPv4Network] = {}
    profile_ranges: dict[str, tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]] = {}
    for name, profile_value in profiles.items():
        _require(isinstance(profile_value, dict), f"network profile {name} must be an object")
        profile: dict[str, Any] = profile_value
        _ensure_keys(profile, PROFILE_KEYS, f"networkProfiles.{name}")
        _require(profile.get("interface") in {"lan", "opt1", "opt2"}, f"invalid profile interface for {name}")
        subnet_value = profile.get("subnet")
        _require(isinstance(subnet_value, str), f"profile {name}.subnet is required")
        subnet = subnet_value
        try:
            network = ipaddress.ip_network(subnet, strict=True)
        except ValueError as exc:
            raise InventoryError(f"invalid network profile subnet in {name}") from exc
        _require(network.version == 4, f"network profile {name} must be IPv4 for Gate 1A")
        gateway = _ipv4(profile.get("gateway"), f"networkProfiles.{name}.gateway")
        _require(gateway in network, f"gateway is outside profile subnet: {name}")
        dhcp_range_value = profile.get("dhcpRange")
        if not isinstance(dhcp_range_value, dict):
            raise InventoryError(f"profile {name}.dhcpRange is required")
        dhcp_range = dhcp_range_value
        _ensure_keys(dhcp_range, RANGE_KEYS, f"networkProfiles.{name}.dhcpRange")
        range_from = _ipv4(dhcp_range.get("from"), f"networkProfiles.{name}.dhcpRange.from")
        range_to = _ipv4(dhcp_range.get("to"), f"networkProfiles.{name}.dhcpRange.to")
        _require(range_from <= range_to, f"DHCP range is reversed: {name}")
        _require(range_from in network and range_to in network, f"DHCP range is outside profile subnet: {name}")
        dns = profile.get("dnsDuringPhase1")
        _require(isinstance(dns, list) and all(isinstance(x, str) for x in dns), f"profile {name}.dnsDuringPhase1 must be a string list")
        profile_networks[name] = ipaddress.IPv4Network(str(network))
        profile_ranges[name] = (range_from, range_to)

    addresses: dict[str, str] = {}
    hostnames: dict[str, str] = {}
    identity_refs: dict[str, str] = {}
    reservations: list[dict[str, Any]] = []
    pool_warnings: list[dict[str, str]] = []
    services: list[dict[str, Any]] = []
    device_addresses: dict[str, str] = {}
    static_guest_records: list[dict[str, Any]] = []
    for guest_name, guest_value in sorted(static_guests.items()):
        _require(isinstance(guest_value, dict), f"static guest {guest_name} must be an object")
        guest = guest_value
        _ensure_keys(guest, STATIC_GUEST_KEYS, f"staticGuests.{guest_name}")
        network_value = guest.get("network")
        _require(isinstance(network_value, dict), f"static guest {guest_name}.network is required")
        network = network_value
        _ensure_keys(network, STATIC_GUEST_NETWORK_KEYS, f"staticGuests.{guest_name}.network")
        interface = network.get("interface")
        _require(isinstance(interface, str) and interface in ALLOWED_INTERFACES, f"invalid static guest interface: {guest_name}")
        _require(interface in profile_networks, f"static guest interface has no matching network profile: {guest_name}")
        address = _ipv4(network.get("address"), f"staticGuests.{guest_name}.network.address")
        _require(address in profile_networks[interface], f"static guest address is outside profile subnet: {guest_name}")
        range_from, range_to = profile_ranges[interface]
        _require(not (range_from <= address <= range_to), f"static guest address is inside dynamic DHCP range: {guest_name}")
        address_text = str(address)
        _require(address_text not in addresses, f"duplicate static guest address {address_text}: {addresses.get(address_text)}")
        hostname = network.get("hostname")
        _require(isinstance(hostname, str) and bool(DNS_HOST_RE.fullmatch(hostname)), f"invalid static guest hostname: {guest_name}")
        _require(hostname not in hostnames and hostname not in {item["hostname"] for item in static_guest_records}, f"duplicate static guest hostname: {hostname}")
        placement_value = guest.get("placement")
        if placement_value is not None:
            _require(isinstance(placement_value, dict), f"staticGuests.{guest_name}.placement must be an object")
            _ensure_keys(placement_value, {"preferredNode", "fallbackNodes"}, f"staticGuests.{guest_name}.placement")
            preferred_node = placement_value.get("preferredNode")
            fallback_nodes = placement_value.get("fallbackNodes")
            _require(isinstance(preferred_node, str) and bool(preferred_node), f"invalid preferredNode: {guest_name}")
            _require(isinstance(fallback_nodes, list) and all(isinstance(node, str) and bool(node) for node in fallback_nodes), f"invalid fallbackNodes: {guest_name}")
            _require(len(fallback_nodes) == len(set(fallback_nodes)), f"duplicate fallback node: {guest_name}")
            _require(preferred_node not in fallback_nodes, f"preferredNode is also a fallback node: {guest_name}")
        static_guest_records.append({"guest": guest_name, "hostname": hostname, "address": address_text, "interface": interface, "placement": placement_value or {}})
        addresses[address_text] = guest_name
        hostnames[hostname] = guest_name
        device_addresses[guest_name] = address_text
    for device_name, device_value in sorted(devices.items()):
        _require(isinstance(device_value, dict), f"device {device_name} must be an object")
        device = device_value
        _ensure_keys(device, DEVICE_KEYS, f"devices.{device_name}")
        placement_value = device.get("placement")
        if placement_value is not None:
            if not isinstance(placement_value, dict):
                raise InventoryError(f"devices.{device_name}.placement must be an object")
            _ensure_keys(placement_value, {"preferredNode", "fallbackNodes"}, f"devices.{device_name}.placement")
            preferred_node = placement_value.get("preferredNode")
            if not isinstance(preferred_node, str) or not preferred_node:
                raise InventoryError(f"invalid preferredNode: {device_name}")
            fallback_nodes = placement_value.get("fallbackNodes")
            if not isinstance(fallback_nodes, list) or not all(isinstance(node, str) and bool(node) for node in fallback_nodes):
                raise InventoryError(f"invalid fallbackNodes: {device_name}")
            fallback_nodes = [node for node in fallback_nodes if isinstance(node, str)]
            _require(len(fallback_nodes) == len(set(fallback_nodes)), f"duplicate fallback node: {device_name}")
            _require(preferred_node not in fallback_nodes, f"preferredNode is also a fallback node: {device_name}")
        network_value = device.get("network")
        _require(isinstance(network_value, dict), f"device {device_name}.network is required")
        network = network_value
        _ensure_keys(network, NETWORK_KEYS, f"devices.{device_name}.network")
        interface = network.get("interface")
        _require(isinstance(interface, str) and interface in ALLOWED_INTERFACES, f"invalid device interface: {device_name}")
        _require(interface in profile_networks, f"device interface has no matching network profile: {device_name}")
        address = _ipv4(network.get("address"), f"devices.{device_name}.network.address")
        _require(address in profile_networks[interface], f"device address is outside profile subnet: {device_name}")
        address_text = str(address)
        _require(address_text not in addresses, f"duplicate address {address_text}: {device_name} and {addresses.get(address_text)}")
        addresses[address_text] = device_name
        device_addresses[device_name] = address_text
        identity_ref = network.get("identityRef")
        _require(bool(isinstance(identity_ref, str) and IDENTITY_REF.fullmatch(identity_ref)), f"invalid identityRef: {device_name}")
        _require(identity_ref not in identity_refs, f"duplicate identityRef {identity_ref}: {device_name} and {identity_refs.get(identity_ref)}")
        identity_refs[identity_ref] = device_name
        group = network.get("piholeGroup")
        _require(bool(group in ALLOWED_GROUPS), f"invalid piholeGroup: {device_name}")
        hostname = network.get("hostname")
        if hostname is not None:
            _require(isinstance(hostname, str) and hostname, f"invalid hostname: {device_name}")
            _require(hostname not in hostnames, f"duplicate hostname {hostname}: {device_name} and {hostnames.get(hostname)}")
            hostnames[hostname] = device_name
        range_from, range_to = profile_ranges[interface]
        if range_from <= address <= range_to:
            pool_warnings.append({"device": device_name, "address": address_text, "interface": interface, "warning": "reservation address is inside dynamic DHCP range; identity resolution is mandatory before apply"})
        reservations.append({
            "device": device_name,
            "hostname": hostname,
            "address": address_text,
            "identityRef": identity_ref,
            "interface": interface,
            "piholeGroup": group,
        })

        device_services = device.get("services", {})
        _require(isinstance(device_services, dict), f"device {device_name}.services must be an object")
        for service_name, service_value in sorted(device_services.items()):
            _require(isinstance(service_value, dict), f"service {device_name}.{service_name} must be an object")
            service = service_value
            _ensure_keys(service, SERVICE_KEYS, f"devices.{device_name}.services.{service_name}")
            for field in ("hostname", "protocol", "upstreamPort", "proxy"):
                _require(field in service, f"service {device_name}.{service_name}.{field} is required")
            _require(isinstance(service.get("hostname"), str) and service["hostname"], f"invalid service hostname: {device_name}.{service_name}")
            _require(service.get("protocol") in {"http", "https"}, f"invalid service protocol: {device_name}.{service_name}")
            _require(isinstance(service.get("upstreamPort"), int) and not isinstance(service["upstreamPort"], bool) and 1 <= service["upstreamPort"] <= 65535, f"invalid service port: {device_name}.{service_name}")
            proxy = service.get("proxy")
            _require(isinstance(proxy, str) and proxy in {"caddy", "none"}, f"invalid service proxy: {device_name}.{service_name}")
            service_hostname = service["hostname"]
            _require(service_hostname not in hostnames, f"service hostname collides with device hostname: {service_hostname}")
            _require(not any(x["hostname"] == service_hostname for x in services), f"duplicate service hostname: {service_hostname}")
            services.append({"device": device_name, "service": service_name, **service})

    local_dns_overrides, local_dns_aliases = _validate_local_dns(inventory)
    return {
        "schemaVersion": 1,
        "ownership": ownership,
        "policy": policy,
        "networkProfiles": profiles,
        "staticGuests": static_guest_records,
        "reservations": reservations,
        "services": services,
        "deviceAddresses": device_addresses,
        "poolWarnings": pool_warnings,
        "unboundHostOverrides": local_dns_overrides,
        "unboundHostAliases": local_dns_aliases,
    }


def render(inventory: dict[str, Any]) -> dict[str, Any]:
    checked = validate_inventory(inventory)
    reservations = checked["reservations"]
    services = checked["services"]
    addresses = checked["deviceAddresses"]
    output = {
        "schemaVersion": checked["schemaVersion"],
        "apply": False,
        "identityResolutionRequired": bool(reservations),
        "ownership": checked["ownership"],
        "networkProfiles": checked["networkProfiles"],
        "staticGuests": checked["staticGuests"],
        "networkReservations": reservations,
        "piholeClients": [
            {
                "clientRef": f"identityRef:{item['identityRef']}",
                "identifier": None,
                "status": "pending-encrypted-identity-resolution",
                "device": item["device"],
                "hostname": item["hostname"],
                "address": item["address"],
                "group": item["piholeGroup"],
            }
            for item in reservations
        ],
        "services": services,
        "caddyRoutes": [
            {
                "device": item["device"],
                "service": item["service"],
                "hostname": item["hostname"],
                "protocol": item["protocol"],
                "upstream": f"{addresses[item['device']]}:{item['upstreamPort']}",
            }
            for item in services
            if item.get("proxy", "caddy") == "caddy"
        ],
        "poolWarnings": checked["poolWarnings"],
        "unresolvedIdentityRefs": sorted(item["identityRef"] for item in reservations),
        "unboundHostOverrides": checked["unboundHostOverrides"],
        "unboundHostAliases": checked["unboundHostAliases"],
    }
    output["policy"] = checked["policy"]
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory-nix", type=Path)
    source.add_argument("--inventory-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="validate only; emit no rendered data")
    args = parser.parse_args()
    try:
        output = render(load_source(args.inventory_nix, args.inventory_json))
    except InventoryError as exc:
        print(f"inventory validation failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"inventory valid: {len(output['networkReservations'])} reservations, {len(output['services'])} services")
        return 0
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
