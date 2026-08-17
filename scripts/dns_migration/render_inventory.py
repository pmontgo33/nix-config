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
TOP_LEVEL_KEYS = {"schemaVersion", "identitySource", "ownership", "networkProfiles", "devices"}
OWNERSHIP_KEYS = {"dhcpv4", "dhcpv6", "routerAdvertisements", "rdnss", "dnsDuringPhase1"}
PROFILE_KEYS = {"interface", "subnet", "gateway", "dhcpRange", "dnsDuringPhase1"}
RANGE_KEYS = {"from", "to"}
DEVICE_KEYS = {"network", "placement", "services"}
NETWORK_KEYS = {"hostname", "address", "identityRef", "interface", "piholeGroup"}
SERVICE_KEYS = {"hostname", "protocol", "upstreamPort", "proxy"}


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
    }, "Gate 1A ownership must keep DHCPv6/RA/RDNSS on OPNsense and DNS on AdGuard")

    profiles = inventory.get("networkProfiles")
    devices = inventory.get("devices")
    _require(bool(isinstance(profiles, dict) and profiles), "networkProfiles must be non-empty")
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

    return {
        "schemaVersion": 1,
        "ownership": ownership,
        "networkProfiles": profiles,
        "reservations": reservations,
        "services": services,
        "deviceAddresses": device_addresses,
        "poolWarnings": pool_warnings,
    }


def render(inventory: dict[str, Any]) -> dict[str, Any]:
    checked = validate_inventory(inventory)
    reservations = checked["reservations"]
    services = checked["services"]
    addresses = checked["deviceAddresses"]
    return {
        "schemaVersion": checked["schemaVersion"],
        "apply": False,
        "identityResolutionRequired": bool(reservations),
        "ownership": checked["ownership"],
        "networkProfiles": checked["networkProfiles"],
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
    }


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
