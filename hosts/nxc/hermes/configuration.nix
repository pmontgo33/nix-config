{ config, pkgs, modulesPath, inputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
    inputs.nix-hermes-agent.nixosModules.default
  ];

  networking.hostName = "hermes";
  networking.firewall.allowedTCPPorts = [ 8642 ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  sops.secrets."openclaw-env".mode = "0444";

  services.hermes-agent = {
    enable = true;
    user = "hermes";
    group = "users";
    createUser = true;
    stateDir = "/var/lib/hermes";
    environmentFiles = [ config.sops.secrets.openclaw-env.path ];
    environment = {
      TZ = "America/New_York";
    };
    # Agent config populated in Part 2 migration
    settings = {};
  };

  environment.systemPackages = with pkgs; [
    pkgs-unstable.claude-code
  ];

  system.stateVersion = "25.11";
}
