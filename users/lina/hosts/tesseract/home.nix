{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

  home.packages = with pkgs; [
    google-chrome
    #cowsay
  ];

  # Plasma 6 configuration
  programs.plasma = {
    enable = true;

    workspace = {
      # Enable numlock on startup
      enableNumlockOnStartup = true;
    };
  };

}
