{ lib, config, pkgs, inputs, outputs, ... }:

{
imports = [ ./home.nix ];

  home.packages = with pkgs; [
    kdePackages.kate
    signal-desktop
    cowsay
  ];

}
