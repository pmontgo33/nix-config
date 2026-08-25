#!/usr/bin/env python3
"""Select the DNS resolvers advertised by DHCP on the managed OPNsense scopes.

The switch is deliberately DHCP-only.  Enabling it advertises OPNsense
Unbound (192.168.86.1) and disabling it advertises both Pi-holes.  It does not
install, inspect, or change packet-filter rules and it does not probe a
resolver's data plane.

The three records are owned by exact descriptions.  Discovery validates the
complete identity tuple before any write, and each authoritative record is
fetched by UUID.  A switch changes only ``value``; all other fields from the
GET response are echoed unchanged.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

UNBOUND_DNS = "192.168.86.1"
PIHOLE_DNS = "192.168.86.101,192.168.86.102"

INTERFACES = ("lan", "opt1", "opt2")
MANAGED_DESCRIPTIONS = {
    "lan": "hermes:dnsmasq:managed-option:lan:dns",
    "opt1": "hermes:dnsmasq:managed-option:opt1:dns",
    "opt2": "hermes:dnsmasq:managed-option:opt2:dns",
}
VALID_VALUES = frozenset((UNBOUND_DNS, PIHOLE_DNS))

DNSMASQ_API_SEARCH = "dnsmasq/settings/search_option"
DNSMASQ_API_GET = "dnsmasq/settings/get_option"
DNSMASQ_API_SET = "dnsmasq/settings/set_option"
DNSMASQ_API_RECONFIGURE = "dnsmasq/settings/service/reconfigure"
SEARCH_PAGE_SIZE = 100

# Kept as a public alias for callers that used the old module's naming style;
# it is a dnsmasq settings endpoint, not a packet-filter lifecycle endpoint.
API_SEARCH_OPTION = DNSMASQ_API_SEARCH
API_GET_OPTION = DNSMASQ_API_GET
API_SET_OPTION = DNSMASQ_API_SET
API_RECONFIGURE = DNSMASQ_API_RECONFIGURE

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class BypassError(RuntimeError):
    """A fail-closed API or validation error."""

    def __init__(self, message: str, *, post_send: bool = False) -> None:
        super().__init__(message)
        self.post_send = post_send


class CredentialError(BypassError):
    """The API rejected the supplied credential or permission set."""


def _api_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


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
    """Call OPNsense without retrying an ambiguous POST.

    The endpoint is POST-shaped even for search and GET-by-UUID operations.
    A second transport is used only when the first hostname cannot be resolved
    before a request is sent.  HTTP errors and every other POST transport
    failure are not retried because the request may have reached the server.
    """
    if not isinstance(method, str) or not isinstance(path, str):
        raise BypassError("invalid API request")
    primary_base = primary.rstrip("/")
    fallback_base = fallback.rstrip("/")
    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    context = _api_context()

    def request(label: str, base: str) -> tuple[str, Any]:
        url = f"{base}/api/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Authorization", f"Basic {credentials}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            response = (
                urllib.request.urlopen(req, timeout=10, context=context)
                if base.startswith("https://")
                else urllib.request.urlopen(req, timeout=10)
            )
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", None)
            if isinstance(reason, socket.gaierror):
                raise
            raise BypassError(
                f"OPNsense API {method} {path} failed on {label} before a "
                "known-safe retry point",
                post_send=True,
            ) from error
        except socket.gaierror:
            raise
        except Exception as error:
            raise BypassError(
                f"OPNsense API {method} {path} failed on {label} with an "
                "ambiguous transport error",
                post_send=True,
            ) from error

        try:
            with response:
                raw = response.read()
        except Exception as error:
            raise BypassError(
                f"OPNsense API {method} {path} failed after sending the "
                "request; response state is ambiguous",
                post_send=True,
            ) from error
        try:
            parsed: Any = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BypassError(
                f"OPNsense API {method} {path} returned invalid JSON",
                post_send=True,
            ) from error
        if isinstance(parsed, dict) and "error" in parsed:
            raise BypassError(f"OPNsense API {method} {path} returned an error")
        return label, parsed

    try:
        return request("primary", primary_base)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise CredentialError(
                f"OPNsense API rejected {method} {path} with HTTP {error.code}"
            ) from error
        raise BypassError(
            f"OPNsense API {method} {path} failed with HTTP {error.code}"
        ) from error
    except socket.gaierror as error:
        if fallback_base == primary_base:
            raise BypassError(
                f"OPNsense API {method} {path} could not resolve its endpoint"
            ) from error
        try:
            return request("fallback", fallback_base)
        except urllib.error.HTTPError as fallback_error:
            if fallback_error.code in (401, 403):
                raise CredentialError(
                    f"OPNsense API rejected {method} {path} with HTTP "
                    f"{fallback_error.code}"
                ) from fallback_error
            raise BypassError(
                f"OPNsense API {method} {path} failed on fallback with "
                f"HTTP {fallback_error.code}"
            ) from fallback_error
        except socket.gaierror as fallback_error:
            raise BypassError(
                f"OPNsense API {method} {path} could not reach either endpoint"
            ) from fallback_error
        except BypassError:
            raise
        except Exception as fallback_error:
            raise BypassError(
                f"OPNsense API {method} {path} failed on fallback"
            ) from fallback_error
    except BypassError:
        raise
    except Exception as error:
        # A raw gaierror can escape urllib on some Python/platform versions.
        if isinstance(error, socket.gaierror):
            raise
        raise BypassError(
            f"OPNsense API {method} {path} failed before a known-safe retry point"
        ) from error


def _require_uuid(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise BypassError(f"OPNsense dnsmasq response has an invalid UUID ({where})")
    return value


def _require_int(value: Any, *, field: str) -> int:
    if type(value) is not int:
        raise BypassError(f"OPNsense dnsmasq response has invalid {field}")
    return value


def _search_options(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    """Fetch every option row, following the OPNsense pagination envelope."""
    rows: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()
    page = 1
    total: int | None = None

    while True:
        _, data = _api_call(
            "POST",
            DNSMASQ_API_SEARCH,
            body={"current": page, "rowCount": SEARCH_PAGE_SIZE},
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            raise BypassError("OPNsense dnsmasq search response has no rows list")
        page_rows = data["rows"]
        page_total = _require_int(data.get("total"), field="total")
        current = _require_int(data.get("current"), field="current")
        row_count = _require_int(data.get("rowCount"), field="rowCount")
        if page_total < 0 or current != page or row_count == 0 or row_count < -1:
            raise BypassError("OPNsense dnsmasq search pagination is invalid")
        if total is None:
            total = page_total
        elif total != page_total:
            raise BypassError("OPNsense dnsmasq search total changed during pagination")

        for row in page_rows:
            if not isinstance(row, dict):
                raise BypassError("OPNsense dnsmasq search returned a non-object row")
            if "uuid" not in row:
                raise BypassError("OPNsense dnsmasq search row is missing uuid")
            uuid = _require_uuid(row["uuid"], where="search")
            if uuid in seen_uuids:
                raise BypassError("OPNsense dnsmasq search returned a duplicate UUID")
            seen_uuids.add(uuid)
            rows.append(row)

        if len(rows) > page_total:
            raise BypassError("OPNsense dnsmasq search returned too many rows")
        if len(rows) == page_total:
            return rows
        if not page_rows:
            raise BypassError("OPNsense dnsmasq search pagination ended early")
        if row_count == -1:
            raise BypassError("OPNsense dnsmasq search returned incomplete unbounded page")
        if len(page_rows) < row_count:
            raise BypassError("OPNsense dnsmasq search pagination ended early")
        page += 1
        if page > 10000:
            raise BypassError("OPNsense dnsmasq search pagination exceeded its limit")


def _selected_field(value: Any, *, field: str, interface: str, where: str) -> str:
    """Return a scalar field or the sole selected key from an OPNsense map."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise BypassError(
            f"managed dnsmasq option {interface} has invalid {field} ({where})"
        )
    selected = [
        key
        for key, item in value.items()
        if isinstance(item, dict) and item.get("selected") in (1, "1", True)
    ]
    if len(selected) != 1:
        raise BypassError(
            f"managed dnsmasq option {interface} has ambiguous {field} ({where})"
        )
    return selected[0]


def _validate_identity(option: Any, interface: str, *, where: str) -> dict[str, Any]:
    if not isinstance(option, dict):
        raise BypassError(f"managed dnsmasq option is not an object ({where})")
    if option.get("description") != MANAGED_DESCRIPTIONS[interface]:
        raise BypassError(
            f"managed dnsmasq option {interface} has drifted description ({where})"
        )

    actual_interface = _selected_field(
        option.get("interface"), field="interface", interface=interface, where=where
    )
    if actual_interface != interface:
        raise BypassError(
            f"managed dnsmasq option {interface} has drifted interface ({where})"
        )

    # The authoritative OPNsense GET uses selected-value maps.  The search
    # projection uses scalar fields.  Keep support for the legacy test fixture's
    # dhcpv4/set representation, but require the real set/option identity when
    # those fields are present.
    type_value = _selected_field(
        option.get("type"), field="type", interface=interface, where=where
    )
    if type_value not in {"set", "dhcpv4"}:
        raise BypassError(
            f"managed dnsmasq option {interface} has drifted type ({where})"
        )

    option_value = option.get("option")
    if option_value is not None:
        selected_option = _selected_field(
            option_value, field="option", interface=interface, where=where
        )
    else:
        selected_option = _selected_field(
            option.get("set"), field="option", interface=interface, where=where
        )
    if selected_option != "6":
        raise BypassError(
            f"managed dnsmasq option {interface} has drifted option ({where})"
        )

    # Some search fixtures expose a redundant scope field.  Live authoritative
    # records bind scope through the selected interface map and omit this field.
    if "scope" in option and option.get("scope") != interface:
        raise BypassError(
            f"managed dnsmasq option {interface} has drifted scope ({where})"
        )

    value = option.get("value")
    if type(value) is not str or value not in VALID_VALUES:
        raise BypassError(
            f"managed dnsmasq option {interface} has a non-scalar or drifted value"
        )
    _require_uuid(option.get("uuid"), where=where)
    return option


def _get_option(
    uuid: str, *, primary: str, fallback: str, key: str, secret: str
) -> dict[str, Any]:
    path = f"{DNSMASQ_API_GET}/{_require_uuid(uuid, where='get path')}"
    _, data = _api_call(
        "POST", path, primary=primary, fallback=fallback, key=key, secret=secret
    )
    if not isinstance(data, dict) or not isinstance(data.get("option"), dict):
        raise BypassError(f"OPNsense API {path} did not return an option envelope")
    option = data["option"]
    if "uuid" in option and option["uuid"] != uuid:
        raise BypassError(f"OPNsense API {path} returned a mismatched UUID")
    option.setdefault("uuid", uuid)
    return option


def _discover_managed_options(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    """Discover, uniquely bind, and validate the three managed records."""
    rows = _search_options(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    by_description: dict[str, dict[str, Any]] = {}
    for row in rows:
        description = row.get("description")
        if not isinstance(description, str) or description not in MANAGED_DESCRIPTIONS.values():
            continue
        if description in by_description:
            raise BypassError(
                f"duplicate managed dnsmasq description {description!r}"
            )
        interface = next(
            name for name, marker in MANAGED_DESCRIPTIONS.items() if marker == description
        )
        search_uuid = _require_uuid(row.get("uuid"), where="search")
        _validate_identity(row, interface, where="search")
        by_description[description] = {"uuid": search_uuid, "interface": interface}

    missing = [
        interface for interface in INTERFACES
        if MANAGED_DESCRIPTIONS[interface] not in by_description
    ]
    if missing:
        raise BypassError(
            "missing managed dnsmasq option(s): " + ", ".join(missing)
        )

    entries: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()
    for interface in INTERFACES:
        marker = MANAGED_DESCRIPTIONS[interface]
        binding = by_description[marker]
        uuid = binding["uuid"]
        if uuid in seen_uuids:
            raise BypassError("managed dnsmasq options do not have unique ownership")
        seen_uuids.add(uuid)
        option = _get_option(
            uuid, primary=primary, fallback=fallback, key=key, secret=secret
        )
        if option.get("uuid") != uuid:
            raise BypassError(f"managed dnsmasq option {interface} returned a mismatched UUID")
        _validate_identity(option, interface, where="get")
        entries.append({"uuid": uuid, "interface": interface, "option": option})
    return entries


def _require_saved(data: Any, path: str) -> None:
    if not isinstance(data, dict) or data.get("result") != "saved":
        raise BypassError(f"OPNsense API {path} did not confirm a saved option")


def _reconfigure(*, primary: str, fallback: str, key: str, secret: str) -> None:
    _, data = _api_call(
        "POST",
        DNSMASQ_API_RECONFIGURE,
        primary=primary,
        fallback=fallback,
        key=key,
        secret=secret,
    )
    if not isinstance(data, dict) or data.get("result") not in {"saved", "reconfigured"}:
        status = data.get("status") if isinstance(data, dict) else None
        if status != "OK":
            raise BypassError(
                f"OPNsense API {DNSMASQ_API_RECONFIGURE} did not confirm reconfigure"
            )


def _set_option_value(
    entry: dict[str, Any], value: str, *, primary: str, fallback: str, key: str, secret: str
) -> None:
    option = copy.deepcopy(entry["option"])
    option["value"] = value
    uuid = _require_uuid(entry["uuid"], where="set path")
    path = f"{DNSMASQ_API_SET}/{uuid}"
    _, data = _api_call(
        "POST",
        path,
        body={"option": option},
        primary=primary,
        fallback=fallback,
        key=key,
        secret=secret,
    )
    _require_saved(data, path)


def _fresh_readback(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    return _discover_managed_options(
        primary=primary, fallback=fallback, key=key, secret=secret
    )


def _rollback(
    changed: list[tuple[dict[str, Any], str]], *, primary: str, fallback: str, key: str, secret: str
) -> None:
    """Restore successful writes in reverse order, preserving full records."""
    for entry, original_value in reversed(changed):
        _set_option_value(
            entry,
            original_value,
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )


def _switch(
    target: str, *, primary: str, fallback: str, key: str, secret: str
) -> str:
    if target not in VALID_VALUES:
        raise BypassError("unsupported DNS target")
    entries = _discover_managed_options(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    changed: list[tuple[dict[str, Any], str]] = []
    already = 0
    for entry in entries:
        original = entry["option"]["value"]
        if original == target:
            already += 1
            continue
        try:
            _set_option_value(
                entry,
                target,
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
        except BypassError as error:
            if error.post_send:
                # The server may have applied this write.  Read fresh state,
                # but never blindly retry the ambiguous POST.
                try:
                    _fresh_readback(
                        primary=primary, fallback=fallback, key=key, secret=secret
                    )
                except BypassError:
                    pass
                raise
            try:
                _rollback(
                    changed,
                    primary=primary,
                    fallback=fallback,
                    key=key,
                    secret=secret,
                )
            except BypassError as rollback_error:
                raise BypassError("DNS option write failed and rollback failed") from rollback_error
            raise
        changed.append((entry, original))

    if not changed:
        return f"changed=0, already={already}, value={target}"

    try:
        _reconfigure(primary=primary, fallback=fallback, key=key, secret=secret)
    except BypassError as error:
        if error.post_send:
            try:
                _fresh_readback(
                    primary=primary, fallback=fallback, key=key, secret=secret
                )
            except BypassError:
                pass
            raise
        try:
            _rollback(
                changed,
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
        except BypassError as rollback_error:
            raise BypassError("DNS reconfigure failed and rollback failed") from rollback_error
        raise

    try:
        final = _fresh_readback(
            primary=primary, fallback=fallback, key=key, secret=secret
        )
        values = {entry["interface"]: entry["option"]["value"] for entry in final}
        if values != {interface: target for interface in INTERFACES}:
            raise BypassError("DNS option read-back did not match the requested target")
    except BypassError:
        try:
            _rollback(
                changed,
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
        except BypassError as rollback_error:
            raise BypassError("DNS option read-back failed and rollback failed") from rollback_error
        raise
    return f"changed={len(changed)}, already={already}, value={target}"


def enable_bypass(*, primary: str, fallback: str, key: str, secret: str) -> str:
    """Advertise OPNsense Unbound through DHCP option 6."""
    return _switch(
        UNBOUND_DNS, primary=primary, fallback=fallback, key=key, secret=secret
    )


def disable_bypass(*, primary: str, fallback: str, key: str, secret: str) -> str:
    """Advertise both Pi-holes through DHCP option 6."""
    return _switch(
        PIHOLE_DNS, primary=primary, fallback=fallback, key=key, secret=secret
    )


def status_bypass(*, primary: str, fallback: str, key: str, secret: str) -> dict[str, Any]:
    """Return the three managed DHCP option values without mutating state."""
    entries = _discover_managed_options(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    options = {entry["interface"]: entry["option"]["value"] for entry in entries}
    return {"options": options, "values": options.copy()}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select DHCP option 6 between OPNsense Unbound and both Pi-holes."
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--enable", action="store_true", help="Advertise OPNsense Unbound.")
    actions.add_argument("--disable", action="store_true", help="Advertise both Pi-holes.")
    actions.add_argument("--status", action="store_true", help="Read and report managed values.")
    return parser.parse_args(argv)


def _credentials() -> tuple[str, str, str, str]:
    return (
        os.environ.get("OPNSENSE_API_PRIMARY", "https://router.montycasa.net"),
        os.environ.get("OPNSENSE_API_FALLBACK", "http://192.168.86.1"),
        os.environ.get("OPNSENSE_KEY", ""),
        os.environ.get("OPNSENSE_SECRET", ""),
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    primary, fallback, key, secret = _credentials()
    if not key or not secret:
        print(
            "ERROR: --enable/--disable/--status require OPNSENSE_KEY and OPNSENSE_SECRET.",
            file=sys.stderr,
        )
        return 3
    kwargs = {"primary": primary, "fallback": fallback, "key": key, "secret": secret}
    try:
        if args.status:
            status = status_bypass(**kwargs)
            print("DHCP DNS options:")
            for interface in INTERFACES:
                print(f"  {interface}: {status['options'][interface]}")
            return 0
        summary = enable_bypass(**kwargs) if args.enable else disable_bypass(**kwargs)
        print(summary)
        return 0
    except CredentialError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 3
    except BypassError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
