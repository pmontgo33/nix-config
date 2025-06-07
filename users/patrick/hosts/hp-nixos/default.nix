{ config, pkgs, inputs, ... }: {

  imports = [ ../../common ./secrets.nix ];

  users.users.patrick = {

      packages = with pkgs; [
        standardnotes
        #cowsay
      ];
  };

  extra-services.mount_media.enable = true;
}
