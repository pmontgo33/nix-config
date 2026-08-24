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

  - firewall:source_nat:add_rule, del_rule, get_rule, set_rule, apply —
    REQUIRED for install/uninstall/enable/disable. NOT currently granted
    on the canonical hermes credential. Add via OPNsense UI: System →
    Access → Users → (user) → Effective Privileges.

OPNsense 26.1 API notes (live-verified 2026-08-24):

  - **Authoritative read is /api/firewall/source_nat/get** (the model
    endpoint). It returns the entire firewall model under
    ``{"filter": {"general": ..., "rules": ..., "snatrules": {"rule": ...},
    ...}}``; the source-NAT ruleset lives at
    ``data["filter"]["snatrules"]["rule"]``. The list is empty (``[]``) when
    no rules are present, and a dict keyed by UUID when populated.

  - **/api/firewall/source_nat/search_rule is NOT authoritative.** On
    26.1.11_6 it returns ONLY rules with ``is_automatic=true`` (the eight
    WAN auto-created rules). Manually-added rules are silently omitted.
    The script no longer calls this endpoint.

  - **Ownership is by full attribute tuple, not by descr.** OPNsense
    26.1.11_6's ``add_rule`` accepts ``descr`` as a documented field but
    silently discards it on save; the field is empty string on read-back
    for every rule added through the API. See BYPASS_MANAGED_KEY below.

  - **Outbound NAT rules live under /api/firewall/source_nat/*, NOT
    /api/firewall/nat/* (the latter returns 404 on 26.1).**

  - **Multi-select fields are dicts in the model, scalars in the add
    payload.** ``interface`` / ``protocol`` / ``ipprotocol`` come back as
    ``{"<opt>": {"value": "<Label>", "selected": 0|1}, ...}`` dicts from
    the read endpoints, but the documented ``add_rule`` payload schema
    accepts the bytestring ("lan", "udp", "inet") and OPNsense
    normalizes it correctly on save. The script keeps the scalar
    shape in the add payload and extracts the canonical "first selected
    option" from the multi-select dicts on read.

  - **apply responds with literal ``{"status": "OK\\n\\n"}``** (trailing
    whitespace and two newlines). Compared against the trimmed value so
    the live response shape is accepted without weakening the
    fail-closed contract on any other payload.

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

# Persisted constant — kept for documentation. The original
# implementation used BYPASS_RULE_DESCR as the ownership marker, but
# OPNsense 26.1.11_6 silently discards ``descr`` on add and returns
# empty string on read-back for rules created via the API. The current
# ownership marker is BYPASS_MANAGED_KEY (below). The constant is
# retained so audit logs and any external tooling that references the
# label can still find the documentation in this module, and so the
# value is unchanged across rewrites (operators should not see the
# descr string mutate under their feet).
BYPASS_RULE_DESCR = "dns-path-switcher: pihole-bypass"

# Hard-coded literal-IP fallback for mutating POSTs. The default primary
# endpoint is hostname-based (router.montycasa.net); when the local DNS
# resolver is down (the very scenario this script exists for) the hostname
# transport fails. POSTs additionally try this literal-IP HTTPS endpoint
# before giving up, so the break-glass path works without DNS. GETs keep
# using the existing HTTP fallback (which is faster and read-only).
BYPASS_POST_HTTPS_FALLBACK = "https://192.168.86.1"

# Authoritative-match field constants. The ownership marker for the
# Pi-hole bypass is the FULL attribute tuple below, NOT a single text
# field. OPNsense 26.1.11_6 silently discards ``descr`` on
# ``add_rule`` and returns empty string on read-back, so any
# descr-based ownership check would either (a) match nothing because
# the read-back descr is "" or (b) match EVERY manual rule because
# the field is shared. The key-tuple below is the only stable
# identifier of a script-installed rule.
#
# Every rule in the OPNsense source_nat model whose attribute tuple
# equals BYPASS_MANAGED_KEY is considered managed by this script.
# Anything else is left alone.
BYPASS_MANAGED_KEY: tuple[tuple[str, str, str, int, str, int, str, str], ...] = (
    # (interface, protocol, target_ip, target_port,
    #  destination_net, destination_port, source_net, ipprotocol)
    ("lan", "udp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
    ("lan", "tcp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
    ("opt1", "udp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
    ("opt1", "tcp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
    ("opt2", "udp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
    ("opt2", "tcp", BYPASS_TARGET_IP, 53, "any", 53, "any", "inet"),
)
BYPASS_MANAGED_KEY_SET: frozenset[tuple[str, str, str, int, str, int, str, str]] = (
    frozenset(BYPASS_MANAGED_KEY)
)

# Expected interface / protocol sets. The validator rejects any rule
# whose interface is outside INTERFACE_ALIASES or whose protocol is
# outside PROTOCOLS, even if all other fields match.
INTERFACE_ALIASES = ("lan", "opt1", "opt2")  # LAN, IoT, Guest
PROTOCOLS = ("udp", "tcp")
DNS_PORT = 53

# OPNsense 26.1 outbound NAT API paths. Note the absence of NAT_API_SEARCH:
# /api/firewall/source_nat/search_rule on 26.1.11_6 returns ONLY
# is_automatic=true rules, so it is NOT authoritative and is never
# called by this script.
NAT_API_MODEL = "firewall/source_nat/get"  # authoritative read
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


# ---------------------------------------------------------------------------
# Multi-select extraction
# ---------------------------------------------------------------------------


def _selected_option(multi_select: Any) -> str | None:
    """Return the bytestring of the single selected option in a multi-select dict.

    OPNsense model fields ``interface``, ``protocol``, and ``ipprotocol``
    come back from the model endpoint as a dict of the form
    ``{"<opt>": {"value": "<Label>", "selected": 0|1}, ...}``. The rule
    is "well-formed for ownership" only when EXACTLY ONE option is
    selected — the canonical value the rule actually matches.

    Returns ``None`` in three cases (the caller fails closed):

      * the input is not a multi-select dict,
      * zero options are selected,
      * two or more options are selected.

    OPNsense can legitimately emit rules with 2+ selected options on
    some models; classifying such a rule as the first selected option
    would let install / enable / disable / uninstall mutate or delete
    a rule that is broader than the script claims to own. Such rules
    are treated as not-managed.
    """
    if not isinstance(multi_select, dict):
        return None
    selected_key: str | None = None
    for key, entry in multi_select.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("selected") == 1:
            if selected_key is not None:
                # Two (or more) selected options: ambiguous; refuse to
                # pick one on the script's behalf.
                return None
            selected_key = str(key)
    return selected_key


# ---------------------------------------------------------------------------
# Rule identity and validation
# ---------------------------------------------------------------------------


def _managed_key_of(rule: dict[str, Any]) -> tuple[str, str, str, int, str, int, str, str] | None:
    """Compute the BYPASS_MANAGED_KEY tuple for a rule, or None if it doesn't match.

    Returns the canonical key tuple (interface, protocol, target_ip,
    target_port, destination_net, destination_port, source_net,
    ipprotocol) if and only if EVERY field has the exact value
    documented in BYPASS_MANAGED_KEY. Otherwise returns None — the rule
    is not managed by this script.
    """
    interface = _selected_option(rule.get("interface"))
    protocol = _selected_option(rule.get("protocol"))
    if interface is None or protocol is None:
        return None
    if interface not in INTERFACE_ALIASES or protocol.lower() not in PROTOCOLS:
        return None
    target = rule.get("target")
    target_port = rule.get("target_port")
    destination_net = rule.get("destination_net")
    destination_port = rule.get("destination_port")
    source_net = rule.get("source_net")
    ipprotocol = _selected_option(rule.get("ipprotocol"))
    if ipprotocol is None:
        return None
    if not all(
        isinstance(field, str)
        for field in (target, destination_net, source_net)
    ):
        return None
    if not all(
        isinstance(field, (str, int)) and not isinstance(field, bool)
        for field in (target_port, destination_port)
    ):
        return None
    # Narrow to str | int for the int() conversion; isinstance above
    # excludes bool (which is a subclass of int in Python). Catch the
    # cases where the model carries a service-name string (e.g.
    # ``"http"``) or any other non-numeric value that ``int()`` cannot
    # parse — treat the rule as not-managed rather than letting a raw
    # ValueError propagate out of status / install / enable / disable /
    # uninstall. TypeError is caught alongside ValueError because
    # ``int(None)`` and similar shape mismatches would otherwise surface
    # the same way.
    try:
        tp: int = int(target_port)  # type: ignore[arg-type]
        dp: int = int(destination_port)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    return (
        interface,
        protocol.lower(),
        str(target),
        tp,
        str(destination_net),
        dp,
        str(source_net),
        ipprotocol,
    )


def _normalize_enabled(value: Any) -> str:
    """Normalize the documented OPNsense enabled representation.

    Accepts ``"0"``/``0`` and ``"1"``/``1``; rejects everything else
    (including bool, because the live model always emits string
    ``"0"``/``"1"`` and a real ``True``/``False`` would mean a
    type-drift anomaly worth failing closed on).
    """
    if isinstance(value, bool):
        raise BypassError("OPNsense managed source_nat row has invalid enabled state")
    if value in ("0", 0):
        return "0"
    if value in ("1", 1):
        return "1"
    raise BypassError("OPNsense managed source_nat row has invalid enabled state")


# ---------------------------------------------------------------------------
# Authoritative read
# ---------------------------------------------------------------------------


def _read_model(
    *, primary: str, fallback: str, key: str, secret: str
) -> dict[str, Any]:
    """Read the authoritative OPNsense source_nat model.

    On 26.1.11_6 the model is a dict with shape
    ``{"filter": {"general": ..., "rules": ..., "snatrules": {"rule": ...},
    "npt": {"rule": ...}, "onetoone": {"rule": ...}}}``. The
    source-NAT ruleset is at ``data["filter"]["snatrules"]["rule"]``
    (empty list ``[]`` when no rules exist, dict keyed by UUID when
    populated). The container is validated up front: anything other
    than a dict or an empty list is treated as schema drift and the
    function fails closed with a precise message. A non-empty list
    shape is also rejected — there is no documented case where
    OPNsense returns a list with entries under ``snatrules.rule`` and
    silently synthesising UUIDs like ``list:0`` would let a malformed
    read-back be treated as a managed row.
    """
    _, data = _api_call(
        "GET", NAT_API_MODEL,
        primary=primary, fallback=fallback, key=key, secret=secret,
    )
    if not isinstance(data, dict):
        raise BypassError(f"OPNsense API {NAT_API_MODEL} did not return a JSON object")
    if "filter" not in data or not isinstance(data["filter"], dict):
        raise BypassError(f"OPNsense API {NAT_API_MODEL} response is missing 'filter'")
    snat_container = data["filter"].get("snatrules")
    if not isinstance(snat_container, dict) or "rule" not in snat_container:
        raise BypassError(
            f"OPNsense API {NAT_API_MODEL} response is missing "
            "filter.snatrules.rule"
        )
    rules_container = snat_container["rule"]
    if isinstance(rules_container, list):
        if rules_container:
            raise BypassError(
                f"OPNsense API {NAT_API_MODEL} returned an unexpected rule "
                f"container shape (list with {len(rules_container)} entries); "
                "refusing to proceed; expected dict keyed by uuid or empty "
                "list for filter.snatrules.rule"
            )
        return data
    if isinstance(rules_container, dict):
        return data
    raise BypassError(
        f"OPNsense API {NAT_API_MODEL} returned an unexpected "
        "filter.snatrules.rule container type "
        f"{type(rules_container).__name__}"
    )


def _search_bypass_rules(
    *, primary: str, fallback: str, key: str, secret: str
) -> list[dict[str, Any]]:
    """Return all source_nat rules owned by this script.

    Walks the authoritative ``/api/firewall/source_nat/get`` model and
    returns the subset of rules whose attribute tuple matches
    ``BYPASS_MANAGED_KEY``. The descriptor (``descr``) is intentionally
    NOT used as the ownership marker: OPNsense 26.1.11_6 silently
    discards ``descr`` on ``add_rule`` and returns empty string on
    read-back, so a descr-based check would either match nothing or
    match every manual rule with a stray matching prefix.

    Each returned entry is ``{"uuid": str, "rule": dict, "key": tuple}``
    so the lifecycle actions can operate by UUID and the caller can
    inspect the matched key without recomputing it.

    Fail-closed on any unexpected model shape. The container
    validation in ``_read_model`` has already guaranteed that the
    rules container is either a dict (keyed by UUID) or an empty
    list, so this function only needs to iterate the dict shape
    and validate each entry.
    """
    model = _read_model(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    rules_container = model["filter"]["snatrules"]["rule"]
    if not rules_container:
        # Empty list (the live empty-state shape): no rules installed.
        return []
    managed: list[dict[str, Any]] = []
    for uuid, rule in rules_container.items():
        if not isinstance(rule, dict):
            raise BypassError(
                f"OPNsense API {NAT_API_MODEL} returned a non-object "
                f"source_nat rule for uuid {uuid!r}"
            )
        key_tuple = _managed_key_of(rule)
        if key_tuple is None:
            continue
        if key_tuple not in BYPASS_MANAGED_KEY_SET:
            # Rule is shape-compatible (correct interface/protocol/etc.)
            # but not part of the bypass tuple — e.g. an admin-installed
            # rule with a different target. Don't include it.
            continue
        # Validate enabled field type before accepting: a rule whose
        # enabled is not "0"/"1"/0/1 means the schema has drifted and
        # we refuse to trust it.
        if "enabled" in rule:
            _normalize_enabled(rule["enabled"])
        managed.append({"uuid": uuid, "rule": rule, "key": key_tuple})
    return managed


# ---------------------------------------------------------------------------
# Ruleset completeness
# ---------------------------------------------------------------------------


def _expected_managed_keys() -> set[tuple[str, str, str, int, str, int, str, str]]:
    return set(BYPASS_MANAGED_KEY_SET)


def _require_complete_managed_set(
    entries: list[dict[str, Any]], *, allow_empty: bool
) -> None:
    """Reject partial, duplicate, or unexpected managed rule sets.

    The ``allow_empty`` flag controls whether an empty ruleset is
    treated as the natural "not installed" state or as a post-mutation
    read-back anomaly:

      * ``allow_empty=True`` (default, used by ``status_bypass`` and the
        post-uninstall read-back) — empty is valid. The bypass may
        simply not be installed yet.
      * ``allow_empty=False`` (used by ``install_bypass``,
        ``enable_bypass``, and ``disable_bypass`` immediately after they
        issue a mutating POST) — empty is an actionable anomaly. If we
        just added or flipped six rules and the immediate read-back
        returns zero, the firewall table is in a state we did not
        author. Raise the actionable
        ``ruleset incomplete: have 0/6, run --install to repair``
        message instead.

    A partial (non-empty, non-full) set always raises the actionable
    message so operators see a precise remediation. A full-6 + extras
    case raises the generic ambiguous-set error because
    ``install_bypass`` cannot safely dedupe that.
    """
    keys = [entry["key"] for entry in entries]
    expected = _expected_managed_keys()
    seen = set(keys)
    if not seen:
        if allow_empty:
            return
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
        # Unexpected (key-tuple) entries — install cannot repair
        # safely because it doesn't know whether the extras are
        # mis-tagged rows someone added manually. Bail with the generic
        # ambiguous-set message.
        raise BypassError("OPNsense managed source_nat ruleset is incomplete or ambiguous")
    # Partial set of the expected six. Tell the operator exactly what's
    # missing and how to fix it.
    missing_list = ", ".join(
        f"{iface}/{proto}" for iface, proto, *_ in sorted(missing)
    )
    raise BypassError(
        f"ruleset incomplete: have {len(seen)}/6, run --install to repair "
        f"(missing: {missing_list})"
    )


def _rule_state(entries: list[dict[str, Any]]) -> str:
    """Return absent, disabled, enabled, or mixed from authoritative rows."""
    if not entries:
        return "absent"
    states = {_normalize_enabled(entry["rule"].get("enabled")) for entry in entries}
    if states == {"0"}:
        return "disabled"
    if states == {"1"}:
        return "enabled"
    return "mixed"


# ---------------------------------------------------------------------------
# Per-rule get / set helpers
# ---------------------------------------------------------------------------


def _unwrap_rule_response(data: Any, path: str) -> dict[str, Any]:
    """Extract the documented top-level rule envelope from get_rule."""
    if not isinstance(data, dict) or not isinstance(data.get("rule"), dict):
        raise BypassError(f"OPNsense API {path} did not return a rule envelope")
    return data["rule"]


def _require_api_result(data: Any, expected: str, path: str) -> None:
    """Require the documented result marker for a mutating API response.

    OPNsense 26.1.11_6 returns ``{"result": "saved"}`` for add_rule /
    set_rule and ``{"result": "deleted"}`` for del_rule, with no
    trailing whitespace. We compare against the literal value because
    those endpoints have not been observed to emit a trailing-newline
    shape; the trim is reserved for the apply endpoint, which does
    emit ``"OK\\n\\n"`` (see ``_apply_nat``).
    """
    if not isinstance(data, dict) or data.get("result") != expected:
        raise BypassError(f"OPNsense API {path} did not return result={expected!r}")


def _apply_nat(*, primary: str, fallback: str, key: str, secret: str) -> None:
    """Reload the kernel filter so rule changes take effect.

    OPNsense 26.1.11_6 returns ``{"status": "OK\\n\\n"}`` (a leading "OK"
    followed by trailing whitespace and a couple of newlines) on success.
    Compare against the trimmed value so we accept the live response shape
    without weakening the fail-closed contract on any other payload.
    """
    _, data = _api_call(
        "POST", NAT_API_APPLY,
        primary=primary, fallback=fallback, key=key, secret=secret,
    )
    if not isinstance(data, dict):
        raise BypassError(f"OPNsense API {NAT_API_APPLY} did not return a JSON object")
    raw_status = data.get("status")
    if not isinstance(raw_status, str) or raw_status.strip() != "OK":
        raise BypassError(
            f"OPNsense API {NAT_API_APPLY} did not return status='OK'"
        )


def _build_bypass_rule_payload(interface: str, protocol: str) -> dict[str, Any]:
    """Build the OPNsense NAT rule payload for one (interface, protocol).

    Live-verified field choices for OPNsense 26.1.11_6:

      * ``descr`` is silently discarded by ``add_rule`` and is not in
        the read-back. Sending it would add noise to the wire payload
        but not change behavior; we omit it to make the read-back
        shape predictable.
      * ``natreflection`` is silently discarded by ``add_rule`` and is
        not in the read-back. Hairpin / NAT-reflection is therefore
        not configured for these rules. We omit the field so the
        post-apply read-back matches the live shape.
      * ``associated-rule`` is a filter-rule linkage field that the
        source_nat model does not use. Sending it is harmless but
        pointless; we omit it.
      * The source/destination fields use the bytestring aliases
        ("any") that the documented add payload schema accepts. The
        model endpoint reads them back as ``source_net`` /
        ``destination_net``; the controller normalizes the payload
        field name to the model field name internally. This is
        preserved from the merged PR #250 implementation and is the
        only live-confirmed way to add a rule today.

    The OPNsense add payload schema is the documented one. The
    ``protocol`` and ``ipprotocol`` fields accept the bytestring
    ("udp", "inet") and OPNsense normalizes them to the multi-select
    dicts on save. Sending multi-select dicts in the add payload is
    NOT supported.
    """
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
    existing_keys = {entry["key"] for entry in existing}
    expected_keys = _expected_managed_keys()

    # Duplicate detection: if the model has more entries than unique
    # managed keys, two rules share the same BYPASS_MANAGED_KEY signature
    # (e.g. the prior install ghost-rule incident). We cannot safely
    # dedupe that from the script side — refuse to add another rule
    # until the operator cleans up the duplicates through the OPNsense UI.
    if len(existing) != len(existing_keys):
        raise BypassError(
            "OPNsense managed source_nat ruleset is incomplete or ambiguous "
            f"({len(existing)} rules map to {len(existing_keys)} unique BYPASS keys; "
            "remove duplicates through the OPNsense UI before re-installing)"
        )

    # Pre-existing partial set: a non-empty subset of the expected six.
    # Validate that it's a proper subset (no extras), and if it's
    # exactly the full set, return "already installed" instead of
    # re-adding rules.
    if not existing_keys.issubset(expected_keys):
        raise BypassError("OPNsense managed source_nat ruleset is incomplete or ambiguous")
    if existing_keys == expected_keys:
        return (
            f"installed=0, already_present={len(existing)}, "
            f"state={_rule_state(existing)} "
            f"(use --enable to activate)"
        )

    # If we have *some* existing rules but not all 6, complete the set.
    # This shouldn't normally happen but handles partial installs
    # gracefully.
    added = 0
    for managed_key in BYPASS_MANAGED_KEY:
        if managed_key in existing_keys:
            continue
        interface, protocol, *_ = managed_key
        _, data = _api_call(
            "POST",
            NAT_API_ADD,
            body={"rule": _build_bypass_rule_payload(interface, protocol)},
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
    _require_complete_managed_set(final, allow_empty=False)
    return (
        f"installed={added}, total={len(final)}, state={_rule_state(final)} "
        f"(use --enable to activate)"
    )


def uninstall_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Remove all bypass rules. Idempotent.

    Only removes rules whose attribute tuple matches BYPASS_MANAGED_KEY —
    never touches other firewall rules.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    removed = 0
    for entry in existing:
        uuid = entry["uuid"]
        if not uuid:
            continue
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
        _require_complete_managed_set(post_uninstall, allow_empty=True)
        if post_uninstall:
            raise BypassError("OPNsense managed source_nat rules remained after uninstall")
    return f"uninstalled={removed}"


def _flip_managed_enabled(
    *,
    primary: str,
    fallback: str,
    key: str,
    secret: str,
    target_enabled: str,
) -> tuple[int, int]:
    """Flip every managed rule's enabled field to target_enabled.

    Returns (updated, already_in_target_state). Fetches the current
    rule body via get_rule, mutates only the enabled field, and POSTs
    the full body back via set_rule. This is necessary because
    OPNsense's set_rule is a full-replacement endpoint, not a partial
    patch, and the multi-select fields the model endpoint returns must
    be echoed back verbatim or the controller will reject the set.

    Drift detection: if the live rule's target/target_port has moved
    away from BYPASS_MANAGED_KEY (someone edited the rule in the
    OPNsense UI), fail closed so a stale read-back cannot enable a
    rule pointing at the wrong target.
    """
    existing = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_managed_set(existing, allow_empty=False)
    updated = 0
    already_in_state = 0
    for entry in existing:
        uuid = entry["uuid"]
        if not uuid:
            continue
        _, rule_data_raw = _api_call(
            "GET",
            f"{NAT_API_GET}/{uuid}",
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        rule_body = _unwrap_rule_response(rule_data_raw, f"{NAT_API_GET}/{uuid}")
        # Drift detection: the freshly-fetched rule body must still
        # match the BYPASS_MANAGED_KEY signature, with the same UUID
        # we asked for. If it has moved, refuse to set.
        live_key = _managed_key_of(rule_body)
        if live_key != entry["key"]:
            raise BypassError(
                f"OPNsense API {NAT_API_GET}/{uuid} returned a rule whose "
                "attribute tuple no longer matches BYPASS_MANAGED_KEY "
                f"(expected {entry['key']!r}, got {live_key!r}); refusing "
                "to set enabled"
            )
        if _normalize_enabled(rule_body.get("enabled")) == target_enabled:
            already_in_state += 1
            continue
        rule_body["enabled"] = target_enabled
        _, set_data = _api_call(
            "POST",
            f"{NAT_API_SET}/{uuid}",
            body={"rule": rule_body},
            primary=primary,
            fallback=fallback,
            key=key,
            secret=secret,
        )
        _require_api_result(set_data, "saved", NAT_API_SET)
        updated += 1
    return updated, already_in_state


def enable_bypass(
    *, primary: str, fallback: str, key: str, secret: str
) -> str:
    """Activate the bypass rules. Idempotent.

    Installs first if missing, then flips every bypass rule to
    enabled=1 and ensures the target IP is correct. Performs a
    post-enable TCP/53 probe to confirm the resolver is reachable.

    Returns a summary string. Exit code 4 if the post-enable verify fails.
    """
    install_bypass(primary=primary, fallback=fallback, key=key, secret=secret)
    updated, already_enabled = _flip_managed_enabled(
        primary=primary, fallback=fallback, key=key, secret=secret,
        target_enabled="1",
    )
    if updated:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
    final = _search_bypass_rules(
        primary=primary, fallback=fallback, key=key, secret=secret
    )
    _require_complete_managed_set(final, allow_empty=False)
    if _rule_state(final) != "enabled":
        raise BypassError("OPNsense managed source_nat rules were not fully enabled")
    # Drift check on the post-enable read-back: every rule's target
    # and target_port must still be BYPASS_MANAGED_KEY's values, even
    # though the bytestring extraction handles the multi-select. This
    # catches the case where set_rule was accepted by the controller
    # but the model returned a stale row.
    for entry in final:
        if entry["key"] not in BYPASS_MANAGED_KEY_SET:
            raise BypassError(
                "OPNsense managed source_nat ruleset contained an unexpected "
                "managed key on post-enable read-back"
            )
    return (
        f"enabled={updated}, already_enabled={already_enabled}, "
        f"target={BYPASS_TARGET_IP}:{DNS_PORT}"
    )


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
    updated, already_disabled = _flip_managed_enabled(
        primary=primary, fallback=fallback, key=key, secret=secret,
        target_enabled="0",
    )
    if updated:
        _apply_nat(primary=primary, fallback=fallback, key=key, secret=secret)
        final = _search_bypass_rules(
            primary=primary, fallback=fallback, key=key, secret=secret
        )
        _require_complete_managed_set(final, allow_empty=False)
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
    _require_complete_managed_set(existing, allow_empty=True)
    enabled_count = sum(
        1 for e in existing if _normalize_enabled(e["rule"].get("enabled")) == "1"
    )
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
