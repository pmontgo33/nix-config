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
    # OPNsense Unbound has listened on port 53 since the Gate 4 cutover. The
    # earlier `:5353` upstream caused every uncached DNS lookup to time out
    # before FTL fell back to the working port, surfacing as user-visible
    # "no internet" symptoms on guest and IoT VLANs.
    upstreams = [
      "192.168.86.1"
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

  services.pihole-native.lists = [
    # Global baseline — applied to all three cohorts.
    {
      url = "https://big.oisd.nl";
      description = "OISD big — comprehensive ad/tracker/malware list";
      groups = [ "Default" "normal" "kids" ];
    }
    {
      url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/dnsmasq/pro.txt";
      description = "Hagezi Pro — balanced DNS blocklist with malware coverage";
      groups = [ "Default" "normal" "kids" ];
    }

    # Kids-only — applied only to clients in the kids group.
    {
      url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/dnsmasq/nsfw.txt";
      description = "Hagezi NSFW — adult content blocklist";
      groups = [ "kids" ];
    }
    {
      url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/dnsmasq/doh-vpn-proxy-bypass.txt";
      description = "Hagezi DoH/VPN/TOR/Proxy bypass blocklist";
      groups = [ "kids" ];
    }
    {
      url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/dnsmasq/gambling.mini.txt";
      description = "Hagezi Gambling (mini) blocklist";
      groups = [ "kids" ];
    }
  ];

  services.openssh.enable = true;

  system.stateVersion = "26.05";
}
