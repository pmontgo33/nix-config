/*
  Host Check-in Module

  This module enables hosts to automatically report their status to a central host (bifrost)
  and optionally pull the consolidated host-states.md file.

  Features:
  - Periodic check-in with system state information
  - Optional pull of host-states.md from bifrost
  - Configurable timers for both check-in and pull operations

  Usage:
    extra-services.host-checkin = {
      enable = true;
      checkInInterval = "hourly";  # When to send status updates
      pullStates = true;            # Whether to pull host-states.md
      pullInterval = "daily";       # When to pull host-states.md
    };
*/

{ config, lib, pkgs, ... }:

with lib;
let
  cfg = config.extra-services.host-checkin;

  # Central host that collects all check-ins
  centralHost = "bifrost";
  centralUser = "root";
  checkinDir = "/var/lib/host-checkins";

  # Script that sends this host's status to bifrost
  checkinScript = pkgs.writeShellScript "host-checkin" ''
    set -euo pipefail

    HOSTNAME=$(${pkgs.nettools}/bin/hostname)
    TIMESTAMP=$(${pkgs.coreutils}/bin/date -Iseconds)
    NIXOS_VERSION=$(${pkgs.coreutils}/bin/cat /run/current-system/nixos-version)
    LAST_REBUILD=$(${pkgs.coreutils}/bin/stat -c %y /run/current-system | ${pkgs.coreutils}/bin/cut -d' ' -f1)

    # Create JSON payload with host information
    PAYLOAD=$(${pkgs.jq}/bin/jq -n \
      --arg hostname "$HOSTNAME" \
      --arg timestamp "$TIMESTAMP" \
      --arg version "$NIXOS_VERSION" \
      --arg rebuild "$LAST_REBUILD" \
      '{
        hostname: $hostname,
        timestamp: $timestamp,
        nixos_version: $version,
        last_rebuild: $rebuild,
        status: "online"
      }')

    # Send to bifrost via SSH
    echo "$PAYLOAD" | ${pkgs.openssh}/bin/ssh -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=10 \
      ${centralUser}@${centralHost} \
      "mkdir -p ${checkinDir} && cat > ${checkinDir}/$HOSTNAME.json"

    echo "Check-in successful for $HOSTNAME at $TIMESTAMP"
  '';

  # Script that pulls the host-states.md file from bifrost
  pullStatesScript = pkgs.writeShellScript "pull-host-states" ''
    set -euo pipefail

    HOSTNAME=$(${pkgs.nettools}/bin/hostname)
    DEST_DIR="${cfg.stateFileDestination}"

    # Ensure the destination directory exists
    mkdir -p "$DEST_DIR"

    # Pull the host-states.md file from bifrost
    ${pkgs.openssh}/bin/scp -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=10 \
      ${centralUser}@${centralHost}:${checkinDir}/host-states.md \
      "$DEST_DIR/host-states.md"

    echo "Successfully pulled host-states.md to $DEST_DIR"

    # Make it readable by all users
    chmod 644 "$DEST_DIR/host-states.md"
  '';

in {
  options.extra-services.host-checkin = {
    enable = mkEnableOption "host check-in service";

    checkInInterval = mkOption {
      type = types.str;
      default = "hourly";
      example = "hourly";
      description = ''
        How often to check in with bifrost. Can be any systemd timer specification.
        Common values: "hourly", "daily", "weekly", "*:0/30" (every 30 minutes)
      '';
    };

    pullStates = mkOption {
      type = types.bool;
      default = false;
      description = "Whether to pull the host-states.md file from bifrost";
    };

    pullInterval = mkOption {
      type = types.str;
      default = "daily";
      example = "daily";
      description = ''
        How often to pull the host-states.md file from bifrost.
        Only used if pullStates is true.
      '';
    };

    stateFileDestination = mkOption {
      type = types.str;
      default = "/var/lib/host-states";
      example = "/root/nix-config";
      description = ''
        Directory where the host-states.md file will be saved when pulled.
        The directory will be created if it doesn't exist.
        Only used if pullStates is true.
      '';
    };

    isCentralHost = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Set to true on bifrost to enable the aggregation service.
        This host will collect check-ins and generate host-states.md.
      '';
    };
  };

  config = mkIf cfg.enable {
    # Check-in service (all hosts)
    systemd.services.host-checkin = {
      description = "Send host status check-in to bifrost";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${checkinScript}";
        TimeoutStartSec = "30s";
        Restart = "no";
      };
      # Don't fail if bifrost is unreachable
      startLimitBurst = 3;
      startLimitIntervalSec = 300;
    };

    # Check-in timer (all hosts)
    systemd.timers.host-checkin = {
      description = "Timer for host status check-in";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.checkInInterval;
        Persistent = true;
        RandomizedDelaySec = "5min";
      };
    };

    # Pull states service (optional)
    systemd.services.pull-host-states = mkIf cfg.pullStates {
      description = "Pull host-states.md from bifrost";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pullStatesScript}";
        TimeoutStartSec = "30s";
        Restart = "no";
      };
    };

    # Pull states timer (optional)
    systemd.timers.pull-host-states = mkIf cfg.pullStates {
      description = "Timer for pulling host-states.md";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.pullInterval;
        Persistent = true;
      };
    };

    # Central host services (bifrost only)
    systemd.tmpfiles.rules = mkIf cfg.isCentralHost [
      "d ${checkinDir} 0755 root root -"
    ];

    # Aggregation script on bifrost
    systemd.services.aggregate-host-states = mkIf cfg.isCentralHost {
      description = "Aggregate host check-ins into host-states.md";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "aggregate-host-states" ''
          set -euo pipefail

          CHECKIN_DIR="${checkinDir}"
          OUTPUT_FILE="${checkinDir}/host-states.md"
          TIMESTAMP=$(${pkgs.coreutils}/bin/date +%Y-%m-%d)

          # Ensure the directory exists
          mkdir -p "$CHECKIN_DIR"

          # Start building the markdown file
          cat > "$OUTPUT_FILE" << 'EOF'
# NixOS Host States

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, and last rebuild date. This helps identify which hosts need updates or maintenance.

Last updated: TIMESTAMP_PLACEHOLDER

## Host Status Table

| Host Name | Status | NixOS Version | Last Verified | Last Rebuild | Notes |
|-----------|--------|---------------|---------------|--------------|-------|
EOF

          # Replace timestamp placeholder
          ${pkgs.gnused}/bin/sed -i "s/TIMESTAMP_PLACEHOLDER/$TIMESTAMP/" "$OUTPUT_FILE"

          # Process all check-in files
          for checkin_file in "$CHECKIN_DIR"/*.json; do
            if [ -f "$checkin_file" ]; then
              HOSTNAME=$(${pkgs.jq}/bin/jq -r '.hostname' "$checkin_file")
              VERSION=$(${pkgs.jq}/bin/jq -r '.nixos_version' "$checkin_file")
              VERIFIED=$(${pkgs.jq}/bin/jq -r '.timestamp' "$checkin_file" | cut -d'T' -f1)
              REBUILD=$(${pkgs.jq}/bin/jq -r '.last_rebuild' "$checkin_file")

              # Determine version and notes
              if echo "$VERSION" | grep -q "25.11"; then
                VERSION_SHORT="25.11 (Xantusia)"
                NOTES="Upgraded to 25.11"
              elif echo "$VERSION" | grep -q "25.05"; then
                VERSION_SHORT="25.05 (Warbler)"
                NOTES="Needs upgrade to 25.11"
              else
                VERSION_SHORT="$VERSION"
                NOTES="Unknown version"
              fi

              echo "| $HOSTNAME | ✅ Online | $VERSION_SHORT | $VERIFIED | $REBUILD | $NOTES |" >> "$OUTPUT_FILE"
            fi
          done

          # Add static sections
          cat >> "$OUTPUT_FILE" << 'EOF'

## Development/Special Hosts (Not Tracked)

- **nixbook-installer** - Installation media, not a running system
- **nxc-base** - Base container template
- **lxc-tailscale** - Tailscale container template
- **immich** - Development host
- **erpnext** - Development host
- **pocket-id** - Development host
- **onlyoffice** - Development host

## Summary

Statistics are calculated from online hosts only.

## Upgrade Priority

1. High priority services: nextcloud, forgejo, jellyfin
2. Infrastructure: bifrost, local-proxy, homepage
3. Applications: grist, endurain, omnitools, yondu
4. Laptops: emma-book (when online)
EOF

          echo "Generated host-states.md with data from ${checkinDir}"
        '';
      };
    };

    # Timer to aggregate check-ins on bifrost
    systemd.timers.aggregate-host-states = mkIf cfg.isCentralHost {
      description = "Timer for aggregating host states";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "hourly";
        Persistent = true;
      };
    };
  };
}
