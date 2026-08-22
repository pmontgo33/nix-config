import copy
import json
import unittest
from unittest.mock import patch

from scripts.pihole import live_reconcile as live


BASELINE = {
    "dns": {
        "upstreams": ["192.168.86.1#5353"],
        "interface": "eth0",
        "queryLogging": True,
    },
    "database": {"maxDBdays": 91},
}


class FakeTransport(live.UrllibTransport):
    safety_validated = True

    def __init__(self, responses):
        super().__init__("https://pihole.test")
        self.responses = responses
        self.calls = []

    def request(self, method, path, *, payload=None, headers=None):
        self.calls.append({
            "method": method,
            "path": path,
            "payload": copy.deepcopy(payload),
            "headers": dict(headers or {}),
        })
        key = (method, path)
        if key not in self.responses:
            raise AssertionError(f"unexpected request {key}")
        response = self.responses[key]
        return copy.deepcopy(response() if callable(response) else response)

    def open(self, request, timeout=None):
        path = request.full_url.split("https://pihole.test", 1)[1]
        payload = json.loads(request.data.decode()) if request.data else None
        value = self.request(request.method, path, payload=payload, headers=dict(request.header_items()))
        return _FakeResponse(value)


class _FakeResponse:
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode()


class StatefulTransport(FakeTransport):
    def __init__(self, responses, current):
        super().__init__(responses)
        self.current = current
        self.next_id = 100

    def request(self, method, path, *, payload=None, headers=None):
        if method == "POST" and path in {"/api/groups", "/api/clients"}:
            self.next_id += 1
            if path == "/api/groups":
                self.current["groups"].append({"id": self.next_id, **payload})
                self.responses[("POST", path)] = {"groups": [{"id": self.next_id}]}
            else:
                self.current["clients"].append({"id": self.next_id, **payload})
                self.responses[("POST", path)] = {"clients": [{"id": self.next_id}]}
        return super().request(method, path, payload=payload, headers=headers)


def inventory():
    return {
        "base": copy.deepcopy(BASELINE),
        "groups": [
            {"name": "normal", "description": "Normal clients", "enabled": True},
            {"name": "kids", "description": "Kids clients", "enabled": True},
        ],
        "adlists": [],
        "clients": [{
            "identifier": "device-a.example",
            "group": "normal",
        }],
        "localDns": [],
        "rules": {"allow": [], "block": []},
    }


OWNER_TOKEN = live._ownership_token("fixture-value")


def make_plan(desired, observed):
    return live.build_plan(desired, observed, owner_token=OWNER_TOKEN)


def state(*, stale=False, malformed=False, owner_token=OWNER_TOKEN):
    marker = live._owner_comment(owner_token)
    groups = [
        {"id": 10, "name": "normal", "comment": marker, "enabled": True},
        {"id": 11, "name": "kids", "comment": marker, "enabled": True},
        {"id": 12, "name": "local", "comment": "Local administrator", "enabled": True},
    ]
    if stale:
        groups.append({"id": 13, "name": "obsolete", "comment": marker, "enabled": True})
    if malformed:
        groups = {"groups": groups}
    return {
        "config": copy.deepcopy(BASELINE),
        "groups": groups,
        "lists": [{
            "id": 20,
            "address": "file:///var/lib/pihole/baseline.hosts",
            "comment": marker,
            "type": "block",
            "groups": [],
            "enabled": True,
        }],
        "domains": [],
        "clients": [{
            "id": 30,
            "client": "device-a.example",
            "comment": marker,
            "groups": [10],
        }],
        "version": {"version": {"core": {"local": {"version": "v6.0"}}}},
    }


def responses_for(current):
    return {
        ("POST", "/api/auth"): {"session": {"valid": True, "sid": "valid"}},
        ("GET", "/api/config"): {"config": current["config"], "took": 0.001},
        ("GET", "/api/groups"): {"groups": current["groups"], "took": 0.001, "processed": None},
        ("GET", "/api/domains"): {"domains": current["domains"], "took": 0.001, "processed": None},
        ("GET", "/api/clients"): {"clients": current["clients"], "took": 0.001, "processed": None},
        ("GET", "/api/info/version"): {**current["version"], "took": 0.001},
    }


class LivePiHoleReconcileTests(unittest.TestCase):
    def test_dry_run_reads_state_but_never_writes(self):
        transport = FakeTransport(responses_for(state(stale=True, owner_token=live._ownership_token("fixture-value"))))

        result = live.reconcile_live(
            inventory(),
            credential_callback=lambda: "fixture-value",
            transport=transport,
        )

        self.assertFalse(result["apply"])
        self.assertTrue(result["verified"])
        self.assertTrue(result["actions"])
        self.assertTrue(all(call["method"] in {"GET", "POST"} for call in transport.calls))
        self.assertEqual([call["method"] for call in transport.calls].count("POST"), 1)
        self.assertFalse(any(call["method"] in {"PUT", "PATCH", "DELETE"} for call in transport.calls))

    def test_live_policy_reconciler_never_reads_or_writes_adlists(self):
        transport = FakeTransport(responses_for(state()))
        live.reconcile_live(
            inventory(),
            credential_callback=lambda: "fixture-value",
            transport=transport,
        )
        self.assertNotIn("/api/lists", [call["path"] for call in transport.calls])
        self.assertFalse(any(call["path"].startswith("/api/lists") for call in transport.calls))

    def test_apply_requires_exact_confirmation_before_authentication(self):
        transport = FakeTransport({})

        with self.assertRaises(live.LivePolicyError) as raised:
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
                apply=True,
                confirmation="APPLY",
            )

        self.assertEqual(str(raised.exception), "exact apply confirmation is required")
        self.assertEqual(transport.calls, [])
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_malformed_auth_fails_closed_without_state_reads(self):
        transport = FakeTransport({("POST", "/api/auth"): {"session": {"sid": ""}}})

        with self.assertRaises(live.LivePolicyError) as raised:
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
            )

        self.assertEqual(str(raised.exception), "malformed Pi-hole authentication response")
        self.assertEqual([call["path"] for call in transport.calls], ["/api/auth"])
        self.assertNotIn("fixture-value", str(raised.exception))

    def test_auth_without_explicit_validity_flag_fails_closed(self):
        transport = FakeTransport({("POST", "/api/auth"): {"session": {"sid": "valid"}}})

        with self.assertRaises(live.LivePolicyError) as raised:
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
            )

        self.assertEqual(str(raised.exception), "malformed Pi-hole authentication response")
        self.assertEqual(len(transport.calls), 1)

    def test_observed_base_mismatch_fails_closed_before_writes(self):
        current = state()
        current["config"] = {}

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(inventory(), current)

        self.assertEqual(str(raised.exception), "malformed Pi-hole base configuration")

    def test_observed_base_requires_exact_scalar_types(self):
        current = state()
        current["config"]["dns"]["queryLogging"] = 1

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(inventory(), current)

        self.assertEqual(str(raised.exception), "malformed Pi-hole state")

    def test_unsupported_pihole_version_fails_closed(self):
        current = state()
        current["version"]["version"]["core"]["local"]["version"] = "v5.18"
        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(inventory(), current)
        self.assertEqual(str(raised.exception), "unsupported Pi-hole version")

    def test_unknown_config_and_version_fields_fail_closed(self):
        current = state()
        current["config"]["unexpected"] = True
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)
        current = state()
        current["version"]["version"]["unexpected"] = True
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)
        current = state()
        current["config"]["dns"]["unexpected"] = True
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)
        current = state()
        current["version"]["version"]["core"]["local"]["unexpected"] = True
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)

    def test_nested_config_scalar_types_fail_closed(self):
        current = state()
        current["config"]["dhcp"] = {"active": False, "hosts": []}
        self.assertEqual(make_plan(inventory(), current)["actions"], [])
        current["config"]["dhcp"]["active"] = 1
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)

    def test_desired_base_requires_exact_scalar_types(self):
        desired = inventory()
        desired["base"]["queryLogging"] = 1

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(desired, state())

        self.assertEqual(str(raised.exception), "unsupported base configuration")

    def test_unknown_desired_fields_fail_closed(self):
        desired = inventory()
        desired["groups"][0]["enabld"] = False

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(desired, state())

        self.assertEqual(str(raised.exception), "malformed policy inventory")

    def test_invalid_enabled_type_fails_closed(self):
        current = state()
        current["groups"][0]["enabled"] = "true"

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(inventory(), current)

        self.assertEqual(str(raised.exception), "malformed Pi-hole state")


    def test_unknown_observed_fields_fail_closed(self):
        current = state()
        current["groups"][0]["unexpected"] = True

        with self.assertRaises(live.LivePolicyError) as raised:
            make_plan(inventory(), current)

        self.assertEqual(str(raised.exception), "malformed Pi-hole state")

    def test_new_group_dependency_produces_plan_without_key_error(self):
        desired = inventory()
        desired["groups"].append({"name": "new", "description": "New clients", "enabled": True})
        plan = make_plan(desired, state())
        self.assertIn({"action": "create", "family": "groups", "managed": True}, plan["actions"])

    def test_unvalidated_injected_transport_is_rejected(self):
        class UnvalidatedTransport(FakeTransport):
            safety_validated = False

        transport = UnvalidatedTransport(responses_for(state()))
        with self.assertRaises(live.LivePolicyError) as raised:
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
            )

        self.assertEqual(str(raised.exception), "unvalidated Pi-hole transport")
        self.assertEqual(transport.calls, [])

    def test_malformed_state_fails_closed_before_any_write(self):
        current = state(malformed=True)
        transport = FakeTransport(responses_for(current))

        with self.assertRaises(live.LivePolicyError) as raised:
            with patch.object(live, "build_opener", lambda *_args: transport), patch.object(live, "UrllibTransport", type(transport)):
                live.reconcile_live(
                    inventory(),
                    credential_callback=lambda: "fixture-value",
                    transport=transport,
                    apply=True,
                    confirmation=live.APPLY_CONFIRMATION,
                )

        self.assertEqual(str(raised.exception), "malformed Pi-hole state")
        self.assertFalse(any(call["method"] in {"PUT", "PATCH", "DELETE"} for call in transport.calls))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_transport_errors_are_sanitized_and_do_not_retain_context(self):
        class FailingTransport(FakeTransport):
            def request(self, method, path, *, payload=None, headers=None):
                if path == "/api/auth":
                    raise RuntimeError("synthetic transport failure")
                return super().request(method, path, payload=payload, headers=headers)

        with self.assertRaises(live.LivePolicyError) as raised:
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=FailingTransport({}),
            )

        self.assertEqual(str(raised.exception), "Pi-hole API request failed")
        self.assertNotIn("password", str(raised.exception))
        self.assertNotIn("synthetic transport failure", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_only_owned_stale_objects_are_delete_candidates_and_unmanaged_are_preserved(self):
        plan = make_plan(inventory(), state(stale=True))
        serialized = json.dumps(plan, sort_keys=True)

        self.assertIn({"action": "delete", "family": "groups", "managed": True}, plan["actions"])
        self.assertEqual(plan["actions"].count({"action": "delete", "family": "groups", "managed": True}), 1)
        self.assertIn({"family": "groups", "managed": False, "reason": "unmanaged-object"}, plan["preserved"])
        self.assertNotIn("obsolete", serialized)
        self.assertNotIn("device-a.example", serialized)

    def test_nullable_v6_comments_are_valid_unmanaged_state(self):
        current = state()
        current["groups"][2]["comment"] = None
        plan = make_plan(inventory(), current)
        self.assertIn({"family": "groups", "managed": False, "reason": "unmanaged-object"}, plan["preserved"])

    def test_mutation_payloads_exclude_read_only_and_identifier_fields(self):
        current = state()
        current["groups"] = []
        current["lists"] = []
        current["clients"] = []
        transport = StatefulTransport(responses_for(current), current)
        transport.responses.update({
            ("POST", "/api/groups"): {"groups": [{"id": 101}]},
            ("POST", "/api/clients"): {"clients": [{"id": 103}]},
        })

        with patch.object(live, "build_opener", lambda *_args: transport), patch.object(live, "UrllibTransport", type(transport)):
            live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
                apply=True,
                confirmation=live.APPLY_CONFIRMATION,
            )

        writes = [call for call in transport.calls if call["method"] in {"POST", "PUT", "PATCH", "DELETE"}]
        self.assertTrue(writes)
        for call in writes:
            payload = call["payload"]
            if isinstance(payload, dict):
                self.assertNotIn("id", payload)
                self.assertNotIn("date_added", payload)
                self.assertNotIn("identifier", payload)
                self.assertNotIn("client", payload) if call["path"] != "/api/clients" else None
                if call["path"] != "/api/auth":
                    self.assertIn("owner=shared-pihole-policy", json.dumps(payload, sort_keys=True))

    def test_successful_apply_performs_readback_verification(self):
        current = state()
        current["groups"] = []
        current["lists"] = []
        current["clients"] = []
        transport = StatefulTransport(responses_for(current), current)
        transport.responses.update({
            ("POST", "/api/groups"): {"groups": [{"id": 101}]},
            ("POST", "/api/clients"): {"clients": [{"id": 103}]},
        })

        calls = {"reads": 0}
        original = transport.request

        def request(method, path, *, payload=None, headers=None):
            if method == "GET":
                calls["reads"] += 1
            return original(method, path, payload=payload, headers=headers)

        transport.request = request
        with patch.object(live, "build_opener", lambda *_args: transport), patch.object(live, "UrllibTransport", type(transport)):
            result = live.reconcile_live(
                inventory(),
                credential_callback=lambda: "fixture-value",
                transport=transport,
                apply=True,
                confirmation=live.APPLY_CONFIRMATION,
            )

        self.assertTrue(result["apply"])
        self.assertTrue(result["verified"])
        self.assertGreaterEqual(calls["reads"], 10)
        first_write = next(index for index, call in enumerate(transport.calls) if call["method"] in {"POST", "PUT", "PATCH", "DELETE"} and call["path"] != "/api/auth")
        self.assertTrue(all(call["method"] == "GET" for call in transport.calls[1:first_write]))


    def test_direct_transport_mutations_require_apply_capability(self):
        transport = live.UrllibTransport("https://pihole.test")
        with self.assertRaises(live.LivePolicyError) as raised:
            transport.request("DELETE", "/api/groups/1")
        self.assertEqual(str(raised.exception), "policy mutations require reconcile_live apply gate")
        with self.assertRaises(live.LivePolicyError) as raised:
            transport._request_json("DELETE", "/api/groups/1")
        self.assertEqual(str(raised.exception), "policy mutations require reconcile_live apply gate")

        fake = FakeTransport({})
        with self.assertRaises(live.LivePolicyError) as raised:
            live._request(fake, "DELETE", "/api/groups/1")
        self.assertEqual(str(raised.exception), "policy mutations require reconcile_live apply gate")
        self.assertEqual(fake.calls, [])

    def test_v6_base64_sid_is_accepted(self):
        transport = FakeTransport({("POST", "/api/auth"): {"session": {"valid": True, "sid": "vFA+EP4MQ5JJvJg+3Q2Jnw="}, "took": 0.001}})
        self.assertEqual(live._authenticate(transport, lambda: "fixture-value"), "vFA+EP4MQ5JJvJg+3Q2Jnw=")


    def test_unknown_collection_envelope_fields_fail_closed(self):
        current = state()
        current["groups"] = {"groups": current["groups"], "took": 0.001, "unexpected": True}
        with self.assertRaises(live.LivePolicyError):
            make_plan(inventory(), current)


    def test_origin_and_serialization_failures_have_no_exception_context(self):
        with self.assertRaises(live.LivePolicyError) as origin_error:
            live.validate_origin("https://host:secret-port")
        self.assertIsNone(origin_error.exception.__context__)

        transport = live.UrllibTransport("https://pihole.test")
        try:
            with self.assertRaises(live.LivePolicyError) as serialization_error:
                transport._request_json("GET", "/api/groups", payload=object())
        finally:
            pass
        self.assertEqual(str(serialization_error.exception), "invalid Pi-hole API request")
        self.assertIsNone(serialization_error.exception.__context__)

    def test_origin_and_endpoint_guards_fail_closed(self):
        self.assertEqual(live.validate_origin("https://pihole.test"), "https://pihole.test")
        self.assertEqual(live.validate_origin("http://127.0.0.1", allow_private_http=True), "http://127.0.0.1")
        for origin in ("http://pihole.test", "https://pihole.test:bad", "https://host:", "https://host?", "https://host#", "https://host..", "https://-bad.example", "https://bad-.example", "https://pihole.test/api", "https://pihole.test?secret=value", "https://pihole.test "):
            with self.subTest(origin=origin), self.assertRaises(live.LivePolicyError):
                live.validate_origin(origin)
        transport = live.UrllibTransport("https://pihole.test")
        with self.assertRaises(AttributeError):
            setattr(transport, "origin", "https://attacker.test")
        with self.assertRaises(AttributeError):
            transport._origin = "https://attacker.test"
        with self.assertRaises(live.LivePolicyError):
            transport.request("GET", "/api/actions/delete")
        with self.assertRaises(live.LivePolicyError):
            transport.request("PATCH", "/api/config", payload={"dns": {}})

    def test_plan_is_deterministic_when_live_collections_are_reordered(self):
        first = state(stale=True)
        second = copy.deepcopy(first)
        second["groups"] = list(reversed(second["groups"]))
        second["lists"] = list(reversed(second["lists"]))
        second["clients"] = list(reversed(second["clients"]))
        self.assertEqual(make_plan(inventory(), first), make_plan(inventory(), second))


if __name__ == "__main__":
    unittest.main()
