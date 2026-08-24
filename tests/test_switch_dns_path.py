#!/usr/bin/env python3
"""Tests for switch_dns_path.py against the OPNsense 26.1.11_6 model contract.

The live authoritative read endpoint is ``/api/firewall/source_nat/get``
(``NAT_API_MODEL``). The model response shape on 26.1.11_6 is:

    {
      "filter": {
        "general": {...},
        "rules": {"rule": {...}},         # firewall filter rules (not source NAT)
        "snatrules": {"rule": <dict|list>},  # source NAT rules
        "npt": {"rule": <list>},
        "onetoone": {"rule": <list>}
      }
    }

The ``snatrules.rule`` container is an empty list when no rules are
present, and a dict keyed by UUID when populated. The script accepts
both shapes and fails closed on any other type (string, int, malformed
list, etc.).

Each source_nat rule in the model has the multi-select shape for
``interface``, ``protocol``, and ``ipprotocol``:

    "interface": {
        "lan": {"value": "LAN", "selected": 1},
        "opt1": {"value": "IOT", "selected": 0},
        ...
    }

The first selected option is the canonical value. Other fields
(``target``, ``target_port``, ``destination_net``, ``destination_port``,
``source_net``, ``enabled``) are scalars. ``descr`` is present in the
model but is empty string for rules added via the API (it is silently
discarded by ``add_rule`` on 26.1.11_6), so it is NOT used as the
ownership marker — the script uses the full attribute tuple
(``BYPASS_MANAGED_KEY``) instead.
"""

import copy
import io
import unittest
from unittest.mock import patch

from scripts.dns_migration import switch_dns_path


PRIMARY = "https://example.invalid"
FALLBACK = "http://192.0.2.1"
KEY = "test-key"
SECRET = "test-secret"


# ---------------------------------------------------------------------------
# Fixtures: live-shape rule dicts and model responses
# ---------------------------------------------------------------------------


def _multi_select(selected, options):
    """Build an OPNsense multi-select dict shape."""
    return {
        opt: {"value": label, "selected": 1 if opt == selected else 0}
        for opt, label in options.items()
    }


_INTERFACE_OPTIONS = {"lan": "LAN", "opt1": "IOT", "opt2": "GUEST"}
_PROTOCOL_OPTIONS = {"udp": "UDP", "tcp": "TCP", "any": "any", "TCP/UDP": "TCP/UDP"}
_IPPROTOCOL_OPTIONS = {"inet": "IPv4", "inet6": "IPv6", "inet46": "any"}


def _managed_rule(interface, protocol, *, enabled="0", target=None, target_port="53",
                  source_net="any", destination_net="any", destination_port="53",
                  ipprotocol="inet", uuid=None):
    """Build a single managed source_nat rule dict in the live model shape.

    All fields are set to the BYPASS_MANAGED_KEY values by default;
    pass overrides to simulate drift / partial-set scenarios.
    """
    if target is None:
        target = switch_dns_path.BYPASS_TARGET_IP
    return {
        "enabled": enabled,
        "interface": _multi_select(interface, _INTERFACE_OPTIONS),
        "ipprotocol": _multi_select(ipprotocol, _IPPROTOCOL_OPTIONS),
        "protocol": _multi_select(protocol, _PROTOCOL_OPTIONS),
        "source_net": source_net,
        "destination_net": destination_net,
        "destination_port": destination_port,
        "target": target,
        "target_port": target_port,
        "nat_port": "",
        "descr": "",
        "uuid": uuid or "",
        "nonat": "0",
        "nosync": "0",
    }


def _managed_key(interface, protocol):
    """Return the BYPASS_MANAGED_KEY tuple for (interface, protocol)."""
    return (
        interface, protocol,
        switch_dns_path.BYPASS_TARGET_IP, 53,
        "any", 53,
        "any", "inet",
    )


def _full_six_rules(*, enabled="0"):
    """Return the six managed rules in the canonical (interface, protocol) order."""
    pairs = [
        ("lan", "udp"),
        ("lan", "tcp"),
        ("opt1", "udp"),
        ("opt1", "tcp"),
        ("opt2", "udp"),
        ("opt2", "tcp"),
    ]
    return [
        _managed_rule(iface, proto, enabled=enabled, uuid=f"uuid-{i}")
        for i, (iface, proto) in enumerate(pairs)
    ]


def _model_response(rules_by_uuid=None, rules_as_list=None, unrelated=None):
    """Wrap a source_nat rule container in the live model envelope shape.

    ``rules_by_uuid`` populates the canonical dict-keyed-by-UUID shape.
    ``rules_as_list`` populates the (legacy or empty) list shape. Pass
    exactly one of them.
    """
    if rules_by_uuid is not None and rules_as_list is not None:
        raise ValueError("pass exactly one of rules_by_uuid / rules_as_list")
    if rules_by_uuid is None and rules_as_list is None:
        rules_by_uuid = {}
    if rules_by_uuid is not None:
        snatrules_rule = rules_by_uuid
    else:
        snatrules_rule = rules_as_list  # type: ignore[assignment]
    return {
        "filter": {
            "general": {"snat_mode": {"automatic": {"value": "Auto", "selected": 1}}},
            "rules": {"rule": {}},
            "snatrules": {"rule": snatrules_rule},
            "npt": {"rule": []},
            "onetoone": {"rule": []},
        }
    }


def _six_rules_by_uuid(*, enabled="0"):
    """Return a dict {uuid: rule} for the six managed rules."""
    return {f"uuid-{i}": r for i, r in enumerate(_full_six_rules(enabled=enabled))}


# ---------------------------------------------------------------------------
# Shared base for tests
# ---------------------------------------------------------------------------


class _Base(unittest.TestCase):
    def api_kwargs(self):
        return {
            "primary": PRIMARY,
            "fallback": FALLBACK,
            "key": KEY,
            "secret": SECRET,
        }


# ---------------------------------------------------------------------------
# P2.0: Model-endpoint read
# ---------------------------------------------------------------------------


class ModelEndpointReadTests(_Base):
    """The authoritative read is /api/firewall/source_nat/get."""

    def test_search_bypass_rules_reads_from_model_endpoint(self):
        """``_search_bypass_rules`` must hit NAT_API_MODEL, never NAT_API_SEARCH."""
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", _model_response(rules_by_uuid=_six_rules_by_uuid())),
        ) as mock_api:
            switch_dns_path._search_bypass_rules(**self.api_kwargs())

        mock_api.assert_called_once()
        called = mock_api.call_args
        self.assertEqual(called.args[0], "GET")
        self.assertEqual(called.args[1], switch_dns_path.NAT_API_MODEL)

    def test_search_bypass_rules_returns_managed_subset(self):
        """The six rules matching BYPASS_MANAGED_KEY are returned; the rest are not."""
        managed = _six_rules_by_uuid()
        # Add a manual rule with the same fields but a different target — not managed.
        managed["uuid-stray"] = _managed_rule(
            "lan", "udp", target="8.8.8.8", uuid="uuid-stray",
        )
        # Add a non-bypass rule (wrong interface) — not managed.
        managed["uuid-wan"] = _managed_rule("wan", "udp", uuid="uuid-wan")
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", _model_response(rules_by_uuid=managed)),
        ):
            result = switch_dns_path._search_bypass_rules(**self.api_kwargs())

        # Result entries are dicts with uuid, rule, key.
        self.assertEqual(len(result), 6)
        for entry in result:
            self.assertIn("uuid", entry)
            self.assertIn("rule", entry)
            self.assertIn("key", entry)
        uuids = {entry["uuid"] for entry in result}
        # Stray target and wan interface are NOT managed — verify exclusion.
        self.assertNotIn("uuid-stray", uuids)
        self.assertNotIn("uuid-wan", uuids)
        # All six uuids uuid-0..uuid-5 are present.
        self.assertEqual(uuids, {f"uuid-{i}" for i in range(6)})
        # All returned keys are in BYPASS_MANAGED_KEY_SET.
        for entry in result:
            self.assertIn(entry["key"], switch_dns_path.BYPASS_MANAGED_KEY_SET)

    def test_search_bypass_rules_accepts_empty_list_shape(self):
        """An empty list (the live empty-state shape) yields zero managed rules."""
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", _model_response(rules_as_list=[])),
        ):
            result = switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertEqual(result, [])

    def test_search_bypass_rules_fails_closed_on_non_dict_non_list_container(self):
        """Anything other than dict or list at filter.snatrules.rule is rejected."""
        for bad_value in ("oops", 42, True, None):
            with self.subTest(bad_value=bad_value):
                bad_envelope = {
                    "filter": {
                        "general": {},
                        "rules": {"rule": {}},
                        "snatrules": {"rule": bad_value},
                        "npt": {"rule": []},
                        "onetoone": {"rule": []},
                    }
                }
                with patch.object(
                    switch_dns_path, "_api_call", return_value=("test", bad_envelope)
                ):
                    with self.assertRaises(switch_dns_path.BypassError):
                        switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_search_bypass_rules_fails_closed_on_missing_filter(self):
        """A model response missing the 'filter' key fails closed."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", {})
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_search_bypass_rules_fails_closed_on_missing_snatrules(self):
        """A model response missing filter.snatrules.rule fails closed."""
        bad_envelope = {"filter": {"general": {}, "rules": {"rule": {}}}}
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad_envelope)
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_search_bypass_rules_fails_closed_on_non_object_rule_entry(self):
        """A non-object entry in the rules container fails closed."""
        bad_envelope = _model_response(rules_by_uuid={
            "uuid-good": _managed_rule("lan", "udp", uuid="uuid-good"),
            "uuid-bad": "not-a-dict",
        })
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad_envelope)
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_search_bypass_rules_fails_closed_on_invalid_enabled(self):
        """A managed rule whose enabled field is not '0'/'1'/0/1 fails closed."""
        rules = _six_rules_by_uuid()
        rules["uuid-0"]["enabled"] = "yes"
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())


# ---------------------------------------------------------------------------
# P2.0b: _read_model shape validation — no synthetic UUIDs for list shapes.
# ---------------------------------------------------------------------------
#
# Luna xhigh review round 4: a non-empty ``filter.snatrules.rule`` list
# is malformed on the documented 26.1.11_6 contract. The previous
# implementation yielded ``list:0``, ``list:1`` UUIDs and let lifecycle
# actions treat them as managed rows. The fix moves the shape
# validation into ``_read_model`` so the function fails closed on any
# non-dict / non-empty-list container before the iterator is even
# constructed.


class ReadModelShapeValidationTests(_Base):
    """``_read_model`` rejects a non-empty ``filter.snatrules.rule`` list."""

    def test_read_model_accepts_empty_list_shape(self):
        """``_read_model`` returns the model dict when the rule container is ``[]``."""
        model = _model_response(rules_as_list=[])
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", model)
        ) as mock_api_call:
            result = switch_dns_path._read_model(**self.api_kwargs())
        # The model is returned unchanged.
        self.assertEqual(result, model)
        # Only the single NAT_API_MODEL GET was issued.
        self.assertEqual(mock_api_call.call_count, 1)

    def test_read_model_accepts_dict_shape(self):
        """``_read_model`` returns the model dict when the rule container is keyed by uuid."""
        model = _model_response(rules_by_uuid=_six_rules_by_uuid())
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", model)
        ) as mock_api_call:
            result = switch_dns_path._read_model(**self.api_kwargs())
        self.assertEqual(result, model)
        self.assertEqual(mock_api_call.call_count, 1)

    def test_read_model_rejects_non_empty_list_shape(self):
        """A non-empty list shape fails closed with the actionable message.

        The previous implementation yielded ``list:0`` / ``list:1``
        synthetic UUIDs and let lifecycle actions call get_rule /
        del_rule on them. ``_read_model`` must raise before any
        consumer sees the data.
        """
        bad = _model_response(rules_as_list=[
            _managed_rule("lan", "udp", uuid="list:0"),
            _managed_rule("lan", "tcp", uuid="list:1"),
        ])
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad)
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._read_model(**self.api_kwargs())
        # The error message names the exact shape and the remediation.
        self.assertIn("list with 2 entries", str(ctx.exception))
        self.assertIn("refusing to proceed", str(ctx.exception))
        self.assertIn("dict keyed by uuid or empty list", str(ctx.exception))
        # Single GET issued — no fallback, no extra calls.
        self.assertEqual(mock_api_call.call_count, 1)

    def test_read_model_rejects_single_entry_list_shape(self):
        """A list with exactly one entry is also malformed; same actionable error."""
        bad = _model_response(rules_as_list=[
            _managed_rule("lan", "udp", uuid="list:0"),
        ])
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad)
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._read_model(**self.api_kwargs())
        self.assertIn("list with 1 entries", str(ctx.exception))

    def test_read_model_rejects_non_dict_non_list_container(self):
        """Anything other than dict or list at filter.snatrules.rule is rejected."""
        bad_envelope = {
            "filter": {
                "general": {},
                "rules": {"rule": {}},
                "snatrules": {"rule": "oops"},
                "npt": {"rule": []},
                "onetoone": {"rule": []},
            }
        }
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad_envelope)
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._read_model(**self.api_kwargs())
        self.assertIn("unexpected", str(ctx.exception))
        self.assertIn("container type", str(ctx.exception))
        self.assertIn("str", str(ctx.exception))

    def test_search_bypass_rules_on_empty_list_returns_empty(self):
        """An empty ``[]`` model envelope yields zero managed rows without raising."""
        model = _model_response(rules_as_list=[])
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", model)
        ) as mock_api_call:
            result = switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertEqual(result, [])
        # The read-only path issues exactly one model GET and no
        # mutating POSTs. This is the read-only-path invariant.
        self.assertEqual(mock_api_call.call_count, 1)
        method_paths = [(c.args[0], c.args[1]) for c in mock_api_call.call_args_list]
        self.assertEqual(method_paths, [("GET", switch_dns_path.NAT_API_MODEL)])

    def test_search_bypass_rules_on_non_empty_list_raises_without_posting(self):
        """A non-empty list shape must not produce any mutating POST or any
        ``list:N`` synthetic-UUID-keyed get_rule / del_rule call.
        """
        bad = _model_response(rules_as_list=[
            _managed_rule("lan", "udp", uuid="list:0"),
            _managed_rule("lan", "tcp", uuid="list:1"),
        ])
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", bad)
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())
        # The script saw the malformed model and bailed. No mutating
        # POSTs (add_rule, set_rule, del_rule, apply) and no per-rule
        # GETs were issued — the only call is the model read.
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(method, "GET", f"unexpected POST in fail-closed path: {method} {path}")
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
            self.assertFalse(
                path.startswith(f"{switch_dns_path.NAT_API_GET}/"),
                f"unexpected per-rule get_rule in fail-closed path: {method} {path}",
            )
        self.assertEqual(mock_api_call.call_count, 1)


class SyntheticUuidNoMutationTests(_Base):
    """Lifecycle actions must never attempt set_rule / del_rule / get_rule
    on a synthetic ``list:<index>`` UUID when the model returns a
    non-empty list shape. The fail-closed contract lives at the read
    layer; these tests pin the contract from every entry point that
    would otherwise feed an iterator producing synthetic UUIDs.
    """

    def _bad_list_model(self):
        return _model_response(rules_as_list=[
            _managed_rule("lan", "udp", uuid="list:0"),
            _managed_rule("lan", "tcp", uuid="list:1"),
        ])

    def test_install_bypass_does_not_post_on_non_empty_list(self):
        """A non-empty list shape is treated as schema drift; no add_rule or apply."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", self._bad_list_model())
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.install_bypass(**self.api_kwargs())
        # Only the model GET was issued; no mutating POSTs.
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(
                method, "GET",
                f"install leaked a mutating POST on malformed model: {method} {path}",
            )
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
        # Specifically: no add_rule, no apply, no per-rule get_rule/set_rule/del_rule.
        method_paths = [(c.args[0], c.args[1]) for c in mock_api_call.call_args_list]
        for m, p in method_paths:
            self.assertNotEqual(p, switch_dns_path.NAT_API_ADD)
            self.assertNotEqual(p, switch_dns_path.NAT_API_APPLY)
            self.assertFalse(p.startswith(f"{switch_dns_path.NAT_API_GET}/"))
            self.assertFalse(p.startswith(f"{switch_dns_path.NAT_API_SET}/"))
            self.assertFalse(p.startswith(f"{switch_dns_path.NAT_API_DEL}/"))

    def test_enable_bypass_does_not_post_on_non_empty_list(self):
        """enable must not flip or get_rule on synthetic ``list:0`` UUIDs."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", self._bad_list_model())
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(
                method, "GET",
                f"enable leaked a mutating POST on malformed model: {method} {path}",
            )
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_GET}/"))
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_SET}/"))
        self.assertEqual(mock_api_call.call_count, 1)

    def test_disable_bypass_does_not_post_on_non_empty_list(self):
        """disable must not flip or get_rule on synthetic ``list:0`` UUIDs."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", self._bad_list_model())
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.disable_bypass(**self.api_kwargs())
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(
                method, "GET",
                f"disable leaked a mutating POST on malformed model: {method} {path}",
            )
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_GET}/"))
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_SET}/"))
        self.assertEqual(mock_api_call.call_count, 1)

    def test_uninstall_bypass_does_not_post_on_non_empty_list(self):
        """uninstall must not del_rule or get_rule on synthetic ``list:0`` UUIDs."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", self._bad_list_model())
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.uninstall_bypass(**self.api_kwargs())
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(
                method, "GET",
                f"uninstall leaked a mutating POST on malformed model: {method} {path}",
            )
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_GET}/"))
            self.assertFalse(path.startswith(f"{switch_dns_path.NAT_API_DEL}/"))
        self.assertEqual(mock_api_call.call_count, 1)

    def test_status_bypass_does_not_post_on_non_empty_list(self):
        """status is read-only but must still fail closed; no per-rule POSTs."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", self._bad_list_model())
        ) as mock_api_call:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.api_kwargs())
        for c in mock_api_call.call_args_list:
            method, path = c.args[0], c.args[1]
            self.assertEqual(method, "GET")
            self.assertEqual(path, switch_dns_path.NAT_API_MODEL)
        self.assertEqual(mock_api_call.call_count, 1)


# ---------------------------------------------------------------------------
# P2.1: install_bypass
# ---------------------------------------------------------------------------


class InstallBypassTests(_Base):
    def _fake_install(self, *, initial_model, added_uuids=None):
        """Build a fake_api_call that installs and applies. Returns (callable, calls).

        added_uuids: list of uuids to assign in order; defaults to uuid-0..uuid-5
        for the first six add_rule calls.
        """
        added_uuids = added_uuids or [f"uuid-{i}" for i in range(6)]
        model = copy.deepcopy(initial_model)
        calls: list[tuple] = []
        add_idx = [0]

        def fake(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", model
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                body = kwargs["body"]
                rule = body["rule"]
                uuid = added_uuids[add_idx[0]]
                add_idx[0] += 1
                # Reflect the added rule into the model (mirroring the add+get sequence).
                # The script then re-reads via NAT_API_MODEL, so model mutations stick.
                # The full key tuple for this rule:
                new_rule = _managed_rule(
                    rule["interface"], rule["protocol"],
                    enabled=rule["enabled"], uuid=uuid,
                )
                if isinstance(model["filter"]["snatrules"]["rule"], dict):
                    model["filter"]["snatrules"]["rule"][uuid] = new_rule
                else:
                    # List shape — append
                    model["filter"]["snatrules"]["rule"].append(new_rule)
                return "test", {"result": "saved", "uuid": uuid}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        return fake, calls

    def test_install_no_op_when_six_already_present(self):
        """If the model already has all six managed rules, install returns 'already installed'."""
        initial = _model_response(rules_by_uuid=_six_rules_by_uuid(enabled="0"))
        fake, calls = self._fake_install(initial_model=initial)
        # The fake populates the model with the same six rules on every read,
        # so the post-install read-back still has all six.
        # But the fake tries to add on the first read; pre-condition is the
        # six are present BEFORE the first read, so install is a no-op.
        # We need to ensure the fake's add branch never fires.
        def no_add_fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", initial
            self.fail(f"unexpected API call on no-op install: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=no_add_fake):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())
        self.assertIn("installed=0", summary)
        self.assertIn("state=disabled", summary)
        # No add_rule calls, no apply.
        method_paths = [(m, p) for m, p, *_ in calls]
        self.assertEqual(method_paths, [("GET", switch_dns_path.NAT_API_MODEL)])

    def test_install_with_empty_model_adds_six_and_applies(self):
        """Empty model → install adds all six, applies, returns installed=6."""
        initial = _model_response(rules_by_uuid={})
        fake, calls = self._fake_install(initial_model=initial)
        # Override the fake: empty initial model means all six are missing,
        # so the add loop will fire six times. The fake's add branch mutates
        # the model in-place, so the post-install read-back sees six rules.
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())
        self.assertIn("installed=6", summary)
        self.assertIn("total=6", summary)
        self.assertIn("state=disabled", summary)
        add_paths = [p for m, p, _ in calls if m == "POST" and p == switch_dns_path.NAT_API_ADD]
        self.assertEqual(len(add_paths), 6)
        apply_paths = [p for m, p, _ in calls if m == "POST" and p == switch_dns_path.NAT_API_APPLY]
        self.assertEqual(len(apply_paths), 1)
        # Two model reads: pre-install + post-apply.
        get_paths = [p for m, p, _ in calls if m == "GET" and p == switch_dns_path.NAT_API_MODEL]
        self.assertEqual(len(get_paths), 2)

    def test_install_tops_up_partial_set(self):
        """A model with only 3 managed rules → install adds the missing 3."""
        initial_rules = _six_rules_by_uuid(enabled="0")
        # Drop the last 3 (uuid-3, uuid-4, uuid-5) — partial set.
        for k in ("uuid-3", "uuid-4", "uuid-5"):
            initial_rules.pop(k)
        initial = _model_response(rules_by_uuid=initial_rules)
        # Build a fake that mutates the model on add and assigns new uuids.
        added_uuids = [f"uuid-new-{i}" for i in range(3)]
        fake, calls = self._fake_install(initial_model=initial, added_uuids=added_uuids)
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())
        self.assertIn("installed=3", summary)
        self.assertIn("total=6", summary)
        add_paths = [p for m, p, _ in calls if m == "POST" and p == switch_dns_path.NAT_API_ADD]
        self.assertEqual(len(add_paths), 3)

    def test_install_does_not_double_add_when_six_present(self):
        """Idempotency: pre-existing full six → zero add_rule calls."""
        initial = _model_response(rules_by_uuid=_six_rules_by_uuid(enabled="0"))
        # fake that fails if any add_rule happens
        def no_add_fake(method, path, **kwargs):
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                self.fail("add_rule should not be called when six rules are already present")
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", initial
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=no_add_fake):
            switch_dns_path.install_bypass(**self.api_kwargs())

    def test_install_post_apply_readback_empty_raises_actionable_error(self):
        """If the post-apply read returns zero rows, install raises the actionable error."""
        empty_model = _model_response(rules_by_uuid={})
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", empty_model
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                return "test", {"result": "saved", "uuid": f"uuid-{len([c for c in calls if c[1] == switch_dns_path.NAT_API_ADD])}"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.api_kwargs())
        self.assertIn("have 0/6", str(ctx.exception))
        self.assertIn("run --install to repair", str(ctx.exception))

    def test_install_refuses_duplicate_keys(self):
        """If the model has duplicate (interface, protocol) keys in the
        managed shape (e.g. two lan/udp rules both matching BYPASS_MANAGED_KEY),
        install bails with the ambiguous-set message because it cannot
        safely dedupe that — it doesn't know which UUID to keep.
        """
        rules = _six_rules_by_uuid(enabled="0")
        # Add a 7th rule that duplicates (lan, udp) → same key tuple as uuid-0.
        rules["uuid-dup"] = _managed_rule("lan", "udp", uuid="uuid-dup")
        initial = _model_response(rules_by_uuid=rules)
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", initial)
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.api_kwargs())
        # The keys-set-size mismatch is the ambiguous-set error.
        self.assertIn("ambiguous", str(ctx.exception))


# ---------------------------------------------------------------------------
# P2.2: enable_bypass / disable_bypass
# ---------------------------------------------------------------------------


class EnableDisableBypassTests(_Base):
    def _set_up_flip(self, *, initial_enabled="0", target_enabled="1"):
        """Build a fake_api_call that flips enabled and reflects in the model."""
        rules_by_uuid = _six_rules_by_uuid(enabled=initial_enabled)
        model = _model_response(rules_by_uuid=copy.deepcopy(rules_by_uuid))
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", model
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(model["filter"]["snatrules"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                body = kwargs["body"]
                rule = body["rule"]
                uuid = path.rsplit("/", 1)[1]
                model["filter"]["snatrules"]["rule"][uuid]["enabled"] = rule["enabled"]
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                return "test", {"result": "saved", "uuid": "uuid-new"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        return fake, calls, model

    def test_enable_flips_each_rule_to_enabled_1(self):
        fake, calls, _ = self._set_up_flip(initial_enabled="0", target_enabled="1")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.api_kwargs())
        self.assertIn("enabled=6", summary)
        # 6 set_rule calls + 1 apply.
        set_paths = [p for m, p, _ in calls if m == "POST" and p.startswith(f"{switch_dns_path.NAT_API_SET}/")]
        self.assertEqual(len(set_paths), 6)
        # Each set_rule body has enabled="1".
        for c in calls:
            if c[0] == "POST" and c[1].startswith(f"{switch_dns_path.NAT_API_SET}/"):
                self.assertEqual(c[2]["rule"]["enabled"], "1")

    def test_enable_idempotent_when_already_enabled(self):
        """Re-running --enable when already enabled makes zero set_rule calls."""
        fake, calls, _ = self._set_up_flip(initial_enabled="1", target_enabled="1")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.enable_bypass(**self.api_kwargs())
        self.assertIn("enabled=0", summary)
        self.assertIn("already_enabled=6", summary)
        set_paths = [p for m, p, _ in calls if m == "POST" and p.startswith(f"{switch_dns_path.NAT_API_SET}/")]
        self.assertEqual(len(set_paths), 0)

    def test_disable_flips_each_rule_to_enabled_0(self):
        fake, calls, _ = self._set_up_flip(initial_enabled="1", target_enabled="0")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.api_kwargs())
        self.assertIn("disabled=6", summary)
        for c in calls:
            if c[0] == "POST" and c[1].startswith(f"{switch_dns_path.NAT_API_SET}/"):
                self.assertEqual(c[2]["rule"]["enabled"], "0")

    def test_disable_idempotent_when_already_disabled(self):
        """Re-running --disable when already disabled makes zero set_rule calls."""
        fake, calls, _ = self._set_up_flip(initial_enabled="0", target_enabled="0")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.disable_bypass(**self.api_kwargs())
        self.assertIn("disabled=0", summary)
        self.assertIn("already_disabled=6", summary)
        set_paths = [p for m, p, _ in calls if m == "POST" and p.startswith(f"{switch_dns_path.NAT_API_SET}/")]
        self.assertEqual(len(set_paths), 0)

    def test_enable_rejects_model_endpoint_with_unexpected_shape(self):
        """If NAT_API_MODEL returns a non-dict, enable fails closed without flipping."""
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", ["not", "a", "dict"])
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())

    def test_enable_rejects_missing_filter_in_model(self):
        """A model response missing 'filter' fails closed without flipping."""
        with patch.object(switch_dns_path, "_api_call", return_value=("test", {})):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())

    def test_enable_drift_detection_on_get_rule(self):
        """If get_rule returns a rule whose attribute tuple has drifted, enable fails closed."""
        rules_by_uuid = _six_rules_by_uuid(enabled="0")
        model = _model_response(rules_by_uuid=copy.deepcopy(rules_by_uuid))
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", model
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                # Return a rule whose target has drifted away from BYPASS_MANAGED_KEY.
                drifted = copy.deepcopy(model["filter"]["snatrules"]["rule"][uuid])
                drifted["target"] = "8.8.8.8"
                return "test", {"rule": drifted}
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.enable_bypass(**self.api_kwargs())
        self.assertIn("no longer matches", str(ctx.exception))
        # No set_rule calls — drift is caught before mutation.
        self.assertFalse(
            any(p.startswith(f"{switch_dns_path.NAT_API_SET}/") for m, p in calls if m == "POST")
        )

    def test_disable_post_disable_readback_empty_raises(self):
        """If disable's post-apply read returns zero rows, it raises the actionable error."""
        rules_by_uuid = _six_rules_by_uuid(enabled="1")
        initial = _model_response(rules_by_uuid=rules_by_uuid)
        calls: list = []
        # A model that goes empty after apply
        empty = _model_response(rules_by_uuid={})

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                # Before apply: full set. After apply: empty.
                if any(c[1] == switch_dns_path.NAT_API_APPLY for c in calls):
                    return "test", empty
                return "test", initial
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                return "test", {"rule": copy.deepcopy(initial["filter"]["snatrules"]["rule"][uuid])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.disable_bypass(**self.api_kwargs())
        self.assertIn("have 0/6", str(ctx.exception))
        self.assertIn("run --install to repair", str(ctx.exception))


# ---------------------------------------------------------------------------
# P2.3: uninstall_bypass
# ---------------------------------------------------------------------------


class UninstallBypassTests(_Base):
    def test_uninstall_removes_each_managed_rule(self):
        rules_by_uuid = _six_rules_by_uuid(enabled="0")
        model = _model_response(rules_by_uuid=copy.deepcopy(rules_by_uuid))
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", model
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                uuid = path.rsplit("/", 1)[1]
                if isinstance(model["filter"]["snatrules"]["rule"], dict):
                    model["filter"]["snatrules"]["rule"].pop(uuid, None)
                return "test", {"result": "deleted"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.uninstall_bypass(**self.api_kwargs())
        self.assertEqual(summary, "uninstalled=6")
        del_paths = [p for m, p in calls if m == "POST" and p.startswith(f"{switch_dns_path.NAT_API_DEL}/")]
        self.assertEqual(len(del_paths), 6)
        # Final call is the post-uninstall read-back.
        self.assertEqual(calls[-1][0:2], ("GET", switch_dns_path.NAT_API_MODEL))

    def test_uninstall_idempotent_when_empty(self):
        """No managed rules → uninstall is a no-op returning 'uninstalled=0'."""
        empty = _model_response(rules_by_uuid={})
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", empty
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            summary = switch_dns_path.uninstall_bypass(**self.api_kwargs())
        self.assertEqual(summary, "uninstalled=0")
        # No del_rule or apply calls.
        self.assertFalse(
            any(m == "POST" for m, _ in calls)
        )

    def test_uninstall_uses_key_tuple_not_descr(self):
        """uninstall must use the key-tuple identifier, not descr.

        The model contains an unrelated manual rule (different target)
        with the same fields otherwise. uninstall must NOT touch it.
        """
        rules_by_uuid = _six_rules_by_uuid(enabled="0")
        rules_by_uuid["uuid-stray"] = _managed_rule(
            "lan", "udp", target="8.8.8.8", uuid="uuid-stray",
        )
        model = _model_response(rules_by_uuid=copy.deepcopy(rules_by_uuid))
        calls: list = []

        def fake(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_MODEL:
                return "test", model
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                uuid = path.rsplit("/", 1)[1]
                model["filter"]["snatrules"]["rule"].pop(uuid, None)
                return "test", {"result": "deleted"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")
        with patch.object(switch_dns_path, "_api_call", side_effect=fake):
            switch_dns_path.uninstall_bypass(**self.api_kwargs())
        # del_rule called six times — for the six managed uuids, NOT for uuid-stray.
        del_uuids = [p.rsplit("/", 1)[1] for m, p in calls if m == "POST" and p.startswith(f"{switch_dns_path.NAT_API_DEL}/")]
        self.assertEqual(len(del_uuids), 6)
        self.assertNotIn("uuid-stray", del_uuids)
        # Stray rule is still in the post-uninstall model (untouched).
        self.assertIn("uuid-stray", model["filter"]["snatrules"]["rule"])


# ---------------------------------------------------------------------------
# P2.4: status_bypass
# ---------------------------------------------------------------------------


class StatusBypassTests(_Base):
    def test_status_reports_disabled_when_six_all_off(self):
        rules = _six_rules_by_uuid(enabled="0")
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            st = switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertEqual(st["state"], "disabled")
        self.assertEqual(st["total_rules"], 6)
        self.assertEqual(st["enabled"], 0)
        self.assertEqual(st["disabled"], 6)
        self.assertTrue(st["installed"])

    def test_status_reports_enabled_when_six_all_on(self):
        rules = _six_rules_by_uuid(enabled="1")
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            st = switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertEqual(st["state"], "enabled")
        self.assertEqual(st["total_rules"], 6)
        self.assertEqual(st["enabled"], 6)
        self.assertEqual(st["disabled"], 0)

    def test_status_reports_mixed_when_some_on_some_off(self):
        rules = _six_rules_by_uuid(enabled="0")
        rules["uuid-0"]["enabled"] = "1"
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            st = switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertEqual(st["state"], "mixed")
        self.assertEqual(st["enabled"], 1)
        self.assertEqual(st["disabled"], 5)

    def test_status_reports_not_installed_when_empty(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid={}))
        ):
            st = switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertFalse(st["installed"])
        self.assertEqual(st["state"], "absent")
        self.assertEqual(st["total_rules"], 0)

    def test_status_fails_closed_on_partial_set(self):
        rules = _six_rules_by_uuid(enabled="0")
        for k in ("uuid-3", "uuid-4", "uuid-5"):
            rules.pop(k)
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertIn("have 3/6", str(ctx.exception))
        self.assertIn("run --install to repair", str(ctx.exception))


# ---------------------------------------------------------------------------
# P2.5: payload shape
# ---------------------------------------------------------------------------


class BuildPayloadTests(_Base):
    def test_payload_omits_descr(self):
        """descr is silently discarded by add_rule and is not in the model — do not send it."""
        payload = switch_dns_path._build_bypass_rule_payload("lan", "udp")
        self.assertNotIn("descr", payload)
        self.assertNotIn("description", payload)

    def test_payload_omits_natreflection(self):
        """natreflection is silently discarded by add_rule and is not in the model — do not send it."""
        payload = switch_dns_path._build_bypass_rule_payload("lan", "udp")
        self.assertNotIn("natreflection", payload)

    def test_payload_omits_associated_rule(self):
        """associated-rule is a filter-rule linkage field the source_nat model does not use."""
        payload = switch_dns_path._build_bypass_rule_payload("lan", "udp")
        self.assertNotIn("associated-rule", payload)

    def test_payload_includes_documented_add_fields(self):
        """The payload keeps the documented add fields: interface, ipprotocol, protocol,
        source, destination, destination_port, target, target_port, nat_port, enabled."""
        payload = switch_dns_path._build_bypass_rule_payload("lan", "udp")
        for key in (
            "interface", "ipprotocol", "protocol", "source", "destination",
            "destination_port", "target", "target_port", "nat_port", "enabled",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["interface"], "lan")
        self.assertEqual(payload["protocol"], "udp")
        self.assertEqual(payload["ipprotocol"], "inet")
        self.assertEqual(payload["source"], "any")
        self.assertEqual(payload["destination"], "any")
        self.assertEqual(payload["destination_port"], "53")
        self.assertEqual(payload["target"], switch_dns_path.BYPASS_TARGET_IP)
        self.assertEqual(payload["target_port"], "53")
        self.assertEqual(payload["nat_port"], "")
        self.assertEqual(payload["enabled"], "0")  # install disabled; --enable flips

    def test_payload_target_and_target_port_unchanged_across_calls(self):
        for (iface, proto) in (("lan", "udp"), ("opt1", "tcp"), ("opt2", "udp")):
            payload = switch_dns_path._build_bypass_rule_payload(iface, proto)
            self.assertEqual(payload["target"], switch_dns_path.BYPASS_TARGET_IP)
            self.assertEqual(payload["target_port"], "53")


# ---------------------------------------------------------------------------
# P2.6: ruleset completeness (preserved from earlier PRs)
# ---------------------------------------------------------------------------


class RulesetCompletenessTests(_Base):
    def test_full_six_passes(self):
        rules = _six_rules_by_uuid(enabled="0")
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
        ):
            switch_dns_path.status_bypass(**self.api_kwargs())  # should not raise

    def test_empty_passes(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid={}))
        ):
            switch_dns_path.status_bypass(**self.api_kwargs())  # should not raise

    def test_partial_set_raises_actionable_error(self):
        for count in (1, 2, 3, 4, 5):
            rules = _six_rules_by_uuid(enabled="0")
            for k in list(rules.keys())[count:]:
                rules.pop(k)
            with self.subTest(count=count), patch.object(
                switch_dns_path, "_api_call", return_value=("test", _model_response(rules_by_uuid=rules))
            ):
                with self.assertRaises(switch_dns_path.BypassError) as ctx:
                    switch_dns_path.status_bypass(**self.api_kwargs())
            self.assertIn(f"have {count}/6", str(ctx.exception))


# ---------------------------------------------------------------------------
# P2.7: apply_nat
# ---------------------------------------------------------------------------


class ApplyNatTests(_Base):
    def test_apply_nat_accepts_trimmed_ok_status(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", {"status": "OK\n\n"})
        ) as mock_api_call:
            switch_dns_path._apply_nat(**self.api_kwargs())
        mock_api_call.assert_called_once()

    def test_apply_nat_rejects_non_trimmable_status(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", {"status": "ERR"})
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._apply_nat(**self.api_kwargs())
        self.assertIn("status='OK'", str(ctx.exception))

    def test_apply_nat_rejects_non_dict_response(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", ["not", "a", "dict"])
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._apply_nat(**self.api_kwargs())

    def test_apply_nat_rejects_missing_status_key(self):
        with patch.object(
            switch_dns_path, "_api_call", return_value=("test", {"result": "saved"})
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._apply_nat(**self.api_kwargs())
        self.assertIn("status='OK'", str(ctx.exception))


# ---------------------------------------------------------------------------
# P2.8: mutating POST transport split (preserved)
# ---------------------------------------------------------------------------


class MutatingPostTransportTests(_Base):
    def test_post_dns_error_falls_through_to_literal_ip(self):
        """A pre-send gaierror on the hostname primary → literal-IP POST fallback."""
        import socket
        with patch.object(
            switch_dns_path.urllib.request, "urlopen",
            side_effect=socket.gaierror(-2, "Name or service not known"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )
        self.assertEqual(urlopen.call_count, 2)
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        self.assertTrue(urls[0].startswith(PRIMARY))
        self.assertTrue(urls[1].startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK))
        self.assertFalse(any(u.startswith(FALLBACK) for u in urls))

    def test_post_non_dns_urlopen_error_does_not_fall_through(self):
        """A non-DNS urlopen error on POST must not contact the literal-IP fallback."""
        import urllib.error
        for raised, label in (
            (urllib.error.URLError("primary transport failed"), "URLError"),
            (ConnectionResetError("simulated mid-connect reset"), "ConnectionError"),
            (BrokenPipeError("simulated EPIPE on body write"), "OSError/EPIPE"),
        ):
            with self.subTest(failure_shape=label):
                with patch.object(
                    switch_dns_path.urllib.request, "urlopen", side_effect=raised
                ) as urlopen:
                    with self.assertRaises(switch_dns_path.BypassError) as ctx:
                        switch_dns_path._api_call(
                            "POST", switch_dns_path.NAT_API_ADD,
                            body={"rule": {"interface": "lan"}},
                            **self.api_kwargs(),
                        )
                self.assertEqual(urlopen.call_count, 1)
                self.assertTrue(getattr(ctx.exception, "post_send", False))

    def test_get_falls_through_to_operator_http_fallback(self):
        """A non-credential GET failure falls through to the operator-configured HTTP fallback."""
        import urllib.error
        with patch.object(
            switch_dns_path.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("primary transport failed"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "GET", switch_dns_path.NAT_API_MODEL,
                    **self.api_kwargs(),
                )
        self.assertEqual(urlopen.call_count, 2)
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        self.assertTrue(urls[0].startswith(PRIMARY))
        self.assertTrue(urls[1].startswith(FALLBACK))
        self.assertFalse(
            any(u.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK) for u in urls)
        )

    def test_post_send_read_failure_does_not_fall_through(self):
        """A POST whose primary urlopen succeeds but r.read() then raises must not fall through."""

        class _ReadFailResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                raise ConnectionResetError("simulated mid-response reset")

        with patch.object(
            switch_dns_path.urllib.request, "urlopen", return_value=_ReadFailResponse()
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(getattr(ctx.exception, "post_send", False))
        self.assertIn("AFTER request was sent", str(ctx.exception))

    def test_http_error_401_on_post_raises_credential_error(self):
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://example.invalid/api/firewall/source_nat/addRule",
            code=401, msg="Unauthorized", hdrs={}, fp=io.BytesIO(b'{"error":"x"}'),
        )
        with patch.object(
            switch_dns_path.urllib.request, "urlopen", side_effect=http_err
        ) as urlopen:
            with self.assertRaises(switch_dns_path.CredentialError):
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )
        self.assertEqual(urlopen.call_count, 1)

    def test_http_error_500_on_get_falls_through(self):
        import urllib.error

        class _OkResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                # Return a valid empty model shape so the second call succeeds.
                import json as _json
                return _json.dumps(_model_response(rules_by_uuid={})).encode()

        primary_err = urllib.error.HTTPError(
            url="https://example.invalid/api/x", code=500,
            msg="Internal Server Error", hdrs={}, fp=io.BytesIO(b""),
        )
        with patch.object(
            switch_dns_path.urllib.request, "urlopen", side_effect=[primary_err, _OkResponse()]
        ) as urlopen:
            switch_dns_path._api_call(
                "GET", switch_dns_path.NAT_API_MODEL, **self.api_kwargs(),
            )
        self.assertEqual(urlopen.call_count, 2)

    def test_bad_status_line_on_post_does_not_fall_through(self):
        import http.client
        with patch.object(
            switch_dns_path.urllib.request, "urlopen",
            side_effect=http.client.BadStatusLine("simulated server reset"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST", switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(getattr(ctx.exception, "post_send", False))
        self.assertNotIsInstance(ctx.exception, switch_dns_path.CredentialError)


# ---------------------------------------------------------------------------
# P2.9: BypassError has post_send flag (preserved)
# ---------------------------------------------------------------------------


class BypassErrorTests(unittest.TestCase):
    def test_post_send_flag_default_false(self):
        e = switch_dns_path.BypassError("x")
        self.assertFalse(e.post_send)

    def test_post_send_flag_opt_in(self):
        e = switch_dns_path.BypassError("x", post_send=True)
        self.assertTrue(e.post_send)


# ---------------------------------------------------------------------------
# P2.10: _managed_key_of multi-select and port-tolerance regressions
# ---------------------------------------------------------------------------


class ManagedKeyOfRegressionTests(unittest.TestCase):
    """Regression tests for two Luna xhigh review findings:

    1. ``_selected_option`` must require EXACTLY ONE selected option. A
       rule with two ``selected:1`` entries must be treated as
       not-managed (None), not silently classified as the first one.
    2. ``_managed_key_of`` must tolerate non-numeric ``target_port`` /
       ``destination_port`` values (e.g. ``"http"``) and return None
       instead of letting ``ValueError`` propagate out of status /
       install / enable / disable / uninstall.

    The second finding is verified by asserting the call does not
    raise — it would have raised ``ValueError`` before the fix.
    """

    @staticmethod
    def _build_rule(**overrides):
        """Build a fully-managed rule shape; pass overrides to mutate fields.

        Defaults are the BYPASS_MANAGED_KEY values. Override ``interface``
        or ``target_port`` / ``destination_port`` to test failure modes.
        """
        rule = _managed_rule("lan", "udp", uuid="uuid-regression")
        rule.update(overrides)
        return rule

    def test_managed_key_of_accepts_single_selected_option(self):
        """Sanity: a rule with exactly one selected option still classifies
        as managed (positive control for the regressions below)."""
        rule = self._build_rule()
        # _managed_rule's helper puts exactly one ``selected:1`` entry per
        # multi-select field. Confirm via the helper.
        for ms in (rule["interface"], rule["protocol"], rule["ipprotocol"]):
            self.assertEqual(sum(1 for v in ms.values() if v.get("selected") == 1), 1)
        key = switch_dns_path._managed_key_of(rule)
        self.assertEqual(key, _managed_key("lan", "udp"))

    def test_managed_key_of_rejects_multi_select_with_two_selected(self):
        """A rule with two ``selected:1`` entries in the interface multi-select
        must be classified as not-managed (None)."""
        rule = self._build_rule()
        # Force TWO interfaces to be marked selected.
        rule["interface"]["lan"]["selected"] = 1
        rule["interface"]["opt1"]["selected"] = 1
        # Sanity: exactly two are selected now.
        self.assertEqual(
            sum(1 for v in rule["interface"].values() if v.get("selected") == 1),
            2,
        )
        self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_managed_key_of_rejects_multi_select_with_zero_selected(self):
        """A rule with ZERO ``selected:1`` entries in the interface multi-select
        must be classified as not-managed (None)."""
        rule = self._build_rule()
        # Clear ALL selected entries on the interface multi-select.
        for entry in rule["interface"].values():
            entry["selected"] = 0
        # Sanity: zero are selected now.
        self.assertEqual(
            sum(1 for v in rule["interface"].values() if v.get("selected") == 1),
            0,
        )
        self.assertIsNone(switch_dns_path._managed_key_of(rule))

    def test_managed_key_of_rejects_nonnumeric_target_port(self):
        """A non-numeric ``target_port`` (e.g. ``"http"``) must classify the rule
        as not-managed (None). Previously this raised ValueError out of int()
        and would propagate out of status/install/enable/disable/uninstall."""
        rule = self._build_rule(target_port="http")
        # Must not raise; must return None.
        try:
            result = switch_dns_path._managed_key_of(rule)
        except (ValueError, TypeError) as e:
            self.fail(f"_managed_key_of must not raise on nonnumeric port, got {type(e).__name__}: {e}")
        self.assertIsNone(result)

    def test_managed_key_of_rejects_nonnumeric_destination_port(self):
        """A non-numeric ``destination_port`` (e.g. ``"http"``) must classify
        the rule as not-managed (None). Previously this raised ValueError
        out of int()."""
        rule = self._build_rule(destination_port="http")
        try:
            result = switch_dns_path._managed_key_of(rule)
        except (ValueError, TypeError) as e:
            self.fail(f"_managed_key_of must not raise on nonnumeric port, got {type(e).__name__}: {e}")
        self.assertIsNone(result)

    def test_selected_option_helper_is_shared(self):
        """Both _managed_key_of and the regression tests rely on the same
        _selected_option helper. Sanity-check that helper directly so a
        future rename of the private symbol is caught here instead of
        silently breaking ownership detection."""
        # Two selected → None.
        ms_two = {"lan": {"value": "LAN", "selected": 1}, "opt1": {"value": "IOT", "selected": 1}}
        self.assertIsNone(switch_dns_path._selected_option(ms_two))
        # Zero selected → None.
        ms_zero = {"lan": {"value": "LAN", "selected": 0}, "opt1": {"value": "IOT", "selected": 0}}
        self.assertIsNone(switch_dns_path._selected_option(ms_zero))
        # One selected → that key as a str.
        ms_one = {"lan": {"value": "LAN", "selected": 1}, "opt1": {"value": "IOT", "selected": 0}}
        self.assertEqual(switch_dns_path._selected_option(ms_one), "lan")
        # Non-dict → None.
        self.assertIsNone(switch_dns_path._selected_option("not a dict"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
