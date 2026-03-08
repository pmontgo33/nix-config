---
# Critical Packages Inventory (Phase 1)

This document tracks the critical packages across the homelab infrastructure as identified from `patrick/nix-config`. This is the source of truth for "Major" update monitoring.

**Status:** Expanded Unified Inventory (Mar 5, 2026)

---

## 1. Core Infrastructure & Channels

| Package | Current Version/Revision/Channel | Source |
|---|---|---|
| **nixpkgs** | `nixos-25.11` | `flake.nix` |
| **nixpkgs-unstable** | `nixos-unstable` | `flake.nix` |
| **home-manager** | `release-25.11` | `flake.nix` |

---

## 2. Homelab Host Inventory (Unified)

| Host Name          | Type        | Critical Package(s) / Services                                                                                                                                    | Version / Tag / Channel  | Config Location                        |
| ------------------ | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ | -------------------------------------- |
| **nix-fury**       | Server      | `uptime-kuma`, `gotify-server`, `glance`, `scrutiny`, `dozzle`, `ntfy-sh`                                                                                         | `nixpkgs-unstable`       | `hosts/nix-fury/default.nix`           |
| **yondu**          | Server      | `sonarr`, `radarr`, `prowlarr`, `bazarr`, `transmission`, `pinchflat`, `gluetun`, `flaresolverr`, `unpackerr`, `youtarr`, `jackett`, `qbittorrent`, `dispatcharr` | `nixos-25.11`            | `hosts/yondu/default.nix`              |
| **nextcloud**      | Server      | `nextcloud`, `onlyoffice-documentserver`                                                                                                                          | `nixos-25.11` / `latest` | `hosts/nextcloud/default.nix`          |
| **tesseract**      | Workstation | `plasma-manager`, `nix-flatpak`, `via`, `vscode`                                                                                                                  | `nixos-25.11`            | `hosts/tesseract/default.nix`          |
| **hp-nixos**       | Workstation | Base System, `home-manager`                                                                                                                                       | `nixos-25.11`            | `hosts/hp-nixos/default.nix`           |
| **bifrost**        | Server      | `unbound`, `prometheus-node-exporter`                                                                                                                             | `nixos-25.11`            | `hosts/bifrost/default.nix`            |
| **ali-book**       | Laptop      | `home-manager`, Plasma Desktop                                                                                                                                    | `release-25.11`          | `hosts/nixbooks/ali-book.nix`          |
| **emma-book**      | Laptop      | `home-manager`, Plasma Desktop                                                                                                                                    | `release-25.11`          | `hosts/nixbooks/emma-book.nix`         |
| **cora-book**      | Laptop      | `home-manager`, Plasma Desktop                                                                                                                                    | `release-25.11`          | `hosts/nixbooks/cora-book.nix`         |
| **frigate**        | LXC (NXC)   | `frigate`                                                                                                                                                         | `stable`                 | `hosts/nxc/frigate/default.nix`        |
| **audiobookshelf** | LXC (NXC)   | `audiobookshelf-server`                                                                                                                                           | `latest`                 | `hosts/nxc/audiobookshelf/default.nix` |
| **jellyfin**       | LXC (NXC)   | `jellyfin`, `jellyfin-web`, `jellyfin-ffmpeg`                                                                                                                     | `latest`                 | `hosts/nxc/jellyfin/default.nix`       |
| **forgejo**        | LXC (NXC)   | `forgejo`, `forgejo-mcp`                                                                                                                                          | `nixos-25.11` / `main`   | `hosts/nxc/forgejo/default.nix`        |
| **openclaw**       | LXC (NXC)   | `openclaw`, `openclaw-browser-control`                                                                                                                            | `nix-openclaw (main)`    | `hosts/nxc/openclaw/default.nix`       |
| **immich**         | LXC (NXC)   | `immich-server`, `immich-machine-learning`                                                                                                                        | `stable`                 | `hosts/nxc/immich/default.nix`         |
| **paperless-ngx**  | LXC (NXC)   | `paperless-ngx`, `paperless-ai`                                                                                                                                   | `latest`                 | `hosts/nxc/paperless-ngx/default.nix`  |
| **wallabag**       | LXC (NXC)   | `wallabag`                                                                                                                                                        | `latest`                 | `hosts/nxc/wallabag/default.nix`       |
| **mealie**         | LXC (NXC)   | `mealie`                                                                                                                                                          | `latest`                 | `hosts/nxc/mealie/default.nix`         |
| **grist**          | LXC (NXC)   | `grist-core`                                                                                                                                                      | `latest`                 | `hosts/nxc/grist/default.nix`          |
| **homepage**       | LXC (NXC)   | `homepage`                                                                                                                                                        | `latest`                 | `hosts/nxc/homepage/default.nix`       |
| **omada**          | LXC (NXC)   | `omada-controller`                                                                                                                                                | `6.0.x`                  | `hosts/nxc/omada/default.nix`          |
| **netalertx**      | LXC (NXC)   | `netalertx`                                                                                                                                                       | `latest`                 | `hosts/nxc/netalertx/default.nix`      |
| **obsidian**       | LXC (NXC)   | `obsidian-remote`                                                                                                                                                 | `latest`                 | `hosts/nxc/obsidian/default.nix`       |
| **local-proxy**    | LXC (NXC)   | `nginx`, `certbot`                                                                                                                                                | `nixos-25.11`            | `hosts/nxc/local-proxy/default.nix`    |
| **pocket-id**      | LXC (NXC)   | `pocket-id`                                                                                                                                                       | `latest`                 | `hosts/nxc/pocket-id/default.nix`      |
| **omnitools**      | LXC (NXC)   | `omni-tools`                                                                                                                                                      | `0.6.0`                  | `hosts/nxc/omnitools/default.nix`      |
| **erpnext**        | Dev (LXC)   | `erpnext` (Frappe framework)                                                                                                                                      | `latest`                 | `hosts/dev/erpnext/default.nix`        |
| **onlyoffice**     | Dev (LXC)   | `onlyoffice-documentserver`                                                                                                                                       | `latest`                 | `hosts/dev/onlyoffice/default.nix`     |

---

## 3. Definition of "Major" Updates

Based on this inventory, a **Major Update** is defined as:
1. **System level:** A bump in the `nixpkgs` version (e.g., 25.11 -> 26.05).
2. **Container level:** A version jump in the upstream image (e.g., Frigate v0.13 -> v0.14) or a security-critical CVE release.
3. **Application level:** Large version increments for targeted apps (e.g., Nextcloud 29 -> 30, Omada 5.x -> 6.x).

---

*Last Updated: 2026-03-07 08:17 EST*
