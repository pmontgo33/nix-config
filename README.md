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

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [Sascha Koenig YouTube Playlist](https://www.youtube.com/playlist?list=PLCQqUlIAw2cCuc3gRV9jIBGHeekVyBUnC)
- [Sascha Koenig Repository](https://code.m3ta.dev/m3tam3re/nixcfg)
