#!/usr/bin/env python3
"""Pi-hole bypass — install, enable, disable, uninstall, status.

This script manages an OPNsense outbound NAT redirect ruleset that bypasses
Pi-hole filtering by rewriting outbound UDP/53 and TCP/53 traffic from
LAN/IoT/Guest to OPNsense Unbound (`192.168.86.1:53`).

It does NOT use DNS to do its work. Every control-plane read and write goes
through the OPNsense HTTP API over the LAN.

Lifecycle (5 actions):

  --install     Add 6 disabled rules tagged with the bypass descr. They
                persist in the firewall table until --uninstall. Safe
                (disabled = no traffic is rewritten).

  --enable      Flip the installed rules to enabled=1 and confirm the
                Unbound resolver is reachable on TCP/53. This is the
                ONLY action that changes live DNS behavior.

  --disable     Flip installed rules back to enabled=0. The rules stay
                in the table but become no-ops.

  --uninstall   Remove the bypass rules entirely.

  --status      Read-only: report whether the rules are installed, enabled,
                disabled, or absent.

How it works:

  The PF rdr-to equivalent is:

    rdr pass on { lan opt1 opt2 } proto { udp tcp } from <lan_subnet> to any \
        port 53 -> 192.168.86.1 port 53

  Implemented as 6 outbound NAT rules in OPNsense (LAN/IoT/Guest × UDP/TCP).
  All rules share the same descr: "dns-path-switcher: pihole-bypass".
  Re-running --install is idempotent (skips if already present).

Why an "always-on but disabled" install:

  The bypass is meant for emergency use ("both Pi-holes are down"). Leaving
  the rules installed in the firewall table (just disabled) means that
  flipping the bypass on is a single, fast API call instead of an install
  under stress. It also means the rules survive OPNsense reboots and config
  restores.

Credentials:

  - OPNsense API:
      OPNSENSE_KEY          (required for any non-status action)
      OPNSENSE_SECRET       (required alongside OPNSENSE_KEY)
      OPNSENSE_API_PRIMARY  (HTTPS endpoint, default https://router.montycasa.net)
      OPNSENSE_API_FALLBACK (HTTP endpoint, default http://192.168.86.1)

OPNsense ACL requirements (verified 2026-08-23 against OPNsense 26.1.11_6):

  - firewall:source_nat:add_rule, del_rule, get_rule, set_rule,
    search_rule, apply — REQUIRED for install/uninstall/enable/disable.
    NOT currently granted on the canonical hermes credential. Add via
    OPNsense UI: System → Access → Users → (user) → Effective Privileges.

OPNsense 26.1 API notes:

  - Outbound NAT rules live under /api/firewall/source_nat/*, NOT
    /api/firewall/nat/* (the latter returns 404 on 26.1).

Scope limitations:

  - IPv4 only (ipprotocol=inet). IPv6 RDNSS bypass is NOT covered.
  - DoH (TCP/443 to known DoH hostnames) is NOT covered. If a device uses
    DoH, it goes around both Pi-hole and this bypass.

Exit codes:

  - 0 : success
  - 1 : usage / argument error
  - 2 : OPNsense API error
  - 3 : credentials missing or ACL insufficient
  - 4 : post-enable verify failed (rules already enabled; --disable to roll back)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BYPASS_TARGET_IP = "192.168.86.1"  # OPNsense Unbound
BYPASS_RULE_DESCR = "dns-path-switcher: pihole-bypass"

# Hard-coded literal-IP fallback for mutating POSTs. The default primary
# endpoint is hostname-based (router.montycasa.net); when the local DNS
# resolver is down (the very scenario this script exists for) the hostname
# transport fails. POSTs additionally try this literal-IP HTTPS endpoint
# before giving up, so the break-glass path works without DNS. GETs keep
# using the existing HTTP fallback (which is faster and read-only).
BYPASS_POST_HTTPS_FALLBACK = "https://192.168.86.1"

# Authoritative-match field constants. Values are exact-match strings as
# observed live on OPNsense 26.1.11_6 and emitted by
# _build_bypass_rule_payload below. Operators tightening the install to
# use an alias for ``source`` would add the alias name to VALID_SOURCES;
# the validator accepts both "any" (what the script installs) and the
# configured alias (a future tightening).
LAN_SUBNET_ALIAS = "lan_subnet"
VALID_SOURCES = frozenset({"any", LAN_SUBNET_ALIAS})
EXPECTED_DESTINATION = "any"
EXPECTED_NATREFLECTION = "disable"

INTERFACE_ALIASES = ("lan", "opt1", "opt2")  # LAN, IoT, Guest
PROTOCOLS = ("udp", "tcp")
DNS_PORT = 53

# OPNsense 26.1 outbound NAT API paths.
NAT_API_SEARCH = "firewall/source_nat/search_rule"
NAT_API_ADD = "firewall/source_nat/add_rule"
NAT_API_GET = "firewall/source_nat/get_rule"
NAT_API_SET = "firewall/source_nat/set_rule"
NAT_API_DEL = "firewall/source_nat/del_rule"
NAT_API_APPLY = "firewall/source_nat/apply"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BypassError(RuntimeError):
    """Recoverable error during a bypass operation. Exit code 2.

    The ``post_send`` flag is set when the error was raised AFTER the
    HTTP request body was flushed to the wire (i.e. ``urlopen``
    returned a response object and ``r.read()`` then failed). For a
    mutating POST this means OPNsense may have already committed the
    change, so retrying against a second transport is unsafe. The POST
    fallback path uses this flag to refuse retry; the GET fallback path
    ignores it because reads are idempotent.
    """

    def __init__(self, message: str, *, post_send: bool = False) -> None:
        super().__init__(message)
        self.post_send = post_send


class CredentialError(BypassError):
    """Credentials missing or insufficient. Exit code 3."""


# ---------------------------------------------------------------------------
# OPNsense API
# ---------------------------------------------------------------------------


def _api_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    primary: str,
    fallback: str,
    key: str,
    secret: str,
) -> tuple[str, Any]:
    """Call the OPNsense API. Returns (transport_label, parsed_response).

    Transport matrix:

      * GETs try the hostname HTTPS primary, then the operator-configured
        HTTP fallback (preserved behaviour; reads are safe to retry on a
        second transport).
      * POSTs try the hostname HTTPS primary. On a *transport* failure
        (DNS or TCP unreachable), they additionally try the hard-coded
        literal-IP HTTPS fallback so the break-glass path still works
        when the local DNS resolver is down. POSTs never try the
        operator-configured HTTP fallback, because that would double-write
        against a misconfigured firewall. HTTPError from the hostname
        primary (e.g. 401/403/4xx/5xx) does NOT trigger the literal-IP
        fallback — a reachable-but-rejecting primary is an ACL or result
        failure, not a transport problem.
    """
    creds = base64.b64encode(f"{key}:{secret}".encode()).decode()
    ctx = _api_context()
    is_post = method.upper() == "POST"
    primary_base = primary.rstrip("/")
    primary_label = "https-primary"

    def _do_request(label: str, base: str) -> tuple[str, Any]:
        """Send one HTTP request and return (transport_label, parsed_response).

        Transport failures are split into two phases so the caller can
        decide whether retrying on a second transport is safe:

          * **Pre-send, retry-safe** — the ONLY urlopen-time error that is
            unambiguously pre-send is :class:`socket.gaierror` raised
            before the hostname could be resolved. The hostname never
            reached DNS resolution, so no TCP connection was opened and no
            HTTP body was flushed. This is the one case the outer caller
            is allowed to retry on a second transport — it is the entire
            purpose of the break-glass literal-IP POST fallback.

          * **Post-send, ambiguous** — every OTHER urlopen-time failure
            (URLError without a gaierror cause, TLS handshake errors, TCP
            resets mid-connect, EPIPE on body write, partial-request
            ConnectionError, raw OSError) is reported as
            ``BypassError(post_send=True)``. urlopen may have buffered and
            partially transmitted the request body before the error
            surfaced, so for a mutating POST OPNsense could already have
            committed the change by the time the exception propagates out.
            The outer caller treats this flag as a hard refusal to fall
            through to the literal-IP HTTPS POST fallback. For a GET the
            same flag is treated as a transport hiccup (reads are
            idempotent) and falls through to the operator-configured
            HTTP fallback.

          * **Post-send, unambiguous** — anything raised *after*
            ``urlopen`` returned a response object (the canonical case is
            ``r.read()`` failing mid-response: truncated payload, server
            reset, idle timeout) is also wrapped in
            ``BypassError(post_send=True)``.
        """
        url = f"{base}/api/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Basic {creds}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            opener = (
                urllib.request.urlopen(req, timeout=10, context=ctx)
                if base.startswith("https://")
                else urllib.request.urlopen(req, timeout=10)
            )
        except urllib.error.HTTPError:
            # CRITICAL ORDERING: HTTPError is a subclass of URLError
            # (urllib.error.HTTPError -> urllib.error.URLError -> OSError).
            # If we let the URLError branch below catch it, the outer
            # handler's ``except urllib.error.HTTPError as e:`` branch
            # (which maps 401/403 to CredentialError and routes other
            # status codes) becomes unreachable. An HTTPError means the
            # server was reached and explicitly returned a status; the
            # outer caller needs to see that raw HTTPError, not a wrapped
            # BypassError. Re-raise as-is.
            raise
        except (urllib.error.URLError,) as e:
            # urlopen raises URLError wrapping the underlying cause. If
            # the inner cause is a DNS-resolution failure (gaierror or
            # a URLError whose .reason is a gaierror), the hostname never
            # resolved, no TCP connection was opened, and no HTTP body was
            # flushed. That is the ONLY unambiguously pre-send transport
            # error and the outer caller is allowed to retry it on the
            # literal-IP POST fallback. Surface the raw URLError (still
            # carrying its gaierror .reason) so the outer caller can
            # classify it the same way it classifies a raw gaierror.
            inner = getattr(e, "reason", None)
            if isinstance(inner, socket.gaierror):
                raise
            # URLError that is NOT a gaierror in disguise: TLS handshake
            # failure, connection refused, server reset mid-handshake, etc.
            # urlopen may have partially transmitted the request body
            # before surfacing this, so for a mutating POST OPNsense
            # could already have committed the change. Treat as
            # ambiguous post-send.
            raise BypassError(
                f"OPNsense API {method} {path} on {label} raised "
                f"{type(e).__name__} (reason={type(inner).__name__ if inner is not None else 'None'}) "
                f"during urlopen (not a DNS-resolution failure; request "
                f"body may have been partially transmitted): "
                f"{e}",
                post_send=True,
            ) from e
        except socket.gaierror:
            # urlopen raised a raw gaierror (no urllib wrapper): the
            # hostname never resolved. Outer caller may safely retry.
            raise
        except (
            ConnectionError,
            OSError,
        ) as e:
            # Every other urlopen-time failure is AMBIGUOUS. urlopen may
            # have buffered and partially flushed the request body before
            # the error surfaced, so for a mutating POST OPNsense could
            # already have committed the change. Surface as
            # post_send=True so the outer POST handler refuses the
            # literal-IP fallback; the GET handler still falls through
            # because reads are idempotent.
            #
            # Note: socket.gaierror is a subclass of OSError, so it would
            # match here too — it is caught above by name (or via the
            # URLError-wraps-gaierror branch) first to preserve the
            # unambiguously-pre-send classification.
            raise BypassError(
                f"OPNsense API {method} {path} on {label} raised "
                f"{type(e).__name__} during urlopen (not a DNS-resolution "
                f"failure; request body may have been partially transmitted): "
                f"{e}",
                post_send=True,
            ) from e
        except Exception as e:
            # Catch-all for urlopen-time errors that are NOT
            # URLError/HTTPError/gaierror/ConnectionError/OSError.
            # The canonical case is ``http.client.BadStatusLine`` (raised
            # when the server closes the connection without sending a
            # valid HTTP status line); its MRO is
            # ``BadStatusLine -> HTTPException -> Exception`` only, so it
            # would otherwise escape this function and reach the outer
            # ``except Exception`` in ``_api_call`` — which sets
            # ``primary_transport_err`` and would fall through to the
            # literal-IP HTTPS POST fallback. That is unsafe for a
            # mutating POST: urlopen may have buffered and partially
            # transmitted the request body before the server reset, so
            # OPNsense could already have committed the mutation by the
            # time the exception propagates out. Wrap as
            # ``BypassError(post_send=True)`` so the outer POST handler
            # refuses the literal-IP fallback while the GET handler
            # still falls through (reads are idempotent).
            raise BypassError(
                f"OPNsense API {method} {path} on {label} raised "
                f"{type(e).__name__} during urlopen (not a transport-class "
                f"error this function classifies as safely retryable; "
                f"request body may have been partially transmitted): "
                f"{e}",
                post_send=True,
            ) from e
        # urlopen returned — the request body has been sent. From this
        # point on, an exception is post-send and must not be retried
        # against a different transport for a mutating POST.
        try:
            with opener as r:
                raw = r.read()
        except BypassError:
            raise
        except Exception as e:
            raise BypassError(
                f"OPNsense API {method} {path} failed AFTER request was sent "
                f"on {label}: {type(e).__name__}: {e}",
                post_send=True,
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw.decode(errors="replace")
        if isinstance(parsed, dict) and "error" in parsed:
            raise BypassError(f"OPNsense API {method} {path} returned an error response")
        return label, parsed

    primary_transport_err: str | None = None
    try:
        return _do_request(primary_label, primary_base)
    except urllib.error.HTTPError as e:
        # Reachable host, but rejected the call. Do NOT fall through to the
        # literal-IP fallback — that is a different host, not a transport
        # problem. Preserve the existing credential-error fast-path.
        body_text = e.read().decode(errors="replace")[:300]
        primary_err = f"{primary_label}: HTTP {e.code}: {body_text}"
        if e.code in (401, 403):
            raise CredentialError(
                f"OPNsense API rejected {method} {path} with HTTP {e.code}. "
                "Add the firewall:source_nat privilege to the credential "
                "(OPNsense UI: System → Access → Users → user → "
                "Effective Privileges)."
            ) from e
        if is_post:
            raise BypassError(
                f"OPNsense API {method} {path} failed: {primary_err}"
            ) from e
        primary_transport_err = primary_err
    except BypassError as e:
        if getattr(e, "post_send", False) and not is_post:
            # GET path: a mid-response read failure is a transport hiccup
            # (truncated payload, server reset) and reads are idempotent,
            # so treat it like a transport error and fall through to the
            # operator-configured HTTP fallback. For POSTs the same
            # condition re-raises immediately: OPNsense may have already
            # committed the mutation before the connection died, and
            # retrying the same write against a second transport risks
            # double-applying the change.
            primary_transport_err = (
                f"{primary_label}: {type(e).__name__}: {e} (post-send)"
            )
        else:
            raise
    except Exception as e:
        # Pre-send transport failures that ``_do_request`` propagates
        # as-is. After narrowing the pre-send classifier, the ONLY
        # non-BypassError exceptions that escape are DNS-resolution
        # failures (raw ``socket.gaierror`` or ``URLError`` whose
        # ``.reason`` is a ``gaierror``). Every other urlopen-time
        # failure is reported as BypassError(post_send=True) and caught
        # by the ``except BypassError`` branch above.
        primary_transport_err = f"{primary_label}: {type(e).__name__}: {e}"

    # GETs fall back to the operator-configured HTTP fallback on any
    # non-credential failure from the HTTPS primary. POSTs fall back to the
    # hard-coded literal-IP HTTPS endpoint only.
    if is_post:
        try:
            return _do_request("https-literal-fallback", BYPASS_POST_HTTPS_FALLBACK)
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")[:300]
            fallback_err = f"https-literal-fallback: HTTP {e.code}: {body_text}"
            if e.code in (401, 403):
                raise CredentialError(
                    f"OPNsense API rejected {method} {path} with HTTP {e.code}. "
                    "Add the firewall:source_nat privilege to the credential "
                    "(OPNsense UI: System → Access → Users → user → "
                    "Effective Privileges)."
                ) from e
            raise BypassError(
                f"OPNsense API {method} {path} failed: {primary_transport_err}; "
                f"{fallback_err}"
            ) from e
        except BypassError:
            raise
        except Exception as e:
            fallback_err = f"https-literal-fallback: {type(e).__name__}: {e}"
            raise BypassError(
                f"OPNsense API {method} {path} failed: {primary_transport_err}; "
                f"{fallback_err}"
            ) from e

    # GET path: try operator-configured HTTP fallback.
    fallback_base = fallback.rstrip("/")
    fallback_label = "http-fallback"
    try:
        return _do_request(fallback_label, fallback_base)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")[:300]
        fallback_err = f"{fallback_label}: HTTP {e.code}: {body_text}"
        if e.code in (401, 403):
            raise CredentialError(
                f"OPNsense API rejected {method} {path} with HTTP {e.code}. "
                "Add the firewall:source_nat privilege to the credential "
                "(OPNsense UI: System → Access → Users → user → "
                "Effective Privileges)."
            ) from e
        raise BypassError(
            f"OPNsense API {method} {path} failed: {primary_transport_err}; "
            f"{fallback_err}"
        ) from e
    except BypassError:
        raise
    except Exception as e:
        fallback_err = f"{fallback_label}: {type(e).__name__}: {e}"
        raise BypassError(
            f"OPNsense API {method} {path} failed: {primary_transport_err}; "
            f"{fallback_err}"
        ) from e


def _search_bypass_rules(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    """Return all outbound NAT rules tagged with the bypass label.

    OPNsense's source_nat search response uses ``description`` for the row
    label, while the documented add payload still uses ``descr``.  Accept
    either spelling when reading rows so status and lifecycle operations use
    the live response shape without changing the add contract.
    """
    _, data = _api_call(
        "GET", NAT_API_SEARCH, primary=primary, fallback=fallback, key=key, secret=secret
    )
    if not isinstance(data, dict):
        raise BypassError("OPNsense source_nat search response is not an object")
    for field in ("current", "rowCount", "rows", "total"):
        if field not in data:
            raise BypassError(f"OPNsense source_nat search response is missing {field}")
    for field in ("current", "rowCount", "total"):
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BypassError(f"OPNsense source_nat search response has invalid {field}")
    rows_obj: Any = data["rows"]
    if not isinstance(rows_obj, list):
        raise BypassError("OPNsense source_nat search response is missing a rows list")
    if not all(isinstance(row, dict) for row in rows_obj):
        raise BypassError("OPNsense source_nat search response contains a non-object row")
    rows: list[dict[str, Any]] = rows_obj
    matching: list[dict[str, Any]] = []
    for row in rows:
        if row.get("description") != BYPASS_RULE_DESCR and row.get("descr") != BYPASS_RULE_DESCR:
            continue
        _validate_managed_row(row)
        matching.append(row)
    return matching


def _validate_managed_row(row: dict[str, Any]) -> None:
    """Validate the identity, redirect, and state fields in a search row."""
    for field in ("interface", "protocol", "uuid"):
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise BypassError(f"OPNsense managed source_nat row is missing {field}")
    _rule_ownership_label(row)
    _validate_redirect_fields(row, "OPNsense managed source_nat row")
    _normalize_enabled(row.get("enabled"))


def _rule_ownership_label(rule: dict[str, Any]) -> str:
    """Return the exact managed label from either known API field spelling."""
    labels = [rule[field] for field in ("description", "descr") if field in rule]
    if not labels or any(label != BYPASS_RULE_DESCR for label in labels):
        raise BypassError("OPNsense managed source_nat rule has an invalid ownership label")
    return BYPASS_RULE_DESCR


def _normalize_dns_port(value: Any, field: str, context: str) -> str:
    """Normalize a DNS port while rejecting every value other than 53."""
    if isinstance(value, bool) or value not in (DNS_PORT, str(DNS_PORT)):
        raise BypassError(f"{context} has invalid {field}")
    return str(DNS_PORT)


def _validate_match_fields(rule: dict[str, Any], context: str) -> None:
    """Validate the source / destination / natreflection match fields.

    OPNsense source_nat rules carry ``source`` (e.g. "any" or a configured
    LAN-subnet alias name), ``destination`` ("any"), and ``natreflection``
    ("disable" to avoid hairpin). Drift in any of these means the live
    row's match side has changed away from what the script installed and
    we refuse to treat it as one of our managed rules.
    """
    source = rule.get("source")
    if not isinstance(source, str) or source not in VALID_SOURCES:
        raise BypassError(f"{context} has invalid source")
    destination = rule.get("destination")
    if not isinstance(destination, str) or destination != EXPECTED_DESTINATION:
        raise BypassError(f"{context} has invalid destination")
    natreflection = rule.get("natreflection")
    if not isinstance(natreflection, str) or natreflection != EXPECTED_NATREFLECTION:
        raise BypassError(f"{context} has invalid natreflection")


def _validate_redirect_fields(rule: dict[str, Any], context: str) -> None:
    """Require the redirect configuration that determines DNS bypass behavior.

    This covers the match-side (``source`` / ``destination`` /
    ``natreflection``) AND the redirect-side (``ipprotocol`` / ``target`` /
    ``destination_port`` / ``target_port``) fields. All values are
    exact-match strings as observed live on OPNsense 26.1.11_6; anything
    else means the live row has drifted away from what the script installed
    and we refuse to trust it.
    """
    if rule.get("ipprotocol") != "inet":
        raise BypassError(f"{context} has invalid ipprotocol")
    if rule.get("target") != BYPASS_TARGET_IP:
        raise BypassError(f"{context} has an unexpected target")
    _normalize_dns_port(rule.get("destination_port"), "destination_port", context)
    _normalize_dns_port(rule.get("target_port"), "target_port", context)
    _validate_match_fields(rule, context)


def _validate_fetched_rule(rule: dict[str, Any], search_row: dict[str, Any], path: str) -> None:
    """Validate a get_rule result against the exact search row being updated."""
    # OPNsense 26.1 may omit uuid from the get_rule body. The search result
    # remains the identity authority because it supplied the requested path.
    if "uuid" in rule:
        fetched_uuid = rule["uuid"]
        if not isinstance(fetched_uuid, str) or not fetched_uuid:
            raise BypassError(f"OPNsense API {path} returned an invalid rule UUID")
        if fetched_uuid != search_row["uuid"]:
            raise BypassError(f"OPNsense API {path} returned a rule for the wrong UUID")

    for field in ("interface", "protocol"):
        value = rule.get(field)
        if not isinstance(value, str) or not value:
            raise BypassError(f"OPNsense API {path} returned an invalid {field}")
        if value != search_row[field]:
            raise BypassError(f"OPNsense API {path} returned a rule for the wrong {field}")
    if _rule_ownership_label(rule) != _rule_ownership_label(search_row):
        raise BypassError(f"OPNsense API {path} returned a rule with the wrong ownership label")
    if _normalize_enabled(rule.get("enabled")) != _normalize_enabled(search_row["enabled"]):
        raise BypassError(f"OPNsense API {path} returned a rule with mismatched enabled state")

    _validate_redirect_fields(rule, f"OPNsense API {path} rule")
    for field in ("ipprotocol", "target"):
        if rule[field] != search_row[field]:
            raise BypassError(f"OPNsense API {path} returned a rule with mismatched {field}")
    for field in ("destination_port", "target_port"):
        if _normalize_dns_port(rule[field], field, f"OPNsense API {path} rule") != _normalize_dns_port(
            search_row[field], field, "OPNsense search row"
        ):
            raise BypassError(f"OPNsense API {path} returned a rule with mismatched {field}")
    for field in ("source", "destination", "natreflection"):
        if rule.get(field) != search_row.get(field):
            raise BypassError(f"OPNsense API {path} returned a rule with mismatched {field}")


def _normalize_enabled(value: Any) -> str:
    """Normalize the documented OPNsense enabled representation."""
    if isinstance(value, bool):
        raise BypassError("OPNsense managed source_nat row has invalid enabled state")
    if value in ("0", 0):
        return "0"
    if value in ("1", 1):
        return "1"
    raise BypassError("OPNsense managed source_nat row has invalid enabled state")


def _rule_state(rows: list[dict[str, Any]]) -> str:
    """Return absent, disabled, enabled, or mixed from authoritative rows."""
    if not rows:
        return "absent"
    states = {_normalize_enabled(row.get("enabled")) for row in rows}
    if states == {"0"}:
        return "disabled"
    if states == {"1"}:
        return "enabled"
    return "mixed"


def _expected_rule_keys() -> set[tuple[str, str]]:
    return {
        (interface, protocol)
        for interface in INTERFACE_ALIASES
        for protocol in PROTOCOLS
    }


def _require_complete_rule_set(
    rows: list[dict[str, Any]], *, allow_empty: bool = True
) -> None:
    """Reject partial, duplicate, or unexpected managed rule sets.

    The ``allow_empty`` flag controls whether an empty ruleset is treated
    as the natural "not installed" state or as a post-mutation read-back
    anomaly:

      * ``allow_empty=True`` (default, used by ``status_bypass`` and the
        post-uninstall read-back) — empty is valid. The bypass may simply
        not be installed yet.
      * ``allow_empty=False`` (used by ``install_bypass``,
        ``enable_bypass``, and ``disable_bypass`` immediately after they
        issue a mutating POST) — empty is an actionable anomaly. If we
        just added or flipped six rules and the immediate read-back
        returns zero, the firewall table is in a state we did not
        author, and reporting ``installed=N, total=0, state=absent`` would
        silently lie to the operator. We raise the actionable
        ``ruleset incomplete: have 0/6, run --install to repair`` message
        instead.

    A partial (non-empty, non-full) set always raises the actionable
    ``ruleset incomplete: have N/6, run --install to repair`` message so
    operators see a precise remediation instead of a generic
    ambiguous-set error. The full-6 + extras (duplicates) case raises
    the ambiguous-set error because install cannot safely dedupe that.
    """
    keys = [(row["interface"], row["protocol"]) for row in rows]
    expected = _expected_rule_keys()
    seen = set(keys)
    if not seen:
        if allow_empty:
            return  # absent — the natural "not installed" state
        raise BypassError(
            "ruleset incomplete: have 0/6, run --install to repair "
            "(post-mutation read-back returned no managed rows)"
        )
    if len(keys) != len(seen):
        raise BypassError("OPNsense managed source_nat ruleset is incomplete or ambiguous")
    if seen == expected:
        return
    missing = expected - seen
    extras = seen - expected
    if extras:
        # Unexpected (interface, protocol) pairs — install cannot repair
        # safely because it doesn't know whether the extras are
        # mis-tagged rows someone added manually. Bail with the generic
        # ambiguous-set message.
        raise BypassError("OPNsense managed source_nat ruleset is incomplete or ambiguous")
    # Partial set of the expected six. Tell the operator exactly what's
    # missing and how to fix it.
    missing_list = ", ".join(
        f"{iface}/{proto}" for iface, proto in sorted(missing)
    )
    raise BypassError(
        f"ruleset incomplete: have {len(seen)}/6, run --install to repair "
        f"(missing: {missing_list})"
    )


def _unwrap_rule_response(data: Any, path: str) -> dict[str, Any]:
    """Extract the documented top-level rule envelope from get_rule."""
    if not isinstance(data, dict) or not isinstance(data.get("rule"), dict):
        raise BypassError(f"OPNsense API {path} did not return a rule envelope")
    return data["rule"]


def _require_api_result(data: Any, expected: str, path: str) -> None:
    """Require the documented result marker for a mutating API response."""
    if not isinstance(data, dict) or data.get("result") != expected:
        raise BypassError(f"OPNsense API {path} did not return result={expected!r}")


def _apply_nat(*, primary: str, fallback: str, key: str, secret: str) -> None:
    """Reload the kernel filter so rule changes take effect."""
    _, data = _api_call(
        "POST", NAT_API_APPLY, primary=primary, fallback=fallback, key=key, secret=secret
    )
    if not isinstance(data, dict) or data.get("status") != "OK":
        raise BypassError(f"OPNsense API {NAT_API_APPLY} did not return status='OK'")


def _build_bypass_rule_payload(interface: str, protocol: str) -> dict[str, Any]:
    """Build the OPNsense NAT rule payload for one (interface, protocol)."""
    return {
        "interface": interface,
        "ipprotocol": "inet",
        "protocol": protocol,
        "source": "any",
        "destination": "any",
        "destination_port": str(DNS_PORT),
        "target": BYPASS_TARGET_IP,
        "target_port": str(DNS_PORT),
        "nat_port": "",
        "descr": BYPASS_RULE_DESCR,
        "associated-rule": [],
        "natreflection": "disable",
        "enabled": "0",  # install disabled; --enable flips to "1"
    }


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------


def install_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Install the 6 disabled bypass rules. Idempotent.

    Returns a summary string. Raises BypassError or CredentialError on failure.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    expected_keys = _expected_rule_keys()
    existing_keys = {(r["interface"], r["protocol"]) for r in existing}
    if len(existing_keys) != len(existing) or not existing_keys.issubset(expected_keys):
        raise BypassError("OPNsense managed source_nat ruleset is incomplete or ambiguous")
    if len(existing) == len(expected_keys) and existing_keys == expected_keys:
        return (
            f"installed=0, already_present={len(existing)}, state={_rule_state(existing)} "
            f"(use --enable to activate)"
        )

    # If we have *some* existing rules but not all 6, complete the set. This
    # shouldn't normally happen but handles partial installs gracefully.
    added = 0
    for iface in INTERFACE_ALIASES:
        for proto in PROTOCOLS:
            if (iface, proto) in existing_keys:
                continue
            _, data = _api_call(
                "POST",
                NAT_API_ADD,
                body={"rule": _build_bypass_rule_payload(iface, proto)},
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
            _require_api_result(data, "saved", NAT_API_ADD)
            added += 1

    _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    final = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_rule_set(final, allow_empty=False)
    return (
        f"installed={added}, total={len(final)}, state={_rule_state(final)} "
        f"(use --enable to activate)"
    )


def uninstall_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Remove all bypass rules. Idempotent.

    Only removes rules tagged with BYPASS_RULE_DESCR — never touches other
    firewall rules.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    removed = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if uuid:
            _, data = _api_call(
                "POST",
                f"{NAT_API_DEL}/{uuid}",
                primary=primary,
                fallback=fallback,
                key=key,
                secret=secret,
            )
            _require_api_result(data, "deleted", NAT_API_DEL)
            removed += 1
    if removed:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
        post_uninstall = _search_bypass_rules(
            primary=primary, fallback=fallback, key=key, secret=secret
        )
        # Empty is the *desired* post-uninstall state, so allow_empty=True.
        # We additionally assert the truthy "no rules remain" invariant
        # explicitly so the failure mode reads "rules remained" rather
        # than the generic "ruleset incomplete" message.
        _require_complete_rule_set(post_uninstall, allow_empty=True)
        if post_uninstall:
            raise BypassError("OPNsense managed source_nat rules remained after uninstall")
    return f"uninstalled={removed}"


def enable_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Activate the bypass rules. Idempotent.

    Installs first if missing, then flips every bypass rule to enabled=1 and
    ensures the target IP is correct. Performs a post-enable TCP/53 probe to
    confirm the resolver is reachable.

    Returns a summary string. Exit code 4 if the post-enable verify fails.
    """
    install_bypass(primary=primary, fallback=fallback, key=key, secret=secret)

    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    updated = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if not uuid:
            continue
        _, rule_data = _api_call(
            "GET",
            f"{NAT_API_GET}/{uuid}",
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        rule_data = _unwrap_rule_response(rule_data, f"{NAT_API_GET}/{uuid}")
        _validate_fetched_rule(rule_data, row, f"{NAT_API_GET}/{uuid}")
        rule_data["target"] = BYPASS_TARGET_IP
        rule_data["target_port"] = str(DNS_PORT)
        rule_data["enabled"] = "1"
        _, set_data = _api_call(
            "POST",
            f"{NAT_API_SET}/{uuid}",
            body={"rule": rule_data},
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        _require_api_result(set_data, "saved", NAT_API_SET)
        updated += 1
    _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    final = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_rule_set(final, allow_empty=False)
    if _rule_state(final) != "enabled":
        raise BypassError("OPNsense managed source_nat rules were not fully enabled")
    return f"enabled={updated}, target={BYPASS_TARGET_IP}:{DNS_PORT}"


def disable_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Deactivate the bypass rules. Rules remain installed; enabled=0.

    Refuses to treat a partial ruleset as ``already_disabled`` — a partial
    set means the live firewall table is missing some of the six rules
    that the bypass is supposed to cover, so a no-op would silently leave
    the bypass partially active. Fails closed with the actionable
    "ruleset incomplete: have N/6, run --install to repair" message.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_rule_set(existing)
    updated = 0
    already_disabled = 0
    for row in existing:
        uuid = str(row.get("uuid", ""))
        if not uuid:
            continue
        _, rule_data = _api_call(
            "GET",
            f"{NAT_API_GET}/{uuid}",
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        rule_data = _unwrap_rule_response(rule_data, f"{NAT_API_GET}/{uuid}")
        _validate_fetched_rule(rule_data, row, f"{NAT_API_GET}/{uuid}")
        if _normalize_enabled(rule_data.get("enabled")) == "0":
            already_disabled += 1
            continue
        rule_data["enabled"] = "0"
        _, set_data = _api_call(
            "POST",
            f"{NAT_API_SET}/{uuid}",
            body={"rule": rule_data},
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        _require_api_result(set_data, "saved", NAT_API_SET)
        updated += 1
    if updated:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
        final = _search_bypass_rules(
            primary=primary, fallback=fallback, key=key, secret=secret
        )
        _require_complete_rule_set(final, allow_empty=False)
        if _rule_state(final) != "disabled":
            raise BypassError("OPNsense managed source_nat rules were not fully disabled")
    return f"disabled={updated}, already_disabled={already_disabled}"


def status_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> dict[str, Any]:
    """Return a structured summary of the bypass state.

    A partial ruleset is *not* a valid ``installed=True`` state. If the
    firewall table holds only some of the six expected rows, status fails
    closed with the actionable "ruleset incomplete: have N/6, run --install
    to repair" message rather than reporting the bypass as installed-and-
    disabled on fewer than six rows.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_rule_set(existing)
    enabled_count = sum(1 for r in existing if _normalize_enabled(r.get("enabled")) == "1")
    disabled_count = len(existing) - enabled_count
    return {
        "installed": len(existing) > 0,
        "total_rules": len(existing),
        "state": _rule_state(existing),
        "enabled": enabled_count,
        "disabled": disabled_count,
        "target_ip": BYPASS_TARGET_IP,
        "descr": BYPASS_RULE_DESCR,
        "interfaces": INTERFACE_ALIASES,
        "protocols": PROTOCOLS,
    }


def _tcp_53_probe(ip: str, timeout: float = 3.0) -> bool:
    """Probe a resolver over TCP/53. Does NOT use DNS to resolve the target."""
    try:
        with socket.create_connection((ip, DNS_PORT), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Pi-hole bypass: install/enable/disable/uninstall an OPNsense outbound "
            "NAT ruleset that routes LAN/IoT/Guest DNS to OPNsense Unbound."
        )
    )
    actions = p.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--install",
        action="store_true",
        help="Add the disabled bypass rules to the firewall (persistent safety net).",
    )
    actions.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the bypass rules from the firewall.",
    )
    actions.add_argument(
        "--enable",
        action="store_true",
        help="Activate the bypass: routes all LAN/IoT/Guest DNS to OPNsense Unbound.",
    )
    actions.add_argument(
        "--disable",
        action="store_true",
        help="Deactivate the bypass: rules stay installed but become no-ops.",
    )
    actions.add_argument(
        "--status",
        action="store_true",
        help="Read-only: report whether the bypass rules are installed and enabled.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    key = os.environ.get("OPNSENSE_KEY", "")
    secret = os.environ.get("OPNSENSE_SECRET", "")
    primary = os.environ.get("OPNSENSE_API_PRIMARY", "https://router.montycasa.net")
    fallback = os.environ.get("OPNSENSE_API_FALLBACK", "http://192.168.86.1")
    have_credentials = bool(key and secret)

    # --status is the only action that doesn't require credentials for a useful
    # read; but it does need them to query the live API.
    if args.status:
        if not have_credentials:
            print(
                "ERROR: --status requires OPNSENSE_KEY and OPNSENSE_SECRET.",
                file=sys.stderr,
            )
            return 3
        try:
            st = status_bypass(primary=primary, fallback=fallback, key=key, secret=secret)
        except CredentialError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 3
        except BypassError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if not st["installed"]:
            print("Pi-hole bypass: NOT INSTALLED")
            print(f"  Run --install to add the persistent safety net (6 disabled rules).")
            print(f"  When both Pi-holes are down: --install && --enable")
            print(f"  When Pi-hole recovers:        --disable")
            return 0
        state = st["state"]
        if state == "enabled":
            state_label = "ENABLED"
        elif state == "disabled":
            state_label = "INSTALLED (disabled)"
        else:
            state_label = "MIXED (partially enabled)"
        print(f"Pi-hole bypass: {state_label}")
        print(f"  Total rules: {st['total_rules']}  (enabled={st['enabled']}, disabled={st['disabled']})")
        print(f"  Target:      {st['target_ip']}:{DNS_PORT} (OPNsense Unbound)")
        print(f"  Interfaces:  {', '.join(st['interfaces'])}")
        print(f"  Protocols:   {', '.join(st['protocols'])}")
        print(f"  Descr:       {st['descr']!r}")
        return 0

    # All other actions require credentials.
    if not have_credentials:
        print(
            "ERROR: --install/--enable/--disable/--uninstall require "
            "OPNSENSE_KEY and OPNSENSE_SECRET.",
            file=sys.stderr,
        )
        return 3

    try:
        if args.install:
            summary = install_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Install: {summary}")
            return 0

        if args.uninstall:
            summary = uninstall_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Uninstall: {summary}")
            return 0

        if args.disable:
            summary = disable_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Disable: {summary}")
            return 0

        if args.enable:
            summary = enable_bypass(
                primary=primary, fallback=fallback, key=key, secret=secret
            )
            print(f"Enable: {summary}")
            # Post-enable verify: confirm Unbound is reachable on TCP/53.
            ok = _tcp_53_probe(BYPASS_TARGET_IP)
            if ok:
                print(
                    f"Verify: {BYPASS_TARGET_IP}:{DNS_PORT} is reachable. "
                    "Bypass is live; new outbound DNS packets from LAN/IoT/Guest "
                    "will be rewritten to OPNsense Unbound on the next match."
                )
                return 0
            else:
                print(
                    f"Verify FAILED: {BYPASS_TARGET_IP}:{DNS_PORT} did not accept a "
                    "TCP connection within 3s. The bypass is enabled but the "
                    "resolver is unreachable. Run --disable to roll back, then "
                    "investigate OPNsense Unbound before re-enabling.",
                    file=sys.stderr,
                )
                return 4

    except CredentialError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except BypassError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # argparse's mutually_exclusive_group(required=True) prevents reaching here.
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
