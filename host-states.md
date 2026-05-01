# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, last rebuild date, and OCI container status. This helps identify which hosts need updates or maintenance.

Last updated: 2026-04-30

## Host Status Table

| Host Name | NixOS Version | Last Verified | Last Rebuild | Containers | Notes |
|-----------|---------------|---------------|--------------|------------|-------|
| ali-book | 25.11 (Xantusia) | 2026-04-28 | 2026-04-24 | None | Upgraded to 25.11 |
| audiobookshelf | 25.11 (Xantusia) | 2026-04-30 | 2026-03-11 | **audiobookshelf**: 2.32.1 ⚠️ | Upgraded to 25.11 |
| bifrost | 25.11 (Xantusia) | 2026-04-30 | 2026-01-10 | None | Upgraded to 25.11 |
| emma-book | 25.11 (Xantusia) | 2026-04-26 | 2026-04-25 | None | Upgraded to 25.11 |
| endurain | 25.11 (Xantusia) | 2026-04-30 | 2026-04-29 | **endurain**: v0.17.7 ✓ | Upgraded to 25.11 |
| forgejo | 25.11 (Xantusia) | 2026-04-30 | 2026-04-27 | None | Upgraded to 25.11 |
| frigate | 25.11 (Xantusia) | 2026-04-30 | 2026-03-09 | **frigate**: 0.17.0 ⚠️ | Upgraded to 25.11 |
| grist | 25.11 (Xantusia) | 2026-03-31 | 2025-12-10 | **grist**: latest ⚠️ | Upgraded to 25.11 |
| homepage | 25.11 (Xantusia) | 2026-04-26 | 2026-04-11 | None | Upgraded to 25.11 |
| jellyfin | 25.11 (Xantusia) | 2026-04-30 | 2026-04-25 | None | Upgraded to 25.11 |
| local-proxy | 25.11 (Xantusia) | 2026-04-30 | 2026-04-27 | None | Upgraded to 25.11 |
| mealie | 25.11 (Xantusia) | 2026-04-30 | 2026-03-31 | None | Upgraded to 25.11 |
| moltbot | 25.11 (Xantusia) | 2026-02-03 | 2026-01-30 | None | Upgraded to 25.11 |
| netalertx | 25.11 (Xantusia) | 2026-04-30 | 2026-04-26 | **netalertx**: 25.11 ⚠️ | Upgraded to 25.11 |
| nextcloud | 25.11 (Xantusia) | 2026-04-30 | 2026-02-10 | None | Upgraded to 25.11 |
| nix-fury | 25.11 (Xantusia) | 2026-04-30 | 2026-04-26 | **myspeed**: latest ✓ | Upgraded to 25.11 |
| obsidian | 25.11 (Xantusia) | 2026-04-30 | 2026-04-12 | None | Upgraded to 25.11 |
| ollama | 25.11 (Xantusia) | 2026-04-30 | 2026-04-09 | None | Upgraded to 25.11 |
| omada | 25.11 (Xantusia) | 2026-04-30 | 2026-04-26 | **omada-controller**: 6.0.0.25 ⚠️ | Upgraded to 25.11 |
| omnitools | 25.11 (Xantusia) | 2026-02-15 | 2025-12-10 | **omni-tools**: 0.6.0 ✓ | Upgraded to 25.11 |
| openclaw | 25.11 (Xantusia) | 2026-04-30 | 2026-04-25 | None | Upgraded to 25.11 |
| paperless-ngx | 25.11 (Xantusia) | 2026-04-30 | 2026-03-31 | **paperless-ai**: latest ⚠️ | Upgraded to 25.11 |
| plasma-vm-nixos | 25.11 (Xantusia) | 2026-02-27 | 2025-12-30 | None | Upgraded to 25.11 |
| pocket-id | 25.11 (Xantusia) | 2026-04-30 | 2026-04-26 | None | Upgraded to 25.11 |
| searxng | 25.11 (Xantusia) | 2026-04-30 | 2026-03-31 | None | Upgraded to 25.11 |
| tesseract | 25.11 (Xantusia) | 2026-04-29 | 2026-04-23 | None | Upgraded to 25.11 |
| wallabag | 25.11 (Xantusia) | 2026-04-30 | 2026-03-31 | **wallabag**: 2.6.14 ✓ | Upgraded to 25.11 |
| yondu | 25.11 (Xantusia) | 2026-04-30 | 2026-02-27 | **flaresolverr**: latest ⚠️<br>**dispatcharr**: latest ⚠️<br>**prowlarr**: latest ⚠️<br>**radarr**: latest ⚠️<br>**sonarr**: latest ⚠️<br>**transmission_novpn**: latest ⚠️<br>**bazarr**: latest ⚠️<br>**pinchflat**: latest ✓<br>**youtarr**: latest ⚠️<br>**unpackerr**: latest ⚠️<br>**gluetun**: latest ⚠️<br>**jackett**: latest ⚠️<br>**qbittorrent**: latest ⚠️ | Upgraded to 25.11 |

## Development/Special Hosts (Not Tracked)

- **nixbook-installer** - Installation media, not a running system
- **nxc-base** - Base container template
- **lxc-tailscale** - Tailscale container template
- **immich** - Development host
- **erpnext** - Development host
- **pocket-id** - Development host
- **onlyoffice** - Development host

## Summary

Statistics are calculated from online hosts only.

## Upgrade Priority

1. High priority services: nextcloud, forgejo, jellyfin
2. Infrastructure: bifrost, local-proxy, homepage
3. Applications: grist, endurain, omnitools, yondu
4. Laptops: emma-book (when online)
