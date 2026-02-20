{ config, pkgs, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  networking.hostName = "openclaw";
  networking.firewall.allowedTCPPorts = [ 8384 22000 18789 ];
  networking.firewall.allowedUDPPorts = [ 22000 21027 ];

  environment.systemPackages = with pkgs; [ jq ];

  services.openssh.enable = true;

  # Syncthing (runs as root)
  services.syncthing = {
    enable = true;
    user = "root";
    group = "root";
    dataDir = "/var/lib/syncthing";
    guiAddress = "0.0.0.0:8384";
    settings.gui = {
      user = "patrick";
      password = "$2b$05$HyI3HBR7.6RpSjKnXJVXgOVfq/Kvmc6sDOpnYJ8EbY5U199kmLKZG";
    };
  };

  sops.secrets = {
    openclaw-telegram-token = {};
    openclaw-env = {};
  };

  # Enable Podman
  virtualisation.podman = {
    enable = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  # Create data directory (uid 1000 = container's node user)
  systemd.tmpfiles.rules = [
    "d /var/lib/openclaw 0755 1000 100 -"
  ];

  # OpenClaw container
  virtualisation.oci-containers = {
    backend = "podman";
    containers.openclaw = {
      image = "ghcr.io/openclaw/openclaw:latest";
      ports = [ "18789:18789" ];

      volumes = [
        "/var/lib/openclaw:/home/node/.openclaw:z"
        # Mount SOPS secrets directly
        "${config.sops.secrets.openclaw-env.path}:/home/node/.openclaw/.env:ro"
        "${config.sops.secrets.openclaw-telegram-token.path}:/home/node/.openclaw/secrets/telegram-token:ro"
      ];

      environment = {
        TZ = "America/New_York";
        OPENCLAW_GATEWAY_BIND = "0.0.0.0";
      };

      extraOptions = [
        "--pull=newer"
        "--user=1000:100"
        "--network=host"
      ];
    };
  };

  systemd.services.podman-openclaw = {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
  };

  system.stateVersion = "25.11";
}
