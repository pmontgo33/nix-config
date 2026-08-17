# Gate 1A DNS inventory rendering

This directory contains offline, review-only renderers. They do not call OPNsense,
Pi-hole, Proxmox, or Tailscale.

## Validate the Nix inventory

```bash
scripts/dns_migration/render_inventory.py \
  --inventory-nix inventory/default.nix \
  --check
```

## Render consumer views

```bash
mkdir -p /tmp/pihole-gate-1a
scripts/dns_migration/render_inventory.py \
  --inventory-nix inventory/default.nix \
  --output /tmp/pihole-gate-1a/inventory-render.json

scripts/dns_migration/render_dnsmasq_plan.py \
  --rendered-inventory /tmp/pihole-gate-1a/inventory-render.json \
  --output /tmp/pihole-gate-1a/dnsmasq-plan.json
```

The dnsmasq plan has `apply: false`, keeps DHCPv6/RA under OPNsense ownership,
and marks reservations pending until an encrypted runtime identity map resolves
each `identityRef`. No MAC address or client identifier belongs in this source
inventory.

## OPNsense apply boundary

The renderer is not an OPNsense adapter. It must never contact the firewall,
write `config.xml`, restart DHCP, or apply a generated plan.

The Gate 1B apply path is:

1. Prefer a dedicated least-privilege OPNsense API credential with dnsmasq
   read/apply/read-back access. The existing discovery credential currently
   receives `403` from dnsmasq control endpoints.
2. If that ACL cannot be granted, Patrick manually applies a reviewed,
   identity-resolved artifact during the approved maintenance window.
3. Before applying, capture the current ISC state, lease behavior, and rollback
   commands. Never run ISC DHCP and dnsmasq concurrently on the same serving
   interface.
4. After applying, read back OPNsense state and validate one representative
   DHCP renewal on LAN, IoT, and Guest. Abort and restore ISC on any gateway,
   DNS, lease, or custom-option mismatch.

Do not edit OPNsense `config.xml` directly. Do not advertise Pi-hole during
this DHCP-only phase; AdGuard remains the DNS service until DHCP acceptance is
complete.

## Tests

Run from the repository root in a Python environment with Nix available:

```bash
python -m unittest discover -s tests -p 'test_dns_migration.py' -v
```
