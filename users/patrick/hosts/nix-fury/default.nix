{ config, pkgs, inputs, ... }: {

  imports = [ ../../common ];

  users.users.patrick = {

      packages = with pkgs; [
        cowsay
      ];
  };

}
