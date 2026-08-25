#!/usr/bin/env python3
"""Offline tests for the DHCP-only OPNsense DNS path switch."""

from __future__ import annotations

import copy
import io
import json
import socket
import unittest
import urllib.error
from unittest.mock import patch

from scripts.dns_migration import switch_dns_path


PRIMARY = "https://example.invalid"
FALLBACK = "http://192.0.2.1"
KEY = "test-key"
SECRET = "test-secret"
INTERFACES = ("lan", "opt1", "opt2")
DESCRIPTIONS = switch_dns_path.MANAGED_DESCRIPTIONS
UNBOUND = switch_dns_path.UNBOUND_DNS
PIHOLES = switch_dns_path.PIHOLE_DNS


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index + 1:012x}"


def _option(interface: str, value: str = PIHOLES, *, uuid: str | None = None) -> dict[str, object]:
    return {
        "uuid": uuid or _uuid(INTERFACES.index(interface)),
        "description": DESCRIPTIONS[interface],
        "scope": interface,
        "interface": interface,
        "type": "dhcpv4",
        "set": "6",
        "value": value,
        "tag": "preserve-me",
        "extra": {"operator": "unmanaged-fields-stay"},
    }


def _managed_values(value: str = PIHOLES) -> dict[str, dict[str, object]]:
    return {
        interface: _option(interface, value=value)
        for interface in INTERFACES
    }


def _search_page(rows: list[dict[str, object]], *, total: int | None = None, row_count: int = 100, current: int = 1):
    return {
        "rows": copy.deepcopy(rows),
        "total": len(rows) if total is None else total,
        "current": current,
        "rowCount": row_count,
    }


class Base(unittest.TestCase):
    def kwargs(self):
        return {"primary": PRIMARY, "fallback": FALLBACK, "key": KEY, "secret": SECRET}


class FixtureTransport:
    """Self-contained fake API with search, get, set, and reconfigure calls."""

    def __init__(self, *, values: str = PIHOLES, unrelated: bool = True, page_size: int = 100):
        self.options = {
            item["uuid"]: item
            for item in _managed_values(values).values()
        }
        if unrelated:
            row = _option("lan", uuid="00000000-0000-4000-8000-000000000099")
            row.update({"description": "operator:unrelated", "set": "15", "value": "example"})
            self.options[row["uuid"]] = row
        self.calls: list[tuple[str, str, object]] = []
        self.set_bodies: list[dict[str, object]] = []
        self.page_size = page_size
        self.fail_set_for: str | None = None
        self.fail_reconfigure = False
        self.ambiguous_set_for: str | None = None

    def __call__(self, method: str, path: str, **kwargs):
        body = copy.deepcopy(kwargs.get("body"))
        self.calls.append((method, path, body))
        if method != "POST":
            raise AssertionError(f"all dnsmasq API calls must be POST: {method} {path}")
        if path == switch_dns_path.DNSMASQ_API_SEARCH:
            rows = list(self.options.values())
            page = int(body["current"])
            start = (page - 1) * self.page_size
            end = start + self.page_size
            return "fake", _search_page(rows[start:end], total=len(rows), row_count=self.page_size, current=page)
        if path.startswith(f"{switch_dns_path.DNSMASQ_API_GET}/"):
            uuid = path.rsplit("/", 1)[1]
            option = copy.deepcopy(self.options[uuid])
            option.pop("uuid", None)
            return "fake", {"option": option}
        if path.startswith(f"{switch_dns_path.DNSMASQ_API_SET}/"):
            uuid = path.rsplit("/", 1)[1]
            if uuid == self.fail_set_for:
                return "fake", {"result": "failed"}
            option = copy.deepcopy(body["option"])
            self.set_bodies.append(option)
            self.options[uuid] = option
            if uuid == self.ambiguous_set_for:
                raise switch_dns_path.BypassError("connection lost after set", post_send=True)
            return "fake", {"result": "saved"}
        if path == switch_dns_path.DNSMASQ_API_RECONFIGURE:
            if self.fail_reconfigure:
                return "fake", {"result": "failed"}
            return "fake", {"result": "saved"}
        raise AssertionError(f"unexpected API call: {method} {path}")

    def paths(self, suffix: str) -> list[str]:
        return [path for _, path, _ in self.calls if path == suffix or path.startswith(suffix + "/")]


class DiscoveryTests(Base):
    def test_search_uses_post_pagination_and_gets_each_marker(self):
        fake = FixtureTransport(page_size=2)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            entries = switch_dns_path._discover_managed_options(**self.kwargs())
        self.assertEqual([entry["interface"] for entry in entries], list(INTERFACES))
        search_calls = [body for method, path, body in fake.calls if path == switch_dns_path.DNSMASQ_API_SEARCH]
        self.assertEqual([body["current"] for body in search_calls], [1, 2])
        self.assertTrue(all(method == "POST" for method, _, _ in fake.calls))
        self.assertEqual(len(fake.paths(switch_dns_path.DNSMASQ_API_GET)), 3)

    def test_exact_description_and_scope_interface_ownership(self):
        fake = FixtureTransport()
        fake.options[_uuid(0)]["description"] = "hermes:dnsmasq:managed-option:lan:DNS"
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

        fake = FixtureTransport()
        fake.options[_uuid(0)]["scope"] = "opt1"
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

        fake = FixtureTransport()
        fake.options[_uuid(0)]["interface"] = "opt1"
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

    def test_authoritative_selected_maps_are_accepted(self):
        fake = FixtureTransport()
        for option in fake.options.values():
            if option.get("description") not in DESCRIPTIONS.values():
                continue
            interface = str(option["interface"])
            option["interface"] = {
                "": {"selected": 0, "value": "Any"},
                interface: {"selected": 1, "value": interface.upper()},
            }
            option["type"] = {
                "match": {"selected": 0, "value": "Match"},
                "set": {"selected": 1, "value": "Set"},
            }
            option["option"] = {
                "6": {"selected": 1, "value": "dns-server [6]"},
                "15": {"selected": 0, "value": "domain-name [15]"},
            }
            option.pop("set", None)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            status = switch_dns_path.status_bypass(**self.kwargs())
        self.assertEqual(status["options"], {interface: PIHOLES for interface in INTERFACES})

    def test_dhcpv4_type_and_option_set_are_required(self):
        for field, value in (("type", "dhcpv6"), ("set", "15")):
            fake = FixtureTransport()
            fake.options[_uuid(0)][field] = value
            with self.subTest(field=field), patch.object(switch_dns_path, "_api_call", side_effect=fake):
                with self.assertRaises(switch_dns_path.BypassError):
                    switch_dns_path.status_bypass(**self.kwargs())

    def test_missing_marker_fails_before_any_set(self):
        fake = FixtureTransport()
        del fake.options[_uuid(2)]
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])

    def test_duplicate_marker_fails_before_any_set(self):
        fake = FixtureTransport()
        duplicate = _option("lan", uuid="00000000-0000-4000-8000-000000000098")
        fake.options[duplicate["uuid"]] = duplicate
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.disable_bypass(**self.kwargs())
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])

    def test_scalar_values_are_required_and_unknown_drift_is_rejected(self):
        for value in ([], {"value": PIHOLES}, "192.168.86.53"):
            fake = FixtureTransport()
            fake.options[_uuid(0)]["value"] = value
            with self.subTest(value=value), patch.object(switch_dns_path, "_api_call", side_effect=fake):
                with self.assertRaises(switch_dns_path.BypassError):
                    switch_dns_path.status_bypass(**self.kwargs())

    def test_get_drift_is_rejected_before_mutation(self):
        fake = FixtureTransport()
        original = fake.__call__

        def drift(method, path, **kwargs):
            result = original(method, path, **kwargs)
            if path == f"{switch_dns_path.DNSMASQ_API_GET}/{_uuid(0)}":
                result[1]["option"]["scope"] = "opt1"
            return result

        with patch.object(switch_dns_path, "_api_call", side_effect=drift):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])


class LifecycleTests(Base):
    def test_enable_selects_unbound_with_scalar_value(self):
        fake = FixtureTransport()
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.kwargs())
        self.assertIn("changed=3", summary)
        self.assertEqual(
            {entry["interface"]: entry["value"] for entry in fake.set_bodies},
            {interface: UNBOUND for interface in INTERFACES},
        )
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [switch_dns_path.DNSMASQ_API_RECONFIGURE])

    def test_disable_selects_both_piholes_with_scalar_value(self):
        fake = FixtureTransport(values=UNBOUND)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.kwargs())
        self.assertIn("changed=3", summary)
        self.assertEqual(
            {entry["interface"]: entry["value"] for entry in fake.set_bodies},
            {interface: PIHOLES for interface in INTERFACES},
        )

    def test_enable_is_idempotent_without_sets_or_reconfigure(self):
        fake = FixtureTransport(values=UNBOUND)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.kwargs())
        self.assertIn("changed=0", summary)
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [])

    def test_disable_is_idempotent_without_sets_or_reconfigure(self):
        fake = FixtureTransport(values=PIHOLES)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.kwargs())
        self.assertIn("changed=0", summary)
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [])

    def test_only_value_changes_and_unrelated_row_is_preserved(self):
        fake = FixtureTransport()
        before = copy.deepcopy(fake.options)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            switch_dns_path.enable_bypass(**self.kwargs())
        for interface in INTERFACES:
            uuid = _uuid(INTERFACES.index(interface))
            expected = copy.deepcopy(before[uuid])
            expected["value"] = UNBOUND
            self.assertEqual(fake.options[uuid], expected)
        unrelated_uuid = "00000000-0000-4000-8000-000000000099"
        self.assertEqual(fake.options[unrelated_uuid], before[unrelated_uuid])

    def test_reconfigure_occurs_after_all_writes_and_before_readback(self):
        fake = FixtureTransport()
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            switch_dns_path.enable_bypass(**self.kwargs())
        set_indexes = [i for i, (_, path, _) in enumerate(fake.calls) if path.startswith(switch_dns_path.DNSMASQ_API_SET + "/")]
        reconfigure_index = next(i for i, (_, path, _) in enumerate(fake.calls) if path == switch_dns_path.DNSMASQ_API_RECONFIGURE)
        readback_index = next(i for i, (_, path, _) in enumerate(fake.calls[reconfigure_index + 1:], reconfigure_index + 1) if path.startswith(switch_dns_path.DNSMASQ_API_SEARCH))
        self.assertTrue(all(index < reconfigure_index for index in set_indexes))
        self.assertLess(reconfigure_index, readback_index)

    def test_known_set_failure_rolls_back_successful_writes_in_reverse_order(self):
        fake = FixtureTransport()
        fake.fail_set_for = _uuid(1)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        set_paths = fake.paths(switch_dns_path.DNSMASQ_API_SET)
        self.assertEqual(
            [path.rsplit("/", 1)[1] for path in set_paths],
            [_uuid(0), _uuid(1), _uuid(0)],
        )
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [])
        self.assertEqual(fake.options[_uuid(0)]["value"], PIHOLES)

    def test_ambiguous_set_failure_fresh_reads_without_blind_retry(self):
        fake = FixtureTransport()
        fake.ambiguous_set_for = _uuid(1)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.enable_bypass(**self.kwargs())
        self.assertTrue(ctx.exception.post_send)
        set_paths = fake.paths(switch_dns_path.DNSMASQ_API_SET)
        self.assertEqual([path.rsplit("/", 1)[1] for path in set_paths], [_uuid(0), _uuid(1)])
        self.assertGreaterEqual(len(fake.paths(switch_dns_path.DNSMASQ_API_SEARCH)), 2)
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [])

    def test_reconfigure_failure_rolls_back_successful_writes(self):
        fake = FixtureTransport()
        fake.fail_reconfigure = True
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        set_paths = fake.paths(switch_dns_path.DNSMASQ_API_SET)
        self.assertEqual(
            [path.rsplit("/", 1)[1] for path in set_paths],
            [_uuid(0), _uuid(1), _uuid(2), _uuid(2), _uuid(1), _uuid(0)],
        )
        self.assertEqual([fake.options[_uuid(i)]["value"] for i in range(3)], [PIHOLES] * 3)


class StatusAndTransportTests(Base):
    def test_status_reports_all_three_managed_values_read_only(self):
        fake = FixtureTransport()
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            status = switch_dns_path.status_bypass(**self.kwargs())
        self.assertEqual(status["options"], {interface: PIHOLES for interface in INTERFACES})
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_SET), [])
        self.assertEqual(fake.paths(switch_dns_path.DNSMASQ_API_RECONFIGURE), [])

    def test_no_nat_lifecycle_or_direct_resolver_probe_remains(self):
        with open(switch_dns_path.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertNotIn("d_nat", source)
        self.assertNotIn("firewall/", source)
        self.assertNotIn("create_connection", source)
        self.assertNotIn("_tcp_53_probe", source)

    def test_request_body_header_is_only_present_for_json(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"rows": [], "total": 0, "current": 1, "rowCount": 100}'

        with patch.object(switch_dns_path.urllib.request, "urlopen", return_value=Response()) as call:
            switch_dns_path._api_call("POST", switch_dns_path.DNSMASQ_API_SEARCH, body={"current": 1}, **self.kwargs())
            request = call.call_args.args[0]
        self.assertIn("Content-type", request.headers)

    def test_ambiguous_post_transport_failure_is_not_retried(self):
        failure = urllib.error.URLError("connection reset")
        with patch.object(switch_dns_path.urllib.request, "urlopen", side_effect=failure) as call:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call("POST", switch_dns_path.DNSMASQ_API_SET + "/" + _uuid(0), body={"option": {}}, **self.kwargs())
        self.assertEqual(call.call_count, 1)
        self.assertTrue(ctx.exception.post_send)

    def test_dns_resolution_failure_can_use_configured_fallback(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"result": "saved"}'

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=[socket.gaierror(-2, "dns"), Response()],
        ) as call:
            transport, response = switch_dns_path._api_call(
                "POST", switch_dns_path.DNSMASQ_API_SET + "/" + _uuid(0), body={"option": {}}, **self.kwargs()
            )
        self.assertEqual(call.call_count, 2)
        self.assertEqual(transport, "fallback")
        self.assertEqual(response["result"], "saved")

    def test_http_403_is_credential_error_without_fallback(self):
        err = urllib.error.HTTPError(
            PRIMARY + "/api/" + switch_dns_path.DNSMASQ_API_SEARCH,
            403,
            "Forbidden",
            {},
            io.BytesIO(b"x"),
        )
        with patch.object(switch_dns_path.urllib.request, "urlopen", side_effect=err) as call:
            with self.assertRaises(switch_dns_path.CredentialError):
                switch_dns_path._api_call("POST", switch_dns_path.DNSMASQ_API_SEARCH, body={}, **self.kwargs())
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
