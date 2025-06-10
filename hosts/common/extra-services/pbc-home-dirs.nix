{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.pbc-home-dirs;
in {
  options.extra-services.pbc-home-dirs.enable = mkEnableOption "enable pbc-home-dirs config";

  config = mkIf cfg.enable {

    # SOPS secrets configuration
#     sops.secrets.pbs-password = {
#       sopsFile = ./secrets.yaml;  # Adjust path to your secrets file
#       mode = "0400";
#       owner = "root";
#       group = "root";
#     };
#
#     sops.secrets.pbs-fingerprint = {
#       sopsFile = ./secrets.yaml;  # Adjust path to your secrets file
#       mode = "0400";
#       owner = "root";
#       group = "root";
#     };

    # Install Proxmox Backup Client and utility scripts
    environment.systemPackages = with pkgs; [
      proxmox-backup-client

      # Utility scripts
      (writeScriptBin "pbs-backup-homes-now" ''
        #!${pkgs.bash}/bin/bash
        echo "Starting Proxmox home directories backup..."
        sudo systemctl start proxmox-backup-homes.service
        echo "Backup started. Check logs: journalctl -u proxmox-backup-homes.service -f"
      '')

      (writeScriptBin "pbs-status" ''
        #!${pkgs.bash}/bin/bash
        echo "=== Proxmox Backup Service Status ==="
        systemctl status proxmox-backup.service
        echo
        echo "=== Proxmox Backup Timer Status ==="
        systemctl status proxmox-backup.timer
        echo
        echo "=== Recent Backup Logs ==="
        if [ -f /var/log/proxmox-backup.log ]; then
            tail -20 /var/log/proxmox-backup.log
        else
            echo "No backup log found yet"
        fi
      '')

      (writeScriptBin "pbs-list-backups" ''
        #!${pkgs.bash}/bin/bash
        PBS_REPOSITORY="backup-user@your-pbs-server:datastore-name"
        PBS_PASSWORD_FILE="${config.sops.secrets.pbs-password.path}"
        PBS_FINGERPRINT=$(cat "${config.sops.secrets.pbs-fingerprint.path}")

        proxmox-backup-client list \
          --repository "$PBS_REPOSITORY" \
          --password-file "$PBS_PASSWORD_FILE" \
          --fingerprint "$PBS_FINGERPRINT"
      '')
    ];

    # Timer for home directories backup
    systemd.timers.proxmox-backup-homes = {
      description = "Run Proxmox home directories backup every 6 hours";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "15min";  # Wait 15 minutes after boot
        OnUnitActiveSec = "6h";  # Run every 6 hours
        Persistent = true;  # Catch up on missed runs
        RandomizedDelaySec = "30min";  # Add some randomization
      };
    };

    # Create Backup of all Home directories
    systemd.services.proxmox-backup-homes = {
      description = "Proxmox Backup Home Directories";
      wants = [ "network-online.target" "sops-nix.service" ];
      after = [ "network-online.target" "sops-nix.service" ];
      serviceConfig = {
        Type = "oneshot";
        User = "root";
        ExecStart = "${pkgs.writeShellScript "proxmox-backup-homes" ''
          #!${pkgs.bash}/bin/bash

          PBS_REPOSITORY="pbs:pbs"
          PBS_PASSWORD_FILE="${config.sops.secrets.pbs-password.path}"
          PBS_FINGERPRINT=$(cat "${config.sops.secrets.pbs-fingerprint.path}")
          BACKUP_ID="$(cat /etc/hostname)"
          LOG_FILE="/var/log/proxmox-backup-homes.log"

          touch "$LOG_FILE"

          log_message() {
              echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
          }

          log_message "Starting home directories backup"

          # Check connectivity
          PBS_HOST=$(echo "$PBS_REPOSITORY" | cut -d'@' -f2 | cut -d':' -f1)
          if ! ping -c 1 -W 5 "$PBS_HOST" > /dev/null 2>&1; then
              log_message "ERROR: PBS server not reachable"
              exit 1
          fi

          # Backup home directories
          if proxmox-backup-client backup \
              --repository "$PBS_REPOSITORY" \
              --password-file "$PBS_PASSWORD_FILE" \
              --fingerprint "$PBS_FINGERPRINT" \
              --backup-id "$BACKUP_ID" \
              --backup-time $(date +%s) \
              home.pxar:/home \
              --exclude=/home/*/.cache \
              --exclude=/home/*/.local/share/Trash \
              --exclude=/home/*/.thumbnails \
              --exclude=/home/*/Downloads \
              --exclude='*.tmp' \
              --exclude='node_modules' \
              --exclude='.git' \
              >> "$LOG_FILE" 2>&1; then
              log_message "Home directories backup completed successfully"
          else
              log_message "ERROR: Home directories backup failed"
              exit 1
          fi
        ''}";
      };
    };
  };
}
