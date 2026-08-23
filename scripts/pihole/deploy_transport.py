"""Transport-recovery helpers for scripts/pihole/deploy.py.

Background
----------

`nixos-rebuild switch --target-host` runs an SSH connection to the
target. When Tailscale restarts mid-build (which has been observed
during Pi-hole rollouts), the SSH connection drops mid-activation:

    nixos-rebuild  ->  builds locally  ->  activates remotely
                                       ^^^^^^^^
                                       Tailscale restarts here

The remote activation may complete successfully (the requested
generation IS the new /run/current-system), but `nixos-rebuild` exits
255 because the SSH transport died before it could confirm. The deploy
wrapper treats that 255 as a hard failure and aborts.

This module distinguishes *transport loss* (the build may have
succeeded) from *real failure* (the build definitely did not succeed),
then verifies convergence by re-reading /run/current-system over a
fresh SSH connection. Activation is accepted only if the requested
generation is live; otherwise bounded retries run.

Hard rules (from the canonical plan):
- Detect the Tailscale-induced disconnect signature.
- Record the requested generation from rebuild output where possible.
- Reconnect and verify /run/current-system matches the requested
  generation before treating deploy as failed.
- Continue with the normal setup stop / reset / restart if activation
  actually succeeded.
- Only retry the rebuild a bounded number of times.
- Keep sequential fail-fast behaviour; never mask genuine activation
  / setup / API / drift failures.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional, Tuple

# SSH options used by the deploy wrapper; kept here so verification
# matches the live deploy host in identity (BatchMode, ConnectTimeout).
_VERIFY_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
]

_VERIFY_TIMEOUT = 15

# OpenSSH/Tailscale disconnect signatures. Keep these tied to the SSH
# diagnostic shape: generic phrases such as ``Connection refused`` or
# ``Connection to`` can also occur in activation/API diagnostics.
_TRANSPORT_SIGNATURES = tuple(re.compile(
    pattern, re.IGNORECASE | re.MULTILINE
) for pattern in (
    r"(?:kex_exchange_identification|ssh_exchange_identification):[^\n]*(?:connection reset by peer|connection closed by remote host)",
    r"(?:read from socket failed|write failed):[^\n]*(?:connection reset by peer|broken pipe)",
    r"client_loop:[^\n]*broken pipe",
    r"connection to \S+(?: port \d+)? closed by remote host\.?$",
    r"ssh:\s+connect to host \S+ port \d+: connection (?:refused|timed out|no route to host)\.?$",
    r"connection timed out during banner exchange",
))

# Matches the store path nixos-rebuild passes to nix-env or systemd-run
# when activating. Two real forms seen in the wild:
#   nix-env -p /nix/var/nix/profiles/system --set /nix/store/<hash>-nixos-system-<host>-<date>.<build>
#   systemd-run ... /nix/store/<hash>-nixos-system-<host>-<date>.<build>/bin/switch-to-configuration ...
# The nixos-system-<host>- segment is the discriminator that keeps us
# from matching arbitrary /nix/store/<hash>-<name>-<version> paths.
_GENERATION_RE = re.compile(
    r"/nix/store/[0-9a-z]{32}-nixos-system-[A-Za-z0-9][A-Za-z0-9._+-]*"
)


def _is_transport_signature(text: str) -> bool:
    """True if `text` contains a known SSH / Tailscale disconnect marker."""
    return any(signature.search(text) for signature in _TRANSPORT_SIGNATURES)


def _classify_rebuild_exit(returncode: int, stderr: str, stdout: str) -> str:
    """Classify a rebuild result.

    Returns one of:
      "ok"              -- rebuild succeeded.
      "transport_loss"  -- the connection died; the build may have
                           succeeded. Caller should verify convergence.
      "failed"          -- a genuine failure; do not retry.
    """
    if returncode == 0:
        return "ok"
    combined_output = "\n".join(part for part in (stdout, stderr) if part)
    if returncode == 255 and _is_transport_signature(combined_output):
        return "transport_loss"
    return "failed"


def _extract_requested_generation(output: str) -> Optional[str]:
    """Pull the requested-generation store path out of rebuild output.

    Returns the matching /nix/store/<hash>-nixos-system-... link, or
    None if rebuild did not reach the activation phase. The caller may
    provide stdout and stderr merged together because nixos-rebuild-ng
    puts its CalledProcessError activation diagnostics on stderr.
    """
    matches = _GENERATION_RE.findall(output)
    if not matches:
        return None
    # If the rebuild printed multiple candidates (rare; only when both
    # nix-env and systemd-run lines appear in error messages), prefer
    # the first -- that is the outer activation attempt.
    return matches[0]


def _verify_generation_convergence(host: str, expected_link: str) -> bool:
    """Read /run/current-system over a fresh SSH connection.

    Returns True iff the remote `/run/current-system` resolves to
    `expected_link`. Returns False on SSH failure, timeout, or
    mismatch -- never raises.
    """
    cmd = [
        "ssh", *_VERIFY_SSH_OPTS,
        # Do not accidentally reuse a long-lived ControlMaster from the
        # rebuild command; convergence must be checked on a fresh transport.
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        f"root@{host}",
        "readlink", "/run/current-system",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_VERIFY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == expected_link


def run_rebuild_with_recovery(
    host: str,
    *,
    rebuild_cmd_factory,
    max_attempts: int = 3,
    cwd=None,
    timeout: int = 600,
) -> Tuple[bool, Optional[str]]:
    """Run `nixos-rebuild switch` with bounded transport-loss recovery.

    `rebuild_cmd_factory` is a zero-arg callable that returns the
    command list to invoke `nixos-rebuild switch --target-host`. It
    is called once per attempt so any per-attempt setup (e.g. fresh
    SSH config) is the caller's responsibility.

    Returns (ok, requested_link):
      - (True, None)                 on first-attempt success.
      - (True, requested_link)       on transport-loss recovery where
                                      /run/current-system actually
                                      converged to the requested link.
      - (False, None)                on real failure, retry exhaustion,
                                      or transport loss with no
                                      extractable requested link.

    The function never masks genuine failures: a non-transport exit
    code on the first attempt aborts immediately without retry.
    """
    last_link: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        try:
            cmd = rebuild_cmd_factory()
        except Exception:
            return False, None
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            # A 10-minute rebuild timeout is a real failure, not
            # transport loss. Do not retry.
            return False, None
        except Exception:
            return False, None

        verdict = _classify_rebuild_exit(
            result.returncode, result.stderr, result.stdout)

        if verdict == "ok":
            return True, None

        if verdict == "failed":
            # Real failure; do not retry, do not mask.
            return False, None

        # verdict == "transport_loss"
        combined_output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        )
        last_link = _extract_requested_generation(combined_output)
        if last_link is None:
            # Transport loss before activation. There is no generation
            # link to verify, so we cannot recover -- retry the rebuild.
            # If the rebuild itself keeps losing transport without ever
            # reaching activation, we exhaust attempts below.
            if attempt >= max_attempts:
                return False, None
            continue

        # Transport loss after activation: verify convergence once.
        if _verify_generation_convergence(host, last_link):
            return True, last_link

        # Activation did NOT converge (transport died before
        # nix-env / systemd-run completed). Retry.
        if attempt >= max_attempts:
            return False, None

    return False, last_link
