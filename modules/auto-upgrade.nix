{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.auto-upgrade;
in {
  options.extra-services.auto-upgrade.enable = mkEnableOption "enable auto-upgrade config";

  config = mkIf cfg.enable {

    # Auto Upgrade NixOS
    system.autoUpgrade = {
      enable = true;
      flake = "github:pmontgo33/nix-config#${config.networking.hostName}";
      flags = [
        "-L" # print build logs
        "--refresh"
      ];
      dates = "Mon *-*-* 02:00:00";
      randomizedDelaySec = "45min";
      operation = "boot";
      persistent = true;  # Run on next boot if missed
    };

    # Ensure the timer is persistent at the systemd level
    systemd.timers.nixos-upgrade = {
      timerConfig = {
        Persistent = true;
      };
    };

  };
}