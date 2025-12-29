{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.auto-upgrade;
in {
  options.extra-services.auto-upgrade = {
    enable = mkEnableOption "enable auto-upgrade config";

    buildHost = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Remote host to use for building. If set, the system will be built
        on the specified host via SSH and the result copied back.
        Useful for resource-constrained hosts.
        Example: "root@builder-host"
      '';
    };
  };

  config = mkIf cfg.enable {

    # Auto Upgrade NixOS
    system.autoUpgrade = {
      enable = true;
      flake = "github:pmontgo33/nix-config#${config.networking.hostName}";
      flags = [
        "-L" # print build logs
        "--refresh"
      ] ++ optionals (cfg.buildHost != null) [
        "--build-host"
        cfg.buildHost
      ];
      # dates = "Mon *-*-* 02:00:00";
      dates = "Tue *-*-* 02:00:00";
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

    # Add build host to known_hosts if it's nix-fury
    # Uses a unique key name to avoid conflicts if defined elsewhere
    programs.ssh.knownHosts = mkIf (cfg.buildHost == "root@nix-fury") {
      "nix-fury-auto-upgrade" = {
        hostNames = [ "nix-fury" ];
        publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILvna6/m8kyTOf78WA680y4z+wzJ2NjNwnNjnC78GSCf";
      };
    };

  };
}