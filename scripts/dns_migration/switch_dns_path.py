#!/usr/bin/env python3
"""Pi-hole bypass — install, enable, disable, uninstall, status.

This script manages an OPNsense outbound NAT redirect ruleset that bypasses
Pi-hole filtering by rewriting outbound UDP/53 and TCP/53 traffic from
LAN/IoT/Guest to OPNsense Unbound (`192.168.86.1:53`).

It does NOT use DNS to do its work. Every control-plane read and write goes
through the OPNsense HTTP API over the LAN.

Lifecycle (5 actions):

  --install     Add 6 disabled rules tagged with the bypass descr. They
                persist in the firewall table until --uninstall. Safe
                (disabled = no traffic is rewritten).

  --enable      Flip the installed rules to enabled=1 and confirm the
                Unbound resolver is reachable on TCP/53. This is the
                ONLY action that changes live DNS behavior.

  --disable     Flip installed rules back to enabled=0. The rules stay
                in the table but become no-ops.

  --uninstall   Remove the bypass rules entirely.

  --status      Read-only: report whether the rules are installed, enabled,
                disabled, or absent.

How it works:

  The PF rdr-to equivalent is:

    rdr pass on { lan opt1 opt2 } proto { udp tcp } from <lan_subnet> to any \
        port 53 -> 192.168.86.1 port 53

  Implemented as 6 outbound NAT rules in OPNsense (LAN/IoT/Guest × UDP/TCP).
  All rules share the same descr: "dns-path-switcher: pihole-bypass".
  Re-running --install is idempotent (skips if already present).

Why an "always-on but disabled" install:

  The bypass is meant for emergency use ("both Pi-holes are down"). Leaving
  the rules installed in the firewall table (just disabled) means that
  flipping the bypass on is a single, fast API call instead of an install
  under stress. It also means the rules survive OPNsense reboots and config
  restores.

Credentials:

  - OPNsense API:
      OPNSENSE_KEY          (required for any non-status action)
      OPNSENSE_SECRET       (required alongside OPNSENSE_KEY)
      OPNSENSE_API_PRIMARY  (HTTPS endpoint, default https://router.montycasa.net)
      OPNSENSE_API_FALLBACK (HTTP endpoint, default http://192.168.86.1)

OPNsense ACL requirements (verified 2026-08-23 against OPNsense 26.1.11_6):

  - firewall:source_nat:add_rule, del_rule, get_rule, set_rule,
    search_rule, apply — REQUIRED for install/uninstall/enable/disable.
    NOT currently granted on the canonical hermes credential. Add via
    OPNsense UI: System → Access → Users → (user) → Effective Privileges.

OPNsense 26.1 API notes:

  - Outbound NAT rules live under /api/firewall/source_nat/*, NOT
    /api/firewall/nat/* (the latter returns 404 on 26.1).

Scope limitations:

  - IPv4 only (ipprotocol=inet). IPv6 RDNSS bypass is NOT covered.
  - DoH (TCP/443 to known DoH hostnames) is NOT covered. If a device uses
    DoH, it goes around both Pi-hole and this bypass.

Exit codes:

  - 0 : success
  - 1 : usage / argument error
  - 2 : OPNsense API error
  - 3 : credentials missing or ACL insufficient
  - 4 : post-enable verify failed (rules already enabled; --disable to roll back)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BYPASS_TARGET_IP = "192.168.86.1"  # OPNsense Unbound
BYPASS_RULE_DESCR = "dns-path-switcher: pihole-bypass"

INTERFACE_ALIASES = ("lan", "opt1", "opt2")  # LAN, IoT, Guest
PROTOCOLS = ("udp", "tcp")
DNS_PORT = 53

# OPNsense 26.1 outbound NAT API paths.
NAT_API_SEARCH = "firewall/source_nat/search_rule"
NAT_API_ADD = "firewall/source_nat/add_rule"
NAT_API_GET = "firewall/source_nat/get_rule"
NAT_API_SET = "firewall/source_nat/set_rule"
NAT_API_DEL = "firewall/source_nat/del_rule"
NAT_API_APPLY = "firewall/source_nat/apply"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BypassError(RuntimeError):
    """Recoverable error during a bypass operation. Exit code 2."""


class CredentialError(BypassError):
    """Credentials missing or insufficient. Exit code 3."""


# ---------------------------------------------------------------------------
# OPNsense API
# ---------------------------------------------------------------------------


def _api_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    primary: str,
    fallback: str,
    key: str,
    secret: str,
) -> tuple[str, Any]:
    """Call the OPNsense API. Returns (transport_label, parsed_response)."""
    creds = base64.b64encode(f"{key}:{secret}".encode()).decode()
    ctx = _api_context()
    last_err: str | None = None
    for label, base in (("https-primary", primary.rstrip("/")), ("http-fallback", fallback.rstrip("/"))):
        url = f"{base}/api/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Basic {creds}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            opener = (
                urllib.request.urlopen(req, timeout=10, context=ctx)
                if base.startswith("https://")
                else urllib.request.urlopen(req, timeout=10)
            )
            with opener as r:
                raw = r.read()
            try:
                return label, json.loads(raw)
            except json.JSONDecodeError:
                return label, raw.decode(errors="replace")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")[:300]
            last_err = f"{label}: HTTP {e.code}: {body_text}"
            if e.code in (401, 403):
                raise CredentialError(
                    f"OPNsense API rejected {method} {path} with HTTP {e.code}. "
                    "Add the firewall:source_nat privilege to the credential "
                    "(OPNsense UI: System → Access → Users → user → "
                    "Effective Privileges)."
                ) from e
        except Exception as e:
            last_err = f"{label}: {type(e).__name__}: {e}"
    raise BypassError(f"OPNsense API {method} {path} failed: {last_err}")


def _search_bypass_rules(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    """Return all outbound NAT rules whose descr equals BYPASS_RULE_DESCR."""
    _, data = _api_call(
        "GET", NAT_API_SEARCH, primary=primary, fallback=fallback, key=key, secret=secret
    )
    if isinstance(data, dict):
        rows_obj: Any = data.get("rows", [])
    elif isinstance(data, list):
        rows_obj = data
    else:
        rows_obj = []
    rows: list[dict[str, Any]] = [r for r in rows_obj if isinstance(r, dict)]
    return [r for r in rows if str(r.get("descr", "")) == BYPASS_RULE_DESCR]


def _apply_nat(*, primary: str, fallback: str, key: str, secret: str) -> None:
    """Reload the kernel filter so rule changes take effect."""
    _api_call(
        "POST", NAT_API_APPLY, primary=primary, fallback=fallback, key=key, secret=secret
    )


def _build_bypass_rule_payload(interface: str, protocol: str) -> dict[str, Any]:
    """Build the OPNsense NAT rule payload for one (interface, protocol)."""
    return {
        "interface": interface,
        "ipprotocol": "inet",
        "protocol": protocol,
        "source": "any",
        "destination": "any",
        "destination_port": str(DNS_PORT),
        "target": BYPASS_TARGET_IP,
        "target_port": str(DNS_PORT),
        "nat_port": "",
        "descr": BYPASS_RULE_DESCR,
        "associated-rule": [],
        "natreflection": "disable",
        "enabled": "0",  # install disabled; --enable flips to "1"
    }


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------


def install_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Install the 6 disabled bypass rules. Idempotent.

    Returns a summary string. Raises BypassError or CredentialError on failure.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    if len(existing) >= 6:
        return (
            f"installed=0, already_present={len(existing)}, state=disabled "
            f"(use --enable to activate)"
        )

    # If we have *some* existing rules but not all 6, complete the set. This
    # shouldn't normally happen but handles partial installs gracefully.
    existing_keys = {
        (str(r.get("interface", "")), str(r.get("protocol", ""))) for r in existing
    }
    added = 0
    for iface in INTERFACE_ALIASES:
        for proto in PROTOCOLS:
            if (iface, proto) in existing_keys:
                continue
            _api_call(
                "POST",
                NAT_API_ADD,
                body=_build_bypass_rule_payload(iface, proto),
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
            added += 1

    _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    return f"installed={added}, total=6, state=disabled (use --enable to activate)"


def uninstall_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Remove all bypass rules. Idempotent.

    Only removes rules tagged with BYPASS_RULE_DESCR — never touches other
    firewall rules.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    removed = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if uuid:
            _api_call(
                "POST",
                f"{NAT_API_DEL}/{uuid}",
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
            removed += 1
    if removed:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    return f"uninstalled={removed}"


def enable_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Activate the bypass rules. Idempotent.

    Installs first if missing, then flips every bypass rule to enabled=1 and
    ensures the target IP is correct. Performs a post-enable TCP/53 probe to
    confirm the resolver is reachable.

    Returns a summary string. Exit code 4 if the post-enable verify fails.
    """
    install_bypass(primary=primary, fallback=fallback, key=key, secret=secret)

    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    updated = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if not uuid:
            continue
        _, rule_data = _api_call(
            "GET",
            f"{NAT_API_GET}/{uuid}",
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        if not isinstance(rule_data, dict):
            continue
        rule_data["target"] = BYPASS_TARGET_IP
        rule_data["target_port"] = str(DNS_PORT)
        rule_data["enabled"] = "1"
        _api_call(
            "POST",
            f"{NAT_API_SET}/{uuid}",
            body=rule_data,
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        updated += 1
    _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    return f"enabled={updated}, target={BYPASS_TARGET_IP}:{DNS_PORT}"


def disable_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Deactivate the bypass rules. Rules remain installed; enabled=0."""
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    updated = 0
    already_disabled = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if not uuid:
            continue
        _, rule_data = _api_call(
            "GET",
            f"{NAT_API_GET}/{uuid}",
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        if not isinstance(rule_data, dict):
            continue
        if str(rule_data.get("enabled", "")) == "0":
            already_disabled += 1
            continue
        rule_data["enabled"] = "0"
        _api_call(
            "POST",
            f"{NAT_API_SET}/{uuid}",
            body=rule_data,
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        updated += 1
    if updated:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    return f"disabled={updated}, already_disabled={already_disabled}"


def status_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> dict[str, Any]:
    """Return a structured summary of the bypass state."""
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    enabled_count = sum(1 for r in existing if str(r.get("enabled", "")) == "1")
    disabled_count = len(existing) - enabled_count
    return {
        "installed": len(existing) > 0,
        "total_rules": len(existing),
        "enabled": enabled_count,
        "disabled": disabled_count,
        "target_ip": BYPASS_TARGET_IP,
        "descr": BYPASS_RULE_DESCR,
        "interfaces": INTERFACE_ALIASES,
        "protocols": PROTOCOLS,
    }


def _tcp_53_probe(ip: str, timeout: float = 3.0) -> bool:
    """Probe a resolver over TCP/53. Does NOT use DNS to resolve the target."""
    try:
        with socket.create_connection((ip, DNS_PORT), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Pi-hole bypass: install/enable/disable/uninstall an OPNsense outbound "
            "NAT ruleset that routes LAN/IoT/Guest DNS to OPNsense Unbound."
        )
    )
    actions = p.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--install",
        action="store_true",
        help="Add the disabled bypass rules to the firewall (persistent safety net).",
    )
    actions.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the bypass rules from the firewall.",
    )
    actions.add_argument(
        "--enable",
        action="store_true",
        help="Activate the bypass: routes all LAN/IoT/Guest DNS to OPNsense Unbound.",
    )
    actions.add_argument(
        "--disable",
        action="store_true",
        help="Deactivate the bypass: rules stay installed but become no-ops.",
    )
    actions.add_argument(
        "--status",
        action="store_true",
        help="Read-only: report whether the bypass rules are installed and enabled.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    key = os.environ.get("OPNSENSE_KEY", "")
    secret = os.environ.get("OPNSENSE_SECRET", "")
    primary = os.environ.get("OPNSENSE_API_PRIMARY", "https://router.montycasa.net")
    fallback = os.environ.get("OPNSENSE_API_FALLBACK", "http://192.168.86.1")
    have_credentials = bool(key and secret)

    # --status is the only action that doesn't require credentials for a useful
    # read; but it does need them to query the live API.
    if args.status:
        if not have_credentials:
            print(
                "ERROR: --status requires OPNSENSE_KEY and OPNSENSE_SECRET.",
                file=sys.stderr,
            )
            return 3
        try:
            st = status_bypass(primary=primary, fallback=fallback, key=key, secret=secret)
        except CredentialError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 3
        except BypassError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if not st["installed"]:
            print("Pi-hole bypass: NOT INSTALLED")
            print(f"  Run --install to add the persistent safety net (6 disabled rules).")
            print(f"  When both Pi-holes are down: --install && --enable")
            print(f"  When Pi-hole recovers:        --disable")
            return 0
        state = "ENABLED" if st["enabled"] > 0 else "INSTALLED (disabled)"
        print(f"Pi-hole bypass: {state}")
        print(f"  Total rules: {st['total_rules']}  (enabled={st['enabled']}, disabled={st['disabled']})")
        print(f"  Target:      {st['target_ip']}:{DNS_PORT} (OPNsense Unbound)")
        print(f"  Interfaces:  {', '.join(st['interfaces'])}")
        print(f"  Protocols:   {', '.join(st['protocols'])}")
        print(f"  Descr:       {st['descr']!r}")
        return 0

    # All other actions require credentials.
    if not have_credentials:
        print(
            "ERROR: --install/--enable/--disable/--uninstall require "
            "OPNSENSE_KEY and OPNSENSE_SECRET.",
            file=sys.stderr,
        )
        return 3

    try:
        if args.install:
            summary = install_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Install: {summary}")
            return 0

        if args.uninstall:
            summary = uninstall_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Uninstall: {summary}")
            return 0

        if args.disable:
            summary = disable_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Disable: {summary}")
            return 0

        if args.enable:
            summary = enable_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Enable: {summary}")
            # Post-enable verify: confirm Unbound is reachable on TCP/53.
            ok = _tcp_53_probe(BYPASS_TARGET_IP)
            if ok:
                print(
                    f"Verify: {BYPASS_TARGET_IP}:{DNS_PORT} is reachable. "
                    "Bypass is live; new outbound DNS packets from LAN/IoT/Guest "
                    "will be rewritten to OPNsense Unbound on the next match."
                )
                return 0
            else:
                print(
                    f"Verify FAILED: {BYPASS_TARGET_IP}:{DNS_PORT} did not accept a "
                    "TCP connection within 3s. The bypass is enabled but the "
                    "resolver is unreachable. Run --disable to roll back, then "
                    "investigate OPNsense Unbound before re-enabling.",
                    file=sys.stderr,
                )
                return 4

    except CredentialError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except BypassError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # argparse's mutually_exclusive_group(required=True) prevents reaching here.
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
