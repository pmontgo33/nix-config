{ config, modulesPath, piholeHostName, pkgs, ... }:

{
  proxmoxLXC.manageHostName = true;
  networking.hostName = piholeHostName;

  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  # Both production Pi-hole instances use the shared API credential from SOPS.
  # Runtime state and desired policy remain independent; no plaintext
  # credential belongs in this repository.
  sops.secrets."pihole-api-password" = {
    mode = "0400";
    owner = "root";
    group = "root";
  };

  sops.templates."pihole-api-env" = {
    content = ''
      FTLCONF_webserver_api_password=${config.sops.placeholder."pihole-api-password"}
    '';
    mode = "0400";
    owner = "root";
    group = "root";
    restartUnits = [ "pihole-ftl.service" ];
  };

  services.pihole-native = {
    enable = true;
    interface = "eth0";
    # Temporary upstream while AdGuard owns OPNsense port 53. This preserves
    # the existing recursive path through OPNsense Unbound.
    upstreams = [
      "192.168.86.1#5353"
    ];
    # Keep the API loopback-only during initial provisioning. Remote audit
    # access will use an explicitly approved private path after smoke testing.
    webListenAddress = "127.0.0.1";
    apiPasswordEnvironmentFile = config.sops.templates."pihole-api-env".path;
    webPort = 8080;
    openFirewallDNS = true;
  };

  services.pihole-ftl.lists = [
    {
      url = "file://${pkgs.stevenblack-blocklist}/hosts";
      description = "Shared Pi-hole baseline adlist";
    }
  ];

  services.openssh.enable = true;

  system.stateVersion = "26.05";
}
