# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, last rebuild date, and OCI container status. This helps identify which hosts need updates or maintenance.

Last updated: 2025-12-26

## Host Status Table

| Host Name | NixOS Version | Last Verified | Last Rebuild | Containers | Notes |
|-----------|---------------|---------------|--------------|------------|-------|
| ali-book | 25.11 (Xantusia) | 2025-12-24 | 2025-12-19 | None | Upgraded to 25.11 |
| bifrost | 25.11 (Xantusia) | 2025-12-26 | 2025-12-11 | None | Upgraded to 25.11 |
| emma-book | 25.11 (Xantusia) | 2025-12-24 | 2025-12-19 | None | Upgraded to 25.11 |
| endurain | 25.11 (Xantusia) | 2025-12-26 | 2025-12-10 | **endurain**: v0.16.0 ⚠️ | Upgraded to 25.11 |
| forgejo | 25.11 (Xantusia) | 2025-12-26 | 2025-12-04 | None | Upgraded to 25.11 |
| frigate | 25.11 (Xantusia) | 2025-12-26 | 2025-12-17 | **frigate**: 0.16.0 ⚠️ | Upgraded to 25.11 |
| grist | 25.11 (Xantusia) | 2025-12-26 | 2025-12-10 | **grist**: latest ⚠️ | Upgraded to 25.11 |
| homepage | 25.11 (Xantusia) | 2025-12-26 | 2025-12-22 | None | Upgraded to 25.11 |
| jellyfin | 25.11 (Xantusia) | 2025-12-26 | 2025-12-22 | None | Upgraded to 25.11 |
| local-proxy | 25.11 (Xantusia) | 2025-12-26 | 2025-12-23 | None | Upgraded to 25.11 |
| nextcloud | 25.11 (Xantusia) | 2025-12-26 | 2025-12-11 | None | Upgraded to 25.11 |
| nix-fury | 25.11 (Xantusia) | 2025-12-26 | 2025-12-19 | **myspeed**: latest ✓ | Upgraded to 25.11 |
| omada | 25.11 (Xantusia) | 2025-12-26 | 2025-12-23 | **omada-controller**: 6.0.0.25 ✓ | Upgraded to 25.11 |
| omnitools | 25.11 (Xantusia) | 2025-12-26 | 2025-12-10 | **omni-tools**: 0.6.0 ✓ | Upgraded to 25.11 |
| plasma-vm-nixos | 25.11 (Xantusia) | 2025-12-22 | 2025-12-05 | None | Upgraded to 25.11 |
| pocket-id | 25.11 (Xantusia) | 2025-12-26 | 2025-12-06 | None | Upgraded to 25.11 |
| tesseract | 25.11 (Xantusia) | 2025-12-24 | 2025-12-22 | None | Upgraded to 25.11 |
| yondu | 25.11 (Xantusia) | 2025-12-26 | 2025-12-22 | **huntarr**: latest ✓<br>**flaresolverr**: latest ⚠️<br>**gluetun**: latest ⚠️<br>**dispatcharr**: latest ✓<br>**prowlarr**: latest ✓<br>**transmission_novpn**: latest ✓<br>**radarr**: latest ✓<br>**sonarr**: latest ✓<br>**bazarr**: latest ⚠️<br>**unpackerr**: latest ⚠️<br>**pinchflat**: latest ✓<br>**jackett**: latest ⚠️<br>**qbittorrent**: latest ✓<br>**youtarr**: latest ✓ | Upgraded to 25.11 |

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
