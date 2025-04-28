{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.desktop;
in {
  options.extra-services.tmux.enable = mkEnableOption "enable tmux and config";

  config = mkIf cfg.enable {

		programs.tmux = {
			enable = true;
			extraConfig = builtins.readFile ./tmux.conf;
		};

  };
}
