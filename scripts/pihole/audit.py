#!/usr/bin/env python3
"""Read-only, sanitized Pi-hole and OPNsense audit/export command."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import math
import os
import re
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REDACTED = "[REDACTED]"
SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|sid|session|csrf|cookie|authorization|authentication|auth|bearer|credential|client|record|"
    r"api[-_]?key|access[-_]?key|access[-_]?token|client[-_]?secret|private[-_]?key|client[-_]?id|record[-_]?id|identifier|mac|uuid)",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    r"(?is)['\"]?(?:[a-z0-9_. -]*(?:password|passwd|secret|token|sid|cookie|credential|bearer|basic|authorization|authentication|session|csrf|auth|api[_. -]?key|access[_. -]?key|private[_. -]?key|jwt|client|record|identifier|mac|uuid|address)[a-z0-9_. -]*|(?:[a-z0-9_. -]*[_. -])?ip(?:[_. -][a-z0-9_. -]*)?|(?:[a-z0-9_. -]*[_. -])?id(?:[_. -][a-z0-9_. -]*)?)['\"]?\s*[:=]\s*['\"]?[\s\S]*"
)
URL_USERINFO = re.compile(r"(?i)(?:(?:\b[a-z][a-z0-9+.-]*:)?//)[^/\s@]+@")
UUID_VALUE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])")
MAC_VALUE = re.compile(r"(?i)(?<![0-9a-f])(?:(?:[0-9a-f]{2}[:.\-]){5}[0-9a-f]{2}|(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}|[0-9a-f]{12})(?![0-9a-f])")
IPV4_VALUE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
IPV6_CANDIDATE = re.compile(r"(?i)(?<![0-9a-f:])[0-9a-f:.]*:[0-9a-f:.]+(?:%[0-9a-z_.~-]+)?(?![0-9a-f:])")
CLIENT_ADDRESS_KEYS = {"ip", "ipaddress", "address", "client", "host", "hostname"}
IDENTIFIER_CONTEXTS = {"clients", "hostOverrides", "hostAliases"}
COLLECTION_RESOURCES = {"groups", "lists", "domains", "clients", "hostOverrides", "hostAliases"}
OBJECT_RESOURCES = {"config", "version", "service"}
PIHOLE_ENDPOINTS = {
    "config": "/api/config",
    "groups": "/api/groups",
    "lists": "/api/lists",
    "domains": "/api/domains",
    "clients": "/api/clients",
    "version": "/api/info/version",
}
OPNSENSE_ENDPOINTS = {
    "service": "/api/unbound/service/status",
    "hostOverrides": "/api/unbound/settings/searchHostOverride",
    "hostAliases": "/api/unbound/settings/searchHostAlias",
}


OPNSENSE_RESOURCE_NAMES = frozenset(OPNSENSE_ENDPOINTS)
PIHOLE_RESOURCE_NAMES = frozenset(PIHOLE_ENDPOINTS)
ALL_RESOURCE_NAMES = PIHOLE_RESOURCE_NAMES | OPNSENSE_RESOURCE_NAMES
ROOT_KEYS = {"pihole", "opnsense"}


SID_VALUE = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")
PIHOLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_SAFE_OPENER = build_opener(_NoRedirectHandler)


def _safe_urlopen(request: Request, timeout: int = 15):
    return _SAFE_OPENER.open(request, timeout=timeout)


class AuditError(RuntimeError):
    """Raised for a failed read-only audit operation."""


def _is_sensitive(key: str, context: str | None) -> bool:
    if context in ALL_RESOURCE_NAMES and key == context:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if SENSITIVE_KEY.search(key):
        return True
    if normalized in {"id", "client", "record"}:
        return True
    if re.search(r"(?i)(?:^|[_ .-])id$|[a-z]Id$", key):
        return True
    if re.search(r"(?i)(?:^|[_ .-])(?:ip|address)$|[a-z](?:Ip|Address)$", key):
        return True
    return context in IDENTIFIER_CONTEXTS and normalized in {
        re.sub(r"[^a-z0-9]", "", item)
        for item in CLIENT_ADDRESS_KEYS | {"id"}
    }


def _redact_string(value: str, context: str | None) -> str:
    value = URL_USERINFO.sub(lambda match: match.group(0).split("//", 1)[0] + "//", value)
    value = SECRET_VALUE.sub(REDACTED, value)
    for pattern in (UUID_VALUE, MAC_VALUE):
        value = pattern.sub(REDACTED, value)

    def redact_ipv6(match: re.Match[str]) -> str:
        candidate = match.group(0).split("%", 1)[0]
        try:
            return REDACTED if ipaddress.ip_address(candidate).version == 6 else match.group(0)
        except ValueError:
            return match.group(0)

    value = IPV6_CANDIDATE.sub(redact_ipv6, value)
    return IPV4_VALUE.sub(REDACTED, value)


def sanitize_payload(value: Any, context: str | None = None) -> Any:
    """Return a JSON-compatible copy with secrets and raw client IDs removed."""
    if isinstance(value, float) and not math.isfinite(value):
        raise AuditError("non-finite JSON value")
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        entries: list[tuple[str, str, Any, bool]] = []
        for key, child in value.items():
            if not isinstance(key, str):
                raise AuditError("object keys must be strings")
            key_text = key
            sensitive = _is_sensitive(key_text, context)
            if sensitive:
                candidate = REDACTED
            else:
                candidate = _redact_string(key_text, context)
                if candidate == key_text and (key_text == REDACTED or key_text.startswith("[LITERAL_KEY:")):
                    candidate = f"[LITERAL_KEY:{key_text}]"
            entries.append((candidate, key_text, child, sensitive))
        used: set[str] = set()
        for candidate, key_text, child, sensitive in sorted(entries, key=lambda item: (item[0], item[1])):
            output_key = candidate
            suffix = 1
            while output_key in used:
                output_key = f"{candidate}:{suffix}"
                suffix += 1
            used.add(output_key)
            child_context = next((item for item in IDENTIFIER_CONTEXTS if re.sub(r"[^a-z0-9]", "", item.lower()) == re.sub(r"[^a-z0-9]", "", key_text.lower())), context)
            if key_text.lower() in {"client", "clients"}:
                child_context = "clients"
            output[output_key] = REDACTED if sensitive else sanitize_payload(child, child_context)
        return output
    if isinstance(value, list):
        return [sanitize_payload(child, context) for child in value]
    if isinstance(value, tuple):
        return [sanitize_payload(child, context) for child in value]
    if isinstance(value, str):
        return _redact_string(value, context)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return REDACTED


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_envelope(payload: dict[str, Any], value_key: str, value: list[Any], name: str, require_metadata: bool = False) -> None:
    allowed = {value_key, "current", "rowCount", "total"}
    if set(payload) - allowed or (require_metadata and set(payload) != allowed):
        raise AuditError(f"malformed {name} resource envelope")
    for field in ("current", "rowCount", "total"):
        if field in payload and (not isinstance(payload[field], int) or isinstance(payload[field], bool) or payload[field] < 0):
            raise AuditError(f"malformed {name} resource envelope")
    if "current" in payload and payload["current"] < 1:
        raise AuditError(f"malformed {name} resource envelope")
    if "rowCount" in payload and payload["rowCount"] != len(value):
        raise AuditError(f"malformed {name} resource envelope")
    if "total" in payload and payload["total"] < len(value):
        raise AuditError(f"malformed {name} resource envelope")
    if "current" in payload and "total" in payload and ((payload["total"] == 0 and payload["current"] != 1) or (payload["total"] > 0 and (payload["rowCount"] == 0 or payload["current"] > (payload["total"] + payload["rowCount"] - 1) // payload["rowCount"]))):
        raise AuditError(f"malformed {name} resource envelope")


def _resource_value(payload: Any, name: str) -> tuple[Any, dict[str, Any]]:
    if not isinstance(payload, dict):
        return payload, {}
    if "rows" in payload:
        if not isinstance(payload["rows"], list):
            raise AuditError(f"malformed {name} resource envelope")
        _validate_envelope(payload, "rows", payload["rows"], name, require_metadata=True)
        return payload["rows"], {field: payload[field] for field in ("current", "rowCount", "total")}
    if name in payload:
        value = payload[name]
        if name == "version" and isinstance(value, str):
            return payload, {}
        if name in COLLECTION_RESOURCES:
            if not isinstance(value, list):
                raise AuditError(f"malformed {name} resource envelope")
            has_metadata = bool(set(payload) & {"current", "rowCount", "total"})
            _validate_envelope(payload, name, value, name, require_metadata=has_metadata)
            return value, {field: payload[field] for field in ("current", "rowCount", "total") if field in payload}
        elif set(payload) != {name}:
            raise AuditError(f"malformed {name} resource envelope")
        return value, {}
    if name in COLLECTION_RESOURCES:
        raise AuditError(f"malformed {name} resource envelope")
    return payload, {}


def _validate_resource(name: str, value: Any) -> None:
    if name in COLLECTION_RESOURCES:
        if isinstance(value, list):
            if not all(isinstance(item, dict) for item in value):
                raise AuditError(f"malformed {name} resource collection")
            return
        if isinstance(value, dict) and value and all(isinstance(items, list) for items in value.values()):
            if not all(isinstance(item, dict) for items in value.values() for item in items):
                raise AuditError(f"malformed {name} resource collection")
            return
        raise AuditError(f"malformed {name} resource collection")
    if name in OBJECT_RESOURCES:
        if not isinstance(value, dict):
            raise AuditError(f"malformed {name} resource object")
        if name == "config" and not value:
            raise AuditError("malformed config resource object")
        if name == "version" and (not isinstance(value.get("version"), str) or not value["version"]):
            raise AuditError("malformed version resource object")
        if name == "service" and (not isinstance(value.get("status"), str) or not value["status"]):
            raise AuditError("malformed service resource object")
        return
    raise AuditError(f"unsupported resource {name}")


def _resource_count(name: str, value: Any) -> int:
    if name in COLLECTION_RESOURCES:
        if isinstance(value, list):
            return len(value)
        return sum(len(items) for items in value.values())
    return 0 if value in ({}, None) else 1


def _ensure_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise AuditError("non-finite JSON number is not allowed")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise AuditError("JSON object keys must be strings")
            _ensure_finite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _ensure_finite(child)


def _validate_snapshot_name(name: Any) -> str:
    if not isinstance(name, str) or not PIHOLE_NAME.fullmatch(name) or _is_sensitive(name, None) or _redact_string(name, None) != name:
        raise AuditError("invalid audit snapshot name")
    return name


def snapshot_from_payloads(name: str, payloads: Any, allowed_resources: Collection[str]) -> dict[str, Any]:
    name = _validate_snapshot_name(name)
    if not isinstance(allowed_resources, (set, frozenset)):
        raise AuditError(f"{name} resource allowlist is immutable")
    provided_resources = frozenset(allowed_resources)
    if provided_resources == frozenset(PIHOLE_RESOURCE_NAMES):
        expected_resources = PIHOLE_RESOURCE_NAMES
    elif provided_resources == frozenset(OPNSENSE_RESOURCE_NAMES):
        expected_resources = OPNSENSE_RESOURCE_NAMES
    else:
        raise AuditError(f"{name} resource allowlist is immutable")
    if not isinstance(payloads, dict):
        raise AuditError(f"{name} audit section must be an object")
    if not all(isinstance(resource, str) for resource in payloads) or frozenset(payloads) != frozenset(expected_resources):
        raise AuditError(f"{name} snapshot must contain every required resource")
    resources: dict[str, Any] = {}
    envelopes: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for resource in sorted(payloads):
        payload = payloads[resource]
        _ensure_finite(payload)
        value, envelope = _resource_value(payload, resource)
        _validate_resource(resource, value)
        sanitized = _canonical(sanitize_payload(value, resource))
        resources[resource] = sanitized
        if envelope:
            envelopes[resource] = _canonical(sanitize_payload(envelope))
        counts[resource] = _resource_count(resource, sanitized)
    fingerprint_input = {"resources": resources, "envelopes": envelopes}
    return {
        "name": name,
        "resourceCounts": counts,
        "resources": resources,
        "envelopeMetadata": envelopes,
        "fingerprint": _fingerprint(fingerprint_input),
    }


def build_report(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != ROOT_KEYS:
        raise AuditError("audit input must contain exactly pihole and opnsense sections")
    pihole_data = data["pihole"]
    opnsense_data = data["opnsense"]
    if not isinstance(pihole_data, dict) or not pihole_data:
        raise AuditError("audit requires at least one Pi-hole")
    if not isinstance(opnsense_data, dict):
        raise AuditError("audit input sections must be objects")
    if not all(isinstance(name, str) for name in pihole_data):
        raise AuditError("Pi-hole names must be strings")
    pihole: dict[str, dict[str, Any]] = {}
    for name in sorted(pihole_data):
        payloads = pihole_data[name]
        if not isinstance(payloads, dict) or set(payloads) != PIHOLE_RESOURCE_NAMES:
            raise AuditError("each Pi-hole audit section must contain every required resource")
        pihole[name] = snapshot_from_payloads(name, payloads, PIHOLE_RESOURCE_NAMES)
    if set(opnsense_data) != OPNSENSE_RESOURCE_NAMES:
        raise AuditError("OPNsense audit section must contain every required resource")
    opnsense = snapshot_from_payloads("opnsense", opnsense_data, OPNSENSE_RESOURCE_NAMES)
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "comparisonLimits": [
            "client identifiers, IP addresses, and MAC addresses are redacted; client identity equivalence is not asserted",
            "OPNsense UUIDs are redacted; record UUID resolution remains a separate gated operation",
        ],
        "pihole": pihole,
        "opnsense": opnsense,
    }


def _validate_base_url(base_url: str, https_only: bool = False) -> str:
    if not isinstance(base_url, str) or any(ord(character) < 0x20 or character.isspace() or ord(character) == 0x7F for character in base_url):
        raise AuditError("invalid API origin")
    parse_failed = False
    parsed = None
    try:
        parsed = urlsplit(base_url)
        _ = parsed.port
    except (TypeError, ValueError):
        parse_failed = True
    if parse_failed or parsed is None:
        raise AuditError("invalid API origin")
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme.lower() not in allowed or not parsed.hostname or "?" in base_url or "#" in base_url or "@" in parsed.netloc or parsed.path not in {"", "/"}:
        raise AuditError("invalid API origin")
    if parsed.netloc.startswith("["):
        if not re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?", parsed.netloc):
            raise AuditError("invalid API origin")
        ipv6_valid = False
        try:
            ipv6_valid = ipaddress.ip_address(parsed.hostname).version == 6
        except ValueError:
            pass
        if not ipv6_valid:
            raise AuditError("invalid API origin")
    else:
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc):
            raise AuditError("invalid API origin")
        label = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
        if not re.fullmatch(rf"{label}(?:\.{label})*", parsed.hostname):
            raise AuditError("invalid API origin")
    return base_url.rstrip("/")


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse_json(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_json_constant, object_pairs_hook=_reject_duplicate_keys)


class _JsonHttpClient:
    """Tiny fixed-surface JSON client; no mutating HTTP verbs are exposed."""

    ALLOWED_PATHS = frozenset()

    def __init__(self, base_url: str, headers: dict[str, str] | None = None, opener: Callable[..., Any] = _safe_urlopen, https_only: bool = False):
        self.base_url = _validate_base_url(base_url, https_only=https_only)
        self.headers = dict(headers or {})
        self.opener = opener

    def get(self, path: str) -> Any:
        if path not in self.ALLOWED_PATHS:
            raise AuditError("unsupported GET endpoint")
        request = Request(self.base_url + path, headers=self.headers, method="GET")
        return self._read(request)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        if path != "/api/auth":
            raise AuditError("unsupported POST endpoint")
        serialization_failed = False
        body: bytes | None = None
        try:
            body = json.dumps(payload, allow_nan=False).encode()
        except (TypeError, ValueError):
            serialization_failed = True
        if serialization_failed or body is None:
            raise AuditError("invalid authentication payload")
        headers = {**self.headers, "Content-Type": "application/json"}
        request = Request(self.base_url + path, headers=headers, data=body, method="POST")
        return self._read(request)

    def _read(self, request: Request) -> Any:
        result: Any = None
        failed = False
        try:
            with self.opener(request, timeout=15) as response:
                result = _parse_json(response.read().decode())
        except Exception:
            failed = True
        if failed:
            raise AuditError("read-only API request failed")
        return result


class _PiHoleClient(_JsonHttpClient):
    ALLOWED_PATHS = frozenset(PIHOLE_ENDPOINTS.values())


class _OpnsenseClient(_JsonHttpClient):
    ALLOWED_PATHS = frozenset(OPNSENSE_ENDPOINTS.values())

    def __init__(self, base_url: str, headers: dict[str, str] | None = None, opener: Callable[..., Any] = _safe_urlopen):
        super().__init__(base_url, headers=headers, opener=opener, https_only=True)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        raise AuditError("unsupported OPNsense HTTP method")


def _collect_pihole(base_url: str, password: str, opener: Callable[..., Any]) -> dict[str, Any]:
    base_url = _validate_base_url(base_url)
    unauthenticated = _JsonHttpClient(base_url, opener=opener)
    auth = unauthenticated.post_json("/api/auth", {"password": password})
    if not isinstance(auth, dict) or not isinstance(auth.get("session"), dict):
        raise AuditError("Pi-hole authentication response was malformed")
    session = auth["session"]
    sid = session.get("sid")
    if session.get("valid") is not True or not isinstance(sid, str) or not SID_VALUE.fullmatch(sid):
        raise AuditError("Pi-hole authentication response was invalid")
    client = _PiHoleClient(base_url, headers={"X-FTL-SID": sid}, opener=opener)
    return {name: client.get(path) for name, path in PIHOLE_ENDPOINTS.items()}


def _sanitize_collected_payloads(payloads: dict[str, Any], resources: frozenset[str], service: str) -> dict[str, Any]:
    sanitized = {name: sanitize_payload(payload, name) for name, payload in payloads.items()}
    snapshot_from_payloads(service, sanitized, resources)
    return sanitized


def collect_pihole(base_url: str, password: str) -> dict[str, Any]:
    return _sanitize_collected_payloads(_collect_pihole(base_url, password, _safe_urlopen), PIHOLE_RESOURCE_NAMES, "pihole")


def _collect_opnsense(base_url: str, key: str, secret: str, opener: Callable[..., Any]) -> dict[str, Any]:
    base_url = _validate_base_url(base_url, https_only=True)
    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    client = _OpnsenseClient(base_url, headers={"Authorization": f"Basic {credentials}"}, opener=opener)
    return {name: client.get(path) for name, path in OPNSENSE_ENDPOINTS.items()}


def collect_opnsense(base_url: str, key: str, secret: str) -> dict[str, Any]:
    return _sanitize_collected_payloads(_collect_opnsense(base_url, key, secret, _safe_urlopen), OPNSENSE_RESOURCE_NAMES, "opnsense")


def _fixture(path: Path) -> dict[str, Any]:
    read_failed = False
    value: Any = None
    try:
        value = _parse_json(path.read_text())
    except (OSError, ValueError):
        read_failed = True
    if read_failed:
        raise AuditError("unable to read audit fixture")
    if not isinstance(value, dict):
        raise AuditError("audit fixture must be an object")
    return value


def _parse_pihole_spec(spec: str) -> tuple[str, str, str]:
    parse_failed = False
    name = remainder = url = password_env = ""
    try:
        name, remainder = spec.split("=", 1)
        url, password_env = remainder.rsplit(":", 1)
    except ValueError:
        parse_failed = True
    if parse_failed or not name or not url or not password_env:
        raise AuditError("Pi-hole spec must be NAME=URL:PASSWORD_ENV")
    return name, url, password_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path)
    source.add_argument("--live", action="store_true")
    parser.add_argument("--pihole", action="append", default=[], metavar="NAME=URL:PASSWORD_ENV")
    parser.add_argument("--opnsense-url")
    parser.add_argument("--opnsense-key-env", default="OPNSENSE_KEY")
    parser.add_argument("--opnsense-secret-env", default="OPNSENSE_SECRET")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.fixture:
            report = build_report(_fixture(args.fixture))
        else:
            if not args.pihole or not args.opnsense_url:
                raise AuditError("--live requires at least one --pihole and --opnsense-url")
            _validate_base_url(args.opnsense_url, https_only=True)
            prepared: list[tuple[str, str, str]] = []
            names: set[str] = set()
            for spec in args.pihole:
                name, url, password_env = _parse_pihole_spec(spec)
                _validate_snapshot_name(name)
                _validate_base_url(url)
                if name in names:
                    raise AuditError("duplicate Pi-hole name")
                names.add(name)
                password = os.environ.get(password_env)
                if not password:
                    raise AuditError("missing Pi-hole password environment variable")
                prepared.append((name, url, password))
            key = os.environ.get(args.opnsense_key_env)
            secret = os.environ.get(args.opnsense_secret_env)
            if not key or not secret:
                raise AuditError("missing OPNsense credential environment variable")
            pihole = {name: collect_pihole(url, password) for name, url, password in prepared}
            report = build_report({"pihole": pihole, "opnsense": collect_opnsense(args.opnsense_url, key, secret)})
    except AuditError as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        try:
            args.output.write_text(rendered)
        except OSError:
            print("audit failed: unable to write audit output", file=sys.stderr)
            return 2
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
