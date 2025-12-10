# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, and last rebuild date. This helps identify which hosts need updates or maintenance.

Last updated: 2025-12-09 

## Host Status Table

| Host Name | Status | NixOS Version | Last Verified | Last Rebuild | Notes |
|-----------|--------|---------------|---------------|--------------|-------|
| bifrost | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-06 | Upgraded to 25.11 |
| emma-book | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-09 | Upgraded to 25.11 |
| endurain | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| forgejo | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| grist | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| homepage | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| jellyfin | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| local-proxy | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-05 | Upgraded to 25.11 |
| nextcloud | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |
| nix-fury | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-07 | Upgraded to 25.11 |
| omnitools | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-06 | Upgraded to 25.11 |
| plasma-vm-nixos | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-05 | Upgraded to 25.11 |
| pocket-id | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-06 | Upgraded to 25.11 |
| tesseract | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-06 | Upgraded to 25.11 |
| yondu | ✅ Online | 25.11 (Xantusia) | 2025-12-09 | 2025-12-04 | Upgraded to 25.11 |

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
