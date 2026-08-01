# nix-config

Personal NixOS homelab configuration managing 30+ hosts across Proxmox LXC containers, servers, and laptops. Built on NixOS flakes with nixpkgs 25.11 (Xantusia).

## Structure

- `flake.nix` — entry point; inputs include home-manager, disko, sops-nix, plasma-manager
- `hosts/` — per-host configurations
  - `nxc/` — 25+ LXC containers on Proxmox (Jellyfin, Nextcloud, Forgejo, Paperless-NGX, Ollama, etc.); new containers provisioned with [nxc-scripts](https://github.com/pmontgo33/nxc-scripts)
  - `nixbooks/` — laptop configurations (ali-book, emma-book, cora-book)
  - individual servers: bifrost, tesseract, yondu, and others
  - `rescue/`, `dev/` — live image and dev environment configs
- `modules/` — reusable NixOS modules (auto-upgrade, tailscale, caddy-proxy, mount helpers, host-checkin)
- `users/` — home-manager user configurations (patrick, lina)
- `ansible/` — bootstrap playbooks for non-NixOS infra
- `secrets/` — sops-nix encrypted secrets
- `packages/` — custom package definitions
- `justfile` — task runner for common workflows

See [host-states.md](host-states.md) for the full host inventory with NixOS versions and rebuild history.

## Common Commands

```bash
just nrs                     # nixos-rebuild switch (local)
just nrs-r HOST              # nixos-rebuild switch (remote)
just nrsb-r HOST             # build then switch (remote)
just nfc                     # nix flake check
just secrets                 # edit sops-encrypted secrets
just rescue-build            # build rescue ISO
just ap HOST                 # run ansible playbook against host
```

## Guarded PR workflow

The repository-local `scripts/nix-pr` wrapper owns branch safety, validation
receipts, commit-message checks, and verified Forgejo PR submission. It does
not edit Nix, deploy systems, merge PRs, or depend on an LLM.

```bash
scripts/nix-pr start <slug>
scripts/nix-pr status                 # confirm branch/base state
# edit normally, then stage one logical change group
scripts/nix-pr check --second-review-file /path/to/review.txt
scripts/nix-pr commit --message-file /path/to/commit-message.txt
scripts/nix-pr submit --dry-run       # inspect the Forgejo payload
scripts/nix-pr submit                 # push and open the PR after review
```

`status` and `submit --dry-run` are read-only. The final `submit` step is the
only command in this sequence that pushes the branch and opens a Forgejo PR.

Use `--host NAME` for a shared Nix change when the affected host cannot be
inferred from the path, or `--all-hosts` when every flake configuration must
be built. Validation receipts are stored outside the repository under the
user's XDG state directory. Run the test suite with:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Commit messages must use imperative mood, explain why the change is needed,
include operational context and validation results, omit trailing punctuation,
and contain no `Co-Authored-By` or AI attribution.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [Sascha Koenig YouTube Playlist](https://www.youtube.com/playlist?list=PLCQqUlIAw2cCuc3gRV9jIBGHeekVyBUnC)
- [Sascha Koenig Repository](https://code.m3ta.dev/m3tam3re/nixcfg)
