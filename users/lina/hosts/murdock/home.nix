{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

  home.packages = with pkgs; [
    google-chrome
  ];

  programs.plasma = {
    enable = true;

    configFile = {
      kcminputrc = {
        Keyboard = {
          NumLock = 0;
        };
      };
    };
  };

}
