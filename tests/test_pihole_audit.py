import inspect
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pihole import audit


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, request, timeout=0):
        self.calls.append((request.method, request.full_url, dict(request.headers)))
        key = (request.method, request.full_url)
        if key not in self.responses:
            raise AssertionError(f"unexpected request: {key}")
        return FakeResponse(self.responses[key])


class PiHoleAuditTests(unittest.TestCase):
    @staticmethod
    def _complete_pihole(overrides):
        payloads = {"config": {"dns": {}}, "groups": [], "lists": [], "domains": [], "clients": [], "version": {"version": "v6.0"}}
        payloads.update(overrides)
        return payloads

    def test_sanitize_redacts_secrets_and_client_identifiers(self):
        payload = {
            "sid": "sid-secret",
            "session_id": "session-secret",
            "csrf": "csrf-secret",
            "credential": "credential-secret",
            "bearer": "bearer-secret",
            "authorization": "Bearer authorization-secret",
            "api_key": "api_key-secret",
            "access_key": "access_key-secret",
            "client_id": "client-id-secret",
            "cookie": "cookie-secret",
            "record_id": "record-secret",
            "password": "password-secret",
            "config": {
                "upstreams": ["192.168.86.1"],
                "client": 54321,
                "record": 67890,
                "description": "uuid=123e4567-e89b-12d3-a456-426614174000 mac=AA-BB-CC-DD-EE-FF cisco=aabb.ccdd.eeff compact=AABBCCDDEEFF dotted=AA.BB.CC.DD.EE.FF ipv6=2001:db8::1 loopback=::1 session=session-secret session_id=session-id-free auth=auth-secret auth_token=auth-token-free csrf_token=csrf-token-free authorization=authorization-free authentication=authentication-free cookie=cookie-free client_id=client-id-free client=12345 record=67890 mapped=::ffff:192.0.2.1 scoped=fe80::1%eth0 url=https://user:pass@example.test/ relative=//user:pass@example.test/",
            },
            "clients": [
                {
                    "identifier": "AA:BB:CC:DD:EE:FF",
                    "id": 12345,
                    "ip": "192.168.86.42",
                    "description": "client uuid=123e4567-e89b-12d3-a456-426614174000 password=hidden",
                    "name": "phone",
                }
            ],
        }

        sanitized = audit.sanitize_payload(payload)
        encoded = json.dumps(sanitized, sort_keys=True)

        self.assertNotIn("sid-secret", encoded)
        self.assertNotIn("session-secret", encoded)
        self.assertNotIn("csrf-secret", encoded)
        self.assertNotIn("credential-secret", encoded)
        self.assertNotIn("bearer-secret", encoded)
        self.assertNotIn("record-secret", encoded)
        self.assertNotIn("api_key-secret", encoded)
        self.assertNotIn("access_key-secret", encoded)
        self.assertNotIn("client-id-secret", encoded)
        self.assertNotIn("cookie-secret", encoded)
        self.assertNotIn("12345", encoded)
        self.assertNotIn("password-secret", encoded)
        self.assertNotIn("123e4567-e89b-12d3-a456-426614174000", encoded)
        self.assertNotIn("hidden", encoded)
        self.assertNotIn("AA:BB:CC:DD:EE:FF", encoded)
        self.assertNotIn("AA-BB-CC-DD-EE-FF", encoded)
        self.assertNotIn("AABBCCDDEEFF", encoded)
        self.assertNotIn("AA.BB.CC.DD.EE.FF", encoded)
        self.assertNotIn("aabb.ccdd.eeff", encoded)
        self.assertNotIn("user:pass@", encoded)
        self.assertNotIn("auth-secret", encoded)
        self.assertNotIn("session-id-free", encoded)
        self.assertNotIn("auth-token-free", encoded)
        self.assertNotIn("csrf-token-free", encoded)
        self.assertNotIn("authorization-free", encoded)
        self.assertNotIn("authentication-free", encoded)
        self.assertNotIn("cookie-free", encoded)
        self.assertNotIn("client-id-free", encoded)
        self.assertNotIn("12345", encoded)
        self.assertNotIn("67890", encoded)
        self.assertNotIn("54321", encoded)
        self.assertNotIn("192.0.2.1", encoded)
        self.assertNotIn("::ffff:", encoded)
        self.assertNotIn("192.168.86.42", encoded)
        self.assertNotIn("fe80::1%eth0", encoded)
        self.assertNotIn("user:pass@", encoded)
        self.assertNotIn("192.168.86.1", encoded)
        self.assertNotIn("2001:db8::1", encoded)
        self.assertNotIn("::1", encoded)
        self.assertNotIn('"password"', encoded)
        self.assertNotIn('"authorization"', encoded)
        self.assertEqual(sanitized["config"]["upstreams"], [audit.REDACTED])

    def test_reserved_redaction_keys_are_order_independent(self):
        first_config = {"[REDACTED]": "literal-a", "[LITERAL_KEY:[REDACTED]]": "literal-b", "[LITERAL_KEY:[LITERAL_KEY:[REDACTED]]]": "literal-c", "password": "secret"}
        second_config = dict(reversed(list(first_config.items())))
        first = audit.snapshot_from_payloads("pihole-a", self._complete_pihole({"config": first_config}), audit.PIHOLE_RESOURCE_NAMES)
        second = audit.snapshot_from_payloads("pihole-a", self._complete_pihole({"config": second_config}), audit.PIHOLE_RESOURCE_NAMES)
        self.assertEqual(first, second)

    def test_sensitive_shaped_keys_are_redacted_and_collision_safe(self):
        first = {
            "ip=192.0.2.1": "one",
            "uuid=123e4567-e89b-12d3-a456-426614174000": "two",
            "mac=aa:bb:cc:dd:ee:ff": "three",
            "url=https://user:pass@example.test/": "four",
            "ip=192.0.2.2": "five",
        }
        second = dict(reversed(list(first.items())))
        first_clean = audit.sanitize_payload(first)
        second_clean = audit.sanitize_payload(second)
        self.assertEqual(first_clean, second_clean)
        encoded = json.dumps(first_clean, sort_keys=True)
        for raw in first:
            self.assertNotIn(raw, encoded)
        self.assertNotIn("192.0.2.1", encoded)
        self.assertNotIn("123e4567-e89b-12d3-a456-426614174000", encoded)
        self.assertNotIn("aa:bb:cc:dd:ee:ff", encoded)
        self.assertNotIn("user:pass@", encoded)

    def test_numeric_ids_are_redacted_in_every_resource_context(self):
        snapshot = audit.snapshot_from_payloads(
            "pihole-a",
            self._complete_pihole({
                "config": {"id": 10101},
                "groups": [{"id": 20202}],
                "lists": [{"id": 30303}],
                "domains": [{"id": 40404}],
                "clients": [{"id": 50505}],
            }),
            audit.PIHOLE_RESOURCE_NAMES,
        )
        encoded = json.dumps(snapshot, sort_keys=True)
        for value in (10101, 20202, 30303, 40404, 50505):
            self.assertNotIn(str(value), encoded)

    def test_sanitize_rejects_non_string_keys(self):
        with self.assertRaises(audit.AuditError):
            audit.sanitize_payload({1: "A", "1": "B"})
        with self.assertRaises(audit.AuditError):
            audit.sanitize_payload({"x": float("nan")})
        with self.assertRaises(audit.AuditError):
            audit.sanitize_payload({"x": float("inf")})
        self.assertNotIn("secret-value", audit.sanitize_payload("password=my secret-value"))
        self.assertNotIn("compound-secret", audit.sanitize_payload("foo_password=compound-secret"))
        self.assertNotIn("compound-session", audit.sanitize_payload("foo_session_id=compound-session"))
        for label in ("client_address", "client-address"):
            self.assertNotIn("compound-client", audit.sanitize_payload(f"{label}=compound-client"))
        for label in ("api_key", "api-key", "access_key", "private_key", "client.id", "record.id", "id", "identifier", "mac", "uuid"):
            self.assertNotIn("separator-secret", audit.sanitize_payload(f"{label}=separator-secret"))
        for text in ('"password": "quoted-secret"', "'password': 'quoted-secret'", '"foo_id": "opaque-secret"'):
            self.assertNotIn("quoted-secret", audit.sanitize_payload(text))
            self.assertNotIn("opaque-secret", audit.sanitize_payload(text))
        for value in ("secret-client", "secret-bearer", "secret-cookie", "secret-credential"):
            self.assertNotIn(value, audit.sanitize_payload(f"clientAddress={value}" if value == "secret-client" else f"{value.split('-')[1]}_value={value}"))

    def test_snapshot_is_order_independent_and_policy_sensitive(self):
        first = {
            "groups": [{"id": 2, "name": "normal"}, {"id": 1, "name": "default"}],
            "lists": [{"id": 9, "address": "https://example.test/list"}],
            "domains": [{"id": 4, "domain": "ads.example"}],
            "clients": [{"id": 3, "identifier": "AA:BB:CC:DD:EE:FF", "comment": "phone"}],
        }
        second = {
            "clients": list(reversed(first["clients"])),
            "domains": list(reversed(first["domains"])),
            "lists": list(reversed(first["lists"])),
            "groups": list(reversed(first["groups"])),
        }

        first_complete = self._complete_pihole(first)
        second_complete = self._complete_pihole(second)
        one = audit.snapshot_from_payloads("pihole-a", first_complete, audit.PIHOLE_RESOURCE_NAMES)
        two = audit.snapshot_from_payloads("pihole-a", second_complete, audit.PIHOLE_RESOURCE_NAMES)
        self.assertEqual(one, two)
        wrapped = audit.snapshot_from_payloads("opnsense", {"service": {"status": "running"}, "hostOverrides": {"rows": [{"hostname": "router"}], "current": 1, "rowCount": 1, "total": 1}, "hostAliases": []}, audit.OPNSENSE_RESOURCE_NAMES)
        self.assertEqual(wrapped["resourceCounts"]["hostOverrides"], 1)

        changed = dict(second)
        changed["groups"] = [{"id": 1, "name": "default"}, {"id": 2, "name": "strict"}]
        self.assertNotEqual(one["fingerprint"], audit.snapshot_from_payloads("pihole-a", self._complete_pihole(changed), audit.PIHOLE_RESOURCE_NAMES)["fingerprint"])

    def test_live_collectors_use_auth_then_get_only(self):
        responses = {
            ("POST", "https://pihole.test/api/auth"): {"session": {"valid": True, "sid": "temporary-sid"}},
            ("GET", "https://pihole.test/api/config"): {},
            ("GET", "https://pihole.test/api/groups"): {"groups": []},
            ("GET", "https://pihole.test/api/lists"): {"lists": []},
            ("GET", "https://pihole.test/api/domains"): {"domains": []},
            ("GET", "https://pihole.test/api/clients"): {"clients": []},
            ("GET", "https://pihole.test/api/info/version"): {},
            ("GET", "https://router.test/api/unbound/service/status"): {},
            ("GET", "https://router.test/api/unbound/settings/searchHostOverride"): {"rows": [], "current": 1, "rowCount": 0, "total": 0},
            ("GET", "https://router.test/api/unbound/settings/searchHostAlias"): {"rows": [], "current": 1, "rowCount": 0, "total": 0},
        }
        opener = FakeOpener(responses)

        audit._collect_pihole("https://pihole.test", "temporary-password", opener)
        audit._collect_opnsense("https://router.test", "key", "secret", opener)

        methods = [method for method, _url, _headers in opener.calls]
        self.assertEqual(methods[0], "POST")
        self.assertTrue(all(method == "GET" for method in methods[1:]))
        self.assertNotIn("PUT", methods)
        self.assertNotIn("PATCH", methods)
        self.assertNotIn("DELETE", methods)
        auth_headers = [headers for method, url, headers in opener.calls if method == "GET" and "pihole.test" in url]
        self.assertTrue(any("x-ftl-sid" in {key.lower() for key in headers} for headers in auth_headers))

    def test_public_collectors_return_sanitized_payloads(self):
        responses = {
            ("POST", "https://pihole.test/api/auth"): {"session": {"valid": True, "sid": "temporary-sid"}},
            ("GET", "https://pihole.test/api/config"): {"id": 12345, "description": "session_id=secret"},
            ("GET", "https://pihole.test/api/groups"): {"groups": [{"id": 12346}]},
            ("GET", "https://pihole.test/api/lists"): {"lists": []},
            ("GET", "https://pihole.test/api/domains"): {"domains": []},
            ("GET", "https://pihole.test/api/clients"): {"clients": []},
            ("GET", "https://pihole.test/api/info/version"): {"version": "v6.0", "branch": "master", "hash": "abc"},
            ("GET", "https://router.test/api/unbound/service/status"): {"status": "running", "id": 22345, "description": "auth_token=secret"},
            ("GET", "https://router.test/api/unbound/settings/searchHostOverride"): {"rows": [{"id": 22346}], "current": 1, "rowCount": 1, "total": 1},
            ("GET", "https://router.test/api/unbound/settings/searchHostAlias"): {"rows": [], "current": 1, "rowCount": 0, "total": 0},
        }
        opener = FakeOpener(responses)
        with patch.object(audit, "_safe_urlopen", opener):
            pihole = audit.collect_pihole("https://pihole.test", "temporary-password")
            opnsense = audit.collect_opnsense("https://router.test", "key", "secret")
        encoded = json.dumps({"pihole": pihole, "opnsense": opnsense}, sort_keys=True)
        for raw in ("12345", "12346", "22345", "22346", "session_id=secret", "auth_token=secret"):
            self.assertNotIn(raw, encoded)

    def test_invalid_pihole_session_stops_before_reads(self):
        opener = FakeOpener({("POST", "https://pihole.test/api/auth"): {"session": {"valid": False, "sid": "temporary-sid"}}})
        with self.assertRaises(audit.AuditError):
            audit._collect_pihole("https://pihole.test", "password", opener)
        self.assertEqual([method for method, _url, _headers in opener.calls], ["POST"])
        malformed_sid = FakeOpener({("POST", "https://pihole.test/api/auth"): {"session": {"valid": True, "sid": "sid\r\nInjected: yes"}}})
        with self.assertRaises(audit.AuditError):
            audit._collect_pihole("https://pihole.test", "password", malformed_sid)

    def test_invalid_origins_fail_closed(self):
        for url in ("\x01https://host.test", "\x1bhttps://host.test", "\x00https://host.test", " https://pihole.test", "https://pihole.test ", "ftp://pihole.test", "https://pihole.test/api", "https://pihole.test?secret=value", "https://pihole.test?", "https://pihole.test#", "https://@pihole.test", "https://pihole.test:", "https://pihole\r\n.test", "https://pihole\t.test", "https://pihole\x00.test", "https://host name", "https://host\\evil", "https://host%40evil", "https://a.-b.example", "https://a-.example", "https://[v1.fe80]"):
            with self.assertRaises(audit.AuditError):
                audit.collect_pihole(url, "password")
        with self.assertRaises(audit.AuditError) as raised:
            audit.collect_pihole("https://host:secret", "password")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(audit._validate_base_url("https://[::ffff:192.0.2.1]"), "https://[::ffff:192.0.2.1]")
        with self.assertRaises(audit.AuditError):
            audit.collect_opnsense("https://router.test/api?secret=value", "key", "secret")

    def test_opnsense_requires_https(self):
        with self.assertRaises(audit.AuditError):
            audit.collect_opnsense("http://router.test", "key", "secret")

    def test_request_errors_do_not_echo_url_credentials(self):
        def failing_opener(_request, timeout=0):
            raise audit.URLError("https://user:password@example.test/session=secret")

        with self.assertRaises(audit.AuditError) as raised:
            audit._PiHoleClient("https://example.test", opener=failing_opener).get("/api/config")
        self.assertNotIn("password", str(raised.exception))
        self.assertNotIn("example.test", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_read_only_post_and_redirect_guards(self):
        self.assertFalse(hasattr(audit, "JsonHttpClient"))
        self.assertNotIn("opener", inspect.signature(audit.collect_pihole).parameters)
        self.assertNotIn("opener", inspect.signature(audit.collect_opnsense).parameters)
        with self.assertRaises(audit.AuditError):
            audit._OpnsenseClient("http://example.test")
        with self.assertRaises(audit.AuditError):
            audit._OpnsenseClient("https://example.test").post_json("/api/auth", {})
        client = audit._JsonHttpClient("https://example.test", opener=FakeOpener({}))
        with self.assertRaises(audit.AuditError):
            client.post_json("/api/config", {})
        with self.assertRaises(audit.AuditError) as raised:
            client.post_json("/api/auth", {"value": float("nan")})
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        with self.assertRaises(audit.AuditError):
            client.get("/api/actions/delete")
        request = audit.Request("https://example.test/api/config")
        self.assertIsNone(audit._NoRedirectHandler().redirect_request(request, None, 302, "redirect", None, "https://other.test"))  # type: ignore[arg-type]

    def test_scalar_version_response_is_accepted(self):
        snapshot = audit.snapshot_from_payloads(
            "pihole-a",
            {"config": {"dns": {}}, "groups": [], "lists": [], "domains": [], "clients": [], "version": {"version": "v6.0", "branch": "master", "hash": "abc123"}},
            audit.PIHOLE_RESOURCE_NAMES,
        )
        self.assertEqual(snapshot["resources"]["version"]["version"], "v6.0")
        self.assertEqual(snapshot["resources"]["version"]["branch"], "master")

    def test_resource_allowlist_cannot_be_overridden(self):
        pihole_named_opnsense = audit.snapshot_from_payloads("opnsense", self._complete_pihole({}), audit.PIHOLE_RESOURCE_NAMES)
        self.assertEqual(pihole_named_opnsense["name"], "opnsense")
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("pihole-a", {}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("pihole-a", {"../secret": [{"id": 123}]}, {"../secret"})

    def test_malformed_audit_section_fails_closed(self):
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("192.0.2.1", {}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("AA-BB-CC-DD-EE-FF", {}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("malformed", {"version": {}}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {}, "opnsense": {}})
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {"a": {}}, "opnsense": {}})
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {}, "opnsense": {}, "extra": {}})
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {"bad/name": {}}, "opnsense": {}})
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("malformed", {"unknown": [{"id": "raw"}]}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("malformed", {"groups": [{"value": float("nan")}]}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("malformed", {"config": {1: "raw"}}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {1: {}}, "opnsense": {}})
        with self.assertRaises(audit.AuditError):
            audit.snapshot_from_payloads("malformed", {1: []}, audit.PIHOLE_RESOURCE_NAMES)
        with self.assertRaises(audit.AuditError):
            audit.build_report({"pihole": {"a": []}, "opnsense": {}})
        for payloads in (
            {"groups": "not-a-list"},
            {"clients": ["not-an-object"]},
            {"hostOverrides": {"rows": "not-a-list"}},
            {"hostOverrides": {"rows": [], "total": "invalid"}},
            {"hostOverrides": {"rows": []}},
            {"hostOverrides": {"rows": [], "unexpected": 1}},
            {"hostOverrides": {"rows": [], "current": 0, "rowCount": 0, "total": 0}},
            {"hostOverrides": {"rows": [], "current": 999, "rowCount": 0, "total": 0}},
            {"groups": {"groups": [], "unexpected": 1}},
            {"groups": {"unexpected": []}},
            {"groups": [], "total": 0},
            {"domains": {"exact": ["not-an-object"]}},
        ):
            with self.assertRaises(audit.AuditError):
                audit.snapshot_from_payloads("malformed", payloads, audit.PIHOLE_RESOURCE_NAMES)
        for rows, current, row_count, total in (([{}] * 100, 2, 100, 100), ([{}] * 3, 3, 3, 5), ([], 1, 0, 5)):
            with self.assertRaises(audit.AuditError):
                audit.snapshot_from_payloads("malformed", {"hostOverrides": {"rows": rows, "current": current, "rowCount": row_count, "total": total}}, audit.PIHOLE_RESOURCE_NAMES)

    def test_duplicate_json_keys_fail_closed(self):
        with self.assertRaises(ValueError):
            audit._parse_json('{"pihole": {"a": 1, "a": 2}}')

    def test_cli_input_errors_have_no_exception_context(self):
        for operation in (
            lambda: audit._fixture(Path("/definitely/missing/pihole-audit.json")),
            lambda: audit._parse_pihole_spec("not-a-valid-spec"),
        ):
            with self.assertRaises(audit.AuditError) as raised:
                operation()
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

    def test_cli_rejects_duplicate_pihole_names(self):
        with patch.dict(os.environ, {"PW": "temporary-password"}, clear=False), patch.object(audit, "collect_pihole", return_value={}):
            result = audit.main(["--live", "--opnsense-url", "https://router.test", "--pihole", "a=https://pihole.test:PW", "--pihole", "a=https://other.test:PW"])
        self.assertEqual(result, 2)

    def test_cli_fixture_writes_sanitized_json(self):
        fixture = {
            "pihole": {"a": {"config": {"dns": {}}, "groups": [], "lists": [], "domains": [], "clients": [{"identifier": "AA:BB:CC:DD:EE:FF"}], "version": {"version": "v6.0"}}},
            "opnsense": {"service": {"status": "running"}, "hostOverrides": [{"uuid": "secret-uuid", "hostname": "router"}], "hostAliases": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "input.json"
            output_path = Path(directory) / "audit.json"
            fixture_path.write_text(json.dumps(fixture))
            self.assertEqual(audit.main(["--fixture", str(fixture_path), "--output", str(output_path)]), 0)
            rendered = output_path.read_text()
        self.assertNotIn("AA:BB:CC:DD:EE:FF", rendered)
        self.assertNotIn("secret-uuid", rendered)
        self.assertIn('"readOnly": true', rendered)

    def test_cli_missing_password_env_does_not_echo_name(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), patch("sys.stderr", stderr):
            os.environ.pop("SECRET_ENV_SENTINEL", None)
            result = audit.main(["--live", "--pihole", "a=https://pihole.invalid:SECRET_ENV_SENTINEL", "--opnsense-url", "https://opnsense.invalid"])
        self.assertEqual(result, 2)
        self.assertNotIn("SECRET_ENV_SENTINEL", stderr.getvalue())

    def test_cli_preflights_names_before_network(self):
        stderr = io.StringIO()
        env = {"PW": "password", "OPNSENSE_KEY": "key", "OPNSENSE_SECRET": "secret"}
        with patch.dict(os.environ, env, clear=False), patch("sys.stderr", stderr), patch.object(audit, "collect_pihole") as pihole, patch.object(audit, "collect_opnsense") as opnsense:
            result = audit.main(["--live", "--pihole", "password=https://pihole.test:PW", "--opnsense-url", "https://router.test"])
        self.assertEqual(result, 2)
        pihole.assert_not_called()
        opnsense.assert_not_called()

    def test_sensitive_snapshot_names_fail_closed(self):
        for name in ("password", "foo_password", "client", "session", "api_key", "foo_id"):
            with self.assertRaises(audit.AuditError):
                audit.snapshot_from_payloads(name, self._complete_pihole({}), audit.PIHOLE_RESOURCE_NAMES)

    def test_public_collectors_reject_incomplete_payloads(self):
        with patch.object(audit, "_collect_pihole", return_value={}):
            with self.assertRaises(audit.AuditError):
                audit.collect_pihole("https://pihole.test", "password")
        with patch.object(audit, "_collect_opnsense", return_value={}):
            with self.assertRaises(audit.AuditError):
                audit.collect_opnsense("https://router.test", "key", "secret")

    def test_report_has_no_raw_sensitive_values(self):
        report = audit.build_report(
            {
                "pihole": {
                    "a": {
                        "config": {"webserver": {"api": {"password": "secret"}}},
                        "groups": [],
                        "lists": [],
                        "domains": [],
                        "clients": [{"identifier": "AA:BB:CC:DD:EE:FF"}],
                        "version": {"version": "v6.0"},
                    }
                },
                "opnsense": {
                    "service": {"status": "running"},
                    "hostOverrides": [{"uuid": "secret-uuid", "id": 12345, "hostname": "router"}],
                    "hostAliases": [],
                },
            }
        )
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("AA:BB:CC:DD:EE:FF", encoded)
        self.assertNotIn("secret-uuid", encoded)
        self.assertNotIn("12345", encoded)
        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(len(report["comparisonLimits"]), 2)
        self.assertEqual(report["pihole"]["a"]["resourceCounts"]["clients"], 1)


if __name__ == "__main__":
    unittest.main()
