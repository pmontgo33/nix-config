{ config, pkgs, modulesPath, inputs, outputs, ... }:

let
  searxng = inputs.nixpkgs-unstable.legacyPackages.${pkgs.stdenv.hostPlatform.system}.searxng;
in
{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  networking.hostName = "searxng";

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  sops.secrets."searxng-env" = {
    mode = "0400";
    owner = "searx";
    group = "searx";
  };

  # Native NixOS SearXNG service (no OCI container)
  services.searx = {
    enable = true;
    package = searxng;
    redisCreateLocally = true;
    environmentFile = config.sops.secrets."searxng-env".path;

    settings = {
      server = {
        bind_address = "0.0.0.0";
        port = 8080;
        base_url = "http://search.montycasa.net/";
        secret_key = "$SEARX_SECRET_KEY";
      };

      # search = {
      #   safe_search = 1;
      # };
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];

  system.stateVersion = "25.11";
}
