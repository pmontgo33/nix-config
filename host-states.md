# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, and last rebuild date. This helps identify which hosts need updates or maintenance.

Last updated: 2025-12-01

## Host Status Table

| Host Name | Status | NixOS Version | Last Verified | Last Rebuild | Notes |
|-----------|--------|---------------|---------------|--------------|-------|
| tesseract | ✅ Online | 25.11 (Xantusia) | 2025-12-01 | 2025-11-30 | Upgraded to 25.11 |
| plasma-vm-nixos | ✅ Online | 25.11 (Xantusia) | 2025-12-01 | 2025-11-30 | Upgraded to 25.11 |
| nix-fury | ✅ Online | 25.11 (Xantusia) | 2025-12-01 | 2025-11-30 | Upgraded to 25.11 |
| emma-book | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| yondu | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| bifrost | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| local-proxy | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| jellyfin | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| homepage | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| endurain | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| grist | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| nextcloud | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| forgejo | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| omnitools | ✅ Online | 25.05 (Warbler) | 2025-12-01 | 2025-11-16 | Needs upgrade to 25.11 |
| hp-nixos | 🔴 Unreachable | Unknown | 2025-12-01 | Unknown | Connection timed out |
| ali-book | 🔴 Unreachable | Unknown | 2025-12-01 | Unknown | Connection timed out |
| cora-book | 🔴 Unreachable | Unknown | 2025-12-01 | Unknown | Connection timed out |

## Development/Special Hosts (Not Tracked)

- **nixbook-installer** - Installation media, not a running system
- **nxc-base** - Base container template
- **lxc-tailscale** - Tailscale container template
- **immich** - Development host
- **erpnext** - Development host
- **pocket-id** - Development host
- **onlyoffice** - Development host

## Summary

- **Total Production Hosts:** 17
- **Upgraded to 25.11:** 3 (18%)
- **Still on 25.05:** 11 (65%)
- **Unreachable:** 3 (18%)

## Upgrade Priority

1. High priority services: nextcloud, forgejo, jellyfin
2. Infrastructure: bifrost, local-proxy, homepage
3. Applications: grist, endurain, omnitools, yondu
4. Laptops: emma-book (when online)
5. Investigate unreachable hosts: hp-nixos, ali-book, cora-book
