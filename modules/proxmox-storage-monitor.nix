/*
  Proxmox Storage Monitor Module

  This module monitors storage usage on Proxmox VMs and LXC containers
  and sends Gotify notifications when storage exceeds a threshold.

  Features:
  - Monitor multiple Proxmox hosts
  - Check VM and LXC storage usage
  - Configurable storage threshold (default 80%)
  - Send notifications via Gotify
  - Periodic monitoring via systemd timer
  - Supports SOPS for secure secret management

  Requirements:
  - API Token with VM.Audit and VM.Monitor permissions
  - For VMs: QEMU Guest Agent must be installed and enabled
  - LXC containers work without additional setup

  Usage:
    extra-services.proxmox-storage-monitor = {
      enable = true;
      proxmoxHosts = [
        {
          name = "pve1";
          host = "pve1.example.com";
          user = "root@pam";
          tokenId = "monitoring";
          tokenSecretFile = "/run/secrets/proxmox-token";  # or use tokenSecret directly
        }
      ];
      gotify = {
        url = "https://gotify.example.com";
        tokenFile = "/run/secrets/gotify-token";  # or use token directly
      };
      storageThreshold = 80;
      checkInterval = "hourly";
    };
*/

{ config, lib, pkgs, ... }:

with lib;
let
  cfg = config.extra-services.proxmox-storage-monitor;

  proxmoxHostType = types.submodule {
    options = {
      name = mkOption {
        type = types.str;
        description = "Friendly name for this Proxmox host";
        example = "pve1";
      };

      host = mkOption {
        type = types.str;
        description = "Hostname or IP address of the Proxmox host";
        example = "pve1.example.com";
      };

      user = mkOption {
        type = types.str;
        default = "root@pam";
        description = "Proxmox user for API access";
      };

      tokenId = mkOption {
        type = types.str;
        description = "Proxmox API token ID";
        example = "monitoring";
      };

      tokenSecret = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Proxmox API token secret (use tokenSecretFile for secrets management)";
      };

      tokenSecretFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to file containing Proxmox API token secret (for use with SOPS)";
        example = "/run/secrets/proxmox-token";
      };

      port = mkOption {
        type = types.int;
        default = 8006;
        description = "Proxmox API port";
      };

      verifySsl = mkOption {
        type = types.bool;
        default = false;
        description = "Whether to verify SSL certificates";
      };
    };
  };

  # Script that monitors Proxmox storage and sends notifications
  monitorScript = pkgs.writeShellScript "proxmox-storage-monitor" ''
    set -euo pipefail

    THRESHOLD=${toString cfg.storageThreshold}
    GOTIFY_URL="${cfg.gotify.url}"

    # Read Gotify token from environment (set by systemd EnvironmentFile)
    GOTIFY_TOKEN="''${GOTIFY_TOKEN:-}"

    # Function to send Gotify notification
    send_notification() {
      local title="$1"
      local message="$2"
      local priority="''${3:-5}"

      if [ -z "$GOTIFY_TOKEN" ]; then
        echo "ERROR: GOTIFY_TOKEN not set" >&2
        return 1
      fi

      # Use printf to properly interpret escape sequences
      local formatted_message=$(printf "%b" "$message")

      ${pkgs.curl}/bin/curl -X POST "$GOTIFY_URL/message" \
        -H "X-Gotify-Key: $GOTIFY_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$(${pkgs.jq}/bin/jq -n \
          --arg title "$title" \
          --arg message "$formatted_message" \
          --argjson priority "$priority" \
          '{
            title: $title,
            message: $message,
            priority: $priority,
            extras: {
              "client::display": {
                contentType: "text/markdown"
              }
            }
          }')" \
        --silent --show-error --fail || echo "Failed to send notification"
    }

    # Function to check storage on a Proxmox host
    check_proxmox_host() {
      local pve_name="$1"
      local pve_host="$2"
      local pve_user="$3"
      local pve_token_id="$4"
      local pve_token_secret_var="$5"
      local pve_port="$6"
      local verify_ssl="$7"

      # Read token secret from environment variable
      local pve_token_secret="''${!pve_token_secret_var:-}"

      if [ -z "$pve_token_secret" ]; then
        echo "ERROR: Token secret for $pve_name not found in environment variable $pve_token_secret_var" >&2
        return 1
      fi

      local api_base="https://$pve_host:$pve_port/api2/json"
      local auth_header="PVEAPIToken=$pve_user!$pve_token_id=$pve_token_secret"
      local curl_opts="--silent --show-error"

      if [ "$verify_ssl" = "false" ]; then
        curl_opts="$curl_opts --insecure"
      fi

      echo "Checking Proxmox host: $pve_name ($pve_host)"

      # Get list of nodes
      local nodes=$(${pkgs.curl}/bin/curl $curl_opts \
        -H "Authorization: $auth_header" \
        "$api_base/nodes" | ${pkgs.jq}/bin/jq -r '.data[].node' 2>/dev/null || echo "")

      if [ -z "$nodes" ]; then
        echo "Failed to get nodes from $pve_name, skipping..."
        return
      fi

      # Check each node
      for node in $nodes; do
        echo "  Checking node: $node"

        # Check VMs (qemu)
        local vms=$(${pkgs.curl}/bin/curl $curl_opts \
          -H "Authorization: $auth_header" \
          "$api_base/nodes/$node/qemu" | ${pkgs.jq}/bin/jq -c '.data[]' 2>/dev/null || echo "")

        while IFS= read -r vm; do
          [ -z "$vm" ] && continue

          local vmid=$(echo "$vm" | ${pkgs.jq}/bin/jq -r '.vmid')
          local name=$(echo "$vm" | ${pkgs.jq}/bin/jq -r '.name')
          local status=$(echo "$vm" | ${pkgs.jq}/bin/jq -r '.status')

          # Only check running VMs
          if [ "$status" != "running" ]; then
            continue
          fi

          # Try to get disk info from QEMU guest agent
          local fs_info=$(${pkgs.curl}/bin/curl $curl_opts \
            -H "Authorization: $auth_header" \
            "$api_base/nodes/$node/qemu/$vmid/agent/get-fsinfo" 2>/dev/null | ${pkgs.jq}/bin/jq '.data' 2>/dev/null || echo "null")

          # Check if guest agent data is available
          if [ "$fs_info" != "null" ] && [ "$fs_info" != "" ]; then
            # Parse filesystems from guest agent
            local filesystems=$(echo "$fs_info" | ${pkgs.jq}/bin/jq -c '.result[]?' 2>/dev/null || echo "")

            while IFS= read -r filesystem; do
              [ -z "$filesystem" ] && continue

              local mount=$(echo "$filesystem" | ${pkgs.jq}/bin/jq -r '.mountpoint // .name')
              local total=$(echo "$filesystem" | ${pkgs.jq}/bin/jq -r '.total-bytes // 0')
              local used=$(echo "$filesystem" | ${pkgs.jq}/bin/jq -r '.used-bytes // 0')

              # Skip if no data or system/boot partitions
              if [ "$total" = "0" ] || [ "$total" = "null" ] || \
                 [[ "$mount" == "/boot"* ]] || [[ "$mount" == "/efi"* ]] || [[ "$mount" == "/sys"* ]]; then
                continue
              fi

              # Calculate usage percentage
              if [ "$total" != "0" ]; then
                local usage=$(echo "scale=2; ($used / $total) * 100" | ${pkgs.bc}/bin/bc)
                local usage_int=$(echo "$usage / 1" | ${pkgs.bc}/bin/bc)

                echo "    VM $vmid ($name) [$mount]: ''${usage}% used"

                if [ "$usage_int" -ge "$THRESHOLD" ]; then
                  local used_gb=$(echo "scale=2; $used / 1024 / 1024 / 1024" | ${pkgs.bc}/bin/bc)
                  local total_gb=$(echo "scale=2; $total / 1024 / 1024 / 1024" | ${pkgs.bc}/bin/bc)

                  send_notification \
                    "Proxmox Storage Alert: VM $name" \
                    "**Host:** $pve_name  \\n**Node:** $node  \\n**VM:** $name (ID: $vmid)  \\n**Mount:** $mount  \\n**Storage Usage:** ''${usage}%  \\n**Used:** ''${used_gb}GB / ''${total_gb}GB\\n\\n⚠️ Threshold exceeded: ''${THRESHOLD}%" \
                    8
                fi
              fi
            done <<< "$filesystems"
          else
            # Guest agent not available, skip with note
            echo "    VM $vmid ($name): skipped (guest agent not available or no VM.Monitor permission)"
          fi
        done <<< "$vms"

        # Check LXC containers
        local containers=$(${pkgs.curl}/bin/curl $curl_opts \
          -H "Authorization: $auth_header" \
          "$api_base/nodes/$node/lxc" | ${pkgs.jq}/bin/jq -c '.data[]' 2>/dev/null || echo "")

        while IFS= read -r container; do
          [ -z "$container" ] && continue

          local vmid=$(echo "$container" | ${pkgs.jq}/bin/jq -r '.vmid')
          local name=$(echo "$container" | ${pkgs.jq}/bin/jq -r '.name')
          local status=$(echo "$container" | ${pkgs.jq}/bin/jq -r '.status')

          # Only check running containers
          if [ "$status" != "running" ]; then
            continue
          fi

          # Get RRD data for disk usage
          local rrd_data=$(${pkgs.curl}/bin/curl $curl_opts \
            -H "Authorization: $auth_header" \
            "$api_base/nodes/$node/lxc/$vmid/rrddata?timeframe=hour" | ${pkgs.jq}/bin/jq '.data[-1]' 2>/dev/null || echo "{}")

          # Check disk usage
          local maxdisk=$(echo "$rrd_data" | ${pkgs.jq}/bin/jq -r '.maxdisk // 0')
          local disk=$(echo "$rrd_data" | ${pkgs.jq}/bin/jq -r '.disk // 0')

          if [ "$maxdisk" != "0" ] && [ "$maxdisk" != "null" ]; then
            local usage=$(echo "scale=2; ($disk / $maxdisk) * 100" | ${pkgs.bc}/bin/bc)
            local usage_int=$(echo "$usage / 1" | ${pkgs.bc}/bin/bc)

            echo "    LXC $vmid ($name): ''${usage}% used"

            if [ "$usage_int" -ge "$THRESHOLD" ]; then
              local disk_gb=$(echo "scale=2; $disk / 1024 / 1024 / 1024" | ${pkgs.bc}/bin/bc)
              local maxdisk_gb=$(echo "scale=2; $maxdisk / 1024 / 1024 / 1024" | ${pkgs.bc}/bin/bc)

              send_notification \
                "Proxmox Storage Alert: LXC $name" \
                "**Host:** $pve_name  \\n**Node:** $node  \\n**LXC:** $name (ID: $vmid)  \\n**Storage Usage:** ''${usage}%  \\n**Used:** ''${disk_gb}GB / ''${maxdisk_gb}GB\\n\\n⚠️ Threshold exceeded: ''${THRESHOLD}%" \
                8
            fi
          fi
        done <<< "$containers"
      done
    }

    # Monitor all configured Proxmox hosts
    ${concatStringsSep "\n" (map (host:
      let
        # Create safe environment variable name from host name
        envVarName = "PVE_TOKEN_" + (lib.replaceStrings ["-" "." " "] ["_" "_" "_"] (lib.toUpper host.name));
      in ''
      check_proxmox_host \
        "${host.name}" \
        "${host.host}" \
        "${host.user}" \
        "${host.tokenId}" \
        "${envVarName}" \
        "${toString host.port}" \
        "${if host.verifySsl then "true" else "false"}"
    '') cfg.proxmoxHosts)}

    echo "Proxmox storage monitoring check completed"
  '';

  # Script to generate environment file from secrets
  envFileGenerator = pkgs.writeShellScript "generate-proxmox-monitor-env" ''
    set -euo pipefail

    ENV_FILE="/run/proxmox-storage-monitor/env"
    mkdir -p "$(dirname "$ENV_FILE")"

    # Start with empty file
    : > "$ENV_FILE"

    # Add Gotify token
    ${if cfg.gotify.tokenFile != null then ''
      if [ -f "${cfg.gotify.tokenFile}" ]; then
        echo "GOTIFY_TOKEN=$(cat ${cfg.gotify.tokenFile})" >> "$ENV_FILE"
      else
        echo "ERROR: Gotify token file not found: ${cfg.gotify.tokenFile}" >&2
        exit 1
      fi
    '' else if cfg.gotify.token != null then ''
      echo "GOTIFY_TOKEN=${cfg.gotify.token}" >> "$ENV_FILE"
    '' else ''
      echo "ERROR: Either gotify.token or gotify.tokenFile must be set" >&2
      exit 1
    ''}

    # Add Proxmox host tokens
    ${concatStringsSep "\n" (map (host:
      let
        envVarName = "PVE_TOKEN_" + (lib.replaceStrings ["-" "." " "] ["_" "_" "_"] (lib.toUpper host.name));
      in
        if host.tokenSecretFile != null then ''
          if [ -f "${host.tokenSecretFile}" ]; then
            echo "${envVarName}=$(cat ${host.tokenSecretFile})" >> "$ENV_FILE"
          else
            echo "ERROR: Token file for ${host.name} not found: ${host.tokenSecretFile}" >&2
            exit 1
          fi
        '' else if host.tokenSecret != null then ''
          echo "${envVarName}=${host.tokenSecret}" >> "$ENV_FILE"
        '' else ''
          echo "ERROR: Either tokenSecret or tokenSecretFile must be set for host ${host.name}" >&2
          exit 1
        ''
    ) cfg.proxmoxHosts)}

    chmod 600 "$ENV_FILE"
    echo "Environment file generated at $ENV_FILE"
  '';

in {
  options.extra-services.proxmox-storage-monitor = {
    enable = mkEnableOption "Proxmox storage monitoring service";

    proxmoxHosts = mkOption {
      type = types.listOf proxmoxHostType;
      default = [];
      description = ''
        List of Proxmox hosts to monitor.
        Each host requires API token authentication.
      '';
      example = literalExpression ''
        [
          {
            name = "pve1";
            host = "pve1.example.com";
            user = "root@pam";
            tokenId = "monitoring";
            tokenSecret = "your-token-secret";
          }
        ]
      '';
    };

    gotify = {
      url = mkOption {
        type = types.str;
        description = "Gotify server URL (without trailing slash)";
        example = "https://gotify.example.com";
      };

      token = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Gotify application token (use tokenFile for secrets management)";
      };

      tokenFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to file containing Gotify application token (for use with SOPS)";
        example = "/run/secrets/gotify-token";
      };
    };

    storageThreshold = mkOption {
      type = types.int;
      default = 80;
      description = "Storage usage threshold percentage (0-100)";
    };

    checkInterval = mkOption {
      type = types.str;
      default = "hourly";
      example = "hourly";
      description = ''
        How often to check Proxmox storage. Can be any systemd timer specification.
        Common values: "hourly", "daily", "*:0/30" (every 30 minutes)
      '';
    };
  };

  config = mkIf cfg.enable {
    # Ensure required packages are available
    environment.systemPackages = with pkgs; [
      curl
      jq
      bc
    ];

    # Runtime directory for environment file
    systemd.tmpfiles.rules = [
      "d /run/proxmox-storage-monitor 0700 root root -"
    ];

    # Storage monitoring service
    systemd.services.proxmox-storage-monitor = {
      description = "Monitor Proxmox VM/LXC storage usage";
      serviceConfig = {
        Type = "oneshot";
        ExecStartPre = "${envFileGenerator}";
        ExecStart = "${monitorScript}";
        EnvironmentFile = "-/run/proxmox-storage-monitor/env";
        TimeoutStartSec = "5min";
        Restart = "no";
      };
      # Allow some failures (e.g., if Proxmox host is temporarily unreachable)
      startLimitBurst = 3;
      startLimitIntervalSec = 300;
    };

    # Timer for periodic checks
    systemd.timers.proxmox-storage-monitor = {
      description = "Timer for Proxmox storage monitoring";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.checkInterval;
        Persistent = true;
        RandomizedDelaySec = "2min";
      };
    };
  };
}
