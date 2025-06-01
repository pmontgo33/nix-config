{ lib, config, pkgs, inputs, outputs, ... }:

{
imports = [ ../common ];

  home.packages = with pkgs; [
    kdePackages.kate
    signal-desktop
    cowsay
    anytype
  ];

  services.syncthing.enable = true;

}
