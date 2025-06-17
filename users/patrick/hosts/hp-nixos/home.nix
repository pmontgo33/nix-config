{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

  home.packages = with pkgs; [
    kdePackages.kate
    signal-desktop
    vscodium
    #cowsay
    anytype
  ];
}
