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

    # Enable numlock on startup via config file
    configFile = {
      kcminputrc = {
        Keyboard = {
          NumLock = 0; # 0 = turn on, 1 = turn off, 2 = leave unchanged
        };
      };
    };
  };

}
