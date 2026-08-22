import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.dns_migration import render_inventory
from scripts.pihole import policy_reconcile as policy


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "dns_migration" / "pihole-policy.json"
STALE_STATE = ROOT / "tests" / "fixtures" / "dns_migration" / "pihole-policy-state-stale.json"
UNMANAGED_STATE = ROOT / "tests" / "fixtures" / "dns_migration" / "pihole-policy-state-unmanaged.json"
INVALID_STATE = ROOT / "tests" / "fixtures" / "dns_migration" / "pihole-policy-state-invalid.json"
BASE = {
    "upstreams": ["192.168.86.1#5353"],
    "listeningInterfaces": ["eth0"],
    "queryLogging": True,
    "retention": 91,
}


class PiHolePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input_data = json.loads(FIXTURE.read_text())

    def empty_state(self):
        return {
            "base": copy.deepcopy(BASE),
            "adlists": [],
            "groups": [],
            "clients": [],
            "localDns": [],
            "rules": [],
        }

    def test_render_is_deterministic_and_fingerprint_is_stable(self):
        first = policy.render_policy(copy.deepcopy(self.input_data), "pihole1")
        second = policy.render_policy(copy.deepcopy(self.input_data), "pihole2")

        self.assertEqual(first["policyRevision"], second["policyRevision"])
        self.assertEqual(first["managedObjectFingerprint"], second["managedObjectFingerprint"])
        self.assertEqual(first["desired"], second["desired"])
        self.assertEqual(first["apply"], False)
        self.assertEqual(first["target"], "pihole1")
        encoded = json.dumps(first, sort_keys=True)
        self.assertNotIn("fixture-client-alpha", encoded)
        self.assertNotIn("fixture-client-beta", encoded)
        self.assertNotIn("192.168.86.10", encoded)

    def test_public_normalizer_requires_the_complete_frozen_baseline(self):
        for section in ("base", "adlists", "groups", "groupAssignments", "localDns", "rules"):
            data = copy.deepcopy(self.input_data)
            data["policy"].pop(section)
            with self.subTest(section=section), self.assertRaises(policy.PolicyError):
                policy.render_policy(data, "pihole1")

        mutations = [
            ("base unsupported field", lambda d: d["policy"]["base"].update({"safeSearch": True})),
            ("base drift", lambda d: d["policy"]["base"].update({"retention": 90})),
            ("new adlist URL", lambda d: d["policy"]["adlists"]["standard"].append({"address": "https://new.invalid/list", "enabled": True, "description": "new"})),
            ("kids adlist", lambda d: d["policy"]["adlists"].update({"kids": [{"address": "file:///kids", "enabled": True, "description": "kids"}]})),
            ("extra group", lambda d: d["policy"]["groups"].update({"guest": {"description": "guest"}})),
            ("arbitrary rule", lambda d: d["policy"]["rules"].update({"allow": ["example.invalid"]})),
            ("local DNS record", lambda d: d["policy"].update({"localDns": [{"hostname": "router", "domain": "lan"}]})),
            ("unsupported policy field", lambda d: d["policy"].update({"actions": []})),
        ]
        for label, mutate in mutations:
            data = copy.deepcopy(self.input_data)
            mutate(data)
            with self.subTest(label=label), self.assertRaises(policy.PolicyError):
                policy.render_policy(data, "pihole1")

    def test_empty_adlists_are_valid_when_pi_hole_module_owns_lists(self):
        data = copy.deepcopy(self.input_data)
        data["policy"]["adlists"] = {"standard": [], "kids": []}
        rendered = policy.render_policy(data, "pihole1")
        self.assertEqual(rendered["desired"]["adlists"], [])

    def test_identity_resolution_is_fail_closed_and_identifiers_are_not_rendered(self):
        for label, mutate in [
            ("required flag", lambda d: d.update({"identityResolutionRequired": True})),
            ("unresolved list", lambda d: d.update({"unresolvedIdentityRefs": ["lan-test-server"]})),
            ("alternate status", lambda d: d["piholeClients"][0].update({"status": "unresolved"})),
            ("empty ref", lambda d: d["piholeClients"][0].update({"clientRef": ""})),
            ("malformed ref", lambda d: d["piholeClients"][0].update({"clientRef": "identityRef:bad_ref"})),
            ("short ref token", lambda d: d["piholeClients"][0].update({"clientRef": "identityRef:a"})),
            ("empty identifier", lambda d: d["piholeClients"][0].update({"identifier": ""})),
            ("missing identifier", lambda d: d["piholeClients"][0].pop("identifier")),
        ]:
            data = copy.deepcopy(self.input_data)
            mutate(data)
            with self.subTest(label=label), self.assertRaises(policy.PolicyError):
                policy.render_policy(data, "pihole1")

        rendered = policy.render_policy(self.input_data, "pihole1")
        rendered_text = json.dumps(rendered, sort_keys=True)
        self.assertNotIn("fixture-client-alpha", rendered_text)
        self.assertNotIn("fixture-client-beta", rendered_text)

    def test_group_assignments_must_match_resolved_clients_exactly(self):
        for label, mutate in [
            ("missing assignment", lambda d: d["policy"]["groupAssignments"].pop("identityRef:lan-test-server")),
            ("unknown assignment", lambda d: d["policy"]["groupAssignments"].update({"identityRef:unknown": "normal"})),
            ("wrong assignment", lambda d: d["policy"]["groupAssignments"].update({"identityRef:lan-test-server": "kids"})),
        ]:
            data = copy.deepcopy(self.input_data)
            mutate(data)
            with self.subTest(label=label), self.assertRaises(policy.PolicyError):
                policy.render_policy(data, "pihole1")

    def test_real_rendered_inventory_fails_closed_on_unresolved_identity(self):
        inventory_path = ROOT / "inventory" / "default.nix"
        rendered = render_inventory.render(render_inventory.load_source(inventory_path, None))

        with self.assertRaisesRegex(policy.PolicyError, "unresolved identity references"):
            policy.render_policy(rendered, "pihole1")

    def test_base_drift_is_an_update_and_exact_base_state_is_accepted(self):
        observed = self.empty_state()
        exact = policy.render_policy(self.input_data, "pihole1", observed)
        self.assertFalse(any(item["family"] == "base" for item in exact["reconciliation"]["update"]))

        observed["base"]["retention"] = 90
        drifted = policy.render_policy(self.input_data, "pihole1", observed)
        self.assertIn({"family": "base", "key": "base", "managed": True}, drifted["reconciliation"]["update"])

    def test_stale_managed_objects_are_delete_candidates(self):
        observed = policy.load_json(STALE_STATE)
        rendered = policy.render_policy(self.input_data, "pihole1", observed)

        self.assertTrue(any(item["key"] == "group:obsolete" for item in rendered["reconciliation"]["delete"]))

    def test_unmanaged_objects_are_preserved(self):
        observed = policy.load_json(UNMANAGED_STATE)
        rendered = policy.render_policy(self.input_data, "pihole2", observed)

        preserved = rendered["reconciliation"]["preserveUnmanaged"]
        self.assertTrue(any(item["key"] == "group:family-custom" for item in preserved))
        self.assertFalse(any(item["key"] == "group:family-custom" for item in rendered["reconciliation"]["delete"]))

    def test_optional_observed_effective_fields_do_not_create_false_updates(self):
        desired = policy.render_policy(self.input_data, "pihole1")["desired"]
        observed = self.empty_state()
        observed["groups"] = [
            {
                "name": item["name"],
                "description": item["description"],
                "enabled": True,
                "managed": True,
                "owner": policy.OWNER,
            }
            for item in desired["groups"]
        ]
        observed["adlists"] = [
            {
                "address": item["address"],
                "description": item["description"],
                "enabled": item["enabled"],
                "type": "domain",
                "managed": True,
                "owner": policy.OWNER,
            }
            for item in desired["adlists"]
        ]
        observed["clients"] = [
            {
                "identifier": source["identifier"],
                "group": source["group"],
                "comment": "effective comment",
                "managed": True,
                "owner": policy.OWNER,
            }
            for source in self.input_data["piholeClients"]
        ]

        rendered = policy.render_policy(self.input_data, "pihole1", observed)
        self.assertEqual(rendered["reconciliation"]["update"], [])

        desired_with_rule = copy.deepcopy(desired)
        desired_with_rule["rules"] = [{
            "key": "rule:block:" + policy._digest("example.invalid"),
            "kind": "block",
            "value": "example.invalid",
            "managed": True,
            "owner": policy.OWNER,
        }]
        observed_with_rule = self.empty_state()
        observed_with_rule["rules"] = [{
            "kind": "block",
            "value": "example.invalid",
            "domain": "example.invalid",
            "enabled": True,
            "managed": True,
            "owner": policy.OWNER,
        }]
        plan = policy._reconciliation(desired_with_rule, observed_with_rule)
        self.assertEqual(plan["update"], [])

    def test_rule_kind_and_value_are_validated_before_indexing(self):
        malformed_rules = [
            {"managed": False, "owner": "local-admin", "value": "example.invalid"},
            {"managed": False, "owner": "local-admin", "kind": "block"},
            {"managed": False, "owner": "local-admin", "kind": ["block"], "value": "example.invalid"},
            {"managed": False, "owner": "local-admin", "kind": "block", "value": {"domain": "example.invalid"}},
        ]
        for rule in malformed_rules:
            state = self.empty_state()
            state["rules"] = [rule]
            with self.subTest(rule=rule), self.assertRaises(policy.PolicyError):
                policy.render_policy(self.input_data, "pihole1", state)

    def test_validate_target_rejects_non_string_direct_calls(self):
        for target in ([], {}, None, 1):
            with self.subTest(target=target), self.assertRaises(policy.PolicyError):
                policy._validate_target(target)

    def test_observed_state_is_complete_and_malformed_managed_state_fails_closed(self):
        partial = policy.load_json(INVALID_STATE)
        with self.assertRaises(policy.PolicyError):
            policy.render_policy(self.input_data, "pihole1", partial)

        for state in (
            self.empty_state() | {"base": {}},
            {family: [] for family in ("adlists", "groups", "clients", "localDns", "rules")},
        ):
            with self.subTest(state=state), self.assertRaises(policy.PolicyError):
                policy.render_policy(self.input_data, "pihole1", state)

        malformed = self.empty_state()
        malformed["groups"] = [{"name": "normal", "managed": True, "owner": policy.OWNER}]
        with self.assertRaises(policy.PolicyError):
            policy.render_policy(self.input_data, "pihole1", malformed)

        duplicate = self.empty_state()
        duplicate["groups"] = [
            {"name": "normal", "description": "Normal clients", "managed": True, "owner": policy.OWNER},
            {"name": "normal", "description": "Normal clients", "managed": False, "owner": "local-admin"},
        ]
        with self.assertRaises(policy.PolicyError):
            policy.render_policy(self.input_data, "pihole1", duplicate)

        missing_marker = self.empty_state()
        missing_marker["groups"] = [{"name": "normal", "description": "Normal clients", "owner": policy.OWNER}]
        with self.assertRaises(policy.PolicyError):
            policy.render_policy(self.input_data, "pihole1", missing_marker)

    def test_load_json_errors_have_no_recoverable_exception_context(self):
        marker = "hostile-path-marker"
        with self.assertRaises(policy.PolicyError) as missing:
            policy.load_json(Path(f"/definitely/missing/{marker}.json"))
        self.assertIsNone(missing.exception.__cause__)
        self.assertIsNone(missing.exception.__context__)
        self.assertNotIn(marker, str(missing.exception))

        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.json"
            malformed.write_text('{"marker": ' + marker)
            with self.assertRaises(policy.PolicyError) as invalid:
                policy.load_json(malformed)
        self.assertIsNone(invalid.exception.__cause__)
        self.assertIsNone(invalid.exception.__context__)
        self.assertNotIn(marker, str(invalid.exception))

    def test_cli_output_write_errors_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "missing-parent" / "output.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = policy.main([
                    "--inventory-json", str(FIXTURE),
                    "--target", "pihole1",
                    "--output", str(output),
                ])
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "Pi-hole policy render failed: unable to write JSON policy output\n")

    def test_cli_requires_explicit_target_and_stays_dry_run(self):
        parser = policy.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--inventory-json", str(FIXTURE)])
        args = parser.parse_args(["--inventory-json", str(FIXTURE), "--target", "pihole1"])
        self.assertTrue(args.dry_run)
        self.assertEqual(policy.main(["--inventory-json", str(FIXTURE), "--target", "pihole1", "--apply"]), 2)


if __name__ == "__main__":
    unittest.main()
