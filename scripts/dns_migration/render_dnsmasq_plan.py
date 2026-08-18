#!/usr/bin/env python3
"""Render a no-apply dnsmasq DHCPv4 plan from a validated inventory render.

This produces a review artifact only. It intentionally cannot contact or mutate
OPNsense, and it refuses to emit `dhcp-host` lines until encrypted runtime
identity mappings are supplied by a later gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_OWNERSHIP = {
    "dhcpv4": "opnsense-dnsmasq",
    "dhcpv6": "opnsense",
    "routerAdvertisements": "opnsense",
    "rdnss": "opnsense",
    "dnsDuringPhase1": "adguard",
    "localDns": "opnsense-unbound",
}


class PlanError(ValueError):
    pass


def validate(data: dict[str, Any]) -> None:
    if data.get("schemaVersion") != 1:
        raise PlanError("rendered inventory schemaVersion must be 1")
    if data.get("apply") is not False:
        raise PlanError("rendered inventory must be marked apply=false")
    if data.get("identityResolutionRequired") is not True:
        raise PlanError("rendered inventory must require identity resolution")
    if data.get("ownership") != EXPECTED_OWNERSHIP:
        raise PlanError("rendered inventory does not preserve OPNsense DHCPv6/RA/RDNSS ownership")
    profiles = data.get("networkProfiles")
    reservations = data.get("networkReservations")
    unresolved = data.get("unresolvedIdentityRefs")
    clients = data.get("piholeClients")
    if not isinstance(profiles, dict) or not profiles:
        raise PlanError("rendered inventory must contain networkProfiles")
    if not isinstance(reservations, list):
        raise PlanError("rendered inventory must contain networkReservations")
    if not isinstance(unresolved, list) or not all(isinstance(x, str) for x in unresolved):
        raise PlanError("unresolvedIdentityRefs must be a string list")
    if not isinstance(clients, list) or len(clients) != len(reservations):
        raise PlanError("piholeClients must contain exactly one entry per reservation")

    profile_names = set(profiles)
    reservation_refs: list[str] = []
    reservation_devices: set[str] = set()
    for reservation in reservations:
        if not isinstance(reservation, dict):
            raise PlanError("network reservation must be an object")
        interface = reservation.get("interface")
        if not isinstance(interface, str) or interface not in profile_names:
            raise PlanError(f"network reservation has no matching profile: {interface}")
        for field in ("device", "address", "identityRef", "hostname"):
            if field not in reservation:
                raise PlanError(f"network reservation is missing {field}")
        device = reservation["device"]
        identity_ref = reservation["identityRef"]
        if not isinstance(device, str) or device in reservation_devices:
            raise PlanError(f"network reservation device is missing or duplicated: {device}")
        if not isinstance(identity_ref, str) or identity_ref in reservation_refs:
            raise PlanError(f"network reservation identityRef is missing or duplicated: {identity_ref}")
        reservation_devices.add(device)
        reservation_refs.append(identity_ref)

    if sorted(reservation_refs) != sorted(unresolved):
        raise PlanError("unresolvedIdentityRefs does not match reservation identityRefs")
    client_refs: list[str] = []
    for client in clients:
        if not isinstance(client, dict) or client.get("status") != "pending-encrypted-identity-resolution" or client.get("identifier") is not None:
            raise PlanError("every Pi-hole client must remain explicitly pending with no identifier")
        client_ref = client.get("clientRef")
        if not isinstance(client_ref, str) or not client_ref.startswith("identityRef:"):
            raise PlanError("every Pi-hole client must have an identityRef clientRef")
        client_refs.append(client_ref)
    expected_client_refs = [f"identityRef:{identity_ref}" for identity_ref in reservation_refs]
    if sorted(client_refs) != sorted(expected_client_refs):
        raise PlanError("piholeClients do not match reservation identityRefs")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"unable to read rendered inventory: {path}") from exc
    if not isinstance(value, dict):
        raise PlanError("rendered inventory must be an object")
    validate(value)
    return value


def render(data: dict[str, Any]) -> dict[str, Any]:
    validate(data)
    profiles = data["networkProfiles"]
    reservations = data["networkReservations"]
    by_interface: dict[str, list[dict[str, Any]]] = {}
    for reservation in reservations:
        interface = reservation["interface"]
        by_interface.setdefault(interface, []).append(reservation)

    rendered_profiles = []
    emitted_refs: list[str] = []
    for profile_name, profile_value in sorted(profiles.items()):
        if not isinstance(profile_value, dict):
            raise PlanError(f"network profile is not an object: {profile_name}")
        profile = profile_value
        for field in ("interface", "subnet", "gateway", "dhcpRange", "dnsDuringPhase1"):
            if field not in profile:
                raise PlanError(f"network profile {profile_name} is missing {field}")
        reservations_for_profile = sorted(
            by_interface.get(profile_name, []), key=lambda item: item["address"]
        )
        emitted_refs.extend(item["identityRef"] for item in reservations_for_profile)
        rendered_profiles.append(
            {
                "profile": profile_name,
                "interface": profile["interface"],
                "subnet": profile["subnet"],
                "gateway": profile["gateway"],
                "dhcpRange": profile["dhcpRange"],
                "dnsDuringPhase1": profile["dnsDuringPhase1"],
                "reservationCount": len(reservations_for_profile),
                "reservations": [
                    {
                        "device": item["device"],
                        "address": item["address"],
                        "hostname": item["hostname"],
                        "identityRef": item["identityRef"],
                        "status": "pending-encrypted-identity-resolution",
                    }
                    for item in reservations_for_profile
                ],
            }
        )

    if sorted(emitted_refs) != sorted(item["identityRef"] for item in reservations):
        raise PlanError("planner did not emit every reservation exactly once")

    return {
        "schemaVersion": 1,
        "apply": False,
        "target": "opnsense-dnsmasq-dhcpv4",
        "dnsListener": "disabled-required",
        "dhcpv6AndRA": {
            "dhcpv6": data["ownership"]["dhcpv6"],
            "routerAdvertisements": data["ownership"]["routerAdvertisements"],
            "rdnss": data["ownership"]["rdnss"],
        },
        "dnsDuringPhase1": data["ownership"]["dnsDuringPhase1"],
        "profiles": rendered_profiles,
        "poolWarnings": data.get("poolWarnings", []),
        "unresolvedIdentityRefs": sorted(data["unresolvedIdentityRefs"]),
        "notes": [
            "Review artifact only; no OPNsense API call is made.",
            "Reservations remain pending until SOPS runtime identity mapping is loaded.",
            "Do not run ISC DHCP and dnsmasq concurrently on production interfaces.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rendered-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = render(load(args.rendered_inventory))
    except PlanError as exc:
        print(f"dnsmasq plan failed: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
