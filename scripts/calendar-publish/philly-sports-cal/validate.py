#!/run/current-system/sw/bin/python3
"""Validate the complete Philadelphia sports iCalendar feed set."""
import sys
from pathlib import Path

from icalendar import Calendar

EXPECTED_FILES = ("flyers.ics", "phillies.ics", "eagles.ics")


def validate_ics(path: Path) -> list[str]:
    errors = []

    try:
        text = path.read_text(encoding="utf-8")
        cal = Calendar.from_ical(text)
    except Exception as e:
        return [f"Parse error: {e}"]

    if not cal.get("prodid"):
        errors.append("Missing PRODID")
    if not cal.get("x-wr-calname"):
        errors.append("Missing X-WR-CALNAME")

    events = cal.walk("VEVENT")
    if not events:
        errors.append("No VEVENT entries found")
        return errors

    uids_seen = set()
    for i, event in enumerate(events):
        label = f"Event {i + 1}"
        uid = event.get("uid")
        if not uid:
            errors.append(f"{label}: missing UID")
        else:
            uid_str = str(uid).strip()
            if uid_str in uids_seen:
                errors.append(f"{label}: duplicate UID '{uid_str}'")
            uids_seen.add(uid_str)

        dtstart = event.get("dtstart")
        if not dtstart:
            errors.append(f"{label}: missing DTSTART")
        elif not hasattr(dtstart.dt, "hour"):
            errors.append(f"{label}: DTSTART is date-only, expected datetime")

        dtend = event.get("dtend")
        if not dtend:
            errors.append(f"{label}: missing DTEND")

        if not event.get("summary"):
            errors.append(f"{label}: missing SUMMARY")

        if dtstart and dtend:
            try:
                if dtstart.dt >= dtend.dt:
                    errors.append(f"{label}: DTSTART >= DTEND")
            except TypeError:
                pass

    return errors


def validate_paths(paths: list[Path]) -> dict[str, list[str]]:
    """Validate exactly the three required team files."""
    errors: dict[str, list[str]] = {}
    names = {path.name for path in paths}
    expected = set(EXPECTED_FILES)
    for missing in sorted(expected - names):
        errors[missing] = ["Missing required feed"]
    for unexpected in sorted(names - expected):
        errors[unexpected] = ["Unexpected feed; only the three required feeds are allowed"]
    for path in paths:
        if path.name in expected:
            path_errors = validate_ics(path)
            if path_errors:
                errors[path.name] = path_errors
    return errors


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(arg) for arg in sys.argv[1:]]
    else:
        output_dir = Path(__file__).parent / "output"
        paths = [output_dir / name for name in EXPECTED_FILES]
        actual_ics = sorted(output_dir.glob("*.ics"))
        paths.extend(path for path in actual_ics if path.name not in EXPECTED_FILES)

    errors = validate_paths(paths)
    if errors:
        for name, file_errors in sorted(errors.items()):
            print(f"FAIL {name}:")
            for error in file_errors:
                print(f"  - {error}")
        print("\nValidation FAILED", file=sys.stderr)
        raise SystemExit(1)

    for path in sorted(paths, key=lambda item: item.name):
        count = path.read_text(encoding="utf-8").count("BEGIN:VEVENT")
        print(f"OK   {path.name} ({count} events)")
    print("\nAll files valid.")


if __name__ == "__main__":
    main()
