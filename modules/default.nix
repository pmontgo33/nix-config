{
  imports = [
    ./auto-upgrade.nix
    ./desktop.nix
    ./flutter.nix
    ./tailscale.nix
    ./mount_home_media.nix
    ./mount_media.nix
    ./mount_general.nix
    ./mount_notes.nix
    ./pbs-home-dirs.nix
    ./caddy-proxy.nix
    ./host-checkin.nix
    ./proxmox-storage-monitor.nix
    ./simplex-relay.nix
  ];
}
