{ config, lib, pkgs, ... }:

let
  cfg = config.services.pihole-native;
  ftl = config.services.pihole-ftl;
  listPayloads = map (
    list:
    builtins.toJSON {
      type = list.type;
      enabled = list.enabled;
      address = list.url;
      comment = list.description;
    }
  ) ftl.lists;
  listManifest = builtins.toJSON (map (
    list: {
      type = list.type;
      enabled = list.enabled;
      address = list.url;
      comment = list.description;
    }
  ) ftl.lists);
  setupScript = ''
    # This intentionally mirrors the pinned NixOS 26.05 native setup behavior
    # while making list registration idempotent. Re-review this override when
    # the nixpkgs Pi-hole module or package input changes.
    set +u
    set -eo pipefail
    pihole="${lib.getExe ftl.piholePackage}"
    jq="${lib.getExe pkgs.jq}"
    curl="${lib.getExe pkgs.curl}"
    mktemp="${lib.getExe' pkgs.coreutils "mktemp"}"
    mv="${lib.getExe' pkgs.coreutils "mv"}"
    rm="${lib.getExe' pkgs.coreutils "rm"}"
    install="${lib.getExe' pkgs.coreutils "install"}"
    macvendor_url=${lib.escapeShellArg ftl.macvendorURL}
    desired_lists=${lib.escapeShellArg listManifest}
    pending_marker="${ftl.stateDirectory}/.pihole-ftl-lists-pending"
    lists_changed=false
    any_failed=0

    # Validate local file-backed lists before any destructive API operation.
    while IFS= read -r file_url; do
      [ -z "$file_url" ] && continue
      file_path="''${file_url#file://}"
      if [ ! -f "$file_path" ] || [ ! -r "$file_path" ] || [ ! -s "$file_path" ]; then
        echo "A declared local Pi-hole list is missing, unreadable, or empty"
        exit 1
      fi
    done < <($jq -r '.[] | select(.address | startswith("file://")) | .address' <<< "$desired_lists")

    macvendor_tmp=$($mktemp "${ftl.stateDirectory}/macvendor.db.XXXXXX")
    trap '$rm -f "$macvendor_tmp"' EXIT
    if $curl --fail --retry 3 --retry-delay 5 "$macvendor_url" \
      -o "$macvendor_tmp"; then
      $mv -f "$macvendor_tmp" "${ftl.stateDirectory}/macvendor.db"
    else
      echo "Failed to download the Pi-hole MAC database"
      any_failed=1
      if [ ! -s "${ftl.stateDirectory}/macvendor.db" ]; then
        echo "No existing Pi-hole MAC database is available"
      fi
    fi

    if [ ! -f "${ftl.stateDirectory}/gravity.db" ]; then
      $pihole -g
      main_pid=$(systemctl show --property MainPID --value ${config.systemd.services.pihole-ftl.name})
      if [ -z "$main_pid" ]; then
        echo "Unable to determine the Pi-hole FTL process ID"
        exit 1
      fi
      case "$main_pid" in
        0|*[!0-9]*)
          echo "Unable to determine the Pi-hole FTL process ID"
          exit 1
          ;;
        *)
          ${lib.getExe' pkgs.procps "kill"} -s SIGRTMIN "$main_pid"
          ;;
      esac
    fi

    . ${ftl.piholePackage}/share/pihole/advanced/Scripts/api.sh
    . ${ftl.piholePackage}/share/pihole/advanced/Scripts/utils.sh

    api_ready=false
    for _ in 1 2 3; do
      if (TestAPIAvailability); then
        api_ready=true
        break
      fi
      echo "Retrying API shortly..."
      ${lib.getExe' pkgs.coreutils "sleep"} .5s
    done
    if [ "$api_ready" != true ]; then
      echo "Pi-hole API did not become available"
      exit 1
    fi

    LoginAPI

    reconcileLists() {
      local current_lists current_signature desired_signature delete_payload response status result error id
      if current_lists=$(GetFTLData "lists"); then
        :
      else
        echo "Unable to read current Pi-hole lists"
        exit 1
      fi
      if ! $jq -e '
        type == "object"
        and (.error == null)
        and (.lists | type == "array")
        and all(.lists[];
          (type == "object")
          and ((.type | type) == "string")
          and ((.address | type) == "string")
          and ((.enabled | type) == "boolean")
          and ((.comment == null) or ((.comment | type) == "string"))
        )
      ' >/dev/null <<< "$current_lists"; then
        echo "Unable to read current Pi-hole lists"
        exit 1
      fi

      current_signature=$($jq -c '
        [.lists[] | {type, address, enabled, comment: (if .comment == null then "" else .comment end)}]
        | sort_by(.type, .address)
      ' <<< "$current_lists")
      desired_signature=$($jq -c '
        [.[] | {type, address, enabled, comment: (if .comment == null then "" else .comment end)}]
        | sort_by(.type, .address)
      ' <<< "$desired_lists")
      if [ "$current_signature" = "$desired_signature" ] && [ ! -e "$pending_marker" ]; then
        echo "Pi-hole lists already match the declared configuration"
        return 0
      fi

      if ! $install -D -m 0600 /dev/null "$pending_marker"; then
        echo "Unable to create the Pi-hole list reconciliation marker"
        exit 1
      fi
      lists_changed=true
      delete_payload=$($jq -c '[.lists[] | {item: .address, type: .type}]' <<< "$current_lists")
      if [ "$delete_payload" != "[]" ]; then
        echo "Resetting Pi-hole lists to the declared configuration"
        response=$(PostFTLData "lists:batchDelete" "$delete_payload" status) || {
          echo "Pi-hole API request failed while resetting lists"
          exit 1
        }
        status=''${response: -3}
        case "$status" in
          204) ;;
          *)
            echo "Pi-hole API returned HTTP $status while resetting lists"
            exit 1
            ;;
        esac
      fi

      ensureList() {
        local payload="$1" response status result error id
        # Strip .type from the POST body — Pi-hole v6 only needs type in the query.
        local body
        body=$($jq -c 'del(.type)' <<< "$payload")
        if response=$(PostFTLData "lists?type=$($jq -r '.type' <<< "$payload")" "$body" status); then
          :
        else
          echo "Pi-hole API request failed while adding a declared list"
          exit 1
        fi
        status=''${response: -3}
        result=''${response%???}
        case "$status" in
          2??) ;;
          *)
            echo "Pi-hole API returned HTTP $status while adding a declared list"
            exit 1
            ;;
        esac
        if ! $jq -e 'type == "object" and (.error == null)' >/dev/null <<< "$result"; then
          echo "Pi-hole API rejected a declared list"
          exit 1
        fi
        id=$($jq -r '.lists[]?.id // .list.id // empty' <<< "$result")
        if [ -z "$id" ]; then
          echo "Pi-hole API returned no list ID for a declared list"
          exit 1
        fi
      }

      ${lib.concatMapStringsSep "\n" (payload: "ensureList ${lib.escapeShellArg payload}") listPayloads}
    }

    verifyLists() {
      local actual
      actual=$(GetFTLData "lists") || {
        echo "Unable to read Pi-hole lists for post-rebuild verification"
        exit 1
      }
      $jq -e --argjson desired "$desired_lists" '
        (type == "object" and (.error == null) and (.lists | type == "array"))
        and all(.lists[];
          (type == "object")
          and ((.type | type) == "string")
          and ((.address | type) == "string")
          and ((.enabled | type) == "boolean")
          and ((.comment == null) or ((.comment | type) == "string"))
        )
        and (
          ([.lists[] | {type, address, enabled, comment: (if .comment == null then "" else .comment end)}]
           | sort_by(.type, .address))
          == ($desired
              | map({type, address, enabled, comment: (if .comment == null then "" else .comment end)})
              | sort_by(.type, .address))
        )
      ' <<< "$actual" >/dev/null || {
        echo "Pi-hole list state did not converge to the declared configuration"
        exit 1
      }
    }

    reconcileLists
    if [ "$lists_changed" != true ]; then
      exit $any_failed
    fi
    verifyLists
    if ! $pihole -g; then
      echo "Pi-hole gravity run failed; leaving the reconciliation marker for retry"
      exit 1
    fi
    verifyLists
    $rm -f "$pending_marker"
    exit $any_failed
  '';
in
{
  options.services.pihole-native = {
    enable = lib.mkEnableOption "native Pi-hole FTL and Web services";

    interface = lib.mkOption {
      type = lib.types.str;
      default = "eth0";
      description = "Network interface on which Pi-hole accepts DNS queries.";
    };

    listeningMode = lib.mkOption {
      type = lib.types.enum [
        "LOCAL"
        "SINGLE"
        "BIND"
        "ALL"
        "NONE"
      ];
      default = "BIND";
      description = "Pi-hole FTL DNS listening mode.";
    };

    upstreams = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Upstream DNS servers used by Pi-hole FTL; required per host.";
    };

    webHostName = lib.mkOption {
      type = lib.types.str;
      default = "pi.hole";
      description = "Pi-hole Web virtual host name.";
    };

    webPort = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "TCP port for the Pi-hole Web interface and API.";
    };

    webListenAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address for the Pi-hole Web/API listener.";
    };

    apiPasswordEnvironmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Runtime EnvironmentFile containing FTLCONF_webserver_api_password.
        The file should be rendered by sops-nix and must never be committed
        in plaintext. Required when Web/API is bound beyond loopback. The
        corresponding sops template must restart pihole-ftl.service when it
        changes.
      '';
    };

    openFirewallDNS = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Open the host firewall for DNS.";
    };

    privacyLevel = lib.mkOption {
      type = lib.types.ints.between 0 3;
      default = 0;
      description = "Pi-hole FTL statistics privacy level.";
    };

    dnssec = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether Pi-hole FTL validates DNS replies with DNSSEC.";
    };

    queryLogging = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Whether Pi-hole FTL logs DNS queries and replies.";
    };

    blockingMode = lib.mkOption {
      type = lib.types.enum [ "NULL" "IP_NODATA_AAAA" "IP" "NX" "NODATA" ];
      default = "NULL";
      description = "Response mode for blocked DNS queries.";
    };

    resolver = {
      resolveIPv4 = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Whether FTL resolves IPv4 client addresses to hostnames.";
      };

      resolveIPv6 = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Whether FTL resolves IPv6 client addresses to hostnames.";
      };

      networkNames = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Whether FTL uses network-table names for client attribution.";
      };

      refreshNames = lib.mkOption {
        type = lib.types.enum [ "IPV4_ONLY" "ALL" "UNKNOWN" "NONE" ];
        default = "IPV4_ONLY";
        description = "How FTL refreshes client and upstream hostnames.";
      };
    };

    database = {
      maxDBdays = lib.mkOption {
        type = lib.types.ints.between 0 3650;
        default = 91;
        description = "Number of days FTL retains query history locally.";
      };

      DBinterval = lib.mkOption {
        type = lib.types.ints.between 0 86400;
        default = 60;
        description = "Seconds between FTL query-history database writes.";
      };

      useWAL = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Whether FTL uses SQLite write-ahead logging.";
      };

      networkExpire = lib.mkOption {
        type = lib.types.ints.between 1 3650;
        default = 91;
        description = "Number of days FTL retains network-table addresses locally.";
      };
    };

    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/pihole";
      description = "Persistent Pi-hole state directory.";
    };

    logDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/var/log/pihole";
      description = "Persistent Pi-hole log directory.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.upstreams != [ ];
        message = "services.pihole-native.upstreams must be set explicitly for each Pi-hole host.";
      }
      {
        assertion =
          cfg.privacyLevel == 0
          && cfg.dnssec == false
          && cfg.queryLogging == true
          && cfg.blockingMode == "NULL"
          && cfg.resolver.resolveIPv4 == true
          && cfg.resolver.resolveIPv6 == true
          && cfg.resolver.networkNames == true
          && cfg.resolver.refreshNames == "IPV4_ONLY"
          && cfg.database.maxDBdays == 91
          && cfg.database.DBinterval == 60
          && cfg.database.useWAL == true
          && cfg.database.networkExpire == 91;
        message = "Pi-hole shared FTL baseline settings are fixed and must be identical on both instances.";
      }
      {
        assertion =
          cfg.webListenAddress == "127.0.0.1"
          || (cfg.apiPasswordEnvironmentFile != null && cfg.apiPasswordEnvironmentFile != "");
        message = "services.pihole-native.apiPasswordEnvironmentFile is required when Web/API is bound beyond loopback.";
      }
      {
        assertion = lib.all (list: builtins.match ".*://[^/]*@.*" list.url == null) ftl.lists;
        message = "Pi-hole list URLs must not contain embedded credentials.";
      }
    ];

    services.pihole-ftl = {
      enable = true;
      privacyLevel = cfg.privacyLevel;
      openFirewallDNS = cfg.openFirewallDNS;
      # Do not use openFirewallWebserver — pihole-web.nix sets
      # settings.webserver.port to "host:port" which breaks the NixOS
      # firewall parser's toInt call. Instead, open the port directly.
      openFirewallWebserver = false;
      stateDirectory = cfg.stateDirectory;
      logDirectory = cfg.logDirectory;
      settings = {
        dns = {
          inherit (cfg) interface listeningMode upstreams dnssec queryLogging;
          replyWhenBusy = "ALLOW";
          bogusPriv = true;
          blocking = {
            active = true;
            mode = cfg.blockingMode;
            edns = "TEXT";
          };
          specialDomains = {
            mozillaCanary = true;
            iCloudPrivateRelay = true;
            designatedResolver = true;
          };
        };
        dhcp.active = false;
        ntp = {
          ipv4.active = false;
          ipv6.active = false;
          sync.active = false;
        };
        resolver = {
          inherit (cfg.resolver) resolveIPv4 resolveIPv6 networkNames refreshNames;
        };
        database = {
          DBimport = true;
          inherit (cfg.database) maxDBdays DBinterval useWAL;
          network = {
            parseARPcache = true;
            expire = cfg.database.networkExpire;
          };
        };
        webserver.api = {
          # Required by the native list setup service. Keep this ephemeral
          # credential separate from the eventual reconciler credential.
          cli_pw = true;
          app_sudo = false;
        };
      };
    };

    systemd.services.pihole-ftl.serviceConfig.EnvironmentFile =
      lib.optional
        (cfg.apiPasswordEnvironmentFile != null && cfg.apiPasswordEnvironmentFile != "")
        cfg.apiPasswordEnvironmentFile;

    # Keep the setup unit enabled even when the declared list is empty so a
    # stale runtime list is still removed by the next reconciliation.
    systemd.services.pihole-ftl-setup.enable = lib.mkForce cfg.enable;
    systemd.services.pihole-ftl-setup.script = lib.mkForce setupScript;

    # Open the web interface port when binding beyond loopback.
    # Cannot use pihole-ftl's openFirewallWebserver because pihole-web.nix
    # sets settings.webserver.port to "host:port" which breaks the NixOS
    # firewall parser's toInt call.
    networking.firewall.allowedTCPPorts =
      lib.optionals (cfg.webListenAddress != "127.0.0.1") [ cfg.webPort ];

    services.pihole-web = {
      enable = true;
      hostName = cfg.webHostName;
      ports = [ "${cfg.webListenAddress}:${toString cfg.webPort}" ];
    };
  };
}
