{ pkgs, modulesPath, ... }: 
{
  imports = [
    ./samba.nix
    ../users/root.nix
    ../users/patrick.nix
  ];
	
  nix.settings.experimental-features = "nix-command flakes";

  ### INSTALL PACKAGES ###
  environment.systemPackages = with pkgs; [
    vim
    tmux
    git
    fail2ban
		nix-ld
    just
    iperf
    dig
  ];

  ### SET TIMEZONE ###
  time.timeZone = "America/New_York";

  ### OPENSSH ###
  services.openssh.enable = true;

  ### SETUP PACKAGES ###
  programs.tmux = {
    enable = true;
    extraConfig = builtins.readFile ./tmux.conf;
  };
	
  programs.git.enable = true;
	
  ### ENABLE REMOTE VSCODE TO CONNECT ###
	programs.nix-ld.enable = true;
  /*
  Create symlinks for nixos configuration files so user can edit
  mkdir ~/nixos-config
  sudo mv /etc/nixos/ ~/nixos-config
  sudo chown -R $(id -un):users ~/nixos-config
  sudo ln -s ~/nixos-config/ /etc/nixos
  */


}
