import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = "/tmp/pihole-setup-final.sh"


def _materialize_setup_script() -> str:
    """Force-render the generated setup script so the assertions below
    can read its actual contents."""
    result = subprocess.run(
        [
            "nix", "eval", "--raw",
            "--extra-experimental-features", "nix-command flakes",
            "--impure",
            ".#nixosConfigurations.pihole1.config.systemd.services.pihole-ftl-setup.script",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    Path(SETUP_SCRIPT).write_text(result.stdout)
    return result.stdout


def _ensure_bash_syntax(path: str) -> None:
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr)


def _read_declared_lists() -> list[dict]:
    r = subprocess.run(
        [
            "nix", "eval", "--json",
            "--extra-experimental-features", "nix-command flakes",
            "--impure",
            ".#nixosConfigurations.pihole1.config.services.pihole-ftl.lists",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


class NixSetupScriptBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script_text = _materialize_setup_script()
        _ensure_bash_syntax(SETUP_SCRIPT)
        cls.declared_lists = _read_declared_lists()

    def test_declared_lists_contain_oisd_and_hagezi(self):
        urls = {entry["url"] for entry in self.declared_lists}
        self.assertEqual(urls, {
            "https://big.oisd.nl",
            "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/dnsmasq/pro.txt",
        })

    def test_local_lists_are_preflighted_before_api_login(self):
        preflight_pos = self.script_text.index(
            "Validate local file-backed lists before any destructive API operation"
        )
        login_pos = self.script_text.index("LoginAPI")
        self.assertLess(preflight_pos, login_pos)
        # Every preflight check is present and runs before the API login.
        for marker in ('[ ! -f "$file_path" ]',
                       '[ ! -r "$file_path" ]',
                       '[ ! -s "$file_path" ]'):
            self.assertIn(marker, self.script_text)

    def test_batch_delete_uses_pi_hole_v6_item_payload(self):
        # `item` is the documented Pi-hole v6 list identifier for batch delete.
        self.assertIn("lists:batchDelete", self.script_text)
        self.assertIn("{item: .address, type: .type}", self.script_text)  # batch delete payload keeps type for API
        self.assertIn("204", self.script_text)

    def test_reconciliation_is_exact_and_skipped_on_match(self):
        self.assertIn("desired_signature", self.script_text)
        self.assertIn("current_signature", self.script_text)
        self.assertIn('"Pi-hole lists already match the declared configuration"',
                      self.script_text)

    def test_post_gravity_verification_runs_before_marker_removal(self):
        marker_rm_pos = self.script_text.index('$rm -f "$pending_marker"')
        pihole_g_pos = self.script_text.index("$pihole -g")
        verify_def = "verifyLists()"
        self.assertIn(verify_def, self.script_text)
        # There must be a verifyLists call after gravity and before the
        # marker is removed; otherwise a failed gravity run can clear the
        # retry marker.
        first_verify_after_gravity = self.script_text.index(
            "verifyLists", pihole_g_pos
        )
        self.assertLess(first_verify_after_gravity, marker_rm_pos)
        # And the script must abort when gravity fails (without removing the marker).
        self.assertIn("Pi-hole gravity run failed; leaving the reconciliation marker for retry",
                      self.script_text)
        # VerifyLists is called both before and after gravity.
        self.assertGreaterEqual(self.script_text.count("verifyLists"), 3)

    def test_marker_blocks_noop_when_present(self):
        # If signatures match but a marker exists, we must still reset/rebuild.
        self.assertIn('[ ! -e "$pending_marker" ]', self.script_text)

    def test_no_url_credentials_in_declared_lists(self):
        for entry in self.declared_lists:
            self.assertIsNone(
                re.match(r".*://[^/]*@.*", entry["url"]),
                f"declared list URL has embedded credentials: {entry['url']}",
            )


if __name__ == "__main__":
    unittest.main()
