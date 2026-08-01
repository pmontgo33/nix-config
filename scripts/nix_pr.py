#!/usr/bin/env python3
"""Repository-safe workflow helpers for nix-config pull requests.

The CLI deliberately keeps editing and LLM behavior outside the repository.
It owns repository state, validation receipts, commit-message linting, commit
creation, and the final Forgejo submission checks.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


VERSION = "0.1.0"
EXPECTED_ORIGIN = "patrick/nix-config"
EXPECTED_FORK = "openclaw/nix-config"
REMOTE_BASE = "master"


class NixPrError(RuntimeError):
    """An expected, user-actionable workflow failure."""


class CommandError(NixPrError):
    """A subprocess failed."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str = "") -> None:
        rendered = " ".join(command)
        detail = stderr.strip()
        super().__init__(f"command failed ({returncode}): {rendered}" + (f"\n{detail}" if detail else ""))
        self.command = tuple(command)
        self.returncode = returncode
        self.stderr = stderr


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_subprocess(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        raise CommandError(command, completed.returncode, completed.stderr)
    return completed


class Repository:
    """Small Git adapter that is easy to exercise with temporary repositories."""

    def __init__(self, root: Path | str, runner: Runner | None = None) -> None:
        self.root = Path(root).resolve()
        self.runner = runner or _run_subprocess
        if not (self.root / ".git").exists():
            raise NixPrError(f"not a Git checkout: {self.root}")

    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(command, cwd=self.root, input_text=input_text, check=check)

    def git(self, *args: str, check: bool = True) -> str:
        result = self.run(["git", *args], check=check)
        return result.stdout

    def git_bytes(self, *args: str, check: bool = True) -> bytes:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode:
            raise CommandError(["git", *args], completed.returncode, completed.stderr.decode(errors="replace"))
        return completed.stdout

    def current_branch(self) -> str | None:
        branch = self.git("symbolic-ref", "--short", "-q", "HEAD").strip()
        return branch or None

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def branch_sha(self, branch: str) -> str:
        return self.git("rev-parse", branch).strip()

    def status_short(self) -> str:
        return self.git("status", "--short")

    def is_clean(self) -> bool:
        return not bool(self.status_short().strip())

    def staged_files(self) -> list[str]:
        return _nul_list(self.git("diff", "--cached", "--name-only", "-z"))

    def unstaged_files(self) -> list[str]:
        return _nul_list(self.git("diff", "--name-only", "-z"))

    def untracked_files(self) -> list[str]:
        return _nul_list(self.git("ls-files", "--others", "--exclude-standard", "-z"))

    def changed_files(self, base: str) -> list[str]:
        tracked = _nul_list(self.git("diff", "--name-only", base, "-z"))
        return sorted(set(tracked) | set(self.untracked_files()))

    def remote_url(self, remote: str) -> str:
        return self.git("remote", "get-url", remote).strip()

    def live_remote_sha(self, remote: str, branch: str = REMOTE_BASE) -> str:
        output = self.git("ls-remote", remote, f"refs/heads/{branch}").strip()
        if not output:
            raise NixPrError(f"remote branch not found: {remote}/{branch}")
        return output.split()[0]

    def fetch_remote_branch(self, remote: str, branch: str = REMOTE_BASE) -> None:
        self.git("fetch", remote, branch)

    def has_in_progress_operation(self) -> bool:
        git_dir = Path(self.git("rev-parse", "--git-dir").strip())
        if not git_dir.is_absolute():
            git_dir = self.root / git_dir
        return any(
            (git_dir / marker).exists()
            for marker in (
                "MERGE_HEAD",
                "CHERRY_PICK_HEAD",
                "REVERT_HEAD",
                "rebase-apply",
                "rebase-merge",
            )
        )

    def commit(self, message_file: Path) -> None:
        self.git("commit", "--file", str(message_file))

    def push(self, branch: str) -> None:
        self.git("push", "fork", f"HEAD:refs/heads/{branch}")


def _nul_list(value: str) -> list[str]:
    return sorted(item for item in value.split("\0") if item)


def _hash_parts(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def content_fingerprint(repo: Repository, base: str) -> str:
    """Hash the final worktree content relative to base, including untracked files."""

    parts: list[bytes] = [b"base\0" + base.encode()]
    parts.append(repo.git_bytes("diff", "--no-ext-diff", "--binary", base, "--"))
    for relative in repo.untracked_files():
        path = repo.root / relative
        if path.is_file():
            parts.append(relative.encode() + b"\0" + path.read_bytes())
        elif path.is_symlink():
            parts.append(relative.encode() + b"\0SYMLINK\0" + os.readlink(path).encode())
    return _hash_parts(parts)


def staged_fingerprint(repo: Repository) -> str:
    return _hash_parts(
        [
            b"staged\0",
            repo.git_bytes("diff", "--cached", "--no-ext-diff", "--binary", "--"),
        ]
    )


def default_state_dir() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return Path(configured).expanduser() / "nix-pr" if configured else Path.home() / ".local" / "state" / "nix-pr"


def repository_state_dir(repo: Repository, state_dir: Path | str | None = None) -> Path:
    root_hash = hashlib.sha256(str(repo.root).encode()).hexdigest()[:16]
    return Path(state_dir or default_state_dir()).expanduser() / root_hash


def receipt_path(repo: Repository, state_dir: Path | str | None = None) -> Path:
    return repository_state_dir(repo, state_dir) / "validation.json"


def write_receipt(repo: Repository, receipt: dict[str, Any], state_dir: Path | str | None = None) -> Path:
    destination = receipt_path(repo, state_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix="validation.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        os.fchmod(temporary.fileno(), 0o600)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)
    os.chmod(destination, 0o600)
    return destination


def load_receipt(repo: Repository, state_dir: Path | str | None = None) -> dict[str, Any] | None:
    path = receipt_path(repo, state_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NixPrError(f"cannot read validation receipt {path}: {exc}") from exc


REQUIRED_SECTIONS = ("why", "affected", "validation")
SECTION_HEADERS = {
    "why": re.compile(r"^why\s*:\s*\S", re.IGNORECASE | re.MULTILINE),
    "affected": re.compile(r"^affected\s*:\s*\S", re.IGNORECASE | re.MULTILINE),
    "validation": re.compile(r"^validation\s*:\s*\S", re.IGNORECASE | re.MULTILINE),
}


def _subject_without_prefix(subject: str) -> str:
    return re.sub(r"^[a-z][a-z0-9-]*(?:\([^)]*\))?!?:\s*", "", subject, flags=re.IGNORECASE)


def is_substantive_change(changed_files: Sequence[str], diff_text: str) -> bool:
    """Return True when the change is more than a small documentation edit.

    A change is substantive when it touches any non-markdown file or exceeds
    20 lines of diff. Documentation-only commits can use the relaxed body
    check; everything else must carry Why, Affected, and Validation sections.
    """

    if any(not relative.endswith(".md") for relative in changed_files):
        return True
    return len(diff_text.splitlines()) > 20


def missing_required_sections(message: str) -> list[str]:
    return [name for name, pattern in SECTION_HEADERS.items() if not pattern.search(message)]


def validate_commit_message(
    message: str,
    *,
    changed_files: Sequence[str] | None = None,
    diff_text: str = "",
) -> list[str]:
    errors: list[str] = []
    if "\x00" in message:
        errors.append("message must not contain NUL bytes")
        return errors
    lines = message.splitlines()
    subject = lines[0].strip() if lines else ""
    if not subject:
        errors.append("subject must not be empty")
        return errors
    if len(subject) > 100:
        errors.append("subject must be at most 100 characters")
    if subject[-1:] in ".!?":
        errors.append("subject must not end with punctuation")
    if re.match(
        r"^(?:Add|Added|Fix|Fixed|Update|Updated|Change|Changed|Remove|Removed|Create|Created|"
        r"Implement|Implemented|Refactor|Refactored|Improve|Improved|Configure|Configured|"
        r"Migrate|Migrated|Rename|Renamed|Delete|Deleted|Bump|Bumped)\b",
        _subject_without_prefix(subject),
        flags=re.IGNORECASE,
    ):
        if re.match(r"^(?:Added|Fixed|Updated|Changed|Removed|Created|Implemented|Refactored|Improved|Configured|Migrated|Renamed|Deleted|Bumped)\b", _subject_without_prefix(subject), flags=re.IGNORECASE):
            errors.append("subject should use imperative mood")
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    if not body:
        errors.append("non-trivial commits require a body")
    if changed_files is not None and is_substantive_change(changed_files, diff_text):
        missing = missing_required_sections(message)
        if missing:
            errors.append(
                "substantive commits require Why:, Affected:, and Validation: sections (missing: "
                + ", ".join(missing)
                + ")"
            )
    lowered = message.lower()
    if "co-authored-by:" in lowered:
        errors.append("message must not contain forbidden attribution")
    if "claude" in lowered:
        errors.append("message must not mention Claude")
    secret_patterns = (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\b(?:ghp_|github_pat_|glpat-|xox[baprs]-)[A-Za-z0-9_-]{10,}",
        r"\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{24,}",
    )
    if any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in secret_patterns):
        errors.append("message must not contain credential-like material")
    return errors


def scan_secrets(repo: Repository, files: Sequence[str]) -> list[str]:
    patterns = (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b(?:ghp_|github_pat_|glpat-|xox[baprs]-)[A-Za-z0-9_-]{10,}"),
        re.compile(r"\b(?:API[_-]?KEY|TOKEN|PASSWORD|SECRET)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{24,}"),
    )
    findings: list[str] = []
    for relative in files:
        path = repo.root / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                findings.append(f"{relative}:{line_number}")
    return findings


def _expected_remote(url: str, expected: str) -> bool:
    normalized = url.lower().removesuffix(".git").rstrip("/")
    return normalized.endswith(expected.lower())


def verify_remote_layout(repo: Repository) -> None:
    try:
        origin = repo.remote_url("origin")
        fork = repo.remote_url("fork")
    except CommandError as exc:
        raise NixPrError("repository must define origin and fork remotes") from exc
    if not _expected_remote(origin, EXPECTED_ORIGIN):
        raise NixPrError(f"origin does not point to {EXPECTED_ORIGIN}")
    if not _expected_remote(fork, EXPECTED_FORK):
        raise NixPrError(f"fork does not point to {EXPECTED_FORK}")


def ensure_ready_repository(repo: Repository, *, clean: bool = False) -> str:
    branch = repo.current_branch()
    if not branch:
        raise NixPrError("detached HEAD is not allowed")
    if repo.has_in_progress_operation():
        raise NixPrError("Git merge, rebase, or other in-progress operation detected")
    verify_remote_layout(repo)
    if clean and not repo.is_clean():
        raise NixPrError("worktree must be clean")
    return branch


def _live_origin(repo: Repository) -> str:
    live = repo.live_remote_sha("origin")
    repo.fetch_remote_branch("origin")
    fetched = repo.branch_sha("origin/master")
    if live != fetched:
        raise NixPrError(f"origin/master changed during fetch: live {live}, fetched {fetched}")
    return live


def valid_branch_slug(slug: str) -> bool:
    return bool(
        slug
        and len(slug) <= 100
        and not slug.startswith(("/", "."))
        and not slug.endswith(("/", "."))
        and ".." not in slug
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", slug) is not None
    )


def start(repo: Repository, slug: str) -> str:
    ensure_ready_repository(repo, clean=True)
    if not valid_branch_slug(slug):
        raise NixPrError("invalid branch slug; use letters, numbers, dots, dashes, underscores, or slashes")
    if repo.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{slug}"], check=False).returncode == 0:
        raise NixPrError(f"local branch already exists: {slug}")
    live_origin = _live_origin(repo)
    remote_branch = repo.run(["git", "ls-remote", "--exit-code", "fork", f"refs/heads/{slug}"], check=False)
    if remote_branch.returncode not in (0, 2):
        raise CommandError(
            ["git", "ls-remote", "--exit-code", "fork", f"refs/heads/{slug}"],
            remote_branch.returncode,
            remote_branch.stderr,
        )
    if remote_branch.returncode == 0:
        raise NixPrError(f"fork branch already exists: {slug}")
    repo.git("checkout", "-b", slug, "origin/master")
    if repo.head_sha() != live_origin:
        raise NixPrError("new branch does not point at the verified live origin/master")
    return slug


def direct_hosts(changed_files: Sequence[str]) -> list[str]:
    hosts: set[str] = set()
    for relative in changed_files:
        parts = Path(relative).parts
        if len(parts) >= 2 and parts[0] == "hosts":
            hosts.add(parts[2] if len(parts) >= 3 and parts[1] == "nxc" else parts[1])
        if len(parts) >= 4 and parts[0] == "users" and parts[2] == "hosts":
            hosts.add(parts[3])
    return sorted(hosts)


def discover_all_hosts(repo: Repository) -> list[str]:
    output = repo.run(
        [
            "nix",
            "eval",
            "--json",
            "--no-write-lock-file",
            ".#nixosConfigurations",
            "--apply",
            "builtins.attrNames",
        ]
    ).stdout
    try:
        hosts = json.loads(output)
    except json.JSONDecodeError as exc:
        raise NixPrError("nix returned invalid host metadata") from exc
    if not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts):
        raise NixPrError("nix returned an invalid host list")
    return sorted(hosts)


def _is_nix_change(relative: str) -> bool:
    return relative.endswith(".nix") or Path(relative).name in {"flake.lock", "flake.nix"}


def _requires_second_review(changed_files: Sequence[str]) -> bool:
    return any(_is_nix_change(relative) for relative in changed_files)


def _run_nix_validation(
    repo: Repository,
    changed_files: Sequence[str],
    *,
    hosts: Sequence[str],
    all_hosts: bool,
    second_review_file: Path | None,
) -> list[dict[str, Any]]:
    if not _requires_second_review(changed_files):
        return []
    if second_review_file is None:
        raise NixPrError("Nix/systemd changes require --second-review-file before validation")
    if not second_review_file.is_file() or not second_review_file.read_text(encoding="utf-8").strip():
        raise NixPrError("second-model review file must exist and contain a non-empty review")
    selected_hosts = list(hosts)
    if all_hosts:
        selected_hosts = discover_all_hosts(repo)
    if not selected_hosts:
        selected_hosts = direct_hosts(changed_files)
    if not selected_hosts:
        raise NixPrError("cannot infer affected hosts; rerun check with --host NAME or --all-hosts")
    checks: list[dict[str, Any]] = []
    for relative in changed_files:
        if not relative.endswith(".nix") or not (repo.root / relative).is_file():
            continue
        repo.run(["nix-instantiate", "--parse", relative])
        checks.append({"name": "nix-parse", "path": relative, "status": "passed"})
    for host in selected_hosts:
        flake_target = f".#nixosConfigurations.{host}.config.system.build.toplevel"
        repo.run(["nix", "eval", "--json", "--no-write-lock-file", f"{flake_target}.drvPath"])
        checks.append({"name": "nix-eval", "host": host, "status": "passed"})
        repo.run(["nix", "build", "--no-link", "--print-build-logs", "--no-write-lock-file", flake_target])
        checks.append({"name": "nix-build", "host": host, "status": "passed"})
    review_digest = hashlib.sha256(second_review_file.read_bytes()).hexdigest()
    checks.append({"name": "second-model-review", "status": "passed", "digest": review_digest})
    return checks


def run_check(
    repo: Repository,
    *,
    state_dir: Path | str | None = None,
    hosts: Sequence[str] = (),
    all_hosts: bool = False,
    second_review_file: Path | None = None,
) -> dict[str, Any]:
    branch = ensure_ready_repository(repo)
    base_sha = _live_origin(repo)
    changed = repo.changed_files(base_sha)
    if not changed:
        raise NixPrError("no changes found relative to live origin/master")
    findings = scan_secrets(repo, changed)
    if findings:
        raise NixPrError("secret scan found credential-like content at: " + ", ".join(findings))
    checks: list[dict[str, Any]] = [
        {"name": "repository-state", "status": "passed"},
        {"name": "secret-scan", "status": "passed", "files": len(changed)},
    ]
    checks.extend(
        _run_nix_validation(
            repo,
            changed,
            hosts=hosts,
            all_hosts=all_hosts,
            second_review_file=second_review_file,
        )
    )
    receipt = {
        "version": 1,
        "cli_version": VERSION,
        "repository": str(repo.root),
        "branch": branch,
        "base_sha": base_sha,
        "head_sha": repo.head_sha(),
        "content_fingerprint": content_fingerprint(repo, base_sha),
        "staged_fingerprint": staged_fingerprint(repo),
        "changed_files": changed,
        "checks": checks,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "committed_head_sha": None,
    }
    path = write_receipt(repo, receipt, state_dir)
    receipt["receipt_path"] = str(path)
    return receipt


def _require_current_receipt(repo: Repository, state_dir: Path | str | None) -> dict[str, Any]:
    receipt = load_receipt(repo, state_dir)
    if receipt is None:
        raise NixPrError("no validation receipt; run nix-pr check first")
    branch = ensure_ready_repository(repo)
    live_base = repo.live_remote_sha("origin")
    if receipt.get("branch") != branch:
        raise NixPrError("validation receipt belongs to a different branch")
    if receipt.get("base_sha") != live_base:
        raise NixPrError("origin/master moved since validation; rerun nix-pr check")
    if receipt.get("content_fingerprint") != content_fingerprint(repo, live_base):
        raise NixPrError("worktree content changed since validation; rerun nix-pr check")
    return receipt


def commit(repo: Repository, message_file: Path, state_dir: Path | str | None = None) -> str:
    receipt = _require_current_receipt(repo, state_dir)
    staged = repo.staged_files()
    if not staged:
        raise NixPrError("nothing is staged; stage exactly one logical change group first")
    overlapping = sorted(set(staged) & set(repo.unstaged_files()))
    if overlapping:
        raise NixPrError("staged files also have unstaged edits: " + ", ".join(overlapping))
    if receipt.get("staged_fingerprint") != staged_fingerprint(repo):
        raise NixPrError("staged content differs from the content that was validated; rerun nix-pr check")
    try:
        message = message_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise NixPrError(f"cannot read commit message file: {message_file}") from exc
    diff_text = repo.git("diff", "--cached", "-U0", "--")
    errors = validate_commit_message(
        message,
        changed_files=staged,
        diff_text=diff_text,
    )
    if errors:
        raise NixPrError("invalid commit message:\n- " + "\n- ".join(errors))
    repo.commit(message_file)
    new_head = repo.head_sha()
    receipt.update(
        {
            "head_sha": new_head,
            "committed_head_sha": new_head,
            "committed_files": staged,
            "commit_message_fingerprint": hashlib.sha256(message.encode()).hexdigest(),
            "committed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_receipt(repo, receipt, state_dir)
    return new_head


def _credential_for_host(repo: Repository, host: str) -> tuple[str, str]:
    credential = repo.run(
        ["git", "credential", "fill"],
        input_text=f"protocol=https\nhost={host}\n\n",
        check=False,
    )
    values: dict[str, str] = {}
    for line in credential.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if values.get("username") and values.get("password"):
        return values["username"], values["password"]
    url = repo.remote_url("fork")
    parsed = urllib.parse.urlsplit(url)
    if parsed.username and parsed.password:
        return urllib.parse.unquote(parsed.username), urllib.parse.unquote(parsed.password)
    raise NixPrError("no Git credential available for Forgejo API access")


def _api_request(
    repo: Repository,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    state_dir: Path,
    filename: str,
) -> dict[str, Any]:
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        raise NixPrError("Forgejo API URL has no host")
    username, password = _credential_for_host(repo, host)
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload, sort_keys=True).encode()
        headers["Content-Type"] = "application/json"
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except urllib.error.URLError as exc:
        raise NixPrError(f"Forgejo API request failed: {exc.reason}") from exc
    state_dir.mkdir(parents=True, exist_ok=True)
    response_path = state_dir / filename
    response_path.write_bytes(raw)
    os.chmod(response_path, 0o600)
    try:
        parsed = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NixPrError(f"Forgejo returned non-JSON response (HTTP {status}); saved {response_path}") from exc
    if status >= 300:
        message = parsed.get("message", "unknown API error") if isinstance(parsed, dict) else "unknown API error"
        raise NixPrError(f"Forgejo API returned HTTP {status}: {message}; saved {response_path}")
    if not isinstance(parsed, dict):
        raise NixPrError(f"Forgejo returned an unexpected response; saved {response_path}")
    return parsed


def submit(repo: Repository, *, state_dir: Path | str | None = None, dry_run: bool = False) -> dict[str, Any]:
    receipt = _require_current_receipt(repo, state_dir)
    branch = ensure_ready_repository(repo, clean=True)
    if receipt.get("committed_head_sha") != repo.head_sha():
        raise NixPrError("current HEAD was not created by the validated commit workflow")
    live_base = repo.live_remote_sha("origin")
    expected_files = sorted(repo.git("diff", "--name-only", f"{live_base}..HEAD", "-z").split("\0"))
    expected_files = [item for item in expected_files if item]
    if expected_files != sorted(receipt.get("changed_files", [])):
        raise NixPrError("committed file list differs from the validation receipt")
    existing = repo.run(["git", "ls-remote", "--exit-code", "fork", f"refs/heads/{branch}"], check=False)
    if existing.returncode == 0:
        raise NixPrError(f"fork branch already exists; refusing to overwrite: {branch}")
    title = repo.git("log", "-1", "--format=%s").strip()
    body = repo.git("log", f"{live_base}..HEAD", "--format=%B").strip()
    commit_message = title + "\n\n" + body if body else title
    final_diff = repo.git("diff", f"{live_base}..HEAD", "-U0", "--")
    final_files = sorted(repo.git("diff", "--name-only", f"{live_base}..HEAD", "-z").split("\0"))
    final_files = [item for item in final_files if item]
    message_errors = validate_commit_message(
        commit_message,
        changed_files=final_files,
        diff_text=final_diff,
    )
    if message_errors:
        raise NixPrError(
            "committed message does not satisfy nix-pr message policy:\n- "
            + "\n- ".join(message_errors)
        )
    payload = {
        "title": title,
        "head": f"openclaw:{branch}",
        "base": REMOTE_BASE,
        "body": body,
    }
    if dry_run:
        return {
            "dry_run": True,
            "branch": branch,
            "head_sha": repo.head_sha(),
            "base_sha": live_base,
            "payload": payload,
        }
    repo.push(branch)
    pushed_sha = repo.live_remote_sha("fork", branch)
    if pushed_sha != repo.head_sha():
        raise NixPrError(f"fork branch SHA mismatch: pushed {pushed_sha}, local {repo.head_sha()}")
    api_root = "https://git.montycasa.net/api/v1/repos/patrick/nix-config"
    state = repository_state_dir(repo, state_dir)
    created = _api_request(
        repo,
        "POST",
        f"{api_root}/pulls",
        payload=payload,
        state_dir=state,
        filename="pr-create-response.json",
    )
    number = created.get("number")
    if not isinstance(number, int):
        raise NixPrError("Forgejo create response did not contain a PR number")
    verified = _api_request(
        repo,
        "GET",
        f"{api_root}/pulls/{number}",
        payload=None,
        state_dir=state,
        filename="pr-verify-response.json",
    )
    head = verified.get("head", {})
    base = verified.get("base", {})
    if head.get("sha") != repo.head_sha():
        raise NixPrError("Forgejo PR head SHA does not match local HEAD")
    if base.get("ref") != REMOTE_BASE or base.get("repo", {}).get("full_name") != EXPECTED_ORIGIN:
        raise NixPrError("Forgejo PR base does not match patrick/nix-config:master")
    return {
        "number": number,
        "url": created.get("html_url"),
        "head_sha": repo.head_sha(),
        "base_sha": live_base,
        "mergeable": verified.get("mergeable"),
    }


def print_status(repo: Repository, state_dir: Path | str | None = None) -> None:
    branch = ensure_ready_repository(repo)
    live_origin = repo.live_remote_sha("origin")
    live_fork = repo.live_remote_sha("fork")
    receipt = load_receipt(repo, state_dir)
    print(f"repository: {repo.root}")
    print(f"branch: {branch}")
    print(f"head: {repo.head_sha()}")
    print(f"origin/master: {live_origin}")
    print(f"fork/master: {live_fork}")
    print(f"origin ahead of fork: {repo.git('rev-list', '--count', f'{live_fork}..{live_origin}').strip()}")
    print(f"fork ahead of origin: {repo.git('rev-list', '--count', f'{live_origin}..{live_fork}').strip()}")
    print(f"worktree: {'clean' if repo.is_clean() else 'dirty'}")
    print(f"staged files: {len(repo.staged_files())}")
    print(f"unstaged files: {len(repo.unstaged_files())}")
    print(f"untracked files: {len(repo.untracked_files())}")
    if receipt:
        try:
            current = receipt.get("content_fingerprint") == content_fingerprint(repo, receipt["base_sha"])
        except (KeyError, CommandError):
            current = False
        print(f"validation receipt: {receipt_path(repo, state_dir)}")
        print(f"validation current: {'yes' if current else 'no'}")
    else:
        print("validation receipt: none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nix-pr", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="nix-config checkout")
    parser.add_argument("--state-dir", type=Path, default=None, help="override validation state directory")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)

    start_parser = commands.add_parser("start", help="create a clean branch from live origin/master")
    start_parser.add_argument("slug")
    commands.add_parser("status", help="show repository and validation state")

    check_parser = commands.add_parser("check", help="validate the current worktree")
    check_parser.add_argument("--host", action="append", default=[], help="affected host; may be repeated")
    check_parser.add_argument("--all-hosts", action="store_true", help="validate every flake host")
    check_parser.add_argument("--second-review-file", type=Path, help="saved second-model review for Nix changes")

    commit_parser = commands.add_parser("commit", help="create a validated commit from the staged group")
    commit_parser.add_argument("--message-file", required=True, type=Path)

    submit_parser = commands.add_parser("submit", help="push and open a verified Forgejo PR")
    submit_parser.add_argument("--dry-run", action="store_true", help="prepare and verify metadata without pushing or opening a PR")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Repository(args.repo)
    try:
        if args.command == "start":
            branch = start(repo, args.slug)
            print(f"created branch: {branch}")
        elif args.command == "status":
            print_status(repo, args.state_dir)
        elif args.command == "check":
            receipt = run_check(
                repo,
                state_dir=args.state_dir,
                hosts=args.host,
                all_hosts=args.all_hosts,
                second_review_file=args.second_review_file,
            )
            print(f"validation passed: {receipt['receipt_path'] if 'receipt_path' in receipt else receipt_path(repo, args.state_dir)}")
        elif args.command == "commit":
            head = commit(repo, args.message_file, args.state_dir)
            print(f"created commit: {head}")
        elif args.command == "submit":
            result = submit(repo, state_dir=args.state_dir, dry_run=args.dry_run)
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except NixPrError as exc:
        print(f"nix-pr: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
