# Agent Instructions

## Commit Workflow

When asked to review and commit changes:
1. Run `git diff` and `git status` to identify all unstaged changes.
2. Group changes by logical concern — one commit per distinct change/update.
3. Stage and commit each group separately.
4. Write commit messages that explain the *why*, not just the *what*. Use imperative mood, no trailing period.
5. Include enough context in the commit body that a future agent in a fresh session could understand the motivation, any non-obvious constraints, and what would be needed to continue or troubleshoot the change.
6. Do not mention Claude or add `Co-Authored-By` lines in commit messages.
7. Pause for confirmation before each commit unless the user has said to proceed without asking.

## Host Access

Hosts are defined as directories under `hosts/` (excluding `common`, which contains shared modules). This includes subdirectories of `hosts/nxc/` (also excluding `nxc/common`), which are LXC containers. Each host can be reached over Tailscale via `ssh root@<hostname>`, where `<hostname>` is the directory name. Before SSHing, run `hostname` to check if the target host is the local machine — if it matches, run commands directly instead.
