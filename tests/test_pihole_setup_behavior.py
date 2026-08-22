"""Behavioural tests for the generated Pi-hole setup script.

These tests render the actual setup script produced by the Nix module, then
substitute a controllable fake `api.sh` so the test can simulate:
- exact-match (no API mutation, no marker, no gravity)
- drift (batch-delete, recreate, gravity, post-gravity verify, marker cleared)
- gravity failure (marker persists for retry)
- partial failure during list creation
- post-gravity drift (verify fails after gravity, marker persists)
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERED_SCRIPT = Path("/tmp/pihole-rendered-setup.sh")
PIHOLE_NIX = "/nix/store/dpk1qdgn7202dn2ad0vmy1vyddg9i8y2-pihole-6.4.2"


def _render_setup_script() -> str:
    result = subprocess.run(
        [
            "nix", "eval", "--raw",
            "--extra-experimental-features", "nix-command flakes",
            "--impure",
            ".#nixosConfigurations.pihole1.config.systemd.services.pihole-ftl-setup.script",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _read_declared_lists() -> list[dict]:
    r = subprocess.run(
        [
            "nix", "eval", "--json",
            "--extra-experimental-features", "nix-command flakes",
            "--impure",
            ".#nixosConfigurations.pihole1.config.services.pihole-ftl.lists",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


class SetupScriptBehaviouralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = _render_setup_script()
        cls.declared = _read_declared_lists()

    def _build_harness(self, tmp: Path, *, state_lists: list[dict], desired_manifest: list[dict] | None = None,
                        gravity_should_fail: bool = False,
                        post_gravity_lists: list[dict] | None = None,
                        first_ensure_fails: bool = False) -> dict:
        """Write a fake api.sh + utils.sh into tmp and rewrite the rendered
        setup script to use them. Returns a state dict the tests can inspect."""
        # Map the declared file:// lists onto a real, readable, non-empty
        # file inside tmp so the preflight check in the setup script passes.
        declared_for_state = []
        for d in self.declared:
            entry = {"type": "block", "address": d["url"],
                     "enabled": d.get("enabled", True),
                     "comment": d.get("description", "")}
            if entry["address"].startswith("file://"):
                rel = entry["address"][len("file://"):].lstrip("/")
                real_path = tmp / "fake_state_dir" / rel
                real_path.parent.mkdir(parents=True, exist_ok=True)
                real_path.write_text("0.0.0.0 example.com\n")
                # Rewrite the file:// URL to point at the local fake path
                # so the preflight inside the script verifies the real file.
                entry["address"] = f"file://{real_path}"
            declared_for_state.append(entry)

        # Rewrite desired_manifest entries so the shell script's
        # desired_lists points at the local fake file path.
        for entry in desired_manifest:
            if entry.get("address", "").startswith("file://"):
                rel = entry["address"][len("file://"):].lstrip("/")
                real_path = tmp / "fake_state_dir" / rel
                real_path.parent.mkdir(parents=True, exist_ok=True)
                if not real_path.exists():
                    real_path.write_text("0.0.0.0 example.com\n")
                entry["address"] = f"file://{real_path}"

        # For pre-state (what the FTL DB "currently" contains), use the
        # caller-provided lists, but rewrite any file:// URLs to the
        # local fake too, so the preflight check can read the file if it
        # happens to inspect them. (The setup script does not currently
        # preflight current-state lists, only desired.)
        current = []
        for l in state_lists:
            entry = dict(l)
            if entry.get("address", "").startswith("file://"):
                rel = entry["address"][len("file://"):].lstrip("/")
                real_path = tmp / "fake_state_dir" / rel
                real_path.parent.mkdir(parents=True, exist_ok=True)
                if not real_path.exists():
                    real_path.write_text("0.0.0.0 dummy\n")
                entry["address"] = f"file://{real_path}"
            current.append(entry)

        state = {
            "calls": [],
            "lists": current,
            "gravity_fail": gravity_should_fail,
            "post_lists": post_gravity_lists,
            "first_ensure_fails": first_ensure_fails,
            "declared": declared_for_state,
        }
        state_path = tmp / "ftl-state.json"
        state_path.write_text(json.dumps(state))

        # Use a separate "live" file the fake reads at request time so
        # post-gravity drift can be simulated after the pihole -g call.
        live_state = tmp / "ftl-state-live.json"
        live_state.write_text(json.dumps(state))

        fake_dir = tmp / "fakepihole"
        fake_dir.mkdir()
        # shim for mktemp, mv, rm, install
        sh = f"""#!/bin/sh
case "$(basename $0)" in
  mktemp) dir=$(dirname -- "$1" 2>/dev/null); [ -d "$dir" ] || mkdir -p "$dir"; exec /run/current-system/sw/bin/mktemp "$@" ;;
  mv) exec /run/current-system/sw/bin/mv "$@" ;;
  rm) exec /run/current-system/sw/bin/rm "$@" ;;
  install) exec /run/current-system/sw/bin/install "$@" ;;
  curl)
    # Pretend the mac vendor download always succeeds with a single 0-byte file.
    out=$(echo "$@" | tr ' ' '\\n' | awk '/^-o$/{{getline x; print x; exit}}')
    [ -n "$out" ] && [ -d "$(dirname "$out")" ] || mkdir -p "$(dirname "$out")"
    : > "$out"
    exit 0
    ;;
  kill) exit 0 ;;
esac
exit 0
"""
        for tool in ("mktemp", "mv", "rm", "install", "curl", "kill"):
            _write_executable(fake_dir / tool, sh)

        # Shim systemctl so the FTL process ID check returns 1 (non-zero
        # pid), keeping the script's case statement happy.
        _write_executable(fake_dir / "systemctl", """#!/bin/sh
case "$1 $2" in
  "show --property") echo "1" ;;
  "is-active") exit 0 ;;
  "is-enabled") exit 0 ;;
  *) exit 0 ;;
esac
""")

        _write_executable(fake_dir / "pihole", f"""#!/bin/sh
COUNT_FILE={state_path}.pihole_count
case "$1" in
  -g)
    n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
    n=$((n+1))
    echo "$n" > "$COUNT_FILE"
    echo "PIH_GRAVITY_$n" >> {state_path}.log
    python3 - <<'PY'
import json, os
live = os.environ.get("STATE_FILE")
if not live:
    raise SystemExit(0)
state = json.loads(open(live).read())
post = state.get("post_lists")
if post is not None and post != state.get("lists"):
    state["lists"] = list(post)
    state["post_lists"] = None
    open(live, "w").write(json.dumps(state))
PY
    if [ "$n" -ge 2 ] && [ "{gravity_should_fail}" = "True" ]; then
      exit 1
    fi
    exit 0
    ;;
esac
exit 0
""")

        # Override jq? No — keep the real jq so sigs/verify work.
        # The test only needs the FTL API + pihole shimmed.

        fake_api = fake_dir / "api.sh"
        fake_utils = fake_dir / "utils.sh"
        # Use a quoted heredoc ('PY') so $variables are not expanded by bash.
        # The python code uses os.environ to receive the absolute path of the
        # state file (set in the wrapper script) and the request payload.
        batch_delete_py = '''import json, os
state_path = os.environ["STATE_FILE"]
state = json.loads(open(state_path).read())
incoming = json.loads(os.environ.get("FTL_PAYLOAD", "[]"))
state.setdefault("requests", []).append({"endpoint": "lists:batchDelete", "payload": incoming})
to_drop = set((i["item"], i["type"]) for i in incoming)
state["lists"] = [item for item in state["lists"] if (item["address"], item["type"]) not in to_drop]
open(state_path, "w").write(json.dumps(state))
'''
        ensure_list_py = '''import json, os
state_path = os.environ["STATE_FILE"]
state = json.loads(open(state_path).read())
if state.get("first_ensure_fails"):
    state["first_ensure_fails"] = False
    open(state_path, "w").write(json.dumps(state))
    raise SystemExit(1)
new = json.loads(os.environ.get("FTL_PAYLOAD", "{}"))
state.setdefault("requests", []).append({
    "endpoint": os.environ.get("FTL_ENDPOINT"),
    "payload": dict(new),
})
new.setdefault("type", os.environ["FTL_ENDPOINT"].split("type=", 1)[1])
new.setdefault("id", 9000 + len(state["lists"]) + 1)
state["lists"].append(new)
open(state_path, "w").write(json.dumps(state))
'''
        fake_api.write_text(
            "export STATE_FILE=" + str(tmp / "ftl-state-live.json") + "\n"
            "echo STATE_FILE=$STATE_FILE >&2\n""TestAPIAvailability() { return 0; }\n"
            "LoginAPI() { SID=test-sid; export SID; return 0; }\n"
            "GetFTLData() {\n"
            "  local endpoint=\"$1\" mode=\"$2\"\n"
            "  local body\n"
            "  case \"$endpoint\" in\n"
            "    lists)\n"
            "      body=$(python3 -c 'import json,os; print(json.dumps({\"lists\": json.load(open(os.environ[\"STATE_FILE\"]))[\"lists\"]}))')\n"
            "      ;;\n"
            "    *) body='{}' ;;\n"
            "  esac\n"
            "  if [ \"$mode\" = \"raw\" ]; then\n"
            "    printf '%s200' \"$body\"\n"
            "  else\n"
            "    printf '%s' \"$body\"\n"
            "  fi\n"
            "}\n"
            "PostFTLData() {\n"
            "  local endpoint=\"$1\" payload=\"$2\" _status_var=$3\n"
            "  case \"$endpoint\" in\n"
            "    lists:batchDelete)\n"
            "      STATE_FILE=\"$STATE_FILE\" FTL_PAYLOAD=\"$payload\" python3 - <<'PY'\n"
            + batch_delete_py + "PY\n"
            "      printf '204'\n"
            "      return 0\n"
            "      ;;\n"
            "    lists?type=*)\n"
            "      if ! STATE_FILE=\"$STATE_FILE\" FTL_ENDPOINT=\"$endpoint\" FTL_PAYLOAD=\"$payload\" python3 - <<'PY'\n"
            + ensure_list_py + "PY\n"
            "      then return 1; fi\n"
            "      printf '{\"lists\":[{\"id\":9999}], \"error\": null}201'\n"
            "      return 0\n"
            "      ;;\n"
            "    *) printf '{\"error\": null}204' ;;\n"
            "  esac\n"
            "}\n"
        )
        fake_utils.write_text("""
# No-op utils shim
exit_test_api() { return 0; }
""")

        # Replace the absolute nix store paths in the rendered script with our
        # fake equivalents. We replace:
        #   /nix/store/...-pihole-6.4.2/share/pihole/advanced/Scripts/api.sh
        #   /nix/store/...-pihole-6.4.2/share/pihole/advanced/Scripts/utils.sh
        #   /nix/store/...-pihole-6.4.2/bin/pihole
        # and rewrite any `/nix/store/...-coreutils...` paths to our PATH shim.
        script_text = self.rendered
        import re
        m = re.search(r'pihole="(/nix/store/[^"]*pihole-6[^"]*)"', script_text)
        assert m, "could not find pihole binary path in rendered script"
        script_text = script_text.replace(m.group(1), str(fake_dir / "pihole"))
        script_text = re.sub(
            r'/nix/store/[^"]+-pihole-[^"]+/share/pihole/advanced/Scripts/api\.sh',
            str(fake_api),
            script_text,
        )
        script_text = re.sub(
            r'/nix/store/[^"]+-pihole-[^"]+/share/pihole/advanced/Scripts/utils\.sh',
            str(fake_utils),
            script_text,
        )
        # Replace every nix store path that points to one of the tools we
        # want to shim. The Nix module hard-codes absolute paths for
        # mktemp/mv/rm/install/curl/kill, and systemctl is invoked inline.
        for tool in ("mktemp", "mv", "rm", "install", "curl", "kill",
                     "systemctl"):
            script_text = re.sub(
                rf'/nix/store/[^"\s]*/bin/{tool}',
                str(fake_dir / tool),
                script_text,
            )
        # Also rewrite the inline `kill -s SIGRTMIN "$main_pid"` so it does
        # not require a real FTL pid. The Nix module uses procps' full path.
        script_text = re.sub(
            r'/nix/store/[^\s]*-procps[^\s]*/bin/kill',
            str(fake_dir / "kill"),
            script_text,
        )
        # And the inline `systemctl show --property MainPID --value pihole-ftl.service`
        # call uses the un-suffixed binary path.
        script_text = re.sub(
            r'/nix/store/[^\s]*-bin/bin/systemctl',
            str(fake_dir / "systemctl"),
            script_text,
        )
        # The install/mktemp/mv/rm binaries are still referenced by variable
        # name; ensure our shim dir is first on PATH so calls reach the
        # wrapped version.
        # Replace the absolute `desired_lists=...` line in the script with
        # the test manifest.
        rewritten_desired = json.dumps(desired_manifest, separators=(",", ":"))
        script_text = re.sub(
            r'^desired_lists=.*$',
            lambda _m: f"desired_lists='{rewritten_desired}'",
            script_text,
            count=1,
            flags=re.M,
        )

        # Rewrite absolute Pi-hole state directory references to the test
        # tmp dir so the marker and mac vendor paths resolve.
        state_dir = tmp / "state"
        script_text = re.sub(
            r'^install=.*$',
            f'install="{fake_dir / "install"}"',
            script_text,
            count=1,
            flags=re.M,
        )
        script_text = re.sub(
            r'^pending_marker=.*$',
            f'pending_marker="{state_dir}/.pihole-ftl-lists-pending"',
            script_text,
            count=1,
            flags=re.M,
        )
        script_text = script_text.replace(
            'pending_marker="/var/lib/pihole/.pihole-ftl-lists-pending"',
            f'pending_marker="{state_dir}/.pihole-ftl-lists-pending"',
        )
        script_text = script_text.replace(
            '"${ftl.stateDirectory}/.pihole-ftl-lists-pending"',
            f'"{state_dir}/.pihole-ftl-lists-pending"',
        )
        script_text = script_text.replace(
            'macvendor_tmp=$($mktemp "/var/lib/pihole/macvendor.db.XXXXXX")',
            f'macvendor_tmp=$($mktemp "{state_dir}/macvendor.db.XXXXXX")',
        )
        script_text = script_text.replace(
            '"${ftl.stateDirectory}/macvendor.db"',
            f'"{state_dir}/macvendor.db"',
        )
        script_text = script_text.replace(
            '"/var/lib/pihole/macvendor.db"',
            f'"{state_dir}/macvendor.db"',
        )
        script_text = script_text.replace(
            '"/var/lib/pihole/gravity.db"',
            f'"{state_dir}/gravity.db"',
        )

        script_path = tmp / "setup.sh"
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            "set +u\n"
            f"export PATH={fake_dir}:$PATH\n"
            f"mkdir -p \"{tmp}/state\"\n"
            f"cd {tmp}\n"
            + script_text
        )
        script_path.chmod(0o755)
        return state

    def _run(self, script_path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(script_path)], capture_output=True,
                              text=True, timeout=60)

    def test_exact_match_is_noop_and_skips_gravity(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            state = self._build_harness(tmp, state_lists=declared, desired_manifest=desired_manifest)
            r = self._run(tmp / "setup.sh")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Pi-hole lists already match", r.stdout)
            self.assertFalse((tmp / "state" / ".pihole-ftl-lists-pending").exists())
            final_state = json.loads((tmp / "ftl-state-live.json").read_text())
            self.assertEqual(final_state.get("requests", []), [])

    def test_malformed_current_comment_fails_before_reset(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            malformed = [{"type": "block", "address": "https://bad.example",
                          "enabled": True, "comment": False}]
            self._build_harness(tmp, state_lists=malformed, desired_manifest=desired_manifest)
            r = self._run(tmp / "setup.sh")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Unable to read current Pi-hole lists", r.stdout)
            self.assertFalse((tmp / "state" / ".pihole-ftl-lists-pending").exists())

    def test_drift_triggers_full_reset_and_marker_clear_on_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            stale = [{"type": "block", "address": "https://stale.example",
                      "enabled": True, "comment": "stale"}]
            # post_gravity_lists defaults to None: no external drift after
            # gravity. After reset+recreate, state matches desired exactly.
            state = self._build_harness(tmp, state_lists=stale + declared[:1], desired_manifest=desired_manifest)
            r = self._run(tmp / "setup.sh")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Resetting Pi-hole lists", r.stdout)
            self.assertFalse((tmp / "state" / ".pihole-ftl-lists-pending").exists())
            final_state = json.loads((tmp / "ftl-state-live.json").read_text())
            delete_requests = [
                request for request in final_state["requests"]
                if request["endpoint"] == "lists:batchDelete"
            ]
            create_requests = [
                request for request in final_state["requests"]
                if request["endpoint"].startswith("lists?type=")
            ]
            self.assertEqual(len(delete_requests), 1)
            self.assertTrue(all(set(item) == {"item", "type"} for item in delete_requests[0]["payload"]))
            self.assertEqual(len(create_requests), 2)
            self.assertTrue(
                all("type" not in request["payload"] for request in create_requests),
                final_state["requests"],
            )
            self.assertTrue(all(request["endpoint"] == "lists?type=block" for request in create_requests))
            final_lists = final_state["lists"]
            self.assertEqual(len(final_lists), 2)
            self.assertTrue(any(l["address"] == "https://big.oisd.nl" for l in final_lists))
            self.assertTrue(any("hagezi" in l["address"] for l in final_lists))

    def test_gravity_failure_keeps_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            state = self._build_harness(tmp, state_lists=declared[:-1], desired_manifest=desired_manifest, gravity_should_fail=True)
            r = self._run(tmp / "setup.sh")
            self.assertNotEqual(r.returncode, 0)
            gravity_log = (tmp / "ftl-state.json.log").read_text() if (tmp / "ftl-state.json.log").exists() else ""
            self.assertIn("PIH_GRAVITY_1", gravity_log)
            self.assertIn("PIH_GRAVITY_2", gravity_log)
            self.assertNotIn("PIH_GRAVITY_3", gravity_log)
            self.assertTrue((tmp / "state" / ".pihole-ftl-lists-pending").exists())

    def test_partial_create_failure_aborts_deployment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            state = self._build_harness(tmp, state_lists=[], desired_manifest=desired_manifest, first_ensure_fails=True)
            r = self._run(tmp / "setup.sh")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((tmp / "state" / ".pihole-ftl-lists-pending").exists())

    def test_post_gravity_drift_keeps_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            post_drift = declared + [
                {"type": "block", "address": "https://runtime.hot",
                 "enabled": True, "comment": "hot"},
            ]
            # Start with a stale pre-state so gravity runs, then the
            # post-gravity drift makes verifyLists fail.
            stale = [{"type": "block", "address": "https://stale.example",
                      "enabled": True, "comment": "stale"}]
            state = self._build_harness(tmp, state_lists=stale, desired_manifest=desired_manifest, post_gravity_lists=post_drift)
            r = self._run(tmp / "setup.sh")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((tmp / "state" / ".pihole-ftl-lists-pending").exists())
    def test_post_gravity_malformed_comment_keeps_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            desired_manifest = [
                {"type": d.get("type", "block"),
                 "enabled": d.get("enabled", True),
                 "address": d["url"],
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            declared = [
                {"type": "block", "address": d["url"],
                 "enabled": d.get("enabled", True),
                 "comment": d.get("description", "")}
                for d in self.declared
            ]
            malformed = declared + [{
                "type": "block", "address": "https://runtime.hot",
                "enabled": True, "comment": False,
            }]
            self._build_harness(
                tmp,
                state_lists=[{"type": "block", "address": "https://stale.example",
                              "enabled": True, "comment": "stale"}],
                desired_manifest=desired_manifest,
                post_gravity_lists=malformed,
            )
            r = self._run(tmp / "setup.sh")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((tmp / "state" / ".pihole-ftl-lists-pending").exists())


if __name__ == "__main__":
    unittest.main()
