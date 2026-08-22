import json
import unittest

from scripts.pihole import live_dry_run as orchestrator


class SopsResolutionTests(unittest.TestCase):
    def test_resolve_sops_uses_path_when_available(self):
        from scripts.pihole import live_dry_run as orchestrator
        import unittest.mock as mock
        with mock.patch.object(orchestrator.shutil, "which", return_value=None), \
             mock.patch.object(orchestrator.os.path, "exists", return_value=True), \
             mock.patch.object(orchestrator.glob, "glob",
                               return_value=["/nix/store/abc-sops-3.13.2/bin/sops"]):
            self.assertEqual(
                orchestrator._resolve_sops(),
                "/nix/store/abc-sops-3.13.2/bin/sops",
            )

    def test_resolve_sops_prefers_path_env(self):
        from scripts.pihole import live_dry_run as orchestrator
        import unittest.mock as mock
        with mock.patch.object(orchestrator.shutil, "which",
                              return_value="/usr/bin/sops"):
            self.assertEqual(orchestrator._resolve_sops(), "/usr/bin/sops")

    def test_resolve_sops_raises_when_unavailable(self):
        from scripts.pihole import live_dry_run as orchestrator
        import unittest.mock as mock
        with mock.patch.object(orchestrator.shutil, "which", return_value=None), \
             mock.patch.object(orchestrator.glob, "glob", return_value=[]):
            with self.assertRaises(orchestrator.OrchestratorError):
                orchestrator._resolve_sops()


class OriginValidationTests(unittest.TestCase):
    def test_accepts_loopback_http(self):
        origin, allow = orchestrator._validate_origin("http://127.0.0.1:80")
        self.assertEqual(origin, "http://127.0.0.1:80")
        self.assertTrue(allow)

    def test_accepts_loopback_https(self):
        origin, allow = orchestrator._validate_origin("https://127.0.0.1")
        self.assertEqual(origin, "https://127.0.0.1")
        self.assertFalse(allow)

    def test_rejects_remote_origin(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_origin("http://192.168.86.1")

    def test_rejects_attacker_origin(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_origin("http://attacker.example/api")

    def test_rejects_path_suffix(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_origin("http://127.0.0.1/api")

    def test_rejects_unsupported_scheme(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_origin("file:///etc/passwd")

    def test_rejects_garbage(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_origin("")


class SshHostValidationTests(unittest.TestCase):
    def test_accepts_root_at_host(self):
        self.assertEqual(orchestrator._validate_ssh_host("root@pihole1", target="pihole1"), "root@pihole1")

    def test_rejects_hostname_mismatch(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_ssh_host("root@attacker.example", target="pihole1")

    def test_rejects_double_at(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_ssh_host("a@b@c", target="pihole1")

    def test_rejects_whitespace(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_ssh_host("root @pihole1", target="pihole1")

    def test_rejects_empty(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_ssh_host("", target="pihole1")


class RemotePathValidationTests(unittest.TestCase):
    def test_accepts_absolute(self):
        self.assertEqual(orchestrator._validate_remote_path("/var/lib/pihole/live_dry_run_remote.py"), "/var/lib/pihole/live_dry_run_remote.py")

    def test_rejects_relative(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_remote_path("scripts/pihole/live_dry_run_remote.py")

    def test_rejects_traversal(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_remote_path("/var/lib/../etc/passwd")

    def test_rejects_shell_metacharacters(self):
        for path in ("/tmp/a;rm -rf /", "/tmp/a|curl evil", "/tmp/a&curl evil", "/tmp/a$(whoami)", "/tmp/`whoami`", "/tmp/a>out", "/tmp/a<in", "/tmp/a\nb"):
            with self.subTest(path=path):
                with self.assertRaises(orchestrator.OrchestratorError):
                    orchestrator._validate_remote_path(path)


class SafeKeysTests(unittest.TestCase):
    def test_rejects_unsafe_keys(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._safe_keys({"good": "ok", "bad key with space": "nope"})

    def test_rejects_unsafe_nested_keys(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._safe_keys({"good": [{"nested": "ok"}, {"bad key": "nope"}]})

    def test_accepts_safe_payload(self):
        orchestrator._safe_keys({"target": "pihole1", "ssh_host": "root@pihole1"})


class PasswordPathValidationTests(unittest.TestCase):
    def test_accepts_absolute(self):
        self.assertEqual(orchestrator._validate_password_path("/run/secrets.d/1/pihole-api-password"), "/run/secrets.d/1/pihole-api-password")

    def test_rejects_relative(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._validate_password_path("secrets/pihole-api-password")


class IdentityYamlParserTests(unittest.TestCase):
    def test_parses_identity_mapping(self):
        text = (
            "identities:\n"
            "    identityRef:alpha:\n"
            "        mac: 88:49:2d:42:92:8c\n"
            "    identityRef:beta:\n"
            "        mac: 9c:8e:cd:2f:67:16\n"
        )
        identities = orchestrator._parse_identity_yaml(text)
        self.assertEqual(set(identities), {"alpha", "beta"})
        self.assertEqual(identities["alpha"]["mac"], "88:49:2d:42:92:8c")
        self.assertEqual(identities["beta"]["mac"], "9c:8e:cd:2f:67:16")

    def test_rejects_unrelated_payload(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator._parse_identity_yaml("just plain text without any identities block")


if __name__ == "__main__":
    unittest.main()
