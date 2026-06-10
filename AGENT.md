# Agent Instructions

## Host Access

Hosts are defined as directories under `hosts/` (excluding `common`, which contains shared modules). This includes subdirectories of `hosts/nxc/` (also excluding `nxc/common`), which are LXC containers. Each host can be reached over Tailscale via `ssh root@<hostname>`, where `<hostname>` is the directory name. Before SSHing, run `hostname` to check if the target host is the local machine — if it matches, run commands directly instead.
