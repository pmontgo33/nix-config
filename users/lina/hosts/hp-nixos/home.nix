{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

  home.packages = with pkgs; [
    google-chrome
    #cowsay
  ];

  services.syncthing.enable = true;
}
