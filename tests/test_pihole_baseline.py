import json
import subprocess
import unittest
from pathlib import Path


class PiHoleBaselineTests(unittest.TestCase):
    repo = Path(__file__).resolve().parents[1]

    def test_native_pihole_emits_shared_baseline_settings(self):
        result = subprocess.run(
            [
                "nix",
                "eval",
                "--json",
                "--impure",
                "--offline",
                ".#nixosConfigurations.pihole-native-test.config.services.pihole-ftl.settings",
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        settings = json.loads(result.stdout)
        self.assertFalse(settings["dhcp"]["active"])
        self.assertFalse(settings["ntp"]["ipv4"]["active"])
        self.assertFalse(settings["ntp"]["ipv6"]["active"])
        self.assertFalse(settings["ntp"]["sync"]["active"])
        self.assertFalse(settings["dns"]["dnssec"])
        self.assertTrue(settings["dns"]["queryLogging"])
        self.assertEqual(settings["dns"]["blocking"], {
            "active": True,
            "edns": "TEXT",
            "mode": "NULL",
        })
        self.assertEqual(settings["resolver"], {
            "networkNames": True,
            "refreshNames": "IPV4_ONLY",
            "resolveIPv4": True,
            "resolveIPv6": True,
        })
        self.assertEqual(settings["database"], {
            "DBimport": True,
            "DBinterval": 60,
            "maxDBdays": 91,
            "network": {"expire": 91, "parseARPcache": True},
            "useWAL": True,
        })
    def test_shared_baseline_rejects_instance_override(self):
        expression = (
            "let f = builtins.getFlake (toString ./.); "
            "c = f.nixosConfigurations.pihole-native-test.extendModules "
            "{ modules = [{ services.pihole-native.dnssec = true; }]; }; "
            "in c.config.system.build.toplevel"
        )
        result = subprocess.run(
            ["nix", "eval", "--impure", "--offline", "--expr", expression],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared FTL baseline settings", result.stderr)


if __name__ == "__main__":
    unittest.main()
