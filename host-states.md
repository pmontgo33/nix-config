# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, last rebuild date, and OCI container status. This helps identify which hosts need updates or maintenance.

Last updated: 2026-06-10

## Host Status Table

| Host Name | NixOS Version | Last Verified | Last Rebuild | Containers | Notes |
|-----------|---------------|---------------|--------------|------------|-------|
| ali-book | 25.11 (Xantusia) | 2026-06-05 | 2026-05-25 | None | Upgraded to 25.11 |
| audiobookshelf | 25.11 (Xantusia) | 2026-06-10 | 2026-03-11 | **audiobookshelf**: 2.32.1 ⚠️ | Upgraded to 25.11 |
| bifrost | 25.11 (Xantusia) | 2026-06-10 | 2026-01-10 | None | Upgraded to 25.11 |
| emma-book | 25.11 (Xantusia) | 2026-05-13 | 2015-12-31 | None | Upgraded to 25.11 |
| endurain | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | **endurain**: v0.17.7 ✓ | Upgraded to 25.11 |
| forgejo | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| frigate | 25.11 (Xantusia) | 2026-06-09 | 2026-05-23 | **frigate**: 0.17.1 ✓ | Upgraded to 25.11 |
| grist | 25.11 (Xantusia) | 2026-03-31 | 2025-12-10 | **grist**: latest ⚠️ | Upgraded to 25.11 |
| hermes | 25.11 (Xantusia) | 2026-06-10 | 2026-06-07 | None | Upgraded to 25.11 |
| homepage | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| jellyfin | 25.11 (Xantusia) | 2026-06-10 | 2026-05-30 | None | Upgraded to 25.11 |
| local-proxy | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| mealie | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| moltbot | 25.11 (Xantusia) | 2026-02-03 | 2026-01-30 | None | Upgraded to 25.11 |
| murdock | 25.11 (Xantusia) | 2026-06-09 | 2026-06-08 | None | Upgraded to 25.11 |
| netalertx | 25.11 (Xantusia) | 2026-05-21 | 2026-05-13 | **netalertx**: 25.11 ⚠️ | Upgraded to 25.11 |
| nextcloud | 25.11 (Xantusia) | 2026-06-10 | 2026-02-10 | None | Upgraded to 25.11 |
| nix-fury | 25.11 (Xantusia) | 2026-06-10 | 2026-05-23 | **myspeed**: latest ✓ | Upgraded to 25.11 |
| obsidian | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| ollama | 25.11 (Xantusia) | 2026-06-10 | 2026-05-23 | None | Upgraded to 25.11 |
| omada | 25.11 (Xantusia) | 2026-06-10 | 2026-05-13 | **omada-controller**: 6.0.0.25 ⚠️ | Upgraded to 25.11 |
| omnitools | 25.11 (Xantusia) | 2026-02-15 | 2025-12-10 | **omni-tools**: 0.6.0 ✓ | Upgraded to 25.11 |
| openclaw | 25.11 (Xantusia) | 2026-05-23 | 2026-04-30 | None | Upgraded to 25.11 |
| paperless-ngx | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | **paperless-ai**: latest ⚠️ | Upgraded to 25.11 |
| plasma-vm-nixos | 25.11 (Xantusia) | 2026-05-14 | 2026-05-12 | None | Upgraded to 25.11 |
| pocket-id | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| searxng | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | None | Upgraded to 25.11 |
| tesseract | 25.11 (Xantusia) | 2026-05-22 | 2026-05-22 | None | Upgraded to 25.11 |
| wallabag | 25.11 (Xantusia) | 2026-06-10 | 2026-06-05 | **wallabag**: 2.6.14 ✓ | Upgraded to 25.11 |
| yondu | 25.11 (Xantusia) | 2026-06-10 | 2026-06-02 | **pinchflat**: latest ✓<br>**unpackerr**: latest ⚠️<br>**gluetun**: latest ⚠️<br>**transmission_novpn**: 4.0.5 ⚠️<br>**qbittorrent**: latest ⚠️<br>**prowlarr**: latest ⚠️<br>**bazarr**: latest ⚠️<br>**sonarr**: latest ⚠️<br>**radarr**: latest ⚠️<br>**youtarr**: latest ⚠️<br>**jackett**: latest ⚠️<br>**flaresolverr**: latest ✓<br>**dispatcharr**: latest ⚠️ | Upgraded to 25.11 |

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
