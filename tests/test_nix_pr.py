#!/usr/bin/env python3
"""Unit tests for the nix-pr repository workflow helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from nix_pr import (  # noqa: E402
    Repository,
    content_fingerprint,
    is_substantive_change,
    load_receipt,
    missing_required_sections,
    receipt_path,
    validate_commit_message,
    write_receipt,
)


class TemporaryGitRepository:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test User")
        (self.path / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "Create base")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()

    def close(self) -> None:
        self.directory.cleanup()


class CommitMessageTests(unittest.TestCase):
    def test_accepts_contextual_imperative_message(self) -> None:
        message = (
            "Add guarded Nix PR workflow\n\n"
            "Keep repository validation and PR creation repeatable so stale bases and "
            "unreviewed changes cannot reach Forgejo.\n\n"
            "Validation:\n"
            "- unittest suite passes\n"
            "- documentation smoke test passes\n"
        )
        self.assertEqual(validate_commit_message(message), [])

    def test_rejects_trailing_period(self) -> None:
        errors = validate_commit_message("Add a workflow.\n\nExplain why it exists.\n")
        self.assertIn("subject must not end with punctuation", errors)

    def test_rejects_missing_body(self) -> None:
        errors = validate_commit_message("Add a workflow\n")
        self.assertIn("non-trivial commits require a body", errors)

    def test_rejects_forbidden_attribution(self) -> None:
        errors = validate_commit_message(
            "Add a workflow\n\nWhy it exists\n\nCo-Authored-By: Claude\n"
        )
        self.assertIn("message must not contain forbidden attribution", errors)
        self.assertIn("message must not mention Claude", errors)

    def test_rejects_obvious_past_tense_subject(self) -> None:
        errors = validate_commit_message("Added a workflow\n\nExplain why it exists\n")
        self.assertIn("subject should use imperative mood", errors)


class SubstantiveMessageTests(unittest.TestCase):
    def test_accepts_historical_markdown_format(self) -> None:
        message = (
            "calendars: publish private TaskNotes iCalendar feed\n\n"
            "## Summary\n\n"
            "Add validated TaskNotes publication through Tailscale SSH with atomic "
            "Bifrost-side feed replacement and Hermes path/timer triggers.\n\n"
            "## Why this matters\n\n"
            "The shared calendar needs a stable authenticated publication path without "
            "exposing the vault.\n\n"
            "## Verification\n\n"
            "- `scripts/nix-pr check` passed\n"
            "- Full affected-host build passed\n"
            "- The generated service configuration was inspected after evaluation\n"
        )
        errors = validate_commit_message(
            message,
            changed_files=["hosts/bifrost/configuration.nix"],
            diff_text="+" * 200,
        )
        self.assertEqual(errors, [])

    def test_rejects_missing_markdown_sections(self) -> None:
        message = (
            "calendars: publish private TaskNotes iCalendar feed\n\n"
            "Add validated TaskNotes publication through Tailscale SSH.\n"
        )
        errors = validate_commit_message(
            message,
            changed_files=["hosts/bifrost/configuration.nix"],
            diff_text="+" * 200,
        )
        self.assertIn("## Summary", "\n".join(errors))
        self.assertIn("## Verification", "\n".join(errors))

    def test_rejects_short_substantive_body(self) -> None:
        message = (
            "calendars: publish private TaskNotes iCalendar feed\n\n"
            "## Summary\n\n"
            "Add the publisher.\n\n"
            "## Verification\n\n"
            "- Parse passed\n"
        )
        errors = validate_commit_message(
            message,
            changed_files=["hosts/bifrost/configuration.nix"],
            diff_text="+" * 200,
        )
        self.assertIn("at least 350 body characters", "\n".join(errors))

    def test_small_documentation_change_still_requires_format(self) -> None:
        message = "docs: clarify PR workflow\n\nDocument the complete guarded PR workflow.\n"
        errors = validate_commit_message(
            message,
            changed_files=["README.md"],
            diff_text="+ one line\n",
        )
        self.assertIn("## Summary", "\n".join(errors))
        self.assertIn("## Verification", "\n".join(errors))

    def test_large_markdown_change_requires_historical_detail(self) -> None:
        message = (
            "docs: overhaul contributor guide\n\n"
            "## Summary\n\n"
            "Rewrite the guide.\n\n"
            "## Verification\n\n"
            "- Read the file\n"
        )
        errors = validate_commit_message(
            message,
            changed_files=["README.md"],
            diff_text="\n".join("+ line" for _ in range(50)),
        )
        self.assertIn("at least 350 body characters", "\n".join(errors))

    def test_missing_sections_helper_reports_empty_and_missing(self) -> None:
        empty = "## Summary\n\n## Verification\n\n"
        self.assertEqual(missing_required_sections(empty), ["Summary", "Verification"])
        full = "## Summary\n\nA summary\n\n## Verification\n\n- Check passed\n"
        self.assertEqual(missing_required_sections(full), [])

    def test_is_substantive_change_classifies_correctly(self) -> None:
        self.assertTrue(is_substantive_change(["hosts/bifrost/configuration.nix"], ""))
        self.assertFalse(is_substantive_change(["README.md"], "+ a\n"))
        self.assertTrue(is_substantive_change(["README.md"], "\n".join("+" for _ in range(30))))


class RepositoryStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_fixture = TemporaryGitRepository()
        self.repo = Repository(self.repo_fixture.path)

    def tearDown(self) -> None:
        self.repo_fixture.close()

    def test_content_fingerprint_changes_with_file_content(self) -> None:
        base = self.repo.git("rev-parse", "HEAD").strip()
        before = content_fingerprint(self.repo, base)
        (self.repo_fixture.path / "README.md").write_text("changed\n", encoding="utf-8")
        after = content_fingerprint(self.repo, base)
        self.assertNotEqual(before, after)

    def test_content_fingerprint_includes_untracked_files(self) -> None:
        base = self.repo.git("rev-parse", "HEAD").strip()
        before = content_fingerprint(self.repo, base)
        (self.repo_fixture.path / "new.md").write_text("new\n", encoding="utf-8")
        after = content_fingerprint(self.repo, base)
        self.assertNotEqual(before, after)

    def test_receipt_round_trip_uses_explicit_state_directory(self) -> None:
        state = self.repo_fixture.path / "state"
        receipt = {
            "base_sha": "base",
            "content_fingerprint": "digest",
            "changed_files": ["README.md"],
        }
        path = write_receipt(self.repo, receipt, state)
        self.assertEqual(path, receipt_path(self.repo, state))
        self.assertEqual(load_receipt(self.repo, state), receipt)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_repository_reports_staged_and_unstaged_files_separately(self) -> None:
        (self.repo_fixture.path / "README.md").write_text("staged\n", encoding="utf-8")
        self.repo.git("add", "README.md")
        (self.repo_fixture.path / "README.md").write_text("unstaged\n", encoding="utf-8")
        (self.repo_fixture.path / "new.md").write_text("new\n", encoding="utf-8")
        self.assertEqual(self.repo.staged_files(), ["README.md"])
        self.assertEqual(self.repo.unstaged_files(), ["README.md"])
        self.assertIn("new.md", self.repo.changed_files(self.repo.git("rev-parse", "HEAD").strip()))


if __name__ == "__main__":
    unittest.main()
