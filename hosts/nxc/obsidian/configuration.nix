{ config, pkgs, modulesPath, inputs, outputs, ... }:

let
  obsidianAutostart = pkgs.writeText "obsidian.desktop" ''
    [Desktop Entry]
    Type=Application
    Name=Obsidian
    Exec=/usr/bin/obsidian
    Hidden=false
    NoDisplay=false
    X-GNOME-Autostart-enabled=true
  '';
in
{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;
  # extra-services.mount_general.enable = true;
  extra-services.mount_notes.enable = true;

  services.openssh.enable = true;

  virtualisation.podman = {
    enable = true;
    dockerCompat = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/obsidian/config 0755 root root -"
    "d /var/lib/obsidian/config/.config/autostart 0755 root root -"
    "C+ /var/lib/obsidian/config/.config/autostart/obsidian.desktop 0644 root root - ${obsidianAutostart}"
  ];

  virtualisation.oci-containers = {
    backend = "podman";
    containers = {
      obsidian = {
        image = "lscr.io/linuxserver/obsidian:latest";
        autoStart = true;
        ports = [
          "3000:3000"
          "3001:3001"
        ];
        volumes = [
          "/var/lib/obsidian/config:/config"
          # "/mnt/general:/mnt/general"
          "/mnt/Notes:/mnt/Notes"
        ];
        environment = {
          PUID = "1000";
          PGID = "1000";
          TZ = "America/New_York";
        };
        extraOptions = [
          "--pull=newer"
          "--shm-size=1g"
          "--dns=1.1.1.1"
          "--dns=8.8.8.8"
        ];
      };
    };
  };

  networking.firewall.allowedTCPPorts = [ 3000 3001 ];

  system.stateVersion = "25.11";
}