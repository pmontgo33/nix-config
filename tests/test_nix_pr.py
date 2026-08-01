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
    load_receipt,
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
