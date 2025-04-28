{ pkgs, ... }: 

let
  code-server = pkgs.callPackage (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/4ecab3273592f27479a583fb6d975d4aba3486fe.tar.gz";
    sha256 = "000000000000000000000000000000";
  }) { };
in
{
  environment.systemPackages = [ code-server ];

  systemd.services.code-server = {
    description = "code-server";
    after = ["network.target"];
    wantedBy = ["multi-user.target"];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.nodePackages.code-server}/bin/code-server --bind-addr 0.0.0.0:8080 --auth password";
      Restart = "always";
      User = "your_username";
      # Password = "password";
    };
  };

  networking.firewall.allowedTCPPorts = [ 8080 ];
}
