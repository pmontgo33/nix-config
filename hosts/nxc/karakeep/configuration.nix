{ config, pkgs, modulesPath, inputs, outputs, lib, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    # Required by Karakeep's build-time dependency; scope exception to this import.
    config.permittedInsecurePackages = [ "pnpm-9.15.9" ];
  };
in
{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  networking.hostName = "karakeep";

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };

  # Node's DNS resolver stops on MagicDNS SERVFAIL responses for public names.
  # Keep MagicDNS for the tailnet suffix, and send all other lookups to public DNS.
  services.dnsmasq = {
    enable = true;
    settings = {
      no-resolv = true;
      listen-address = "127.0.0.1";
      bind-interfaces = true;
      server = [
        "/skink-galaxy.ts.net/100.100.100.100"
        "1.1.1.1"
        "1.0.0.1"
      ];
    };
  };

  # Override the LXC Tailscale module's flat resolver list with the local
  # split resolver, preserving MagicDNS only for tailnet names.
  environment.etc."resolv.conf".text = lib.mkForce ''
    search skink-galaxy.ts.net
    nameserver 127.0.0.1
    options edns0
  '';

  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  services.karakeep = {
    enable = true;
    package = pkgs-unstable.karakeep;
    meilisearch.enable = true;
    extraEnvironment = {
      DISABLE_NEW_RELEASE_CHECK = "true";
    };
  };

  system.stateVersion = "26.05";
}
