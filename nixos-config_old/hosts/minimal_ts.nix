{ pkgs, ... }: 

{
  imports = [
    ./minimal.nix
    ../modules/tailscale.nix
  ];
	
  environment.systemPackages = with pkgs; [
		
  ];
}
