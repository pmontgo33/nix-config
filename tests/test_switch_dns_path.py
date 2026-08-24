#!/usr/bin/env python3
import copy
import io
import unittest
from unittest.mock import patch

from scripts.dns_migration import switch_dns_path


PRIMARY = "https://example.invalid"
FALLBACK = "http://192.0.2.1"
KEY = "test-key"
SECRET = "test-secret"


def observed_search_response(rows):
    """The real OPNsense source_nat search envelope, with sanitized rows."""
    return {
        "current": 1,
        "rowCount": 1000,
        "rows": rows,
        "total": len(rows),
    }


def observed_row(index, *, interface=None, protocol=None, enabled="0"):
    interface = interface or ("lan", "opt1", "opt2")[index // 2]
    protocol = protocol or ("udp", "tcp")[index % 2]
    return {
        "uuid": f"uuid-{index}",
        "interface": interface,
        "protocol": protocol,
        "description": switch_dns_path.BYPASS_RULE_DESCR,
        "ipprotocol": "inet",
        "source": "any",
        "destination": "any",
        "destination_port": "53",
        "target": switch_dns_path.BYPASS_TARGET_IP,
        "target_port": "53",
        "natreflection": "disable",
        "enabled": enabled,
    }


def managed_rows(*, enabled="0"):
    return [observed_row(i, enabled=enabled) for i in range(6)]


def unrelated_wan_rows():
    return [
        {
            "uuid": "wan-uuid-1",
            "interface": "wan",
            "protocol": "udp",
            "description": "unrelated WAN DNS rule",
            "enabled": "1",
        },
        {
            "uuid": "wan-uuid-2",
            "interface": "wan",
            "protocol": "tcp",
            "description": "unrelated WAN DNS rule",
            "enabled": "0",
        },
    ]


class SwitchDnsPathApiShapeTests(unittest.TestCase):
    def api_kwargs(self):
        return {
            "primary": PRIMARY,
            "fallback": FALLBACK,
            "key": KEY,
            "secret": SECRET,
        }

    def test_search_parses_live_description_and_preserves_unrelated_wan_rows(self):
        rows = managed_rows() + unrelated_wan_rows()
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response(rows)),
        ):
            matching = switch_dns_path._search_bypass_rules(**self.api_kwargs())

        self.assertEqual(matching, rows[:6])

    def test_status_reports_disabled_enabled_and_mixed_states(self):
        for enabled, expected_state, expected_enabled, expected_disabled in (
            ("0", "disabled", 0, 6),
            ("1", "enabled", 6, 0),
        ):
            rows = managed_rows(enabled=enabled) + unrelated_wan_rows()
            with self.subTest(expected_state=expected_state), patch.object(
                switch_dns_path,
                "_api_call",
                return_value=("test", observed_search_response(rows)),
            ):
                status = switch_dns_path.status_bypass(**self.api_kwargs())

            self.assertEqual(status["state"], expected_state)
            self.assertEqual(status["total_rules"], 6)
            self.assertEqual(status["enabled"], expected_enabled)
            self.assertEqual(status["disabled"], expected_disabled)

        mixed = managed_rows()
        mixed[0]["enabled"] = "1"
        rows = mixed + unrelated_wan_rows()
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response(rows)),
        ):
            status = switch_dns_path.status_bypass(**self.api_kwargs())

        self.assertEqual(status["state"], "mixed")
        self.assertEqual(status["enabled"], 1)
        self.assertEqual(status["disabled"], 5)

    def test_install_does_not_duplicate_rules_from_live_search_shape(self):
        rows = managed_rows() + unrelated_wan_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            return "test", observed_search_response(rows)

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())

        self.assertIn("installed=0", summary)
        self.assertIn("state=disabled", summary)
        self.assertEqual(
            [(method, path) for method, path, _ in calls],
            [("GET", switch_dns_path.NAT_API_SEARCH)],
        )

    def test_install_reports_actual_enabled_state_instead_of_disabled(self):
        for enabled, expected_state in (("1", "enabled"),):
            rows = managed_rows(enabled=enabled) + unrelated_wan_rows()
            with patch.object(
                switch_dns_path,
                "_api_call",
                return_value=("test", observed_search_response(rows)),
            ):
                summary = switch_dns_path.install_bypass(**self.api_kwargs())

            self.assertIn(f"state={expected_state}", summary)
            self.assertNotIn("state=disabled", summary)

        mixed = managed_rows()
        mixed[0]["enabled"] = "1"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response(mixed + unrelated_wan_rows())),
        ):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())

        self.assertIn("state=mixed", summary)
        self.assertNotIn("state=disabled", summary)

    def test_install_uses_rule_envelope_and_reads_back_disabled_rules(self):
        calls = []
        installed_rows = []
        unrelated = unrelated_wan_rows()

        def fake_api_call(method, path, **kwargs):
            body = kwargs.get("body")
            calls.append((method, path, copy.deepcopy(body)))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(installed_rows + unrelated)
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                if not isinstance(body, dict):
                    self.fail("add_rule body is not an object")
                self.assertEqual(set(body), {"rule"})
                rule = body["rule"]
                self.assertEqual(rule["descr"], switch_dns_path.BYPASS_RULE_DESCR)
                self.assertNotIn("description", rule)
                installed_rows.append(
                    {
                        "uuid": f"uuid-{len(installed_rows)}",
                        "interface": rule["interface"],
                        "protocol": rule["protocol"],
                        "description": rule["descr"],
                        "ipprotocol": rule["ipprotocol"],
                        "source": rule["source"],
                        "destination": rule["destination"],
                        "destination_port": rule["destination_port"],
                        "target": rule["target"],
                        "target_port": rule["target_port"],
                        "natreflection": rule["natreflection"],
                        "enabled": rule["enabled"],
                    }
                )
                return "test", {"result": "saved", "uuid": installed_rows[-1]["uuid"]}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            summary = switch_dns_path.install_bypass(**self.api_kwargs())

        self.assertEqual(summary, "installed=6, total=6, state=disabled (use --enable to activate)")
        self.assertEqual(
            sum(path == switch_dns_path.NAT_API_ADD for _, path, _ in calls),
            6,
        )
        self.assertEqual(
            sum(path == switch_dns_path.NAT_API_SEARCH for _, path, _ in calls),
            2,
        )

    def test_malformed_tagged_rows_fail_closed(self):
        for missing in ("interface", "protocol", "uuid"):
            row = observed_row(0)
            del row[missing]
            with self.subTest(missing=missing), patch.object(
                switch_dns_path,
                "_api_call",
                return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
            ):
                with self.assertRaises(switch_dns_path.BypassError):
                    switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_malformed_enabled_value_fails_closed(self):
        row = observed_row(0, enabled="unexpected")
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._search_bypass_rules(**self.api_kwargs())

    def test_add_response_without_saved_result_fails_before_apply_or_next_add(self):
        calls = []
        responses = iter(
            [
                ("test", observed_search_response([])),
                ("test", {"result": "failed"}),
            ]
        )

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            return next(responses)

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.install_bypass(**self.api_kwargs())

        self.assertEqual(calls, [("GET", switch_dns_path.NAT_API_SEARCH), ("POST", switch_dns_path.NAT_API_ADD)])

    def test_enable_and_disable_use_get_rule_unwrap_set_rule_envelopes(self):
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            body = kwargs.get("body")
            calls.append((method, path, copy.deepcopy(body)))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows + unrelated_wan_rows())
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                row = next(row for row in rows if row["uuid"] == uuid)
                return "test", {"rule": copy.deepcopy(row)}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                if not isinstance(body, dict):
                    self.fail("set_rule body is not an object")
                self.assertEqual(set(body), {"rule"})
                rule = body["rule"]
                row = next(row for row in rows if row["uuid"] == path.rsplit("/", 1)[1])
                row.update(rule)
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            self.assertIn("enabled=6", switch_dns_path.enable_bypass(**self.api_kwargs()))
            self.assertIn("disabled=6", switch_dns_path.disable_bypass(**self.api_kwargs()))

        get_calls = [call for call in calls if call[1].startswith(f"{switch_dns_path.NAT_API_GET}/")]
        set_calls = [call for call in calls if call[1].startswith(f"{switch_dns_path.NAT_API_SET}/")]
        self.assertEqual(len(get_calls), 12)
        self.assertEqual(len(set_calls), 12)
        self.assertTrue(all(call[2] and set(call[2]) == {"rule"} for call in set_calls))

    def test_set_failure_fails_before_apply_and_counter(self):
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows)
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                return "test", {"rule": copy.deepcopy(rows[0])}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                return "test", {"result": "failed"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())

        self.assertFalse(any(path == switch_dns_path.NAT_API_APPLY for _, path in calls))
        self.assertEqual(sum(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path in calls), 1)

    def test_delete_success_is_validated_and_read_back(self):
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows + unrelated_wan_rows())
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                rows[:] = [row for row in rows if row["uuid"] != path.rsplit("/", 1)[1]]
                return "test", {"result": "deleted"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            summary = switch_dns_path.uninstall_bypass(**self.api_kwargs())

        self.assertEqual(summary, "uninstalled=6")
        self.assertEqual(sum(path.startswith(f"{switch_dns_path.NAT_API_DEL}/") for _, path in calls), 6)
        self.assertEqual(calls[-1], ("GET", switch_dns_path.NAT_API_SEARCH))

    def test_delete_failure_fails_before_apply_and_counter(self):
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows)
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                return "test", {"result": "failed"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.uninstall_bypass(**self.api_kwargs())

        self.assertFalse(any(path == switch_dns_path.NAT_API_APPLY for _, path in calls))
        self.assertEqual(sum(path.startswith(f"{switch_dns_path.NAT_API_DEL}/") for _, path in calls), 1)

    def test_status_rejects_wrong_redirect_target_in_authoritative_search_row(self):
        rows = managed_rows()
        rows[0]["target"] = "192.0.2.55"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response(rows)),
        ):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.status_bypass(**self.api_kwargs())

    def test_enable_rejects_mismatched_get_rule_before_set(self):
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path, copy.deepcopy(kwargs.get("body"))))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows)
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                # Return a valid rule envelope for the wrong managed rule.
                return "test", {"rule": copy.deepcopy(rows[1])}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())

        self.assertTrue(any(path.startswith(f"{switch_dns_path.NAT_API_GET}/") for _, path, _ in calls))
        self.assertFalse(any(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path, _ in calls))
        self.assertFalse(any(path == switch_dns_path.NAT_API_APPLY for _, path, _ in calls))

    def test_get_without_rule_envelope_fails_closed(self):
        rows = managed_rows()

        def fake_api_call(method, path, **kwargs):
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows)
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                return "test", {"interface": "lan"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path.enable_bypass(**self.api_kwargs())

    def test_uuid_less_get_rule_uses_search_uuid_for_set_rule_path(self):
        rows = managed_rows()
        set_paths = []

        def fake_api_call(method, path, **kwargs):
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                return "test", observed_search_response(rows)
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                requested_uuid = path.rsplit("/", 1)[1]
                row = next(row for row in rows if row["uuid"] == requested_uuid)
                uuid_less_rule = copy.deepcopy(row)
                del uuid_less_rule["uuid"]
                return "test", {"rule": uuid_less_rule}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                set_paths.append(path)
                requested_uuid = path.rsplit("/", 1)[1]
                set_body = kwargs["body"]
                self.assertIn(requested_uuid, {row["uuid"] for row in rows})
                self.assertNotIn("uuid", set_body["rule"])
                row = next(row for row in rows if row["uuid"] == requested_uuid)
                row.update(set_body["rule"])
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            summary = switch_dns_path.enable_bypass(**self.api_kwargs())

        self.assertIn("enabled=6", summary)
        self.assertEqual(
            set_paths,
            [f"{switch_dns_path.NAT_API_SET}/{row['uuid']}" for row in rows],
        )

    def test_mutating_post_falls_through_to_literal_ip_https_and_skips_http_fallback(self):
        """POSTs whose hostname primary fails with a DNS-resolution error
        (socket.gaierror) fall through to the hard-coded literal-IP HTTPS
        fallback. They NEVER try the operator-configured HTTP fallback
        (which would double-write against a misconfigured firewall), they
        NEVER retry the hostname primary after a successful transport, and
        they do NOT fall through on non-DNS urlopen errors (see
        ``test_post_non_dns_urlopen_error_does_not_fall_through_to_literal_ip``
        for the regressed-on-purpose check).
        """
        import socket

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=socket.gaierror(-2, "Name or service not known"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Two URL opens total: hostname primary + literal-IP HTTPS fallback.
        # No retry of the primary, no operator-configured HTTP fallback.
        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(
            called_urls[1].startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
        )
        # The literal-IP HTTPS fallback URL must NOT be the operator-configured
        # HTTP fallback (different scheme and different host).
        self.assertFalse(any(url.startswith(FALLBACK) for url in called_urls))

    def test_mutating_get_falls_through_to_operator_http_fallback(self):
        """GETs still fall through to the operator-configured HTTP fallback
        after the HTTPS primary fails — preserves existing read transport
        behavior so a hostname resolver outage doesn't block status reads.
        """
        import urllib.error

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("primary transport failed"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "GET",
                    switch_dns_path.NAT_API_SEARCH,
                    **self.api_kwargs(),
                )

        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(called_urls[1].startswith(FALLBACK))
        # GET fallback must be the operator-configured HTTP fallback, NOT the
        # hard-coded literal-IP HTTPS POST fallback.
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            )
        )

    def test_mutating_post_does_not_retry_primary_after_successful_transport(self):
        """A successful transport to the hostname primary that returns a
        4xx/5xx response must NOT fall through to the literal-IP fallback
        — a reachable-but-rejecting primary is an ACL/result failure, not
        a transport problem, and falling through would silently bypass the
        error against a different host.
        """
        import urllib.error

        primary_response = urllib.error.HTTPError(
            PRIMARY + "/api/x", 500, "Internal Server Error", {}, None
        )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=primary_response,
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        self.assertEqual(urlopen.call_count, 1)

    # ---- P1.2: status/disable require complete six-rule set --------------

    def _partial_rows(self, count):
        """Return the first `count` rows of a valid six-rule set."""
        all_rows = managed_rows()
        return all_rows[:count]

    def test_status_fails_closed_on_partial_ruleset_with_actionable_message(self):
        """Status must NOT report the bypass as installed on fewer than
        six rows. It must raise BypassError with the actionable
        ``ruleset incomplete: have N/6, run --install to repair`` message.
        """
        for count in (1, 2, 3, 4, 5):
            with self.subTest(count=count), patch.object(
                switch_dns_path,
                "_api_call",
                return_value=(
                    "test",
                    observed_search_response(self._partial_rows(count) + unrelated_wan_rows()),
                ),
            ):
                with self.assertRaises(switch_dns_path.BypassError) as ctx:
                    switch_dns_path.status_bypass(**self.api_kwargs())
            msg = str(ctx.exception)
            self.assertIn(f"have {count}/6", msg)
            self.assertIn("run --install to repair", msg)

    def test_status_succeeds_on_empty_ruleset(self):
        """Empty ruleset is the natural "not installed" state, NOT a
        partial set — must not raise."""
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([])),
        ):
            status = switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertEqual(status["installed"], False)
        self.assertEqual(status["state"], "absent")
        self.assertEqual(status["total_rules"], 0)

    def test_disable_fails_closed_on_partial_ruleset(self):
        """Disable must NOT treat a partial ruleset as a no-op. It must
        raise BypassError before any set_rule calls are issued, so the
        firewall table is not silently half-toggled.
        """
        for count in (1, 2, 3, 4, 5):
            rows = self._partial_rows(count)
            calls = []

            def fake_api_call(method, path, **kwargs):
                calls.append((method, path))
                if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                    return "test", observed_search_response(rows)
                self.fail(
                    f"unexpected API call on partial disable (count={count}): "
                    f"{method} {path}"
                )

            with self.subTest(count=count), patch.object(
                switch_dns_path, "_api_call", side_effect=fake_api_call
            ):
                with self.assertRaises(switch_dns_path.BypassError) as ctx:
                    switch_dns_path.disable_bypass(**self.api_kwargs())
            msg = str(ctx.exception)
            self.assertIn(f"have {count}/6", msg)
            self.assertIn("run --install to repair", msg)
            # No set_rule calls — disable must fail closed before mutating.
            self.assertFalse(
                any(path.startswith(f"{switch_dns_path.NAT_API_SET}/") for _, path in calls)
            )

    def test_install_repairs_partial_ruleset_then_disable_succeeds(self):
        """Round-trip: a partial ruleset fails disable, install repairs
        the missing rows, then disable succeeds."""
        for count in (1, 2, 3, 4, 5):
            with self.subTest(count=count):
                partial = self._partial_rows(count)
                installed_rows = [copy.deepcopy(row) for row in partial]
                calls = []

                def fake_api_call(method, path, **kwargs):
                    calls.append((method, path))
                    body = kwargs.get("body")
                    if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                        return "test", observed_search_response(
                            installed_rows + unrelated_wan_rows()
                        )
                    if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                        rule = body["rule"]
                        installed_rows.append(
                            {
                                "uuid": f"uuid-{len(installed_rows)}",
                                "interface": rule["interface"],
                                "protocol": rule["protocol"],
                                "description": rule["descr"],
                                "ipprotocol": rule["ipprotocol"],
                                "source": rule["source"],
                                "destination": rule["destination"],
                                "destination_port": rule["destination_port"],
                                "target": rule["target"],
                                "target_port": rule["target_port"],
                                "natreflection": rule["natreflection"],
                                "enabled": rule["enabled"],
                            }
                        )
                        return "test", {
                            "result": "saved",
                            "uuid": installed_rows[-1]["uuid"],
                        }
                    if method == "GET" and path.startswith(
                        f"{switch_dns_path.NAT_API_GET}/"
                    ):
                        uuid = path.rsplit("/", 1)[1]
                        row = next(
                            row for row in installed_rows if row["uuid"] == uuid
                        )
                        return "test", {"rule": copy.deepcopy(row)}
                    if method == "POST" and path.startswith(
                        f"{switch_dns_path.NAT_API_SET}/"
                    ):
                        rule = body["rule"]
                        uuid = path.rsplit("/", 1)[1]
                        row = next(
                            row for row in installed_rows if row["uuid"] == uuid
                        )
                        row.update(rule)
                        return "test", {"result": "saved"}
                    if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                        return "test", {"status": "OK"}
                    self.fail(f"unexpected API call: {method} {path}")

                with patch.object(
                    switch_dns_path, "_api_call", side_effect=fake_api_call
                ):
                    with self.assertRaises(switch_dns_path.BypassError):
                        switch_dns_path.disable_bypass(**self.api_kwargs())
                    install_summary = switch_dns_path.install_bypass(
                        **self.api_kwargs()
                    )
                    # install should have added exactly 6 - count rows.
                    self.assertIn(
                        f"installed={6 - count}, total=6", install_summary
                    )
                    disable_summary = switch_dns_path.disable_bypass(
                        **self.api_kwargs()
                    )
                    self.assertIn("disabled=6", disable_summary)

    # ---- P1.3: source / destination / natreflection match-field validation

    def test_search_row_with_altered_source_fails_closed(self):
        """A tagged row whose source field has drifted away from the
        script-managed value (``any`` or the configured LAN-subnet alias)
        must not be treated as a managed rule.
        """
        row = observed_row(0)
        row["source"] = "192.168.86.0/24"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertIn("source", str(ctx.exception))

    def test_search_row_with_altered_destination_fails_closed(self):
        """A tagged row whose destination field has drifted away from
        ``any`` must not be treated as a managed rule."""
        row = observed_row(0)
        row["destination"] = "wan_subnet"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertIn("destination", str(ctx.exception))

    def test_search_row_with_altered_natreflection_fails_closed(self):
        """A tagged row whose natreflection field has drifted away from
        ``disable`` (e.g. someone enabled hairpin) must not be treated as
        a managed rule.
        """
        row = observed_row(0)
        row["natreflection"] = "enable"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertIn("natreflection", str(ctx.exception))

    def test_search_row_with_lan_subnet_alias_source_passes(self):
        """A tagged row whose source field is the configured LAN-subnet
        alias name (not "any") is still a valid managed rule — operators
        may tighten the source match to an alias later without breaking
        the script's identity check."""
        row = observed_row(0)
        row["source"] = switch_dns_path.LAN_SUBNET_ALIAS
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            matching = switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertEqual(matching, [row])

    def test_search_row_with_missing_source_fails_closed(self):
        """A tagged row missing the source field (e.g. older OPNsense
        schema) must not be treated as a managed rule.
        """
        row = observed_row(0)
        del row["source"]
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response([row] + unrelated_wan_rows())),
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._search_bypass_rules(**self.api_kwargs())
        self.assertIn("source", str(ctx.exception))

    def test_status_rejects_altered_source_in_authoritative_search_row(self):
        """status_bypass must propagate the source-validation failure
        the same way it propagates the target-validation failure."""
        rows = managed_rows()
        rows[0]["source"] = "192.168.86.0/24"
        with patch.object(
            switch_dns_path,
            "_api_call",
            return_value=("test", observed_search_response(rows)),
        ):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.status_bypass(**self.api_kwargs())
        self.assertIn("source", str(ctx.exception))

    # ---- P1.4: literal-IP POST fallback restricted to pre-send transport failures

    def test_post_send_read_failure_does_not_fall_through_to_literal_ip(self):
        """A POST whose primary urlopen succeeds but r.read() then raises
        (e.g. connection reset mid-response) MUST NOT fall through to the
        hard-coded literal-IP HTTPS fallback. The request body was already
        flushed to OPNsense, so retrying the same write against a different
        transport risks double-applying the change. The error must surface
        as BypassError, the literal-IP fallback must NOT be contacted.
        """

        # The "response" object: context-manager-compatible, .read() raises.
        class _ReadFailResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                raise ConnectionResetError(
                    "simulated mid-response reset AFTER request sent"
                )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            return_value=_ReadFailResponse(),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Exactly one URL open: hostname primary. No literal-IP fallback.
        self.assertEqual(urlopen.call_count, 1)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            )
        )
        # The error must explicitly mark itself as post-send so operators
        # can diagnose "OPNsense may have committed" vs "transport down".
        msg = str(ctx.exception)
        self.assertIn("AFTER request was sent", msg)
        # The BypassError must carry post_send=True so the outer handler
        # can route it correctly (POST → refuse retry; GET → fall through).
        self.assertTrue(getattr(ctx.exception, "post_send", False))

    def test_dns_error_on_hostname_primary_falls_through_to_literal_ip_post(self):
        """A pre-send gaierror (DNS resolution failure) on the hostname
        primary MUST still fall through to the literal-IP POST fallback —
        the whole point of the break-glass path is to keep working when
        the local DNS resolver is down. This proves the pre-send vs
        post-send split does not regress the original DNS-down scenario.
        """
        import socket

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=socket.gaierror(-2, "Name or service not known"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Two URL opens: hostname primary + literal-IP HTTPS fallback.
        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(
            called_urls[1].startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
        )
        # Fallback must be the literal-IP HTTPS endpoint, NOT the
        # operator-configured HTTP fallback.
        self.assertFalse(any(url.startswith(FALLBACK) for url in called_urls))
        # The combined error message references both endpoints.
        msg = str(ctx.exception)
        self.assertIn("https-primary", msg)
        self.assertIn("https-literal-fallback", msg)

    def test_urllib_wrapped_gaierror_on_post_falls_through_to_literal_ip(self):
        """Real-world DNS failures inside urlopen surface as
        ``urllib.error.URLError(reason=socket.gaierror(...))``, NOT as a
        raw ``socket.gaierror``. The narrowed pre-send classifier must
        still detect the wrapped gaierror (via ``.reason``) and let the
        POST fall through to the literal-IP HTTPS fallback. This
        regression-locks the real-world shape of a DNS outage against
        a future refactor that only handles the synthetic raw form.
        """
        import socket
        import urllib.error

        # The exact shape urlopen produces when DNS fails on Python 3.12.
        wrapped = urllib.error.URLError(
            socket.gaierror(-2, "Name or service not known")
        )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=wrapped,
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Two URL opens: hostname primary + literal-IP HTTPS fallback.
        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(
            called_urls[1].startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
        )
        self.assertFalse(any(url.startswith(FALLBACK) for url in called_urls))
        msg = str(ctx.exception)
        self.assertIn("https-primary", msg)
        self.assertIn("https-literal-fallback", msg)
        # The propagated error from the fallback itself is NOT a
        # post_send-flagged BypassError (the gaierror was the URL's
        # primary error; the fallback also raised with its own
        # transport-style message).
        self.assertFalse(getattr(ctx.exception, "post_send", False))

    def test_post_non_dns_urlopen_error_does_not_fall_through_to_literal_ip(self):
        """Regression for Luna xhigh #2: a non-DNS urlopen-time error
        on the POST hostname primary must NOT fall through to the
        hard-coded literal-IP HTTPS fallback. urlopen may have buffered
        and partially transmitted the request body before surfacing the
        error, so OPNsense could already have committed the mutation by
        the time the exception propagates out. Retrying the same write
        against a different transport risks double-applying the change.
        The error MUST surface as ``BypassError(post_send=True)`` and
        the literal-IP endpoint MUST NOT be contacted.

        Exercises the three concrete non-DNS failure modes:

          1. Plain ``URLError`` with no ``.reason`` (the synthetic
             shape that is NOT a gaierror in disguise).
          2. ``ConnectionError`` (TCP connect reset mid-handshake).
          3. ``OSError`` / ``BrokenPipeError`` (``EPIPE`` on body write).
        """
        import urllib.error

        # Single shared side_effect sequence: first call raises the
        # non-DNS error, no second call should ever happen.
        non_dns_shapes = [
            (urllib.error.URLError("primary transport failed"), "URLError"),
            (ConnectionResetError("simulated mid-connect reset"), "ConnectionError"),
            (BrokenPipeError("simulated EPIPE on body write"), "OSError/EPIPE"),
        ]
        for raised, label in non_dns_shapes:
            with self.subTest(failure_shape=label):
                with patch.object(
                    switch_dns_path.urllib.request,
                    "urlopen",
                    side_effect=raised,
                ) as urlopen:
                    with self.assertRaises(
                        switch_dns_path.BypassError
                    ) as ctx:
                        switch_dns_path._api_call(
                            "POST",
                            switch_dns_path.NAT_API_ADD,
                            body={"rule": {"interface": "lan"}},
                            **self.api_kwargs(),
                        )

                # Exactly one URL open: hostname primary only. The
                # literal-IP HTTPS fallback MUST NOT be contacted.
                self.assertEqual(
                    urlopen.call_count,
                    1,
                    f"non-DNS {label} must not contact the literal-IP fallback",
                )
                called_urls = [
                    call.args[0].full_url for call in urlopen.call_args_list
                ]
                self.assertTrue(called_urls[0].startswith(PRIMARY))
                self.assertFalse(
                    any(
                        url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                        for url in called_urls
                    ),
                    f"non-DNS {label} must not call the literal-IP fallback",
                )
                self.assertFalse(
                    any(url.startswith(FALLBACK) for url in called_urls),
                    f"non-DNS {label} must not call the operator HTTP fallback",
                )
                # The error MUST carry post_send=True so the outer
                # caller can route it as "ambiguous, refuse retry".
                self.assertTrue(
                    getattr(ctx.exception, "post_send", False),
                    f"non-DNS {label} BypassError must have post_send=True",
                )
                # The error message MUST distinguish this from a
                # DNS-resolution failure so operators can diagnose.
                msg = str(ctx.exception)
                self.assertIn("not a DNS-resolution failure", msg)

    def test_get_non_dns_urlopen_error_falls_through_to_operator_http_fallback(self):
        """Regression for the GET-path preservation clause: a non-DNS
        urlopen-time error on the GET hostname primary MUST still fall
        through to the operator-configured HTTP fallback. Reads are
        idempotent so retrying against a second transport is safe. This
        proves the GET-side behavior is unchanged even though the
        POST-side classifier is now strict.
        """
        import urllib.error

        # Walk every non-DNS failure shape the narrowing changed.
        for raised, label in (
            (urllib.error.URLError("primary transport failed"), "URLError"),
            (ConnectionResetError("simulated mid-connect reset"), "ConnectionError"),
            (BrokenPipeError("simulated EPIPE on body write"), "OSError/EPIPE"),
        ):
            with self.subTest(failure_shape=label):
                with patch.object(
                    switch_dns_path.urllib.request,
                    "urlopen",
                    side_effect=raised,
                ) as urlopen:
                    with self.assertRaises(switch_dns_path.BypassError):
                        switch_dns_path._api_call(
                            "GET",
                            switch_dns_path.NAT_API_SEARCH,
                            **self.api_kwargs(),
                        )

                # Two URL opens: hostname primary + operator HTTP fallback.
                self.assertEqual(urlopen.call_count, 2)
                called_urls = [
                    call.args[0].full_url for call in urlopen.call_args_list
                ]
                self.assertTrue(called_urls[0].startswith(PRIMARY))
                self.assertTrue(called_urls[1].startswith(FALLBACK))
                # GET fallback MUST be the operator-configured HTTP
                # endpoint, NEVER the hard-coded literal-IP HTTPS POST
                # endpoint — that path is POST-only.
                self.assertFalse(
                    any(
                        url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                        for url in called_urls
                    ),
                    f"GET {label} must not contact the literal-IP POST fallback",
                )

    def test_post_send_read_failure_on_get_uses_operator_http_fallback(self):
        """A GET whose primary urlopen succeeds but r.read() then raises
        falls through to the operator-configured HTTP fallback — GETs are
        safe to retry on a second transport because they do not mutate
        the firewall table. This proves the GET path is unchanged.
        """
        class _ReadFailResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                raise ConnectionResetError(
                    "simulated mid-response reset AFTER GET sent"
                )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            return_value=_ReadFailResponse(),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError):
                switch_dns_path._api_call(
                    "GET",
                    switch_dns_path.NAT_API_SEARCH,
                    **self.api_kwargs(),
                )

        # Two URL opens: hostname primary + operator-configured HTTP fallback.
        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(called_urls[1].startswith(FALLBACK))
        # GET fallback must NOT be the hard-coded literal-IP HTTPS POST fallback.
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            )
        )

    # ---- P1.5: post-mutation read-back must require exactly six rules

    def test_install_post_apply_readback_empty_reports_actionable_error(self):
        """install_bypass calls _apply_nat then reads back. If the
        post-apply read returns zero rows, install must raise the
        actionable "ruleset incomplete: have 0/6" error instead of
        silently reporting installed=N, total=0, state=absent.
        """
        calls = []
        installed_rows: list = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                # First search (pre-install): empty. Second search
                # (post-apply read-back): ALSO empty — simulates the
                # firewall table being wiped by something else between
                # add_rule and apply, or apply not propagating.
                return "test", observed_search_response([])
            if method == "POST" and path == switch_dns_path.NAT_API_ADD:
                body = kwargs.get("body") or {}
                installed_rows.append(body)
                return "test", {"result": "saved", "uuid": f"uuid-{len(installed_rows)}"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.install_bypass(**self.api_kwargs())

        msg = str(ctx.exception)
        self.assertIn("have 0/6", msg)
        self.assertIn("run --install to repair", msg)
        # All 6 add_rule calls were issued (the read-back is what catches it).
        self.assertEqual(
            sum(call[1] == switch_dns_path.NAT_API_ADD for call in calls),
            6,
        )
        # Apply was issued (the script does not pre-check empty); the
        # post-apply read-back is the gate.
        self.assertTrue(
            any(call[1] == switch_dns_path.NAT_API_APPLY for call in calls)
        )

    def test_enable_final_readback_empty_reports_actionable_error(self):
        """enable_bypass calls _apply_nat then reads back. If the final
        read returns zero rows, enable must raise the actionable
        "ruleset incomplete: have 0/6" error.
        """
        calls = []
        rows = managed_rows()

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            body = kwargs.get("body")
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                # First search (from inner install_bypass): full set.
                # Second search (post-enable final read-back): EMPTY.
                if not any(c[1] == switch_dns_path.NAT_API_APPLY for c in calls):
                    return "test", observed_search_response(rows)
                return "test", observed_search_response([])
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                row = next(row for row in rows if row["uuid"] == uuid)
                return "test", {"rule": copy.deepcopy(row)}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                if not isinstance(body, dict) or set(body) != {"rule"}:
                    self.fail("set_rule body is not a rule envelope")
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.enable_bypass(**self.api_kwargs())

        msg = str(ctx.exception)
        self.assertIn("have 0/6", msg)
        self.assertIn("run --install to repair", msg)

    def test_disable_post_disable_readback_empty_reports_actionable_error(self):
        """disable_bypass calls _apply_nat then reads back. If the
        post-disable read returns zero rows, disable must raise the
        actionable "ruleset incomplete: have 0/6" error and the
        message must suggest --install as the remediation.
        """
        calls = []
        rows = managed_rows(enabled="1")

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                # First search (pre-disable): full set. Second search
                # (post-disable read-back): EMPTY.
                if not any(c[1] == switch_dns_path.NAT_API_APPLY for c in calls):
                    return "test", observed_search_response(rows)
                return "test", observed_search_response([])
            if method == "GET" and path.startswith(f"{switch_dns_path.NAT_API_GET}/"):
                uuid = path.rsplit("/", 1)[1]
                row = next(row for row in rows if row["uuid"] == uuid)
                return "test", {"rule": copy.deepcopy(row)}
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_SET}/"):
                return "test", {"result": "saved"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path.disable_bypass(**self.api_kwargs())

        msg = str(ctx.exception)
        self.assertIn("have 0/6", msg)
        self.assertIn("run --install to repair", msg)

    def test_uninstall_post_uninstall_readback_empty_returns_visible_state(self):
        """uninstall_bypass's post-uninstall read-back MUST still allow
        the empty state (empty is the desired post-uninstall state).
        Six rules removed → final read-back empty → returns
        ``uninstalled=6`` cleanly with no exception.
        """
        rows = managed_rows()
        calls = []

        def fake_api_call(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == switch_dns_path.NAT_API_SEARCH:
                # First search: full set. Second search (post-uninstall): empty.
                if any(c[1].startswith(f"{switch_dns_path.NAT_API_DEL}/") for c in calls):
                    return "test", observed_search_response([])
                return "test", observed_search_response(rows + unrelated_wan_rows())
            if method == "POST" and path.startswith(f"{switch_dns_path.NAT_API_DEL}/"):
                rows[:] = [row for row in rows if row["uuid"] != path.rsplit("/", 1)[1]]
                return "test", {"result": "deleted"}
            if method == "POST" and path == switch_dns_path.NAT_API_APPLY:
                return "test", {"status": "OK"}
            self.fail(f"unexpected API call: {method} {path}")

        with patch.object(switch_dns_path, "_api_call", side_effect=fake_api_call):
            summary = switch_dns_path.uninstall_bypass(**self.api_kwargs())

        self.assertEqual(summary, "uninstalled=6")
        self.assertEqual(
            sum(call[1].startswith(f"{switch_dns_path.NAT_API_DEL}/") for call in calls),
            6,
        )

    # ---- Luna xhigh regressions: HTTPError ordering + BadStatusLine gap

    def test_bad_status_line_on_post_does_not_fall_through_to_literal_ip(self):
        """Regression for Luna xhigh finding #2: ``http.client.BadStatusLine``
        raised by urlopen on a POST MUST NOT fall through to the literal-IP
        HTTPS fallback. The BadStatusLine MRO is
        ``BadStatusLine -> HTTPException -> Exception`` only — not
        URLError, gaierror, ConnectionError, or OSError. Without the
        catch-all inside ``_do_request`` this would escape and hit the
        outer ``except Exception`` in ``_api_call`` (which sets
        ``primary_transport_err`` and falls through to the literal-IP
        POST fallback). For a mutating POST that is unsafe: urlopen may
        have buffered and partially transmitted the request body before
        the server closed the connection, so OPNsense could already
        have committed the mutation by the time the exception propagates
        out. The error MUST surface as ``BypassError(post_send=True)``
        and the literal-IP endpoint MUST NOT be contacted.
        """

        import http.client

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=http.client.BadStatusLine("simulated server reset"),
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Exactly one URL open: hostname primary only. The literal-IP
        # HTTPS fallback MUST NOT be contacted.
        self.assertEqual(urlopen.call_count, 1)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            ),
            "BadStatusLine on POST must not contact the literal-IP fallback",
        )
        self.assertFalse(
            any(url.startswith(FALLBACK) for url in called_urls),
            "BadStatusLine on POST must not contact the operator HTTP fallback",
        )
        # The BypassError must carry post_send=True so the outer
        # handler routes it correctly (POST -> refuse retry).
        self.assertTrue(
            getattr(ctx.exception, "post_send", False),
            "BadStatusLine on POST must surface as BypassError(post_send=True)",
        )
        # The error must NOT be a CredentialError (BadStatusLine is a
        # transport problem, not a credential problem).
        self.assertNotIsInstance(ctx.exception, switch_dns_path.CredentialError)

    def test_http_error_401_on_post_raises_credential_error(self):
        """Regression for Luna xhigh finding #1: ``urllib.error.HTTPError``
        is a subclass of ``urllib.error.URLError``. If the ``except
        URLError`` branch inside ``_do_request`` runs first, it wraps
        HTTPError in ``BypassError(post_send=True)`` and the outer
        ``except HTTPError -> CredentialError`` fast-path becomes
        unreachable. A 401 on a POST must propagate to the outer handler
        and surface as ``CredentialError`` — NOT ``BypassError`` and
        NOT a fall-through to the literal-IP fallback.
        """

        import urllib.error

        # HTTPError.__init__ wants (url, code, msg, hdrs, fp). Use
        # BytesIO for fp so HTTPError's __del__ cleanup (it tries to
        # close fp) does not raise AttributeError in the GC.
        http_err = urllib.error.HTTPError(
            url="https://example.invalid/api/firewall/source_nat/addRule",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "unauthorized"}'),
        )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=http_err,
        ) as urlopen:
            with self.assertRaises(switch_dns_path.CredentialError):
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Exactly one URL open: hostname primary only. The literal-IP
        # HTTPS fallback MUST NOT be contacted (a 401 is a credential
        # problem, not a transport problem).
        self.assertEqual(urlopen.call_count, 1)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            ),
            "HTTP 401 on POST must not contact the literal-IP fallback",
        )

    def test_http_error_403_on_get_raises_credential_error(self):
        """Regression for Luna xhigh finding #1 (GET path): a 403 on a
        GET must propagate to the outer handler and surface as
        ``CredentialError`` — NOT fall through to the operator-configured
        HTTP fallback (a 403 on the hostname primary means the credential
        does not have the required privilege on this host; retrying the
        same GET against a second transport is pointless and would mask
        the real problem).
        """

        import urllib.error

        http_err = urllib.error.HTTPError(
            url="https://example.invalid/api/firewall/source_nat/search",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"error": "forbidden"}'),
        )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=http_err,
        ) as urlopen:
            with self.assertRaises(switch_dns_path.CredentialError):
                switch_dns_path._api_call(
                    "GET",
                    switch_dns_path.NAT_API_SEARCH,
                    **self.api_kwargs(),
                )

        # Exactly one URL open: hostname primary only. A 403 must NOT
        # fall through to the operator-configured HTTP fallback.
        self.assertEqual(urlopen.call_count, 1)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertFalse(
            any(url.startswith(FALLBACK) for url in called_urls),
            "HTTP 403 on GET must not fall through to the operator HTTP fallback",
        )
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            ),
            "HTTP 403 on GET must not contact the literal-IP POST fallback",
        )

    def test_http_error_500_on_post_raises_bypass_error_not_credential(self):
        """Regression for Luna xhigh finding #1 (non-credential HTTP
        status on POST): a 500 on a POST must propagate as
        ``BypassError`` — NOT ``CredentialError`` (a 500 is not a
        credential failure) and NOT fall through to the literal-IP
        HTTPS fallback (a reachable-but-erroring primary is a server
        result, not a transport problem).
        """

        import urllib.error

        http_err = urllib.error.HTTPError(
            url="https://example.invalid/api/firewall/source_nat/addRule",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error": "internal server error"}'),
        )

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=http_err,
        ) as urlopen:
            with self.assertRaises(switch_dns_path.BypassError) as ctx:
                switch_dns_path._api_call(
                    "POST",
                    switch_dns_path.NAT_API_ADD,
                    body={"rule": {"interface": "lan"}},
                    **self.api_kwargs(),
                )

        # Must NOT be a CredentialError (a 500 is not a credential failure).
        self.assertNotIsInstance(ctx.exception, switch_dns_path.CredentialError)
        # Exactly one URL open: hostname primary only. A 500 on the
        # hostname primary MUST NOT fall through to the literal-IP
        # HTTPS fallback (a reachable-but-erroring primary is not a
        # transport problem; retrying the same write risks double-applying
        # the mutation).
        self.assertEqual(urlopen.call_count, 1)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            ),
            "HTTP 500 on POST must not contact the literal-IP fallback",
        )

    def test_http_error_500_on_get_falls_through_to_operator_http_fallback(self):
        """Regression for the GET-path preservation clause: a non-credential
        HTTP status (500) on a GET MUST fall through to the
        operator-configured HTTP fallback. Reads are safe to retry on a
        second transport, and a 500 from one transport may be a
        transient server problem that the other transport handles.
        """

        import urllib.error

        # First call: 500 on the HTTPS primary. Second call: success on
        # the operator-configured HTTP fallback.
        primary_err = urllib.error.HTTPError(
            url="https://example.invalid/api/firewall/source_nat/search",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"error": "internal server error"}'),
        )

        class _OkResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"current":1,"rowCount":1000,"rows":[],"total":0}'

        with patch.object(
            switch_dns_path.urllib.request,
            "urlopen",
            side_effect=[primary_err, _OkResponse()],
        ) as urlopen:
            label, data = switch_dns_path._api_call(
                "GET",
                switch_dns_path.NAT_API_SEARCH,
                **self.api_kwargs(),
            )

        # Two URL opens: hostname primary + operator HTTP fallback.
        self.assertEqual(urlopen.call_count, 2)
        called_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertTrue(called_urls[0].startswith(PRIMARY))
        self.assertTrue(called_urls[1].startswith(FALLBACK))
        # GET fallback MUST be the operator-configured HTTP endpoint,
        # NEVER the hard-coded literal-IP HTTPS POST endpoint.
        self.assertFalse(
            any(
                url.startswith(switch_dns_path.BYPASS_POST_HTTPS_FALLBACK)
                for url in called_urls
            ),
            "GET HTTP 500 fallback must not contact the literal-IP POST fallback",
        )
        # The successful response came from the operator HTTP fallback.
        self.assertEqual(label, "http-fallback")
        self.assertEqual(data, {"current": 1, "rowCount": 1000, "rows": [], "total": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
