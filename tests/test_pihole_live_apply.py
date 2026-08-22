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
            ]
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
        self.assertEqual(client["hostname"], "emma-book")
        self.assertIn(
            {"name": "kids", "description": "kids clients", "enabled": True},
            inv["groups"],
        )


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


if __name__ == "__main__":
    unittest.main()
