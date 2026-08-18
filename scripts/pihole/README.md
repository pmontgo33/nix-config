# Pi-hole and OPNsense audit

`audit.py` collects a sanitized, deterministic state snapshot using supported APIs.
It is read-only: Pi-hole uses `POST /api/auth` only to obtain a temporary session,
then reads the configured endpoints with `GET`; OPNsense uses `GET` only over HTTPS.
No database files, replication, or configuration mutation endpoints are used.

## Fixture mode

Use fixture mode for deterministic review and tests:

```sh
python3 scripts/pihole/audit.py \
  --fixture tests/fixtures/pihole_audit/minimal.json \
  --output /tmp/pihole-audit.json
```

The exporter redacts passwords, tokens, session/auth labels, UUIDs, MAC addresses,
IPv4/IPv6 addresses, and raw client identifiers from all exported string values.
Identifier-shaped values embedded in descriptions are redacted too. Supported
resource envelopes validate `rows`, `current`, `rowCount`, and `total` metadata
before fingerprints are calculated; complete `rows` metadata is retained in the
report and fingerprint. Unknown roots/resources, unsafe instance names, incomplete
rows envelopes, and malformed payloads fail closed. JSON `NaN`/`Infinity` constants are rejected. Requests do not follow
redirects, and the only POST endpoint exposed is Pi-hole `/api/auth`; GETs are
restricted to the documented collector endpoints. Pi-hole session IDs must match
an HTTP-header-safe shape. Request errors intentionally contain no URL,
credential, response body, or chained exception.

## Live mode

Pi-hole specifications use `NAME=URL:PASSWORD_ENV`; the password value is read
only from the named environment variable and is never written to the report:

```sh
python3 scripts/pihole/audit.py --live \
  --pihole 'a=https://pihole-a.example:PIHOLE_A_PASSWORD' \
  --pihole 'b=https://pihole-b.example:PIHOLE_B_PASSWORD' \
  --opnsense-url 'https://router.montycasa.net' \
  --output /tmp/dual-pihole-audit.json
```

OPNsense credentials default to `OPNSENSE_KEY` and `OPNSENSE_SECRET`; override
those environment-variable names with `--opnsense-key-env` and
`--opnsense-secret-env` when needed.

The output is an audit artifact only. It does not apply Nix policy, alter either
Pi-hole, modify OPNsense, or advertise DNS clients.
