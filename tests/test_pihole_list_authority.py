import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "modules" / "pihole" / "default.nix").read_text()
LIVE = (ROOT / "scripts" / "pihole" / "live_reconcile.py").read_text()
DEPLOY = (ROOT / "scripts" / "pihole" / "deploy.py").read_text()


class PiHoleListAuthorityContractTests(unittest.TestCase):
    def test_setup_reconciler_is_exact_and_retryable(self):
        self.assertIn("lists:batchDelete", MODULE)
        self.assertIn("desired_signature", MODULE)
        self.assertIn("[ ! -e \"$pending_marker\" ]", MODULE)
        self.assertIn("install -D -m 0600", MODULE)
        self.assertIn("$pihole -g", MODULE)
        self.assertIn("$rm -f \"$pending_marker\"", MODULE)

    def test_local_lists_are_validated_before_api_reconciliation(self):
        preflight = MODULE.index("Validate local file-backed lists")
        api = MODULE.index("LoginAPI")
        self.assertLess(preflight, api)
        self.assertIn("[ ! -f \"$file_path\" ]", MODULE)
        self.assertIn("[ ! -s \"$file_path\" ]", MODULE)

    def test_live_policy_reconciler_has_no_adlist_api_surface(self):
        self.assertNotIn("/api/lists", LIVE)
        self.assertNotIn("_same_list", LIVE)
        self.assertIn('adlists"] == []', LIVE)

    def test_setup_timeout_covers_gravity(self):
        self.assertIn("SETUP_TIMEOUT = 300", DEPLOY)
        self.assertIn("timeout=SETUP_TIMEOUT", DEPLOY)


if __name__ == "__main__":
    unittest.main()
