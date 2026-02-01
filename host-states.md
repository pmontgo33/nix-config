# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, last rebuild date, and OCI container status. This helps identify which hosts need updates or maintenance.

Last updated: 2026-01-30

## Host Status Table

| Host Name | NixOS Version | Last Verified | Last Rebuild | Containers | Notes |
|-----------|---------------|---------------|--------------|------------|-------|
| ali-book | 25.11 (Xantusia) | 2026-01-17 | 2026-01-10 | None | Upgraded to 25.11 |
| audiobookshelf | 25.11 (Xantusia) | 2026-01-30 | 2026-01-02 | **audiobookshelf**: latest ✓ | Upgraded to 25.11 |
| bifrost | 25.11 (Xantusia) | 2026-01-30 | 2026-01-10 | None | Upgraded to 25.11 |
| emma-book | 25.11 (Xantusia) | 2026-01-03 | 2026-01-02 | None | Upgraded to 25.11 |
| endurain | 25.11 (Xantusia) | 2026-01-30 | 2025-12-10 | **endurain**: v0.16.0 ⚠️ | Upgraded to 25.11 |
| forgejo | 25.11 (Xantusia) | 2026-01-30 | 2025-12-04 | None | Upgraded to 25.11 |
| frigate | 25.11 (Xantusia) | 2026-01-30 | 2025-12-30 | **frigate**: 0.16.0 ⚠️ | Upgraded to 25.11 |
| grist | 25.11 (Xantusia) | 2026-01-30 | 2025-12-10 | **grist**: latest ⚠️ | Upgraded to 25.11 |
| homepage | 25.11 (Xantusia) | 2026-01-30 | 2026-01-09 | None | Upgraded to 25.11 |
| jellyfin | 25.11 (Xantusia) | 2026-01-30 | 2026-01-17 | None | Upgraded to 25.11 |
| local-proxy | 25.11 (Xantusia) | 2026-01-30 | 2026-01-10 | None | Upgraded to 25.11 |
| mealie | 25.11 (Xantusia) | 2026-01-30 | 2025-12-26 | None | Upgraded to 25.11 |
| moltbot | 25.11 (Xantusia) | 2026-01-30 | 2026-01-29 | None | Upgraded to 25.11 |
| netalertx | 25.11 (Xantusia) | 2026-01-13 | 2026-01-09 | **netalertx**: 25.11 ⚠️ | Upgraded to 25.11 |
| nextcloud | 25.11 (Xantusia) | 2026-01-30 | 2025-12-11 | None | Upgraded to 25.11 |
| nix-fury | 25.11 (Xantusia) | 2026-01-30 | 2025-12-30 | **simplex-relay**: latest ✓<br>**myspeed**: latest ✓ | Upgraded to 25.11 |
| omada | 25.11 (Xantusia) | 2026-01-30 | 2025-12-30 | **omada-controller**: 6.0.0.25 ⚠️ | Upgraded to 25.11 |
| omnitools | 25.11 (Xantusia) | 2026-01-30 | 2025-12-10 | **omni-tools**: 0.6.0 ✓ | Upgraded to 25.11 |
| paperless-ngx | 25.11 (Xantusia) | 2026-01-30 | 2026-01-10 | **paperless-ai**: latest ⚠️ | Upgraded to 25.11 |
| plasma-vm-nixos | 25.11 (Xantusia) | 2026-01-30 | 2025-12-30 | None | Upgraded to 25.11 |
| pocket-id | 25.11 (Xantusia) | 2026-01-30 | 2025-12-06 | None | Upgraded to 25.11 |
| tesseract | 25.11 (Xantusia) | 2026-01-29 | 2026-01-28 | None | Upgraded to 25.11 |
| wallabag | 25.11 (Xantusia) | 2026-01-30 | 2026-01-15 | **wallabag**: latest ✓ | Upgraded to 25.11 |
| yondu | 25.11 (Xantusia) | 2026-01-30 | 2025-12-22 | **huntarr**: latest ⚠️<br>**flaresolverr**: latest ⚠️<br>**gluetun**: latest ⚠️<br>**dispatcharr**: latest ⚠️<br>**prowlarr**: latest ⚠️<br>**transmission_novpn**: latest ⚠️<br>**radarr**: latest ⚠️<br>**sonarr**: latest ⚠️<br>**bazarr**: latest ⚠️<br>**unpackerr**: latest ⚠️<br>**pinchflat**: latest ✓<br>**jackett**: latest ⚠️<br>**qbittorrent**: latest ⚠️<br>**youtarr**: latest ⚠️ | Upgraded to 25.11 |

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
