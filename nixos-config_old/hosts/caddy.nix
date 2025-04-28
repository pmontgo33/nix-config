{ pkgs, ... }: 

{
  imports = [
    ../base/base_config.nix
    ./modules/tailscale.nix
  ];
	
  environment.systemPackages = with pkgs; [
		
  ];


  services.caddy = {
    enable = true;
    virtualHosts."example.org".extraConfig = ''
      reverse_proxy http://10.25.40.6
    '';
    virtualHosts."another.example.org".extraConfig = ''
      reverse_proxy unix//run/gunicorn.sock
    '';
  };
}
