{ config, pkgs, inputs, ... }: {

  imports = [ ../../common ];

  users.users.patrick = {

      packages = with pkgs; [
        standardnotes
        #cowsay
      ];
  };

  extra-services.mount_media.enable = true;
}
