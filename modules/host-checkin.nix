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

  # Script that sends this host's status to bifrost (for remote hosts)
  checkinScriptRemote = pkgs.writeShellScript "host-checkin-remote" ''
    set -euo pipefail

    HOSTNAME=$(${pkgs.nettools}/bin/hostname)
    TIMESTAMP=$(${pkgs.coreutils}/bin/date -Iseconds)
    NIXOS_VERSION=$(${pkgs.coreutils}/bin/cat /run/current-system/nixos-version)
    LAST_REBUILD=$(${pkgs.coreutils}/bin/stat -c %y /run/current-system | ${pkgs.coreutils}/bin/cut -d' ' -f1)

    # Collect container information (podman or docker)
    CONTAINER_INFO="[]"
    if command -v ${pkgs.podman}/bin/podman &> /dev/null; then
      CONTAINER_INFO=$(${pkgs.podman}/bin/podman ps --format json 2>/dev/null | ${pkgs.jq}/bin/jq -c '
        [.[] | select(.Image != null) |
        select(.Image | test("postgres|mysql|mariadb|mongodb|redis|cassandra|elasticsearch"; "i") | not) |
        {
          name: (.Names[0] // .Names // "unknown"),
          image: .Image,
          image_name: (.Image | split("@")[0] | split(":")[0]),
          current_version: (.Image | split(":")[1] // "latest")
        }]
      ' || echo "[]")

      # Enrich with RepoDigests for each container
      if [ "$CONTAINER_INFO" != "[]" ]; then
        TEMP_INFO="[]"
        for container in $(echo "$CONTAINER_INFO" | ${pkgs.jq}/bin/jq -c '.[]'); do
          img_name=$(echo "$container" | ${pkgs.jq}/bin/jq -r '.image_name')
          repo_digest=$(${pkgs.podman}/bin/podman image inspect "$img_name" 2>/dev/null | \
            ${pkgs.jq}/bin/jq -r '.[0].RepoDigests[0] // ""' 2>/dev/null | cut -d'@' -f2 || echo "")
          TEMP_INFO=$(echo "$TEMP_INFO" | ${pkgs.jq}/bin/jq --argjson container "$container" --arg digest "$repo_digest" \
            '. += [$container + {repo_digest: $digest}]')
        done
        CONTAINER_INFO="$TEMP_INFO"
      fi
    elif command -v ${pkgs.docker}/bin/docker &> /dev/null; then
      CONTAINER_INFO=$(${pkgs.docker}/bin/docker ps --format "{{.Names}}\t{{.Image}}" 2>/dev/null | \
        while IFS=$'\t' read -r name image; do
          if ! echo "$image" | ${pkgs.gnugrep}/bin/grep -qiE "(postgres|mysql|mariadb|mongodb|redis|cassandra|elasticsearch)"; then
            # Extract tag
            tag=$(echo "$image" | cut -d':' -f2)
            if [ -z "$tag" ] || [ "$tag" = "$image" ]; then
              tag="latest"
            fi

            # Get RepoDigest from image inspection
            repo_digest=$(${pkgs.docker}/bin/docker image inspect "$image" 2>/dev/null | \
              ${pkgs.jq}/bin/jq -r '.[0].RepoDigests[0] // ""' 2>/dev/null | cut -d'@' -f2 || echo "")

            ${pkgs.jq}/bin/jq -n \
              --arg name "$name" \
              --arg image "$image" \
              --arg repo_digest "$repo_digest" \
              --arg version "$tag" \
              '{name: $name, image: $image, repo_digest: $repo_digest, current_version: $version}'
          fi
        done | ${pkgs.jq}/bin/jq -s -c '.' || echo "[]")
    fi

    # Create JSON payload with host information
    PAYLOAD=$(${pkgs.jq}/bin/jq -n \
      --arg hostname "$HOSTNAME" \
      --arg timestamp "$TIMESTAMP" \
      --arg version "$NIXOS_VERSION" \
      --arg rebuild "$LAST_REBUILD" \
      --argjson containers "$CONTAINER_INFO" \
      '{
        hostname: $hostname,
        timestamp: $timestamp,
        nixos_version: $version,
        last_rebuild: $rebuild,
        containers: $containers
      }')

    # Send to bifrost via SSH
    echo "$PAYLOAD" | ${pkgs.openssh}/bin/ssh -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=10 \
      ${centralUser}@${centralHost} \
      "mkdir -p ${checkinDir} && cat > ${checkinDir}/$HOSTNAME.json"

    echo "Check-in successful for $HOSTNAME at $TIMESTAMP"
  '';

  # Script that writes status locally (for central host)
  checkinScriptLocal = pkgs.writeShellScript "host-checkin-local" ''
    set -euo pipefail

    HOSTNAME=$(${pkgs.nettools}/bin/hostname)
    TIMESTAMP=$(${pkgs.coreutils}/bin/date -Iseconds)
    NIXOS_VERSION=$(${pkgs.coreutils}/bin/cat /run/current-system/nixos-version)
    LAST_REBUILD=$(${pkgs.coreutils}/bin/stat -c %y /run/current-system | ${pkgs.coreutils}/bin/cut -d' ' -f1)

    # Collect container information (podman or docker)
    CONTAINER_INFO="[]"
    if command -v ${pkgs.podman}/bin/podman &> /dev/null; then
      CONTAINER_INFO=$(${pkgs.podman}/bin/podman ps --format json 2>/dev/null | ${pkgs.jq}/bin/jq -c '
        [.[] | select(.Image != null) |
        select(.Image | test("postgres|mysql|mariadb|mongodb|redis|cassandra|elasticsearch"; "i") | not) |
        {
          name: (.Names[0] // .Names // "unknown"),
          image: .Image,
          image_name: (.Image | split("@")[0] | split(":")[0]),
          current_version: (.Image | split(":")[1] // "latest")
        }]
      ' || echo "[]")

      # Enrich with RepoDigests for each container
      if [ "$CONTAINER_INFO" != "[]" ]; then
        TEMP_INFO="[]"
        for container in $(echo "$CONTAINER_INFO" | ${pkgs.jq}/bin/jq -c '.[]'); do
          img_name=$(echo "$container" | ${pkgs.jq}/bin/jq -r '.image_name')
          repo_digest=$(${pkgs.podman}/bin/podman image inspect "$img_name" 2>/dev/null | \
            ${pkgs.jq}/bin/jq -r '.[0].RepoDigests[0] // ""' 2>/dev/null | cut -d'@' -f2 || echo "")
          TEMP_INFO=$(echo "$TEMP_INFO" | ${pkgs.jq}/bin/jq --argjson container "$container" --arg digest "$repo_digest" \
            '. += [$container + {repo_digest: $digest}]')
        done
        CONTAINER_INFO="$TEMP_INFO"
      fi
    elif command -v ${pkgs.docker}/bin/docker &> /dev/null; then
      CONTAINER_INFO=$(${pkgs.docker}/bin/docker ps --format "{{.Names}}\t{{.Image}}" 2>/dev/null | \
        while IFS=$'\t' read -r name image; do
          if ! echo "$image" | ${pkgs.gnugrep}/bin/grep -qiE "(postgres|mysql|mariadb|mongodb|redis|cassandra|elasticsearch)"; then
            # Extract tag
            tag=$(echo "$image" | cut -d':' -f2)
            if [ -z "$tag" ] || [ "$tag" = "$image" ]; then
              tag="latest"
            fi

            # Get RepoDigest from image inspection
            repo_digest=$(${pkgs.docker}/bin/docker image inspect "$image" 2>/dev/null | \
              ${pkgs.jq}/bin/jq -r '.[0].RepoDigests[0] // ""' 2>/dev/null | cut -d'@' -f2 || echo "")

            ${pkgs.jq}/bin/jq -n \
              --arg name "$name" \
              --arg image "$image" \
              --arg repo_digest "$repo_digest" \
              --arg version "$tag" \
              '{name: $name, image: $image, repo_digest: $repo_digest, current_version: $version}'
          fi
        done | ${pkgs.jq}/bin/jq -s -c '.' || echo "[]")
    fi

    # Create JSON payload with host information
    PAYLOAD=$(${pkgs.jq}/bin/jq -n \
      --arg hostname "$HOSTNAME" \
      --arg timestamp "$TIMESTAMP" \
      --arg version "$NIXOS_VERSION" \
      --arg rebuild "$LAST_REBUILD" \
      --argjson containers "$CONTAINER_INFO" \
      '{
        hostname: $hostname,
        timestamp: $timestamp,
        nixos_version: $version,
        last_rebuild: $rebuild,
        containers: $containers
      }')

    # Write locally
    mkdir -p ${checkinDir}
    echo "$PAYLOAD" > ${checkinDir}/$HOSTNAME.json

    echo "Check-in successful for $HOSTNAME at $TIMESTAMP (local)"
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
      default = "daily";
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
        ExecStart = if cfg.isCentralHost then "${checkinScriptLocal}" else "${checkinScriptRemote}";
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

This file tracks the current state of all NixOS hosts in the infrastructure, including their NixOS version, last verification date, last rebuild date, and OCI container status. This helps identify which hosts need updates or maintenance.

Last updated: TIMESTAMP_PLACEHOLDER

## Host Status Table

| Host Name | NixOS Version | Last Verified | Last Rebuild | Containers | Notes |
|-----------|---------------|---------------|--------------|------------|-------|
EOF

          # Replace timestamp placeholder
          ${pkgs.gnused}/bin/sed -i "s/TIMESTAMP_PLACEHOLDER/$TIMESTAMP/" "$OUTPUT_FILE"

          # Function to check if container has updates available
          check_container_update() {
            local image="$1"
            local current_repo_digest="$2"
            local current_tag="$3"

            # Skip check for latest tag or if no tag specified
            if [ "$current_tag" = "latest" ] || [ -z "$current_tag" ]; then
              echo "?"
              return
            fi

            # Extract repository
            local repo=$(echo "$image" | cut -d':' -f1)

            # Add docker.io prefix if no registry specified
            if [[ ! "$repo" =~ \. ]] && [[ ! "$repo" =~ localhost ]] && [[ ! "$repo" =~ / ]]; then
              repo="library/$repo"
            fi
            if [[ ! "$repo" =~ ^[a-z0-9.-]+\/ ]]; then
              repo="docker.io/$repo"
            fi

            # Get all tags and find the latest stable version
            local latest_version=""
            if command -v ${pkgs.skopeo}/bin/skopeo &> /dev/null; then
              # List all tags, filter for semantic versions, exclude pre-releases
              latest_version=$(${pkgs.skopeo}/bin/skopeo list-tags "docker://$repo" 2>/dev/null | \
                ${pkgs.jq}/bin/jq -r '.Tags[]' 2>/dev/null | \
                ${pkgs.gnugrep}/bin/grep -E '^v?[0-9]+\.[0-9]+(\.[0-9]+)?$' 2>/dev/null | \
                ${pkgs.gnugrep}/bin/grep -v -E '(-rc|-RC|-beta|-alpha|-dev|-pre)' 2>/dev/null | \
                ${pkgs.gnused}/bin/sed 's/^v//' 2>/dev/null | \
                ${pkgs.coreutils}/bin/sort -V 2>/dev/null | \
                ${pkgs.coreutils}/bin/tail -n1 2>/dev/null || echo "")
            fi

            # Normalize current version (remove 'v' prefix if present)
            local current_version=$(echo "$current_tag" | ${pkgs.gnused}/bin/sed 's/^v//')

            # Debug logging
            echo "DEBUG: image=$image, repo=$repo" >> /tmp/container-update-debug.log
            echo "DEBUG: current_tag=$current_tag, current_version=$current_version" >> /tmp/container-update-debug.log
            echo "DEBUG: latest_version=$latest_version" >> /tmp/container-update-debug.log

            # Compare versions
            if [ -z "$latest_version" ]; then
              echo "DEBUG: No latest version found" >> /tmp/container-update-debug.log
              echo "?"
            elif [ "$current_version" = "$latest_version" ]; then
              echo "DEBUG: Versions match - up to date" >> /tmp/container-update-debug.log
              echo "✓"
            else
              # Use sort -V to compare versions properly
              local newer=$(printf "%s\n%s" "$current_version" "$latest_version" | ${pkgs.coreutils}/bin/sort -V | ${pkgs.coreutils}/bin/tail -n1)
              echo "DEBUG: newer=$newer (comparing $current_version vs $latest_version)" >> /tmp/container-update-debug.log
              if [ "$newer" = "$latest_version" ]; then
                echo "DEBUG: Update available" >> /tmp/container-update-debug.log
                echo "⚠️"
              else
                echo "DEBUG: Current version is newer or same" >> /tmp/container-update-debug.log
                echo "✓"
              fi
            fi
          }

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

              # Process container information
              CONTAINERS=$(${pkgs.jq}/bin/jq -r '.containers' "$checkin_file" 2>/dev/null || echo "[]")
              CONTAINER_COUNT=$(echo "$CONTAINERS" | ${pkgs.jq}/bin/jq 'length' 2>/dev/null || echo "0")

              CONTAINER_INFO=""
              if [ "$CONTAINER_COUNT" -gt 0 ]; then
                # Build container info string
                CONTAINER_LIST=""
                for i in $(seq 0 $((CONTAINER_COUNT - 1))); do
                  CONTAINER_NAME=$(echo "$CONTAINERS" | ${pkgs.jq}/bin/jq -r ".[$i].name" 2>/dev/null)
                  CONTAINER_IMAGE=$(echo "$CONTAINERS" | ${pkgs.jq}/bin/jq -r ".[$i].image" 2>/dev/null)
                  CONTAINER_REPO_DIGEST=$(echo "$CONTAINERS" | ${pkgs.jq}/bin/jq -r ".[$i].repo_digest" 2>/dev/null)
                  CURRENT_VERSION=$(echo "$CONTAINERS" | ${pkgs.jq}/bin/jq -r ".[$i].current_version" 2>/dev/null)

                  # Check for updates
                  UPDATE_STATUS=$(check_container_update "$CONTAINER_IMAGE" "$CONTAINER_REPO_DIGEST" "$CURRENT_VERSION")

                  if [ -n "$CONTAINER_LIST" ]; then
                    CONTAINER_LIST="$CONTAINER_LIST<br>"
                  fi
                  CONTAINER_LIST="$CONTAINER_LIST**$CONTAINER_NAME**: $CURRENT_VERSION $UPDATE_STATUS"
                done
                CONTAINER_INFO="$CONTAINER_LIST"
              else
                CONTAINER_INFO="None"
              fi

              echo "| $HOSTNAME | $VERSION_SHORT | $VERIFIED | $REBUILD | $CONTAINER_INFO | $NOTES |" >> "$OUTPUT_FILE"
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
