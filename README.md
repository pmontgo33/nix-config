# nix-config

Personal NixOS homelab configuration managing 37 flake configurations across Proxmox LXC containers, servers, laptops, templates, development systems, and installation images. The primary package set tracks NixOS 26.05, with additional unstable and pinned inputs where required.

## Prerequisites

- Nix with flakes enabled
- `just` for common local and remote operations
- Python 3 for the guarded PR workflow and its tests
- SOPS and an age key for editing encrypted secrets
- SSH and Tailscale access for remote builds and deployments
- Forgejo credentials configured through Git's credential helper for PR submission

## Structure

- `flake.nix` — entry point; inputs include home-manager, disko, sops-nix, plasma-manager, and Hermes Agent
- `hosts/` — per-host configurations
  - `nxc/` — Proxmox LXC containers (Jellyfin, Nextcloud, Forgejo, Paperless-NGX, Ollama, etc.); new containers are provisioned with [nxc-scripts](https://github.com/pmontgo33/nxc-scripts)
  - `nixbooks/` — laptop configurations (ali-book, emma-book, cora-book)
  - individual servers such as bifrost, tesseract, and yondu
  - `rescue/`, `dev/` — installation image and development configurations
  - `common/` and `nxc/common/` — shared host modules
- `modules/` — reusable NixOS modules (auto-upgrade, Tailscale, Caddy proxy, mount helpers, host check-in)
- `users/` — home-manager user configurations (patrick, lina)
- `ansible/` — bootstrap playbooks for non-NixOS infrastructure
- `secrets/` — sops-nix encrypted secrets
- `packages/` — custom package definitions
- `scripts/` — operational tooling, including the guarded `nix-pr` workflow
- `tests/` — unit tests for repository tooling
- `AGENT.md` — repository-specific guidance for automation agents
- `justfile` — task runner for common workflows

See [host-states.md](host-states.md) for the latest recorded operational snapshot. The flake remains authoritative for configured hosts.

## Common Commands

```bash
just nrs                     # switch the local running system
just nrs-r HOST              # switch a remote running system
just nrsb-r HOST             # build remotely, then switch the target
just nfc                     # run nix flake check
just secrets                 # edit sops-encrypted secrets
just rescue-build            # build rescue ISO
just ap HOST                 # run ansible playbook against a host
```

The `nrs`, `nrs-r`, and `nrsb-r` commands modify running systems. Verify the target host and rollback access before a production switch. Pull-request submission and deployment are separate operations: `scripts/nix-pr submit` does not build, switch, merge, or deploy a system.

## Guarded PR workflow

The repository-local `scripts/nix-pr` wrapper owns branch safety, validation receipts, commit-message checks, and verified Forgejo PR submission. It does not edit Nix, deploy systems, merge PRs, or depend on an LLM.

Start a new task from a clean checkout, then stage and validate one logical change group:

```bash
scripts/nix-pr start <slug>
# edit normally
git add <intended-files>
scripts/nix-pr check [--second-review-file /path/to/review.txt]
scripts/nix-pr commit --message-file /path/to/commit-message.txt
scripts/nix-pr submit --dry-run
scripts/nix-pr submit
```

Use `scripts/nix-pr status` as the first troubleshooting command. Use `--host NAME` for a shared Nix change when the affected host cannot be inferred from its path, or `--all-hosts` when every flake configuration must be built. Production Nix and systemd changes require a saved second-model review passed through `--second-review-file`; documentation-only changes do not.

Validation receipts are stored outside the repository under the user's XDG state directory. Run the test suite with:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Commit messages must use imperative mood, omit trailing punctuation, and contain no `Co-Authored-By` or AI attribution. They must use this Markdown format so the PR description and future `git log` history remain useful:

```markdown
<scope>: <imperative summary>

## Summary

What changed and what result it provides.

## Why this matters

The problem, root cause, constraints, or design decision.

## Verification

- Exact parse, evaluation, build, test, or runtime checks

## Deployment / Post-merge checklist

Only when relevant: rebuilds, restarts, migrations, rollback steps, or
subscriber/user follow-up.
```

`## Summary` and `## Verification` must be populated. Substantive changes must provide at least 350 body characters. The wrapper validates this format before committing and again before submission.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [Sascha Koenig YouTube Playlist](https://www.youtube.com/playlist?list=PLCQqUlIAw2cCuc3gRV9jIBGHeekVyBUnC)
- [Sascha Koenig Repository](https://code.m3ta.dev/m3tam3re/nixcfg)
