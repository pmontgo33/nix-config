{ config, pkgs, inputs, outputs, ... }:
# let
#   home-manager = builtins.fetchTarball "https://github.com/nix-community/home-manager/archive/release-24.05.tar.gz";
# in
{
  imports = [
    (import "home-manager/nixos")
  ];

  home-manager.users.root = {
    /* The home.stateVersion option does not have a default and must be set */
    home.stateVersion = "18.09";
    
      programs.git = {
        enable = true;
        userName  = "Monty";
        userEmail = "21371673+pmontgo33@users.noreply.github.com";
        extraConfig = {
          receive.denyCurrentBranch = "updateInstead"; 
        };
      };
  };
}
