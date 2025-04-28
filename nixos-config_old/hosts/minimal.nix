{ pkgs, ... }: 

{
  imports = [
    ../base/base_config.nix
  ];
	
  environment.systemPackages = with pkgs; [
		
  ];
}
