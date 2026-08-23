"""Verify the generated Pi-hole setup script authenticates using the SOPS
API credential when one is mounted via the setup oneshot's EnvironmentFile,
and falls back to the upstream CLI password path otherwise.

The setup script cannot read /etc/pihole/cli_pw reliably when
FTLCONF_webserver_api_password is provided, so it must consult the runtime
credential first.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = "/tmp/pihole-setup-sops-cred.sh"


def _render_setup_script() -> str:
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


def _bash_syntax(path: str) -> None:
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr)


def _eval_setup_unit_envfile() -> str:
    result = subprocess.run(
        [
            "nix", "eval", "--json",
            "--extra-experimental-features", "nix-command flakes",
            "--impure",
            ".#nixosConfigurations.pihole1.config.systemd.services.pihole-ftl-setup.serviceConfig.EnvironmentFile",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class SopsCredentialAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script_text = _render_setup_script()
        _bash_syntax(SETUP_SCRIPT)

    def test_setup_prefers_sops_password_when_provided(self):
        # The script must inspect the SOPS-rendered API password first and
        # only fall back to upstream LoginAPI when the env var is empty.
        self.assertIn('${FTLCONF_webserver_api_password:-}', self.script_text)
        # It must POST to the API auth endpoint using the env-var password
        # rather than relying on the CLI password file. The bash fragment
        # uses ${API_URL}auth (with the variable interpolated by FTL's
        # TestAPIAvailability), so we assert the literal URL path is
        # concatenated against API_URL.
        self.assertIn('-X POST "${API_URL}auth"', self.script_text)

    def test_setup_encodes_password_via_jq_arg(self):
        # The password must NOT be interpolated raw into a JSON string
        # literal; that would let quotes/backslashes/control characters
        # in the SOPS credential inject JSON structure or break the body.
        # The safe path is jq -nc --arg p "$FTLCONF_webserver_api_password"
        # '{password: $p, totp: null}'.
        self.assertIn('--arg p "$FTLCONF_webserver_api_password"', self.script_text)
        self.assertIn("'{password: $p, totp: null}'", self.script_text)
        # Defence in depth: assert no raw interpolation of the password
        # variable into a JSON literal anywhere in the script.
        bad = re.search(r'"password"\s*:\s*"\$\{?FTLCONF', self.script_text)
        self.assertIsNone(
            bad,
            "raw password interpolation into JSON literal would allow "
            "credential-content injection",
        )

    def test_test_api_availability_runs_in_calling_shell(self):
        # The upstream `TestAPIAvailability` exits the shell on failure,
        # which would defeat the three-attempt retry loop and bypass our
        # SOPS auth branch. The setup script now defines its own
        # `checkPiHoleApiReady` helper that returns 0/1 cleanly.
        self.assertNotIn("(TestAPIAvailability)", self.script_text)
        self.assertIn("checkPiHoleApiReady() {", self.script_text)
        self.assertIn("if checkPiHoleApiReady; then", self.script_text)

    def test_setup_falls_back_to_loginapi_when_sops_password_missing(self):
        # The fallback to upstream LoginAPI is preserved so the disposable
        # loopback test path (no apiPasswordEnvironmentFile) still works.
        self.assertIn("LoginAPI", self.script_text)
        # The SOPS branch and the LoginAPI branch must be mutually exclusive
        # — the script picks one based on the env var, never both.
        sops_branch_label = "Pi-hole authentication failed using the SOPS API credential"
        self.assertIn(sops_branch_label, self.script_text)
        # Verify the else branch is wired to LoginAPI after the SOPS branch.
        after_sops = self.script_text.split(sops_branch_label, 1)[1]
        self.assertRegex(after_sops, re.compile(r'else\s*\n\s*LoginAPI'))

    def test_needauth_check_is_subshell_safe(self):
        # The SOPS auth branch must guard $needAuth with a default fallback
        # so an unset or empty variable (no upstream LoginAPI call yet) does
        # not make the condition trip on an empty string.
        self.assertIn('"${needAuth:-false}" = true', self.script_text)

    def test_setup_persists_sid_for_getftldata(self):
        # The SOPS auth path must export SID so subsequent GetFTLData /
        # PostFTLData calls reuse the same session.
        self.assertRegex(
            self.script_text,
            re.compile(
                r'SID=\$\(\$jq -r \'\.sid // empty\' <<< "\$auth_session"\).*\n\s*export SID validSession',
                re.DOTALL,
            ),
        )

    def test_setup_unit_mounts_api_password_envfile(self):
        # The setup oneshot must mount the same SOPS-rendered API
        # EnvironmentFile as pihole-ftl.service, otherwise the SOPS auth
        # branch never sees the credential.
        env_files = json.loads(_eval_setup_unit_envfile())
        self.assertEqual(env_files, ["/run/secrets/rendered/pihole-api-env"])

    def test_setup_script_remains_bash_parseable(self):
        # bash -n already passed in setUpClass; this is a deliberate
        # documentation assertion so the test name in CI is descriptive.
        _ = self._testMethodName  # noqa: F841 - keep the assertion reachable
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
