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

  # Per-host decrypted copy of secrets/pihole-identities.yaml. sops-nix renders
  # it to /run/secrets.d/<N>/pihole-identities on every activation; the
  # hermes-side orchestrator resolves the live per-activation path over SSH
  # (fish-safe via _resolve_secrets_path) and reads the plaintext MAC list from
  # there. This mirrors the API-password shape: same mode/owner/group, no
  # plaintext secret on disk outside the runtime path, and no dependency on
  # `sops` on the hermes PATH. PR #236 originally opened the way for sops-nix
  # on these hosts; this entry extends that pattern to the identities file.
  #
  # `sopsFile` must be explicit because the host's defaultSopsFile
  # (secrets/secrets.yaml) does not contain a `pihole-identities:` key; the
  # identities mapping lives in its own dedicated file.
  #
  # `key` is intentionally left empty. sops-nix validates the value of any
  # named `key` and rejects nested mappings (the identities file's
  # `identities:` value is a mapping, not a string), so a non-empty `key`
  # fails the manifest validator with "the value of key '...' is not a
  # string". An empty `key` makes sops-nix render the whole decrypted YAML
  # file as-is; the orchestrator's `_parse_identity_yaml` parser (in
  # `live_dry_run.py`, unchanged here) scans for `identityRef:` / `mac:`
  # lines and silently ignores the `identities:` and `sops:` wrapper
  # lines.
  sops.secrets."pihole-identities" = {
    sopsFile = ../../../secrets/pihole-identities.yaml;
    key = "";
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
