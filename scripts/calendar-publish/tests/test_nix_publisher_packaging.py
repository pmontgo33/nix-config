import unittest
from pathlib import Path


CONFIGURATION = (
    Path(__file__).resolve().parents[3]
    / "hosts"
    / "nxc"
    / "hermes"
    / "configuration.nix"
)


class NixPublisherPackagingTests(unittest.TestCase):
    def test_publishers_package_source_directories(self):
        config = CONFIGURATION.read_text()

        expected_sources = {
            "familyCalendar": (
                "../../../scripts/calendar-publish/family-calendar-router",
                "router.sh",
            ),
            "sportsCalendar": (
                "../../../scripts/calendar-publish/philly-sports-cal",
                "deploy.sh",
            ),
        }

        for binding, (source, entrypoint) in expected_sources.items():
            with self.subTest(binding=binding):
                self.assertIn(f"{binding}Source = {source};", config)
                self.assertIn(
                    f"${{{binding}Source}}/{entrypoint}",
                    config,
                )
                self.assertNotIn(
                    f"${{{source}/{entrypoint}}}",
                    config,
                )


if __name__ == "__main__":
    unittest.main()
