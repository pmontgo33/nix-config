{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

#   age.secrets.tailscale_auth_key.file = ../../../../secrets/tailscale_auth_key.age;
  ### Above was a test. First try to import and use the hosts secrets file. If not, then use an individual secrets file for a user/host

  home.packages = with pkgs; [
    kdePackages.kate
    signal-desktop
    #cowsay
    anytype
  ];

  services.syncthing.enable = true;
}
