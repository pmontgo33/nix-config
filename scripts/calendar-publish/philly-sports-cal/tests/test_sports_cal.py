#!/run/current-system/sw/bin/python3
import contextlib
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from urllib import error as urllib_error
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import generate  # noqa: E402
import validate  # noqa: E402


VALID_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
X-WR-CALNAME:Test
BEGIN:VEVENT
UID:test@example.invalid
DTSTART:20260802T120000Z
DTEND:20260802T150000Z
SUMMARY:Test game
END:VEVENT
END:VCALENDAR
"""


class SportsValidationTests(unittest.TestCase):
    def test_active_seasons_follow_calendar_boundaries(self):
        july = datetime(2026, 7, 1, tzinfo=timezone.utc)
        january = datetime(2027, 1, 15, tzinfo=timezone.utc)
        march = datetime(2027, 3, 1, tzinfo=timezone.utc)
        self.assertEqual(generate.active_nhl_season_year(july), 2026)
        self.assertEqual(generate.active_nhl_season_year(january), 2026)
        self.assertEqual(generate.active_mlb_season_year(january), 2026)
        self.assertEqual(generate.active_mlb_season_year(march), 2027)
        self.assertEqual(generate.active_nfl_season_year(july), 2026)
        self.assertEqual(generate.active_nfl_season_year(january), 2026)

    def test_requires_exact_three_feed_set(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in validate.EXPECTED_FILES:
                (root / name).write_text(VALID_ICS, encoding="utf-8")

            self.assertEqual(validate.validate_paths(list(root.glob("*.ics"))), {})

            (root / "eagles.ics").unlink()
            missing = validate.validate_paths(list(root.glob("*.ics")))
            self.assertEqual(missing["eagles.ics"], ["Missing required feed"])

            (root / "eagles.ics").write_text(VALID_ICS, encoding="utf-8")
            (root / "old-team.ics").write_text(VALID_ICS, encoding="utf-8")
            unexpected = validate.validate_paths(list(root.glob("*.ics")))
            self.assertIn("old-team.ics", unexpected)

    def test_phillies_doubleheader_keeps_both_games(self):
        doubleheader = {
            "dates": [{
                "date": "2026-07-04",
                "games": [
                    {
                        "gamePk": 101,
                        "gameDate": "2026-07-04T16:05:00Z",
                        "teams": {
                            "away": {"team": {"name": "Philadelphia Phillies"}},
                            "home": {"team": {"name": "New York Mets"}},
                        },
                        "venue": {"name": "Citizens Bank Park"},
                    },
                    {
                        "gamePk": 102,
                        "gameDate": "2026-07-04T20:05:00Z",
                        "teams": {
                            "away": {"team": {"name": "Philadelphia Phillies"}},
                            "home": {"team": {"name": "New York Mets"}},
                        },
                        "venue": {"name": "Citizens Bank Park"},
                    },
                ],
            }]
        }
        empty = {"dates": []}
        responses = iter([doubleheader] + [empty] * 7)
        with mock.patch.object(generate, "fetch_json", side_effect=lambda *args, **kwargs: next(responses)):
            events = generate.fetch_phillies(2026)

        self.assertEqual(len(events), 2)
        uids = [line for event in events for line in event.splitlines() if line.startswith("UID:")]
        self.assertEqual(len(uids), 2)
        self.assertNotEqual(uids[0], uids[1])

    def test_generator_stops_before_partial_output_on_team_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            argv = ["generate.py", "--output-dir", str(output)]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                generate, "fetch_flyers", side_effect=RuntimeError("API unavailable")
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    generate.main()

            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(list(output.glob("*.ics")), [])

    @mock.patch("time.sleep")
    @mock.patch.object(generate.urllib.request, "urlopen")
    def test_fetch_json_retries_transient_http_failures(self, urlopen, sleep):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        urlopen.side_effect = [
            urllib_error.HTTPError("https://example.invalid", 503, "busy", Message(), None),
            urllib_error.URLError("temporary network failure"),
            response,
        ]

        self.assertEqual(generate.fetch_json("https://example.invalid/data"), {"ok": True})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(2), mock.call(5)])

    @mock.patch.object(generate.urllib.request, "urlopen")
    def test_fetch_json_logs_permanent_http_failure_without_retry(self, urlopen):
        urlopen.side_effect = urllib_error.HTTPError(
            "https://example.invalid/data", 403, "forbidden", Message(), None
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(urllib_error.HTTPError):
                generate.fetch_json("https://example.invalid/data")

        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("status=403", stderr.getvalue())
    def test_deploy_tracks_remote_swap_state_for_rollback(self):
        deploy = (SCRIPT_DIR / "deploy.sh").read_text()
        self.assertIn("had_previous=0", deploy)
        self.assertIn("new_release=0", deploy)
        self.assertIn("new_release=1", deploy)
        self.assertIn('if [ \\\"\\$new_release\\\" -eq 1 ]', deploy)

    def test_deploy_cleans_remote_stage_after_remote_staging_begins(self):
        deploy = (SCRIPT_DIR / "deploy.sh").read_text()
        self.assertIn("REMOTE_PREPARED=0", deploy)
        self.assertIn("REMOTE_PREPARED=1", deploy)
        self.assertIn('if [ "$REMOTE_PREPARED" -eq 1 ]', deploy)
        self.assertIn("rm -rf '$REMOTE_STAGE' '$REMOTE_BACKUP'", deploy)


if __name__ == "__main__":
    unittest.main()
