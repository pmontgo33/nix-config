"""Regression coverage for the Hermes-Relay plugin import tree.

The directory plugin is loaded by Hermes as ``hermes_plugins.hermes_relay``.
Pairing imports the Relay server and its voice-output support even when no
optional voice provider keys are configured, so ``voice_lab`` is a runtime
package rather than an optional standalone CLI.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path


PLUGIN_DIR = Path(
    os.environ.get(
        "HERMES_RELAY_PLUGIN_DIR",
        str(
            Path(__file__).parents[1]
            / "hosts"
            / "nxc"
            / "hermes"
            / "plugins"
            / "hermes-relay"
        ),
    )
)

_OPTIONAL_VOICE_ENV = (
    "OPENAI_API_KEY",
    "VOICE_TOOLS_OPENAI_KEY",
    "ELEVENLABS_API_KEY",
    "VOICE_TOOLS_ELEVENLABS_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "VOICE_TOOLS_XAI_KEY",
    "XAI_REALTIME_CLIENT_SECRET",
    "XAI_EPHEMERAL_TOKEN",
    "VOICE_LAB_XAI_OAUTH_ACCESS_TOKEN",
    "VOICE_LAB_XAI_OAUTH_CLIENT_ID",
)


def _load_directory_plugin() -> str:
    """Load the plugin using the same namespace shape as Hermes Agent."""
    for name in list(sys.modules):
        if name == "hermes_plugins" or name.startswith("hermes_plugins.hermes_relay"):
            del sys.modules[name]

    namespace = types.ModuleType("hermes_plugins")
    namespace.__path__ = []  # type: ignore[attr-defined]
    namespace.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = namespace

    module_name = "hermes_plugins.hermes_relay"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load plugin from {PLUGIN_DIR}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module_name


class HermesRelayPluginImportTests(unittest.TestCase):
    def test_voice_lab_runtime_package_is_vendored(self) -> None:
        """Pairing's transitive voice imports must exist in the plugin tree."""
        expected = (
            "__init__.py",
            "auth.py",
            "expressions.py",
            "metrics.py",
            "providers/base.py",
            "registry.py",
        )
        for relative_path in expected:
            with self.subTest(relative_path=relative_path):
                self.assertTrue(
                    (PLUGIN_DIR / "voice_lab" / relative_path).is_file(),
                    relative_path,
                )

    def test_pair_import_chain_loads_without_provider_keys(self) -> None:
        """The pair module must import with optional voice keys unset."""
        saved = {
            name: os.environ[name]
            for name in _OPTIONAL_VOICE_ENV
            if name in os.environ
        }
        try:
            for name in _OPTIONAL_VOICE_ENV:
                os.environ.pop(name, None)
            package_name = _load_directory_plugin()
            pair_module = importlib.import_module(f"{package_name}.pair")
            self.assertEqual(pair_module.__name__, f"{package_name}.pair")
        finally:
            for name in _OPTIONAL_VOICE_ENV:
                os.environ.pop(name, None)
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
