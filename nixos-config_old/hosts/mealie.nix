{ config, pkgs, ... }:

let
  unstable = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/nixos-unstable.tar.gz") {
    config = config.nixpkgs.config;
  };
in
{
  imports = [
    ../base/base_config.nix
  ];
	
  environment.systemPackages = with pkgs; [
		unstable.mealie
  ];
  
  services.mealie.enable = true;

  networking.firewall.allowedTCPPorts = [ 9000 ];
}
