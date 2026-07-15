"""Atomic, voice-safe daily weight logging."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


_TIMEZONE = ZoneInfo("America/New_York")
_VAULT_ROOT = Path("/var/lib/hermes/vault/MontyVault")
_GOOGLE_API = Path(
    "/var/lib/hermes/.hermes/skills/productivity/google-workspace/scripts/google_api.py"
)
_SHEET_ID = "1SYuluyvAPBIyM3ZK0_nL2ZIvu-_iWN9KXfT7m5pTbyY"
_SHEET_RANGE = "Weigh-ins!A1:F1000"
_WEIGHT_LINE = re.compile(r"(?m)^\*\*Weight:\*\*\s*(.*?)\s*$")


LOG_WEIGHT_SCHEMA = {
    "name": "log_weight",
    "description": (
        "Log Patrick's body weight for today in pounds. This updates both the "
        "daily Health & Fitness journal and the Weight Loss Challenge spreadsheet. "
        "Use only when the user explicitly asks to log a weight. First ask for "
        "confirmation; call again with confirmed=true only after the user "
        "explicitly confirms the exact value."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "weight_lbs": {
                "type": "number",
                "description": "The weight to log, in pounds.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "True only after the user explicitly confirms logging this exact weight today.",
                "default": False,
            },
        },
        "required": ["weight_lbs"],
    },
}


def _result(**payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _today() -> datetime:
    return datetime.now(_TIMEZONE)


def _journal_relative_path(today: datetime) -> Path:
    return Path("Health & Fitness") / "Journal" / str(today.year) / f"{today:%Y-%m-%d}.md"


def _weight_value(raw: object) -> float | None:
    if raw is None:
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(raw))
    return float(match.group()) if match else None


def _format_weight(weight: float) -> str:
    return f"{weight:.1f}".rstrip("0").rstrip(".")


def _run_google(*args: str) -> object:
    """Run the existing Google Workspace wrapper with the Hermes Python env."""
    completed = subprocess.run(
        [sys.executable, str(_GOOGLE_API), "sheets", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "Google Sheets request failed"
        raise RuntimeError(detail)
    return json.loads(completed.stdout)


def _sheet_row(today: datetime) -> tuple[int, str | None]:
    rows = _run_google("get", _SHEET_ID, _SHEET_RANGE)
    if not isinstance(rows, list):
        raise RuntimeError("Google Sheets returned an unexpected response")
    label = f"{today:%b} {today.day}"
    for index, row in enumerate(rows, start=1):
        if isinstance(row, list) and row and str(row[0]).strip() == label:
            current = str(row[5]).strip() if len(row) > 5 else ""
            return index, current or None
    raise RuntimeError(f"No Weigh-ins row found for {label}")


def _update_sheet(row: int, weight: str) -> None:
    _run_google("update", _SHEET_ID, f"Weigh-ins!F{row}", "--values", json.dumps([[weight]]))


def _journal_content(relative_path: Path) -> tuple[str, float | None]:
    path = _VAULT_ROOT / relative_path
    if not path.is_file():
        raise RuntimeError("Today's Health & Fitness journal has not been created")
    content = path.read_text(encoding="utf-8")
    match = _WEIGHT_LINE.search(content)
    if not match:
        raise RuntimeError("Today's journal has no Body Metrics weight field")
    return content, _weight_value(match.group(1))


def _replace_journal_weight(content: str, weight: str) -> str:
    return _WEIGHT_LINE.sub(f"**Weight:** {weight} lbs", content, count=1)


def _write_journal(relative_path: Path, content: str) -> None:
    sys.path.insert(0, "/var/lib/hermes/.hermes/scripts")
    from vault_writer import VaultWriter

    VaultWriter(root=str(_VAULT_ROOT)).write(str(relative_path), content)


def log_weight(args: dict, **kwargs) -> str:
    """Log today's weight in both canonical stores or return a safe error."""
    del kwargs
    try:
        weight = float(args.get("weight_lbs"))
    except (TypeError, ValueError):
        return _result(success=False, error="weight_lbs must be a number")
    if not 70.0 <= weight <= 500.0:
        return _result(success=False, error="weight_lbs must be between 70 and 500")

    today = _today()
    display_weight = _format_weight(weight)
    relative_path = _journal_relative_path(today)
    try:
        journal_content, journal_weight = _journal_content(relative_path)
        row, sheet_raw = _sheet_row(today)
    except Exception as exc:
        return _result(success=False, error=str(exc))

    sheet_weight = _weight_value(sheet_raw)
    existing = sorted({value for value in (journal_weight, sheet_weight) if value is not None})
    different = any(value != weight for value in existing)
    if existing and not different:
        return _result(success=True, status="already_logged", weight_lbs=weight, date=today.date().isoformat())
    if not bool(args.get("confirmed")):
        return _result(
            success=False,
            needs_confirmation=True,
            existing_journal_weight=journal_weight,
            existing_sheet_weight=sheet_weight,
            requested_weight=weight,
            message=f"Ask the user to confirm logging {display_weight} lb for today before writing.",
        )

    updated_journal = _replace_journal_weight(journal_content, display_weight)
    previous_sheet_value = sheet_raw or ""
    try:
        _update_sheet(row, display_weight)
    except Exception as exc:
        return _result(success=False, error=f"Google Sheet update failed: {exc}")

    try:
        _write_journal(relative_path, updated_journal)
    except Exception as exc:
        try:
            _update_sheet(row, previous_sheet_value)
        except Exception:
            pass
        return _result(success=False, error=f"Journal update failed; the Sheet was rolled back: {exc}")

    return _result(success=True, status="logged", weight_lbs=weight, date=today.date().isoformat())
