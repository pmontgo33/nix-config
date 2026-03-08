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
  extra-services.obsidian.enable = true;

  services.openssh.enable = true;

  systemd.tmpfiles.rules = [
    "d /var/lib/obsidian-vault 0755 syncthing syncthing -"
  ];

  services.syncthing = {
    enable = true;
    dataDir = "/var/lib/obsidian-vault";
    guiAddress = "0.0.0.0:8384";
    overrideDevices = false;  # Don't reset devices on rebuild
    overrideFolders = false;  # Don't reset folders on rebuild
    settings.gui = {
      user = "patrick";
      password = "$2b$05$HyI3HBR7.6RpSjKnXJVXgOVfq/Kvmc6sDOpnYJ8EbY5U199kmLKZG";
    };
  };

  # Serve .ics calendar files from the Obsidian vault
  services.nginx = {
    enable = true;
    virtualHosts."ics" = {
      listen = [{ addr = "0.0.0.0"; port = 8081; }];
      locations."~ \\.ics$" = {
        root = "/var/lib/obsidian-vault/MontyVault";
      };
      locations."/" = {
        return = "403";
      };
    };
  };

  networking.firewall.allowedTCPPorts = [
    8081  # nginx - .ics calendar files
  ];

  system.stateVersion = "25.11";
}
