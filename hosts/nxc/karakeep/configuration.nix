{ config, pkgs, modulesPath, inputs, outputs, ... }:

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
