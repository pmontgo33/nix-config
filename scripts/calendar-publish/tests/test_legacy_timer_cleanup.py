import unittest
from pathlib import Path


CONFIGURATION = (
    Path(__file__).resolve().parents[3]
    / "hosts"
    / "nxc"
    / "hermes"
    / "configuration.nix"
)


class LegacyCalendarTimerCleanupTests(unittest.TestCase):
    def test_legacy_sports_timer_is_disabled_and_stopped_on_activation(self):
        config = CONFIGURATION.read_text()

        timer_start = config.index("systemd.timers.calendar-sports-generate = {")
        timer_end = config.index("\n  };", timer_start) + len("\n  };")
        timer_block = config[timer_start:timer_end]

        self.assertIn("enable = false;", timer_block)
        self.assertIn("wantedBy = [ ];", timer_block)
        self.assertIn(
            "system.activationScripts.disable-legacy-calendar-sports-timer",
            config,
        )
        self.assertIn(
            "systemctl is-active --quiet calendar-sports-generate.timer",
            config,
        )
        self.assertIn(
            "systemctl stop calendar-sports-generate.timer",
            config,
        )


if __name__ == "__main__":
    unittest.main()
