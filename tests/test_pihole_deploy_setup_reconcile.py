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
