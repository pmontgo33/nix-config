{ config, pkgs, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  virtualisation.podman = {
    enable = true;
    dockerCompat = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/obsidian/vaults 0755 root root -"
    "d /var/lib/obsidian/config 0755 root root -"
  ];

  virtualisation.oci-containers = {
    backend = "podman";
    containers = {
      obsidian-remote = {
        image = "ghcr.io/sytone/obsidian-remote:latest";
        autoStart = true;
        ports = [
          "8080:8080"
        ];
        volumes = [
          "/var/lib/obsidian/vaults:/vaults"
          "/var/lib/obsidian/config:/config"
        ];
        environment = {
          PUID = "1000";
          PGID = "1000";
          TZ = "America/New_York";
        };
        extraOptions = [
          "--pull=newer"
        ];
      };
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];

  system.stateVersion = "25.11";
}