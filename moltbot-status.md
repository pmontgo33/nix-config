# Moltbot Status

- `instances.default.config` now defines the Signal channel and plugin slots (replace the deprecated `configOverrides`).
- The system installs `signal-cli` + `qrencode` and uses an `ExecStartPre` helper to inject Signal phone numbers and disable the missing `memory-core` plugin into both `/home/moltbot/.moltbot/moltbot.json` and the filtered cache; the helper now removes the cached file so only the current configuration remains.
- Signal linking succeeded (`signal-cli -u +12158219332 listDevices` shows the new "moltbot" device), and the runtime config contains:
  ```json
  {
    "channels": {
      "signal": {
        "enabled": true,
        "cliPath": "signal-cli",
        "account": "+12158219332",
        "dmPolicy": "allowlist",
        "allowFrom": ["+12154800019"]
      }
    },
    "plugins": { "slots": { "memory": "none" } }
  }
  ```
- The gateway still crash-loops because `moltbot doctor` keeps trying to persist a Discord plugin entry (`plugins.entries.discord`) even though the current config no longer references it; logs repeatedly show `Config validation failed: plugins.entries.discord: plugin not found: discord`. Several cleanup passes (deleting `.moltbot`/`.clawdbot`, removing `moltbot-filtered.json`) have been executed, but the warning resurfaces after each restart.
- **Next action**: run `moltbot doctor --reset`/`--fix` or manually edit the stored JSON while the service is stopped so the doctor has no legacy Discord entry to restore, then restart the gateway and confirm Signal alone remains active. 