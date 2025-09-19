{ lib, config, ... }:
{

  # Auto Upgrade NixOS
  systemd.timers."auto-upgrade" = {
  wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "Mon";
      Persistent = true;
      Unit = "auto-upgrade.service";
    };
  };

  systemd.services.auto-upgrade = {
    description = "Auto-upgrade NixOS system from flake";
    
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      Restart = "on-failure";
      RestartSec = "30s";
      CPUWeight = "20";
      IOWeight = "20";
      MemoryHigh = "500M";
      ExecStart = "${pkgs.bash}/bin/bash -c '${pkgs.nixos-rebuild}/bin/nixos-rebuild boot --flake github:pmontgo33/nix-config/nixbooks#$(${pkgs.nettools}/bin/hostname) --refresh'";
    };
    
    after = [ "network-online.target" "graphical.target" ];
    wants = [ "network-online.target" ];
    
    wantedBy = [ "multi-user.target" ];
  };

}
