{ config, pkgs, inputs, ... }: {

  imports = [ ../../../patrick ];

  users.users.patrick = {

      packages = with pkgs; [
        standardnotes
        #cowsay
      ];
  };

  #extra-services.mount_media.enable = true;
}
