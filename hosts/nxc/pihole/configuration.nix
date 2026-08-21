{ config, modulesPath, piholeHostName, pkgs, ... }:

{
  proxmoxLXC.manageHostName = true;
  networking.hostName = piholeHostName;

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };

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
    # Gate 3B exposes the Pi-hole web admin/API on all interfaces so the
    # Caddy reverse proxy (local-proxy) can terminate HTTPS at
    # pihole1.montycasa.net / pihole2.montycasa.net. Narrowed to private
    # management networks by the Caddy host firewall (LAN + Tailscale only)
    # and proxied over HTTPS — port 8080 is not advertised to clients.
    # SOPS-rendered apiPasswordEnvironmentFile is already required at this
    # bind scope (modules/pihole default.nix).
    webListenAddress = "0.0.0.0";
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
