{ config, pkgs, inputs, ... }: {

    users.users.lina = {
      isNormalUser = true;
      description = "Lina";
      packages = with pkgs; [

      ];
    };
}
