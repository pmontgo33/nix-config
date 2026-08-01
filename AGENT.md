# Agent Instructions

## Commit Workflow

When asked to review and commit changes in this repository:

1. Use `scripts/nix-pr status` to inspect repository and validation state.
2. For a new task, start from a clean checkout with `scripts/nix-pr start <slug>`.
3. Run `git diff` and `git status` to identify all changes.
4. Group changes by logical concern and stage only the intended files for one group.
5. Run `scripts/nix-pr check`; pass `--second-review-file` for production Nix or systemd changes.
6. Create the commit with `scripts/nix-pr commit --message-file <path>`.
7. Submit only when authorized, using `scripts/nix-pr submit`; use `--dry-run` first when appropriate.

The wrapper owns validation receipts, commit-message checks, pushing, and verified Forgejo PR creation. It does not edit files, merge PRs, deploy systems, resolve conflicts, or force-push.

Commit messages must explain the *why*, not just the *what*. Use imperative mood with no trailing punctuation. Include enough context in the body for a fresh session to understand the motivation, non-obvious constraints, validation performed, and any operational implications. Do not mention an AI tool or add `Co-Authored-By` lines.

## Host Access

Hosts are defined as directories under `hosts/` (excluding `common`, which contains shared modules). This includes subdirectories of `hosts/nxc/` (also excluding `nxc/common`), which are LXC containers. Each host can be reached over Tailscale via `ssh root@<hostname>`, where `<hostname>` is the directory name. Before SSHing, run `hostname` to check if the target host is the local machine — if it matches, run commands directly instead.
