import copy
import json
import unittest

from scripts.pihole import live_adapter
from scripts.pihole import live_reconcile as live


_FIXTURE_PATH = "tests/fixtures/dns_migration/pihole-policy.json"


def _rendered():
    data = json.loads(open(_FIXTURE_PATH).read())
    from scripts.pihole import policy_reconcile as policy
    return policy.render_policy(copy.deepcopy(data), "pihole1")


class LiveAdapterTests(unittest.TestCase):
    def test_adapt_produces_shape_accepted_by_live_reconcile(self):
        adapted = live_adapter.adapt(_rendered())
        live._desired(adapted)

    def test_adapt_uses_opaque_client_key_as_live_identifier(self):
        adapted = live_adapter.adapt(_rendered())
        for client in adapted["clients"]:
            self.assertRegex(client["identifier"], r"^client:[0-9a-f]{64}$")

    def test_adapt_does_not_leak_raw_mac_into_live_shape(self):
        rendered = _rendered()
        adapted = live_adapter.adapt(rendered)
        serialised = json.dumps(adapted, sort_keys=True)
        self.assertNotIn("fixture-client-alpha", serialised)
        self.assertNotIn("fixture-client-beta", serialised)
        self.assertNotIn("192.168.86.10", serialised)
        self.assertNotIn("192.168.86.11", serialised)

    def test_adapt_emits_one_blocklist_adlist(self):
        adapted = live_adapter.adapt(_rendered())
        self.assertEqual(len(adapted["adlists"]), 1)
        self.assertEqual(adapted["adlists"][0]["type"], "block")
        self.assertTrue(adapted["adlists"][0]["address"].startswith("file:///"))

    def test_adapt_emits_two_groups_with_expected_names(self):
        adapted = live_adapter.adapt(_rendered())
        names = sorted(group["name"] for group in adapted["groups"])
        self.assertEqual(names, ["kids", "normal"])

    def test_adapt_rules_are_empty_for_baseline(self):
        adapted = live_adapter.adapt(_rendered())
        self.assertEqual(adapted["rules"], {"allow": [], "block": []})

    def test_adapt_local_dns_is_empty_for_baseline(self):
        adapted = live_adapter.adapt(_rendered())
        self.assertEqual(adapted["localDns"], [])

    def test_adapt_base_carries_current_upstream(self):
        adapted = live_adapter.adapt(_rendered())
        self.assertEqual(adapted["base"]["dns"]["upstreams"], ["192.168.86.1#5353"])
        self.assertEqual(adapted["base"]["dns"]["interface"], "eth0")
        self.assertEqual(adapted["base"]["dns"]["queryLogging"], True)
        self.assertEqual(adapted["base"]["database"]["maxDBdays"], 91)

    def test_adapt_rejects_unknown_adlist_type(self):
        rendered = _rendered()
        for entry in rendered["desired"]["adlists"]:
            entry["type"] = "bogus"
        with self.assertRaises(live_adapter.LiveAdapterError):
            live_adapter.adapt(rendered)

    def test_adapt_rejects_empty_local_dns_entries(self):
        rendered = _rendered()
        rendered["desired"]["localDns"] = [{"hostname": "router", "domain": "lan"}]
        with self.assertRaises(live_adapter.LiveAdapterError):
            live_adapter.adapt(rendered)

    def test_adapt_rejects_duplicate_clients(self):
        rendered = _rendered()
        first = rendered["desired"]["clients"][0]
        rendered["desired"]["clients"].append(copy.deepcopy(first))
        with self.assertRaises(live_adapter.LiveAdapterError):
            live_adapter.adapt(rendered)


_UNRESOLVED_INVENTORY = {
    "schemaVersion": 1,
    "apply": False,
    "identityResolutionRequired": True,
    "unresolvedIdentityRefs": ["alpha", "beta"],
    "piholeClients": [
        {
            "clientRef": "identityRef:alpha",
            "identifier": None,
            "status": "pending-encrypted-identity-resolution",
            "device": "alpha-device",
            "hostname": "alpha",
            "address": "192.168.86.10",
            "group": "normal",
        },
        {
            "clientRef": "identityRef:beta",
            "identifier": None,
            "status": "pending-encrypted-identity-resolution",
            "device": "beta-device",
            "hostname": "beta",
            "address": "192.168.86.11",
            "group": "kids",
        },
    ],
    "policy": {
        "base": {
            "upstreams": ["192.168.86.1#5353"],
            "listeningInterfaces": ["eth0"],
            "queryLogging": True,
            "retention": 91,
        },
        "adlists": {
            "standard": [{"address": "file:///var/lib/pihole/baseline.hosts", "enabled": True, "description": "Shared Pi-hole baseline adlist"}],
            "kids": [],
        },
        "groups": {
            "normal": {"description": "Normal clients"},
            "kids": {"description": "Kids clients"},
        },
        "groupAssignments": {},
        "localDns": [],
        "rules": {"allow": [], "block": []},
    },
}


class ResolveIdentitiesTests(unittest.TestCase):
    def test_resolve_identities_replaces_placeholder_with_mac(self):
        identities = {
            "alpha": {"mac": "88:49:2d:42:92:8c"},
            "beta": {"mac": "9c:8e:cd:2f:67:16"},
        }
        resolved = live_adapter.resolve_identities(copy.deepcopy(_UNRESOLVED_INVENTORY), identities)
        self.assertFalse(resolved["identityResolutionRequired"])
        self.assertEqual(resolved["unresolvedIdentityRefs"], [])
        clients = {client["clientRef"]: client for client in resolved["piholeClients"]}
        self.assertEqual(clients["identityRef:alpha"]["identifier"], "88:49:2d:42:92:8c")
        self.assertEqual(clients["identityRef:beta"]["identifier"], "9c:8e:cd:2f:67:16")
        self.assertEqual(clients["identityRef:alpha"]["status"], "resolved")
        self.assertEqual(clients["identityRef:beta"]["status"], "resolved")

    def test_resolve_identities_populates_group_assignments(self):
        identities = {"alpha": {"mac": "88:49:2d:42:92:8c"}, "beta": {"mac": "9c:8e:cd:2f:67:16"}}
        resolved = live_adapter.resolve_identities(copy.deepcopy(_UNRESOLVED_INVENTORY), identities)
        self.assertEqual(resolved["policy"]["groupAssignments"]["identityRef:alpha"], "normal")
        self.assertEqual(resolved["policy"]["groupAssignments"]["identityRef:beta"], "kids")

    def test_resolve_identities_rejects_missing_ref(self):
        identities = {"alpha": {"mac": "88:49:2d:42:92:8c"}}
        with self.assertRaises(live_adapter.LiveAdapterError):
            live_adapter.resolve_identities(copy.deepcopy(_UNRESOLVED_INVENTORY), identities)

    def test_resolve_identities_rejects_duplicate_macs(self):
        identities = {
            "alpha": {"mac": "88:49:2d:42:92:8c"},
            "beta": {"mac": "88:49:2d:42:92:8c"},
        }
        with self.assertRaises(live_adapter.LiveAdapterError):
            live_adapter.resolve_identities(copy.deepcopy(_UNRESOLVED_INVENTORY), identities)

    def test_resolve_identities_produces_renderable_policy(self):
        identities = {
            "alpha": {"mac": "88:49:2d:42:92:8c"},
            "beta": {"mac": "9c:8e:cd:2f:67:16"},
        }
        resolved = live_adapter.resolve_identities(copy.deepcopy(_UNRESOLVED_INVENTORY), identities)
        from scripts.pihole import policy_reconcile as policy
        rendered = policy.render_policy(resolved, "pihole1")
        adapted = live_adapter.adapt(rendered)
        live._desired(adapted)


if __name__ == "__main__":
    unittest.main()
