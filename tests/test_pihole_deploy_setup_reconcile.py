import subprocess
import unittest
from unittest.mock import patch

from scripts.pihole import deploy


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DeploySetupReconcileTests(unittest.TestCase):
    def _run_setup_block(self, restart_effects):
        """Run deploy() and capture all subprocess calls during setup."""
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            # For health checks, return a success.
            if any("curl" in str(c) for c in cmd):
                return _FakeResult(0, '{"session": {}}')
            # For nixos-rebuild, return success.
            if any("nixos-rebuild" in str(c) for c in cmd):
                return _FakeResult(0)
            # For the setup reconcile, apply configured effects.
            for matcher, value in restart_effects.items():
                if matcher(cmd):
                    if isinstance(value, BaseException):
                        raise value
                    return value
            return _FakeResult(0)

        with patch.object(deploy.subprocess, "run", side_effect=fake_run):
            with patch.object(deploy, "log"):
                with patch.object(deploy, "apply_policy", return_value=True):
                    result = deploy.deploy("pihole1")
        return calls, result

    def test_setup_reconcile_runs_stop_reset_restart(self):
        calls, result = self._run_setup_block({})
        # deploy should succeed.
        self.assertTrue(result)
        # The first 3 ssh calls should be stop, reset-failed, restart.
        ssh_calls = [c for c in calls if c["cmd"][0] == "ssh"]
        verbs = [c["cmd"][-2] for c in ssh_calls[:3]]
        self.assertEqual(verbs, ["stop", "reset-failed", "restart"])

    def test_policy_apply_succeeds_before_setup_reconciliation(self):
        calls = []
        events = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            events.append(list(cmd))
            if any("nixos-rebuild" in str(c) for c in cmd):
                return _FakeResult(0)
            if any("curl" in str(c) for c in cmd):
                return _FakeResult(0, '{"session": {}}')
            return _FakeResult(0)

        def applied(host):
            events.append(("policy-apply", host))
            return True

        with patch.object(deploy.subprocess, "run", side_effect=fake_run), \
             patch.object(deploy, "log"), \
             patch.object(deploy, "apply_policy", side_effect=applied) as apply_policy:
            self.assertTrue(deploy.deploy("pihole1"))

        apply_policy.assert_called_once_with("pihole1")
        setup_restart = next(
            index for index, event in enumerate(events)
            if isinstance(event, list) and event[0] == "ssh" and "restart" in event and "pihole-ftl-setup.service" in event
        )
        self.assertLess(events.index(("policy-apply", "pihole1")), setup_restart)

    def test_apply_policy_passes_origin_lock_and_password_path(self):
        """The orchestrator needs the API origin, shared lock path, and a
        resolvable SOPS secret path to function end-to-end."""
        with patch.object(deploy, "apply_policy", return_value=True), \
             patch.object(deploy, "log"):
            self.assertTrue(deploy.apply_policy("pihole1"))
        # Validate the constants the orchestrator passes are correct for
        # the current Nix module: API on 8080, lockfile at the module path.
        self.assertEqual(deploy.PIHOLE_API_ORIGIN, "http://127.0.0.1:8080")
        self.assertEqual(deploy.POLICY_LOCK_PATH, "/var/lib/pihole/.pihole-policy.lock")
        # Apply runner script must exist in the repo and be executable.
        self.assertTrue(deploy.APPLY_RUNNER.exists())

    def test_failed_policy_apply_skips_setup_reconciliation(self):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if any("nixos-rebuild" in str(c) for c in cmd):
                return _FakeResult(0)
            if any("curl" in str(c) for c in cmd):
                return _FakeResult(0, '{"session": {}}')
            return _FakeResult(0)

        with patch.object(deploy.subprocess, "run", side_effect=fake_run), \
             patch.object(deploy, "log"), \
             patch.object(deploy, "apply_policy", return_value=False):
            self.assertFalse(deploy.deploy("pihole1"))

        self.assertFalse(any(
            cmd[0] == "ssh" and "pihole-ftl-setup.service" in cmd
            for cmd in calls
        ))

    def test_timeout_triggers_stop_reset_kill_cleanup(self):
        import subprocess as sp

        def matcher(cmd):
            return cmd[0] == "ssh" and "restart" in cmd

        calls, result = self._run_setup_block({
            matcher: sp.TimeoutExpired(cmd="restart", timeout=300),
        })
        self.assertFalse(result)
        ssh_calls = [c for c in calls if c["cmd"][0] == "ssh"]
        verbs = [c["cmd"][-2] for c in ssh_calls]
        # stop → reset-failed → restart (times out) → stop → reset-failed → kill TERM → kill KILL
        self.assertIn("stop", verbs)
        self.assertIn("reset-failed", verbs)
        self.assertIn("restart", verbs)
        self.assertTrue(any(
            c["cmd"][-3:] == ["kill", "--signal=TERM", "pihole-ftl-setup.service"]
            for c in ssh_calls
        ))
        self.assertTrue(any(
            c["cmd"][-3:] == ["kill", "--signal=KILL", "pihole-ftl-setup.service"]
            for c in ssh_calls
        ))

    def test_nonzero_restart_fails_and_resets_failed_state(self):
        calls, result = self._run_setup_block({
            lambda cmd: cmd[0] == "ssh" and "restart" in cmd: _FakeResult(1),
        })
        self.assertFalse(result)
        ssh_calls = [c for c in calls if c["cmd"][0] == "ssh"]
        # After the failed restart, there should be a reset-failed cleanup.
        verbs = [c["cmd"][-2] for c in ssh_calls]
        self.assertIn("restart", verbs)
        self.assertIn("reset-failed", verbs)


if __name__ == "__main__":
    unittest.main()
