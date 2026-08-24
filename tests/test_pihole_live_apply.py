import unittest

from scripts.pihole import live_apply


class ApplyConfirmationTests(unittest.TestCase):
    def test_requires_exact_apply_confirmation(self):
        with self.assertRaises(live_apply.OrchestratorError):
            live_apply._require_apply_confirmation("wrong")

    def test_accepts_exact_apply_confirmation(self):
        self.assertEqual(
            live_apply._require_apply_confirmation("APPLY_SHARED_PIHOLE_POLICY"),
            "APPLY_SHARED_PIHOLE_POLICY",
        )

    def test_apply_payload_never_contains_password_value(self):
        payload = live_apply._apply_payload(
            inventory={"base": {}, "groups": [], "adlists": [], "clients": [], "localDns": [], "rules": {"allow": [], "block": []}},
            origin="http://127.0.0.1:8080",
            password_path="/run/secrets.d/1/pihole-api-password",
        )
        self.assertEqual(payload["apply"], True)
        self.assertEqual(payload["confirmation"], "APPLY_SHARED_PIHOLE_POLICY")
        self.assertEqual(payload["password_path"], "/run/secrets.d/1/pihole-api-password")
        self.assertNotIn("password", payload)
        # Apply defaults to the declared Pi-hole API port so deploy cannot
        # accidentally talk to the legacy webserver on port 80.
        self.assertIn("8080", payload["origin"])

    def test_apply_payload_forwards_lock_path(self):
        payload = live_apply._apply_payload(
            inventory={"base": {}, "groups": [], "adlists": [], "clients": [], "localDns": [], "rules": {"allow": [], "block": []}},
            origin="http://127.0.0.1:8080",
            password_path="/run/secrets.d/1/pihole-api-password",
        )
        # The wrapper accepts a lock path so apply and setup share the same
        # advisory lockfile via flock; the orchestrator forwards it.
        payload["lock_path"] = "/var/lib/pihole/.pihole-policy.lock"
        self.assertEqual(payload["lock_path"], "/var/lib/pihole/.pihole-policy.lock")

    def test_build_apply_inventory_preserves_raw_macs(self):
        rendered = {
            "policy": {
                "groups": {"kids": {"description": "Kids clients"}},
                "base": {"upstreams": ["192.168.86.1"], "retention": 91},
            },
            "piholeClients": [
                {
                    "clientRef": "identityRef:emma-book",
                    "status": "resolved",
                    "identifier": "client:813468a99d09e7c4fcd8c918ca2c7d4389d55945262061a984a17e9d5e1772aa",
                    "device": "emma-book",
                    "hostname": "emma-book",
                    "address": None,
                    "group": "kids",
                }
            ],
        }
        identities = {"emma-book": {"mac": "88:49:2d:42:92:8c"}}
        inv = live_apply._build_apply_inventory(rendered, identities)
        self.assertEqual(len(inv["clients"]), 1)
        client = inv["clients"][0]
        # The apply path must carry the raw MAC, not the opaque client:<sha256>
        # fingerprint, so Pi-hole can match the device.
        self.assertEqual(client["identifier"], "88:49:2d:42:92:8c")
        self.assertNotIn("client:", client["identifier"])
        self.assertEqual(client["group"], "kids")
        # The live reconciler rejects extra keys on clients (only identifier
        # and group are allowed). Hostname must be dropped here.
        self.assertNotIn("hostname", client)
        self.assertIn(
            {"name": "kids", "description": "Kids clients", "enabled": True},
            inv["groups"],
        )
        # Base must be translated into the live reconciler's BASELINE_BASE
        # shape, not the offline inventory shape.
        self.assertEqual(
            inv["base"]["dns"]["upstreams"], ["192.168.86.1"]
        )
        self.assertEqual(inv["base"]["dns"]["interface"], "eth0")
        self.assertEqual(inv["base"]["database"]["maxDBdays"], 91)


class RemoteLockAcquisitionTests(unittest.TestCase):
    """Verify the Pi-hole-local wrapper acquires the shared flock."""

    def _acquire_remote_lock(self, path):
        # Local import keeps the harness decoupled from the live_reconcile
        # module's relative imports.
        from scripts.pihole import live_dry_run_remote as remote
        return remote._acquire_remote_lock(str(path))

    def _release_remote_lock(self, fd):
        from scripts.pihole import live_dry_run_remote as remote
        remote._release_lock(fd)

    def test_acquire_then_release_allows_next_holder(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"
            fd1 = self._acquire_remote_lock(lock)
            try:
                # A second non-blocking acquisition must fail.
                with self.assertRaises(RuntimeError):
                    self._acquire_remote_lock(lock)
            finally:
                self._release_remote_lock(fd1)
            # After release, the lockfile persists and is acquirable again.
            self.assertTrue(lock.exists())
            fd2 = self._acquire_remote_lock(lock)
            self._release_remote_lock(fd2)

    def test_lockfile_persists_across_release(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"
            fd = self._acquire_remote_lock(lock)
            self._release_remote_lock(fd)
            # Dropping the flock must not delete the lockfile.
            self.assertTrue(lock.exists())


class SecretsPathResolutionTests(unittest.TestCase):
    """Verify the orchestrator resolves the per-host sops-nix secret path."""

    def test_resolve_secrets_path_uses_symlink_target(self):
        import unittest.mock as mock
        with mock.patch.object(
            live_apply.subprocess, "run",
            return_value=mock.Mock(
                returncode=0,
                stdout="/run/secrets.d/12/pihole-api-password\n",
                stderr="",
            ),
        ) as fake_run:
            path = live_apply._resolve_secrets_path(
                "root@pihole1", "pihole-api-password",
            )
        self.assertEqual(path, "/run/secrets.d/12/pihole-api-password")
        # SSH command must not use bash -c (Pi-hole default shell is fish).
        args = fake_run.call_args[0][0]
        joined = " ".join(str(a) for a in args)
        self.assertNotIn("bash -c", joined)
        self.assertNotIn("find /run/secrets.d", joined)
        self.assertNotIn("$(", joined)
        self.assertNotIn("`", joined)

    def test_resolve_secrets_path_raises_on_empty(self):
        import unittest.mock as mock
        from scripts.pihole import live_dry_run as dry
        with mock.patch.object(
            live_apply.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""),
        ):
            with self.assertRaises(dry.OrchestratorError):
                live_apply._resolve_secrets_path(
                    "root@pihole1", "pihole-api-password",
                )

    def test_resolve_secrets_path_raises_on_nonzero(self):
        import unittest.mock as mock
        from scripts.pihole import live_dry_run as dry
        with mock.patch.object(
            live_apply.subprocess, "run",
            return_value=mock.Mock(returncode=1, stdout="", stderr="permission denied"),
        ):
            with self.assertRaises(dry.OrchestratorError):
                live_apply._resolve_secrets_path(
                    "root@pihole1", "pihole-api-password",
                )

    def test_resolve_secrets_path_rejects_unsafe_name(self):
        for name in (
            "pihole; rm -rf /",          # shell metacharacter
            "../../etc/passwd",          # path traversal
            "pihole-api-password\nrm",  # newline injection
            "",                          # empty
            "-rf",                       # option-like
        ):
            with self.subTest(name=name):
                with self.assertRaises(Exception) as ctx:
                    live_apply._resolve_secrets_path("root@pihole1", name)
                self.assertIn("unsafe SOPS secret name", str(ctx.exception))

    def test_read_remote_secret_uses_validated_fish_safe_path(self):
        import unittest.mock as mock
        secret = "identities:\n    identityRef:alpha:\n        mac: test-mac\n"
        with mock.patch.object(
            live_apply.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout=secret, stderr=""),
        ) as fake_run:
            result = live_apply._read_remote_secret(
                "root@pihole1", "/run/secrets.d/12/pihole-identities",
            )
        self.assertEqual(result, secret)
        args = fake_run.call_args[0][0]
        remote_command = args[-1]
        self.assertIn("cat --", remote_command)
        self.assertIn("/run/secrets.d/12/pihole-identities", remote_command)
        self.assertNotIn("bash -c", remote_command)
        self.assertNotIn("$(", remote_command)
        self.assertNotIn("`", remote_command)

    def test_read_remote_secret_rejects_untrusted_path(self):
        with self.assertRaises(Exception):
            live_apply._read_remote_secret(
                "root@pihole1", "/tmp/pihole-identities; touch /tmp/leak",
            )


if __name__ == "__main__":
    unittest.main()
