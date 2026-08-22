import unittest
from unittest.mock import patch

from scripts.pihole import policy_reconcile as policy


class PiHolePolicyReconcileAdlistTests(unittest.TestCase):
    def test_legacy_baseline_is_discarded(self):
        baseline = {
            "base": {
                "upstreams": ["192.168.86.1"],
                "interface": "eth0",
                "queryLogging": True,
                "retention": 91,
            },
            "adlists": {
                "standard": [{
                    "address": "file:///var/lib/pihole/baseline.hosts",
                    "enabled": True,
                    "description": "Shared Pi-hole baseline adlist",
                }],
                "kids": [],
            },
            "groups": {
                "normal": {"description": "Normal clients"},
                "kids": {"description": "Kids clients"},
            },
            "groupAssignments": {},
            "clients": [],
            "localDns": [],
            "rules": {"allow": [], "block": []},
        }
        # Live reconciliation must not see the baseline as an authoritative
        # adlist. services.pihole-ftl.lists is the sole owner.
        result = policy._normalize_adlists(baseline["adlists"])
        self.assertEqual(result, [])

    def test_empty_adlists_pass_through(self):
        self.assertEqual(
            policy._normalize_adlists({"standard": [], "kids": []}), []
        )

    def test_unsupported_kinds_are_rejected(self):
        with self.assertRaises(policy.PolicyError):
            policy._normalize_adlists({"standard": [], "kids": [], "extra": []})


if __name__ == "__main__":
    unittest.main()
