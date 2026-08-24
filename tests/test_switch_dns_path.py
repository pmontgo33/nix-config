#!/usr/bin/env python3
"""Offline tests for the OPNsense 26.1 Destination NAT contract.

The production API namespace is ``firewall/d_nat``.  The authoritative model
read is ``GET /api/firewall/d_nat/get`` and returns ``{"DNat": {"rule": ...}}``.
Rules use nested ``source``/``destination`` match fields, ``target`` plus
``local-port`` for the redirect, and ``disabled`` for state.
"""

from __future__ import annotations

import copy
import io
import json
import unittest
from unittest.mock import patch

from scripts.dns_migration import switch_dns_path


PRIMARY = "https://example.invalid"
FALLBACK = "http://192.0.2.1"
KEY = "test-key"
SECRET = "test-secret"

INTERFACES = {"lan": "LAN", "opt1": "IOT", "opt2": "GUEST"}
PROTOCOLS = {"udp": "UDP", "tcp": "TCP", "any": "any"}
IPPROTOCOLS = {"inet": "IPv4", "inet6": "IPv6", "inet46": "IPv4+IPv6"}
PAIRS = [(iface, proto) for iface in ("lan", "opt1", "opt2") for proto in ("udp", "tcp")]


def _uuid(index: int) -> str:
    """Return a canonical UUID-shaped fixture key."""
    return f"00000000-0000-4000-8000-{index + 1:012x}"


def _option(selected: str, options: dict[str, str]) -> dict[str, dict[str, object]]:
    return {
        key: {"value": label, "selected": int(key == selected)}
        for key, label in options.items()
    }


def _rule(
    interface: str,
    protocol: str,
    *,
    disabled: str = "1",
    target: str | None = None,
    local_port: str = "53",
    destination_network: str = "any",
    destination_port: str = "53",
    source_network: str = "any",
    ipprotocol: str = "inet",
    uuid: str = "",
) -> dict[str, object]:
    return {
        "sequence": str(switch_dns_path.DNAT_SEQUENCE[(interface, protocol)]),
        "disabled": disabled,
        "nordr": "0",
        "interface": _option(interface, INTERFACES),
        "ipprotocol": _option(ipprotocol, IPPROTOCOLS),
        "protocol": _option(protocol, PROTOCOLS),
        "source": {"network": source_network, "port": "", "not": "0"},
        "destination": {
            "network": destination_network,
            "port": destination_port,
            "not": "0",
        },
        "target": target or switch_dns_path.BYPASS_TARGET_IP,
        "local-port": local_port,
        "poolopts": "",
        "log": "0",
        "descr": "",
        "nosync": "0",
        "uuid": uuid,
    }


def _rules(*, disabled: str = "1") -> dict[str, dict[str, object]]:
    return {
        _uuid(index): _rule(iface, proto, disabled=disabled, uuid=_uuid(index))
        for index, (iface, proto) in enumerate(PAIRS)
    }


def _model(rules: dict[str, dict[str, object]] | None = None, *, list_value=None):
    if rules is not None and list_value is not None:
        raise ValueError("choose rules or list_value")
    return {"DNat": {"rule": rules if rules is not None else (list_value if list_value is not None else {})}}


class Base(unittest.TestCase):
    def kwargs(self):
        return {"primary": PRIMARY, "fallback": FALLBACK, "key": KEY, "secret": SECRET}


class ContractReadTests(Base):
    def test_controller_and_model_endpoint(self):
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", _model(_rules())),
        ) as call:
            result = switch_dns_path._read_model(**self.kwargs())
        self.assertEqual(result["DNat"]["rule"].keys(), _rules().keys())
        call.assert_called_once_with("GET", "firewall/d_nat/get", **self.kwargs())

    def test_empty_list_is_valid_model_shape(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model(list_value=[]))
        ):
            self.assertEqual(switch_dns_path._search_bypass_rules(**self.kwargs()), [])

    def test_non_empty_list_is_rejected_before_lifecycle(self):
        malformed = _model(list_value=[_rule("lan", "udp", uuid="list-0")])
        with patch.object(switch_dns_path, "_api_call", return_value=("test", malformed)) as call:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("list with 1 entries", str(ctx.exception))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[:2], ("GET", switch_dns_path.NAT_API_MODEL))

    def test_old_source_nat_model_root_is_rejected(self):
        old_shape = {"filter": {"snatrules": {"rule": {}}}}
        with patch.object(switch_dns_path, "_api_call", return_value=("test", old_shape)):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

    def test_malformed_model_roots_and_containers_are_rejected(self):
        malformed = (
            None,
            [],
            {},
            {"DNat": []},
            {"DNat": {"rule": "oops"}},
            {"DNat": {"rule": 42}},
        )
        for response in malformed:
            with self.subTest(response=response), patch.object(
                switch_dns_path, "_api_call", return_value=("test", response)
            ):
                with self.assertRaises(switch_dns_path.BypassError):
                    switch_dns_path.status_bypass(**self.kwargs())

    def test_non_object_rule_entry_is_rejected(self):
        rules = _rules()
        rules[_uuid(0)] = "not-a-rule"  # type: ignore[assignment]
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

    def test_managed_subset_uses_full_destination_tuple(self):
        rules = _rules()
        rules[_uuid(100)] = _rule("lan", "udp", target="8.8.8.8", uuid=_uuid(100))
        rules[_uuid(101)] = _rule("lan", "udp", destination_port="853", uuid=_uuid(101))
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))):
            entries = switch_dns_path._search_bypass_rules(**self.kwargs())
        self.assertEqual({entry["uuid"] for entry in entries}, set(_rules()))
        self.assertEqual({entry["key"] for entry in entries}, switch_dns_path.BYPASS_MANAGED_KEY_SET)

    def test_nested_port_and_negation_fields_are_owned_exactly(self):
        for field_path, value in (("source.port", "12345"), ("source.not", "1"), ("destination.not", "1")):
            with self.subTest(field_path=field_path):
                rule = _rule("lan", "udp")
                container, field = field_path.split(".")
                rule[container][field] = value
                self.assertNotIn(switch_dns_path._managed_key_of(rule), switch_dns_path.BYPASS_MANAGED_KEY_SET)

    def test_ambiguous_multi_select_is_not_owned(self):
        rule = _rule("lan", "udp")
        rule["interface"]["opt1"]["selected"] = 1  # type: ignore[index]
        self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_scalar_authoritative_option_fields_are_not_owned(self):
        for field, scalar in (("interface", "lan"), ("protocol", "udp"), ("ipprotocol", "inet")):
            with self.subTest(field=field):
                rule = _rule("lan", "udp")
                rule[field] = scalar
                self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_malformed_option_map_is_not_owned(self):
        for malformed in (
            {"lan": "not-an-option-entry"},
            {"lan": {"selected": True}},
            {"lan": {"selected": 0}},
            {"lan": {"selected": 1}, "opt1": {"selected": 1}},
        ):
            with self.subTest(malformed=malformed):
                rule = _rule("lan", "udp")
                rule["interface"] = malformed
                self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_non_object_direct_rule_is_not_owned(self):
        self.assertIsNone(switch_dns_path._managed_key_of(None))  # type: ignore[arg-type]

    def test_malformed_ports_are_not_owned(self):
        for field in ("local-port", "destination"):
            with self.subTest(field=field):
                rule = _rule("lan", "udp")
                if field == "local-port":
                    rule[field] = "http"
                else:
                    rule[field]["port"] = "http"  # type: ignore[index]
                self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_missing_disabled_is_rejected(self):
        rules = _rules()
        rules[_uuid(0)].pop("disabled")
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.kwargs())

    def test_non_integer_disabled_values_are_rejected(self):
        for invalid in (0.0, 1.0, True, False, "2"):
            rules = _rules()
            rules[_uuid(0)]["disabled"] = invalid
            with self.subTest(invalid=invalid):
                with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))):
                    with self.assertRaises(switch_dns_path.BypassError):
                        switch_dns_path.status_bypass(**self.kwargs())


class PayloadTests(Base):
    def test_destination_nat_payload_is_nested_and_disabled(self):
        payload = switch_dns_path._build_bypass_rule_payload("lan", "udp")
        self.assertEqual(payload["sequence"], "1000")
        self.assertEqual(payload["disabled"], "1")
        self.assertEqual(payload["interface"], "lan")
        self.assertEqual(payload["protocol"], "udp")
        self.assertEqual(payload["ipprotocol"], "inet")
        self.assertEqual(payload["source"], {"network": "any", "port": "", "not": "0"})
        self.assertEqual(payload["destination"], {"network": "any", "port": "53", "not": "0"})
        self.assertEqual(payload["target"], switch_dns_path.BYPASS_TARGET_IP)
        self.assertEqual(payload["local-port"], "53")
        for old_field in ("target_port", "destination_port", "source_net", "destination_net", "enabled", "nat_port"):
            self.assertNotIn(old_field, payload)

    def test_payload_covers_all_six_pairs_with_unique_sequences(self):
        payloads = [switch_dns_path._build_bypass_rule_payload(*pair) for pair in PAIRS]
        self.assertEqual({p["sequence"] for p in payloads}, {str(i) for i in range(1000, 1006)})
        self.assertTrue(all(p["disabled"] == "1" for p in payloads))


class LifecycleTests(Base):
    def test_install_adds_missing_rules_and_re_reads_dnat_model(self):
        state = _model({})
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                body = kwargs["body"]["rule"]
                pair = (body["interface"], body["protocol"])
                uuid = _uuid(PAIRS.index(pair))
                state["DNat"]["rule"][uuid] = _rule(*pair, disabled="1", uuid=uuid)
                return "test", {"result": "saved", "uuid": uuid}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK\n\n"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("installed=6", summary)
        self.assertEqual(sum(path == switch_dns_path.NAT_API_ADD for _, path, _ in calls), 6)
        self.assertEqual(calls[-1][:2], ("GET", switch_dns_path.NAT_API_MODEL))
        self.assertTrue(all(body["rule"]["disabled"] == "1" for method, path, body in calls if path == switch_dns_path.NAT_API_ADD))

    def test_install_is_idempotent(self):
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(_rules()))) as call:
            summary = switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("installed=0", summary)
        self.assertEqual(call.call_count, 1)

    def test_install_tops_up_partial_six_rule_set(self):
        state = _model(dict(list(_rules().items())[:3]))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                body = kwargs["body"]["rule"]
                pair = (body["interface"], body["protocol"])
                index = PAIRS.index(pair)
                state["DNat"]["rule"][_uuid(index)] = _rule(*pair, uuid=_uuid(index))
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("installed=3", summary)
        self.assertEqual(sum(path == switch_dns_path.NAT_API_ADD for _, path, _ in calls), 3)

    def test_duplicate_owned_keys_refuse_write(self):
        rules = _rules()
        rules[_uuid(20)] = _rule("lan", "udp", uuid=_uuid(20))
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))) as call:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("ambiguous", str(ctx.exception))
        self.assertEqual(call.call_count, 1)

    def test_enable_flips_disabled_to_zero_and_applies(self):
        state = _model(_rules(disabled="1"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                uuid = path.rsplit("/", 1)[1]
                state["DNat"]["rule"][uuid]["disabled"] = kwargs["body"]["rule"]["disabled"]
                self.assertEqual(kwargs["body"]["rule"]["disabled"], "0")
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.kwargs())
        self.assertIn("enabled=6", summary)
        self.assertEqual(sum(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path, _ in calls), 6)

    def test_disable_flips_disabled_to_one(self):
        state = _model(_rules(disabled="0"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                uuid = path.rsplit("/", 1)[1]
                state["DNat"]["rule"][uuid]["disabled"] = "1"
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.kwargs())
        self.assertIn("disabled=6", summary)

    def test_enable_is_idempotent_when_already_enabled(self):
        state = _model(_rules(disabled="0"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.kwargs())
        self.assertIn("enabled=0", summary)
        self.assertIn("already_enabled=6", summary)
        self.assertFalse(any(method == "POST" for method, _ in calls))

    def test_disable_is_idempotent_when_already_disabled(self):
        state = _model(_rules(disabled="1"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.kwargs())
        self.assertIn("disabled=0", summary)
        self.assertIn("already_disabled=6", summary)
        self.assertFalse(any(method == "POST" for method, _ in calls))

    def test_install_post_mutation_empty_readback_fails_closed(self):
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", _model({})
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.kwargs())
        self.assertIn("have 0/6", str(ctx.exception))
        self.assertEqual(sum(path == switch_dns_path.NAT_API_ADD for _, path in calls), 6)

    def test_disable_post_mutation_empty_readback_fails_closed(self):
        state = _model(_rules(disabled="0"))
        calls = []
        after_apply = [False]

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", _model({}) if after_apply[0] else state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                after_apply[0] = True
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.disable_bypass(**self.kwargs())
        self.assertIn("have 0/6", str(ctx.exception))

    def test_set_rule_echoes_full_authoritative_rule_body(self):
        state = _model(_rules(disabled="1"))
        original = copy.deepcopy(state["DNat"]["rule"][_uuid(0)])
        set_bodies = []

        def fake(method, path, **kwargs):
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                body = copy.deepcopy(kwargs["body"]["rule"])
                set_bodies.append(body)
                uuid = path.rsplit("/", 1)[1]
                state["DNat"]["rule"][uuid] = body
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            switch_dns_path.enable_bypass(**self.kwargs())
        self.assertEqual(len(set_bodies), 6)
        expected = copy.deepcopy(original)
        expected["disabled"] = "0"
        self.assertEqual(set_bodies[0], expected)

    def test_drifted_get_rule_stops_before_set(self):
        state = _model(_rules(disabled="1"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                rule = copy.deepcopy(state["DNat"]["rule"][path.rsplit("/", 1)[1]])
                rule["target"] = "8.8.8.8"
                return "test", {"rule": rule}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        self.assertFalse(any(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path in calls))

    def test_mismatched_get_rule_uuid_stops_before_set(self):
        state = _model(_rules(disabled="1"))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                rule = copy.deepcopy(state["DNat"]["rule"][uuid])
                rule["uuid"] = _uuid(99)
                return "test", {"rule": rule}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.kwargs())
        self.assertFalse(any(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path in calls))

    def test_uninstall_deletes_only_owned_uuids(self):
        state = _model(_rules())
        state["DNat"]["rule"][_uuid(100)] = _rule("lan", "udp", target="8.8.8.8", uuid=_uuid(100))
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(state["DNat"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                state["DNat"]["rule"].pop(path.rsplit("/", 1)[1])
                return "test", {"result": "deleted"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            self.assertEqual(switch_dns_path.uninstall_bypass(**self.kwargs()), "uninstalled=6")
        deleted = [path.rsplit("/", 1)[1] for method, path in calls if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/")]
        self.assertEqual(set(deleted), set(_rules()))
        self.assertNotIn(_uuid(100), deleted)

    def test_uninstall_revalidates_each_rule_before_delete(self):
        state = _model(_rules())
        calls = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", state
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                rule = copy.deepcopy(state["DNat"]["rule"][uuid])
                if uuid == _uuid(0):
                    rule["target"] = "8.8.8.8"
                return "test", {"rule": rule}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.uninstall_bypass(**self.kwargs())
        deleted = [path for method, path in calls if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/")]
        self.assertEqual(deleted, [])

    def test_uninstall_rejects_malformed_uuid_before_delete(self):
        rules = _rules()
        rules["not-a-uuid"] = copy.deepcopy(rules.pop(_uuid(0)))
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))) as call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.uninstall_bypass(**self.kwargs())
        self.assertEqual(call.call_count, 1)

    def test_uninstall_is_idempotent_when_empty(self):
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model({}))) as call:
            self.assertEqual(switch_dns_path.uninstall_bypass(**self.kwargs()), "uninstalled=0")
        call.assert_called_once_with("GET", switch_dns_path.NAT_API_MODEL, **self.kwargs())


class StatusAndTransportTests(Base):
    def test_status_uses_disabled_state(self):
        rules = _rules(disabled="1")
        rules[_uuid(0)]["disabled"] = "0"
        with patch.object(switch_dns_path, "_api_call", return_value=("test", _model(rules))):
            status = switch_dns_path.status_bypass(**self.kwargs())
        self.assertEqual(status["state"], "mixed")
        self.assertEqual(status["enabled"], 1)
        self.assertEqual(status["disabled"], 5)
        self.assertEqual(status["total_rules"], 6)

    def test_apply_accepts_live_trimmed_ok(self):
        with patch.object(switch_dns_path, "_api_call", return_value=("test", {"status": "OK\n\n"})):
            switch_dns_path._apply_nat(**self.kwargs())

    def test_post_dns_failure_uses_literal_ip_only(self):
        import socket
        with patch.object(switch_dns_path.urllib.request, "urlopen", side_effect=socket.gaierror(-2, "dns")) as call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}}, **self.kwargs()
                )
        self.assertEqual(call.call_count, 2)
        urls = [item.args[0].full_url for item in call.call_args_list]
        self.assertTrue(urls[1].startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK))
        self.assertFalse(urls[1].startswith(FALLBACK))

    def test_ambiguous_post_transport_failure_is_not_retried(self):
        import http.client
        import urllib.error

        failures = (
            urllib.error.URLError("connection reset"),
            ConnectionResetError("connection reset"),
            BrokenPipeError("broken pipe"),
            http.client.BadStatusLine("bad status"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.object(
                switch_dns_path.urllib.request, "urlopen", side_effect=failure
            ) as call:
                with self.assertRaises(switch_dns_path.BypassError) as ctx:
                    switch_dns_path._api_call(
                        "POST", switch_dns_path.NAT_API_ADD,
                        body={"rule": {"interface": "lan"}}, **self.kwargs()
                    )
            self.assertEqual(call.call_count, 1)
            self.assertTrue(ctx.exception.post_send)

    def test_post_response_read_failure_is_not_retried(self):
        class ReadFailResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise ConnectionResetError("response reset")

        with patch.object(switch_dns_path.urllib.request, "urlopen", return_value=ReadFailResponse()) as call:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}}, **self.kwargs()
                )
        self.assertEqual(call.call_count, 1)
        self.assertTrue(ctx.exception.post_send)

    def test_post_invalid_utf8_response_is_not_retried(self):
        class InvalidResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"\xff"

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            return_value=InvalidResponse(),
        ) as call:
            transport, response = switch_dns_path._api_call(
                "POST", switch_dns_path.NAT_API_ADD,
                body={"rule": {"interface": "lan"}}, **self.kwargs()
            )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(transport, "https-primary")
        with self.assertRaises(switch_dns_path.BypassError):
            switch_dns_path._require_api_result(response, "saved", switch_dns_path.NAT_API_ADD)

    def test_rejected_mutation_response_is_not_accepted(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", {"result": "failed"})
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._require_api_result({"result": "failed"}, "saved", switch_dns_path.NAT_API_SET)

    def test_apply_rejects_non_ok_response(self):
        for response in (["OK"], {}, {"status": "ERR"}, {"result": "saved"}):
            with self.subTest(response=response), patch.object(
                switch_dns_path, "_api_call", return_value=("test", response)
            ):
                with self.assertRaises(switch_dns_path.BypassError):
                    switch_dns_path._apply_nat(**self.kwargs())

    def test_api_error_envelope_is_rejected(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"error":"rejected"}'

        with patch.object(switch_dns_path.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call("GET", switch_dns_path.NAT_API_MODEL, **self.kwargs())

    def test_http_403_is_credential_error_without_fallback(self):
        import urllib.error
        err = urllib.error.HTTPError("https://example.invalid/api/firewall/d_nat/get", 403, "Forbidden", {}, io.BytesIO(b"x"))
        with patch.object(switch_dns_path.urllib.request, "urlopen", side_effect=err) as call:
            with self.assertRaises(switch_dns_path.CredentialError):
                switch_dns_path._api_call("GET", switch_dns_path.NAT_API_MODEL, **self.kwargs())
        self.assertEqual(call.call_count, 1)

    def test_request_body_header_is_only_present_for_json(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"DNat":{"rule":[]}}'

        with patch.object(switch_dns_path.urllib.request, "urlopen", return_value=Response()) as call:
            switch_dns_path._api_call("GET", switch_dns_path.NAT_API_MODEL, **self.kwargs())
            request = call.call_args.args[0]
        self.assertNotIn("Content-type", request.headers)
        self.assertNotIn("Content-Type", request.headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
