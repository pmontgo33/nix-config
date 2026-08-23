# Shelved: runtime lane-validation automation

This directory holds the `validate_lanes.py` script that was part of the
expert-delegator shipped via PRs #225, #242, and #243. It was shelved
on 2026-08-23 in favor of native Hermes `delegate_task`. The script is
not invoked from production. It is preserved here so the design and
its review history are recoverable.

## What was shelved

- The timer (`bernie-lane-validation.timer`) and one-shot service that
  periodically probed each lane through the standard runner and wrote
  fresh validation state.
- The runtime merge logic in `model_worker.py` that consumed that
  state file at dispatch time.

## What was kept

- `model_worker.py` itself — still importable, still tested. Not called
  by anything in production after this shelving.
- The Nix-managed worker registry at
  `hosts/nxc/hermes/delegation/worker-registry.json`.
- The 42 contract tests in `scripts/bernie/tests/test_model_worker.py`
  that prove the runner's fail-closed behavior.

## Why shelve

Patrick's directive (2026-08-23): lean into the native `delegate_task`
features in Hermes first, with the Luna xhigh review skill that's
already wired in. The custom runner was a hedge against native
delegation's limits; with the SOUL.md rewrite (PR #244), the native path
is the new policy.

## How to revive

The runtime can be brought back by reverting the shelf PR, rebuilding
the NixOS system to deploy the restored systemd units, and starting the
timer. The whole revival is four commands:

```bash
# 1. Revert the shelf PR
git revert <this-PR-sha>

# 2. Rebuild and switch to deploy the restored systemd units
sudo nixos-rebuild switch --flake .#hermes

# 3. Reload systemd (the rebuild already does this, but explicit is fine)
sudo systemctl daemon-reload

# 4. Start the timer (it'll fire once on boot, then every 6 hours)
sudo systemctl start bernie-lane-validation.timer
```

Skipping step 2 leaves the reverted source on disk but the systemd units
will not exist in `/run/current-system/`, so steps 3 and 4 cannot
succeed.

Before reviving, re-confirm with Patrick that the rationale has
changed (e.g. native `delegate_task` proved inadequate for a
specific workload).

## Migration notes

- After revival, the existing `/var/lib/hermes/.local/state/bernie-delegation/`
  directory and its `runs/` receipts are still present.
- The validator script will need one probe cycle to write a fresh
  `lane-validation.json`. The first cycle runs within `OnBootSec` of
  the timer.
- If the registry has changed in the meantime, the validator will
  re-bind fingerprints via the runtime state schema.
