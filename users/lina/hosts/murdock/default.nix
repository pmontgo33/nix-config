{ config, pkgs, inputs, ... }: {

  imports = [ ../../common ];

  users.users.lina = {

      packages = with pkgs; [

      ];
  };

}
