{ inputs, outputs, pkgs, ... }:

{
  imports = [
    ../base/base_config.nix
    ../modules/tailscale.nix
    ../modules/podman.nix
    ../containers/code-server.nix
  ];
	
  environment.systemPackages = with pkgs; [
    ansible
    ansible-lint
    terraform
    sshpass
    lazygit

    # This is required for the ansibleguy.opnsense ansible role
    (python3.withPackages (ps: with ps; [
      httpx
    ]))
  ];

  boot.initrd = { supportedFilesystems = [ "nfs" ]; 
  kernelModules = [ "nfs" ];
  };
  nixpkgs.config.allowUnfree = true;

  fileSystems."/mnt/home_media" = {
    device = "192.168.86.99:/mnt/HDD-Mirror-01/home_media";
    fsType = "nfs"; 
  };
# virtualisation = {
  #   docker = {
  #     enable = true;
  #     autoPrune = {
  #       enable = true;
  #       dates = "weekly";
  #     };
  #   };
  # };
  # users.users.patrick.extraGroups = [ "docker" ];

  #virtualisation.oci-containers.backend = "podman"; #moved to podman.nix
  


}
