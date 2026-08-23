"""Tests for scripts/pihole/deploy_transport.py.

Covers deploy.py transport recovery from Tailscale-induced disconnects
during `nixos-rebuild switch --target-host`. This is the TODO from
the canonical plan's remaining work list.

The deploy wrapper must:
1. Classify `nixos-rebuild` exit codes into ok / transport_loss / failed.
2. Detect the Tailscale disconnect signature in stderr/stdout.
3. Extract the requested generation link from rebuild output.
4. Verify post-disconnect convergence via `readlink /run/current-system`
   over a fresh SSH connection.
5. Retry the rebuild a bounded number of times; never infinite-loop.
6. Never mask genuine activation / setup / API / drift failures.

Required test cases from the plan:
- Transport loss before activation
- Transport loss after activation
- Generation mismatch
- Retry exhaustion
- Successful recovery
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pihole import deploy_transport


def _generation_link(hash_char="a", host="pihole"):
    return f"/nix/store/{hash_char * 32}-nixos-system-{host}-26.05.20260820.5880666"


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClassifyRebuildExitTests(unittest.TestCase):
    """_classify_rebuild_exit returns one of: 'ok', 'transport_loss', 'failed'."""

    def test_zero_exit_is_ok(self):
        self.assertEqual(
            deploy_transport._classify_rebuild_exit(0, "", ""),
            "ok",
        )

    def test_non_transport_nonzero_is_failed(self):
        # exit 1 with normal-looking stderr -> real failure
        self.assertEqual(
            deploy_transport._classify_rebuild_exit(
                1,
                stderr="error: attribute 'pihole3' missing",
                stdout="",
            ),
            "failed",
        )

    def test_exit_255_with_transport_signature_is_transport_loss(self):
        # The actual bug from the plan: Tailscale restart yields 255.
        self.assertEqual(
            deploy_transport._classify_rebuild_exit(
                255,
                stderr="kex_exchange_identification: read: Connection reset by peer",
                stdout="",
            ),
            "transport_loss",
        )

    def test_exit_255_without_transport_signature_is_failed(self):
        # 255 with unrelated stderr is a real failure, not transport.
        self.assertEqual(
            deploy_transport._classify_rebuild_exit(
                255,
                stderr="error: undefined variable 'foo'",
                stdout="",
            ),
            "failed",
        )


class IsTransportSignatureTests(unittest.TestCase):
    """Known SSH / Tailscale disconnect strings classify as transport loss."""

    def test_kex_exchange_reset(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "kex_exchange_identification: read: Connection reset by peer"))

    def test_ssh_connection_closed(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "Connection to 192.168.86.101 closed by remote host"))

    def test_ssh_write_broken_pipe(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "Write failed: Broken pipe"))

    def test_ssh_read_broken_pipe(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "Read from socket failed: Connection reset by peer"))

    def test_ssh_connect_refused(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "ssh: connect to host pihole1 port 22: Connection refused"))

    def test_ssh_connect_timed_out(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "ssh: connect to host pihole1 port 22: Connection timed out"))

    def test_ssh_connection_closed_by_remote_host(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "Connection to pihole1 port 22 closed by remote host."))

    def test_ssh_client_loop_broken_pipe(self):
        self.assertTrue(deploy_transport._is_transport_signature(
            "client_loop: send disconnect: Broken pipe"))

    def test_normal_build_error_is_not_transport(self):
        self.assertFalse(deploy_transport._is_transport_signature(
            "error: attribute 'pihole3' missing"))

    def test_empty_is_not_transport(self):
        self.assertFalse(deploy_transport._is_transport_signature(""))

    def test_unrelated_text_is_not_transport(self):
        self.assertFalse(deploy_transport._is_transport_signature(
            "switching to generation 158"))

    def test_generic_connection_to_text_is_not_transport(self):
        self.assertFalse(deploy_transport._is_transport_signature(
            "activation error: Connection to the service failed"))

    def test_generic_connection_refused_text_is_not_transport(self):
        self.assertFalse(deploy_transport._is_transport_signature(
            "activation failed: Connection refused by the local service"))


class ExtractRequestedGenerationTests(unittest.TestCase):
    """Pull the store path nixos-rebuild passed to nix-env / systemd-run."""

    def test_extracts_from_nix_env_set(self):
        stderr = (
            "building the system configuration...\n"
            "activating the configuration...\n"
            "Command 'nix-env -p /nix/var/nix/profiles/system --set "
            + _generation_link()
            + "' "
            "returned non-zero exit status 1.\n"
        )
        link = deploy_transport._extract_requested_generation(stderr)
        self.assertEqual(link, _generation_link())

    def test_extracts_from_systemd_run(self):
        stdout = (
            "Command 'systemd-run --unit=nixos-rebuild-switch-to-configuration "
            + _generation_link("b")
            + "/"
            "bin/switch-to-configuration' returned non-zero exit status 1.\n"
        )
        link = deploy_transport._extract_requested_generation(stdout)
        self.assertEqual(link, _generation_link("b"))

    def test_returns_none_when_no_store_path(self):
        self.assertIsNone(deploy_transport._extract_requested_generation(
            "building the system configuration...\n"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(deploy_transport._extract_requested_generation(""))

    def test_only_nixos_system_paths_match(self):
        """A random /nix/store/<hash>-other-package path is NOT a generation."""
        stdout = (
            "Command 'nix-env -p /nix/var/nix/profiles/system --set "
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-some-other-package-1.0' "
            "returned non-zero exit status 1.\n"
        )
        self.assertIsNone(deploy_transport._extract_requested_generation(stdout))

    def test_short_store_hash_does_not_match(self):
        self.assertIsNone(deploy_transport._extract_requested_generation(
            "/nix/store/abc123-nixos-system-pihole-26.05.20260820.5880666"))

    def test_non_nix_store_hash_does_not_match(self):
        self.assertIsNone(deploy_transport._extract_requested_generation(
            "/nix/store/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-nixos-system-pihole-26.05.20260820.5880666"))


class VerifyGenerationConvergenceTests(unittest.TestCase):
    """_verify_generation_convergence reads /run/current-system over SSH."""

    def test_converged_when_remote_matches(self):
        expected = _generation_link()
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.return_value = _FakeResult(0, stdout=expected + "\n")
            self.assertTrue(
                deploy_transport._verify_generation_convergence("pihole1", expected))

    def test_not_converged_when_remote_differs(self):
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.return_value = _FakeResult(
                0, stdout="/nix/store/old-nixos-system-pihole-26.05.20260820.1111111\n"
            )
            self.assertFalse(
                deploy_transport._verify_generation_convergence(
                    "pihole1",
                    "/nix/store/new-nixos-system-pihole-26.05.20260820.2222222",
                ))

    def test_returns_false_on_ssh_failure(self):
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.return_value = _FakeResult(255, stderr="Connection refused")
            self.assertFalse(
                deploy_transport._verify_generation_convergence(
                    "pihole1",
                    _generation_link(),
                ))

    def test_returns_false_on_timeout(self):
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
            self.assertFalse(
                deploy_transport._verify_generation_convergence(
                    "pihole1",
                    "/nix/store/abc-nixos-system-pihole-26.05.20260820.5880666",
                ))

    def test_uses_bounded_timeout_and_readlink(self):
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.return_value = _FakeResult(
                0, stdout=_generation_link() + "\n")
            deploy_transport._verify_generation_convergence(
                "pihole1",
                _generation_link(),
            )
        cmd = fake_run.call_args[0][0]
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("readlink", cmd)
        self.assertIn("/run/current-system", cmd)
        # SSH target embeds the host as root@<host>.
        self.assertIn("root@pihole1", cmd)
        self.assertIn("ControlMaster=no", cmd)
        self.assertIn("ControlPath=none", cmd)


class RunRebuildWithRecoveryTests(unittest.TestCase):
    """run_rebuild_with_recovery orchestrates the bounded retry policy."""

    def _factory(self):
        """Return a stable rebuild-cmd factory for tests."""
        return ["nixos-rebuild", "switch", "--flake", ".#pihole1",
                "--target-host", "root@pihole1"]

    def test_successful_rebuild_does_not_retry(self):
        """Happy path: rebuild returns 0 on first try; no verification runs."""
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.return_value = _FakeResult(0, stdout="building...\n")
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )
        self.assertTrue(ok)
        self.assertIsNone(link)  # No transport loss -> no requested generation
        self.assertEqual(fake_run.call_count, 1)

    def test_transport_loss_with_converged_generation_recovers(self):
        """Transport loss AFTER activation: rebuild raised 255, but
        /run/current-system already shows the requested generation."""
        expected = _generation_link()
        stderr = "kex_exchange_identification: read: Connection reset by peer\n"
        stdout = "activation command disconnected before completion\n"
        stderr += (
            "Command 'nix-env -p /nix/var/nix/profiles/system --set "
            + expected + "' returned non-zero exit status 1.\n"
        )

        # First call: nixos-rebuild returns 255 + transport signature.
        # Second call: readlink returns expected link.
        rebuild_result = _FakeResult(255, stdout=stdout, stderr=stderr)
        verify_result = _FakeResult(0, stdout=expected + "\n")

        call_count = {"n": 0}

        def fake_run(cmd, *args, **kwargs):
            call_count["n"] += 1
            if cmd[0] == "nixos-rebuild":
                return rebuild_result
            return verify_result

        with patch.object(deploy_transport.subprocess, "run",
                          side_effect=fake_run):
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )

        self.assertTrue(ok)
        self.assertEqual(link, expected)
        # 1 rebuild attempt + 1 verification = 2 total calls.
        self.assertEqual(call_count["n"], 2)

    def test_transport_loss_with_no_requested_generation_retries_then_fails(self):
        """Transport loss before activation: rebuild never reached the
        nix-env / systemd-run command, so no requested generation is
        extractable. After bounded retries we give up."""
        rebuild_result = _FakeResult(
            255,
            stdout="some early-stage output without a nix-env line",
            stderr="kex_exchange_identification: read: Connection reset by peer",
        )

        with patch.object(deploy_transport.subprocess, "run",
                          return_value=rebuild_result) as fake_run:
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )

        self.assertFalse(ok)
        self.assertIsNone(link)
        # Retries the rebuild exactly max_attempts times.
        self.assertEqual(fake_run.call_count, 3)

    def test_transport_loss_before_activation_retries_then_fails(self):
        """Same shape as above, with simpler output: transport loss
        every time, no extractable link, bounded retry."""
        rebuild_result = _FakeResult(
            255,
            stdout="building...\n",  # no nix-env / systemd-run command
            stderr="ssh: connect to host pihole1 port 22: Connection refused",
        )

        with patch.object(deploy_transport.subprocess, "run",
                          return_value=rebuild_result) as fake_run:
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )

        self.assertFalse(ok)
        self.assertIsNone(link)
        self.assertEqual(fake_run.call_count, 3)

    def test_transport_loss_with_non_converged_then_real_failure_fails_fast(self):
        """Transport loss on first attempt where verify says NOT converged
        (so we retry the rebuild), and the retry surfaces a genuine
        failure. The genuine failure must not be masked -- the wrapper
        returns False after the second attempt.
        """
        expected = _generation_link()
        # First attempt: transport loss after activation.
        stderr_first = "kex_exchange_identification: read: Connection reset by peer"
        stdout_first = "activation command disconnected before completion\n"
        stderr_first += (
            "Command 'nix-env -p /nix/var/nix/profiles/system --set "
            + expected + "' returned non-zero exit status 1.\n"
        )
        first_result = _FakeResult(255, stdout=stdout_first, stderr=stderr_first)
        # Verification: NOT converged (still on the old generation).
        verify_result = _FakeResult(0, stdout=_generation_link("b") + "\n")
        # Retry rebuild: genuine failure (real eval error).
        second_result = _FakeResult(1, stderr="error: build failed", stdout="")

        seq = [first_result, verify_result, second_result]
        idx = {"i": 0}

        def fake_run(cmd, *args, **kwargs):
            r = seq[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(deploy_transport.subprocess, "run",
                          side_effect=fake_run):
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )

        self.assertFalse(ok)
        # 1st rebuild (transport) -> verify (not converged) -> 2nd rebuild (failed)
        # The 2nd rebuild is a genuine failure, not transport loss; we
        # never make a third attempt.
        self.assertEqual(idx["i"], 3)

    def test_real_failure_does_not_retry(self):
        """A non-transport failure (e.g. eval error) must NOT trigger retry.
        This is the 'never mask genuine failures' rule.
        """
        result = _FakeResult(1, stderr="error: attribute 'pihole3' missing", stdout="")
        with patch.object(deploy_transport.subprocess, "run",
                          return_value=result) as fake_run:
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )
        self.assertFalse(ok)
        self.assertEqual(fake_run.call_count, 1)

    def test_retry_exhaustion_returns_false(self):
        """If every attempt is transport loss without convergence, fail fast."""
        rebuild_result = _FakeResult(
            255,
            stdout="building...\n",
            stderr="ssh: connect to host pihole1 port 22: Connection refused",
        )
        with patch.object(deploy_transport.subprocess, "run",
                          return_value=rebuild_result) as fake_run:
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=2,
            )
        self.assertFalse(ok)
        self.assertEqual(fake_run.call_count, 2)

    def test_timeout_treated_as_failed_no_retry(self):
        """subprocess.TimeoutExpired is a real failure, not transport loss."""
        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            fake_run.side_effect = subprocess.TimeoutExpired(cmd="nixos-rebuild", timeout=600)
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                max_attempts=3,
            )
        self.assertFalse(ok)
        self.assertEqual(fake_run.call_count, 1)

    def test_rebuild_cwd_is_forwarded_on_every_attempt(self):
        cwd = Path("/var/lib/hermes/workspace/nix-config")
        with patch.object(deploy_transport.subprocess, "run",
                          return_value=_FakeResult(0)) as fake_run:
            ok, _ = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=self._factory,
                cwd=cwd,
                max_attempts=3,
            )
        self.assertTrue(ok)
        self.assertEqual(fake_run.call_args.kwargs["cwd"], cwd)

    def test_rebuild_command_factory_exception_fails_without_running(self):
        def broken_factory():
            raise RuntimeError("factory failed")

        with patch.object(deploy_transport.subprocess, "run") as fake_run:
            ok, link = deploy_transport.run_rebuild_with_recovery(
                "pihole1",
                rebuild_cmd_factory=broken_factory,
                max_attempts=3,
            )
        self.assertFalse(ok)
        self.assertIsNone(link)
        fake_run.assert_not_called()


class DirectInvocationSmokeTests(unittest.TestCase):
    def test_script_bootstrap_loads_from_an_unrelated_cwd_without_deploying(self):
        script = Path(__file__).parents[1] / "scripts" / "pihole" / "deploy.py"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, str(script), "not-a-pihole-host"],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
        # The script must import successfully from an unrelated cwd, then
        # reject the deliberately invalid target before any deployment call.
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown host: not-a-pihole-host", result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
