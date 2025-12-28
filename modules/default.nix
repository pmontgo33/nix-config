{
  imports = [
    ./auto-upgrade.nix
    ./desktop.nix
    ./tailscale.nix
    ./mount_home_media.nix
    ./mount_media.nix
    ./pbs-home-dirs.nix
    ./caddy-proxy.nix
    ./host-checkin.nix
    ./proxmox-storage-monitor.nix
    ./simplex-relay.nix
  ];
}
