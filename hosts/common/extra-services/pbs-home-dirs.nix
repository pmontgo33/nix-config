{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.pbs-home-dirs;
in {
  options.extra-services.pbs-home-dirs.enable = mkEnableOption "enable pbc-home-dirs config";

  config = mkIf cfg.enable {

    # sops secrets configuration
    sops = {
      defaultSopsFile = ../../../secrets/secrets.yaml;
      secrets = {
        "pbs-password" = {};
        "pbs-fingerprint" = {};
      };
    };

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
        echo "=== Proxmox Backup Homes Service Status ==="
        systemctl status proxmox-backup-homes.service
        echo
        echo "=== Proxmox Backup Homes Timer Status ==="
        systemctl status proxmox-backup-homes.timer
        echo
        echo "=== Recent Backup Logs ==="
        if [ -f /var/log/proxmox-backup-homes.log ]; then
            tail -20 /var/log/proxmox-backup-homes.log
        else
            echo "No backup log found yet"
        fi
      '')

      (writeScriptBin "pbs-list-backups" ''
        #!${pkgs.bash}/bin/bash

        # Note: This script needs to run as root to access SOPS secrets
        if [ "$EUID" -ne 0 ]; then
          echo "This script needs to run as root to access secrets"
          echo "Run: sudo pbs-list-backups"
          exit 1
        fi

        export PBS_REPOSITORY="192.168.86.102:8007:pbs"
        export PBS_PASSWORD="$(cat ${config.sops.secrets.pbs-password.path})"
        export PBS_FINGERPRINT="$(cat ${config.sops.secrets.pbs-fingerprint.path})"

        proxmox-backup-client list --repository "$PBS_REPOSITORY"
      '')

      (writeScriptBin "pbs-debug" ''
        #!${pkgs.bash}/bin/bash

        # Note: This script needs to run as root to access SOPS secrets
        if [ "$EUID" -ne 0 ]; then
          echo "This script needs to run as root to access secrets"
          echo "Run: sudo pbs-debug"
          exit 1
        fi

        echo "=== PBS Debug Information ==="
        echo "Password file exists: $(test -f ${config.sops.secrets.pbs-password.path} && echo "YES" || echo "NO")"
        echo "Fingerprint file exists: $(test -f ${config.sops.secrets.pbs-fingerprint.path} && echo "YES" || echo "NO")"
        echo "Password length: $(cat ${config.sops.secrets.pbs-password.path} | wc -c)"
        echo "Fingerprint: $(cat ${config.sops.secrets.pbs-fingerprint.path})"
        echo
        echo "Testing different repository formats:"

        PBS_PASSWORD="$(cat ${config.sops.secrets.pbs-password.path})"
        PBS_FINGERPRINT="$(cat ${config.sops.secrets.pbs-fingerprint.path})"

        # Test different formats
        for repo in "root@pam@192.168.86.102:pbs" "root@192.168.86.102:pbs" "192.168.86.102:pbs"; do
          echo "Testing: $repo"

          # Set environment variables for this specific test
          export PBS_REPOSITORY="$repo"
          export PBS_PASSWORD="$PBS_PASSWORD"
          export PBS_FINGERPRINT="$PBS_FINGERPRINT"

          if timeout 10 proxmox-backup-client list --repository "$repo" >/dev/null 2>&1; then
            echo "  ✓ SUCCESS: $repo works!"
          else
            echo "  ✗ FAILED: $repo"
            # Show the actual error for debugging
            echo "    Error: $(proxmox-backup-client list --repository "$repo" 2>&1 | head -1)"
          fi
        done
      '')
    ];

    # Timer for home directories backup - 1 minute after user login
    systemd.timers.proxmox-backup-homes = {
      description = "Run Proxmox home directories backup 1 minute after user login";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnActiveSec = "1min";  # Run 1 minute after the timer itself is activated
      };
    };

    # Create Backup of all Home directories
    systemd.services.proxmox-backup-homes = {
      description = "Proxmox Backup Home Directories";
      wants = [ "network-online.target" "sops-nix.service" ];
      after = [ "network-online.target" "sops-nix.service" ];
      requires = [ "network-online.target" ];  # Fail if network not available
      serviceConfig = {
        Type = "oneshot";
        User = "root";
        ExecStart = "${pkgs.writeShellScript "proxmox-backup-homes" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail  # Exit on any error, undefined variable, or pipe failure

          # Ensure we have access to ALL required utilities including proxmox-backup-client
          export PATH="${pkgs.coreutils}/bin:${pkgs.iputils}/bin:${pkgs.openssh}/bin:${pkgs.proxmox-backup-client}/bin:$PATH"

          # Set environment variables for authentication (using same format as working pbs-list-backups)
          export PBS_REPOSITORY="192.168.86.102:8007:pbs"
          export PBS_PASSWORD="$(cat ${config.sops.secrets.pbs-password.path})"
          export PBS_FINGERPRINT="$(cat ${config.sops.secrets.pbs-fingerprint.path})"

          # Backup configuration
          BACKUP_ID="$(cat /etc/hostname)"
          LOG_FILE="/var/log/proxmox-backup-homes.log"

          touch "$LOG_FILE"

          log_message() {
              echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
          }

          log_message "Starting home directories backup"
          log_message "Repository: $PBS_REPOSITORY"
          log_message "Backup ID: $BACKUP_ID"
          log_message "Debug - PBS_PASSWORD length: $(echo -n "$PBS_PASSWORD" | wc -c)"
          log_message "Debug - PBS_FINGERPRINT: $PBS_FINGERPRINT"

          # Debug: Check if proxmox-backup-client is available
          log_message "Debug - proxmox-backup-client location: $(which proxmox-backup-client || echo 'NOT FOUND')"
          log_message "Debug - PATH: $PATH"

          # Check connectivity - extract just the IP address
          PBS_HOST="192.168.86.102"
          log_message "Testing connectivity to $PBS_HOST"
          if ! ping -c 1 -W 5 "$PBS_HOST" > /dev/null 2>&1; then
              log_message "ERROR: PBS server $PBS_HOST not reachable"
              exit 1
          fi
          log_message "Connectivity test passed"

          # Test authentication first
          log_message "Testing PBS authentication"
          log_message "Debug - Running: proxmox-backup-client list --repository $PBS_REPOSITORY"

          # Test authentication with simpler logic
          log_message "Running authentication test..."
          proxmox-backup-client list --repository "$PBS_REPOSITORY" > /tmp/pbs_auth_test.out 2>&1
          AUTH_EXIT_CODE=$?

          if [ $AUTH_EXIT_CODE -eq 0 ]; then
              log_message "Authentication test passed"
              BACKUP_COUNT=$(cat /tmp/pbs_auth_test.out | wc -l)
              log_message "Available backups found: $BACKUP_COUNT lines"
          else
              log_message "ERROR: PBS authentication failed with exit code $AUTH_EXIT_CODE"
              log_message "Error output:"
              cat /tmp/pbs_auth_test.out | tee -a "$LOG_FILE"
              log_message "Repository used: $PBS_REPOSITORY"
              log_message "Password length: $(echo -n "$PBS_PASSWORD" | wc -c)"
              log_message "Fingerprint: $PBS_FINGERPRINT"
              rm -f /tmp/pbs_auth_test.out
              exit 1
          fi
          rm -f /tmp/pbs_auth_test.out

          # Backup home directories
          log_message "Starting backup operation"
          if proxmox-backup-client backup \
              --repository "$PBS_REPOSITORY" \
              --backup-id "$BACKUP_ID" \
              --backup-time $(date +%s) \
              home.pxar:/home \
              --exclude=/home/*/.cache \
              --exclude=/home/*/.local/share/Trash \
              --exclude=/home/*/.thumbnails \
              --exclude=/home/*/Downloads \
              --exclude=/home/*/Nextcloud \
              --exclude=/home/*/mnt \
              --exclude='*.tmp' \
              --exclude='node_modules' \
              --exclude='.git' \
              2>&1 | tee -a "$LOG_FILE"; then
              log_message "Home directories backup completed successfully"
          else
              BACKUP_EXIT_CODE=$?
              log_message "ERROR: Home directories backup failed with exit code $BACKUP_EXIT_CODE"
              exit $BACKUP_EXIT_CODE
          fi
        ''}";
      };
    };


  };
}
