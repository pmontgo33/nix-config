{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.mount_general;
in {
  options.extra-services.mount_general = {
    enable = mkEnableOption "mount the general share from TrueNAS";

    readOnly = mkOption {
      type = types.bool;
      default = false;
      description = "Mount the NFS share as read-only";
    };
  };

  config = mkIf cfg.enable {

    fileSystems."/mnt/general" = {
      device = "192.168.86.99:/mnt/HDD-Mirror-01/general";
      fsType = "nfs";
      options = [ "x-systemd.after=network-online.target" "x-systemd.requires=network-online.target" ] ++ (if cfg.readOnly then [ "ro" ] else []);
    };
    # optional, but ensures rpc-statsd is running for on demand mounting
    boot.supportedFilesystems = [ "nfs" ];

  };
}
