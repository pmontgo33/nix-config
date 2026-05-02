{ config, pkgs, modulesPath, inputs, ... }:

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

  sops.secrets."hermes-env".mode = "0444";

  services.hermes-agent = {
    enable = true;
    user = "hermes";
    group = "users";
    createUser = true;
    stateDir = "/var/lib/hermes";
    environmentFiles = [ config.sops.secrets.hermes-env.path ];
    environment = {
      TZ = "America/New_York";
    };
    # Agent config populated in Part 2 migration
    settings = {};
  };

  system.stateVersion = "25.11";
}
