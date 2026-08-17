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

## Tests

Run from the repository root in a Python environment with Nix available:

```bash
python -m unittest discover -s tests -p 'test_dns_migration.py' -v
```
