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
    macvendor_url=${lib.escapeShellArg ftl.macvendorURL}
    any_failed=0

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

    ensureList() {
      local payload="$1" type address desired_enabled desired_comment list_data existing result error id post_response post_status
      type=$($jq -r '.type' <<< "$payload")
      address=$($jq -r '.address' <<< "$payload")
      desired_enabled=$($jq -r '.enabled' <<< "$payload")
      desired_comment=$($jq -r '.comment' <<< "$payload")
      if list_data=$(GetFTLData "lists?type=$type"); then
        :
      else
        echo "Unable to read Pi-hole lists for type $type"
        any_failed=1
        return 0
      fi
      if ! $jq -e 'type == "object" and (.error == null) and (.lists | type == "array")' >/dev/null <<< "$list_data"; then
        echo "Unable to read Pi-hole lists for type $type"
        any_failed=1
        return 0
      fi

      if existing=$($jq -r --arg address "$address" \
        --arg enabled "$desired_enabled" --arg comment "$desired_comment" \
        '.lists[]? | select(.address == $address and (.enabled|tostring) == $enabled and (.comment // "") == $comment) | .id' <<< "$list_data"); then
        if [ -n "$existing" ]; then
          echo "List already present for type $type (ID $existing)"
          return 0
        fi
      fi

      if existing=$($jq -r --arg address "$address" '.lists[]? | select(.address == $address) | .id' <<< "$list_data"); then
        if [ -n "$existing" ]; then
          echo "List policy drift detected for type $type (ID $existing)"
          any_failed=1
          return 0
        fi
      fi

      echo "Adding list of type $type"
      if post_response=$(PostFTLData "lists?type=$type" "$payload" status); then
        :
      else
        echo "Pi-hole API request failed while adding a list of type $type"
        any_failed=1
        return 0
      fi
      post_status=''${post_response: -3}
      result=''${post_response%???}
      case "$post_status" in
        2??) ;;
        *)
          echo "Pi-hole API returned HTTP $post_status while adding a list of type $type"
          any_failed=1
          return 0
          ;;
      esac
      if ! $jq -e 'type == "object"' >/dev/null <<< "$result"; then
        echo "Pi-hole API returned an invalid response for type $type"
        any_failed=1
        return 0
      fi
      error=$($jq '.error' <<< "$result")
      if [[ "$error" != "null" ]]; then
        echo "Pi-hole API rejected a list of type $type"
        any_failed=1
        return 0
      fi

      id=$($jq -r '.lists[]?.id // .list.id // empty' <<< "$result")
      if [ -z "$id" ]; then
        echo "Pi-hole API returned no list ID for type $type"
        any_failed=1
        return 0
      fi
      echo "Added list ID $id"
    }

    ${lib.concatMapStringsSep "\n" (payload: "ensureList ${lib.escapeShellArg payload}") listPayloads}
    $pihole -g
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
    ];

    services.pihole-ftl = {
      enable = true;
      privacyLevel = cfg.privacyLevel;
      openFirewallDNS = cfg.openFirewallDNS;
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

    systemd.services.pihole-ftl-setup.script = lib.mkForce setupScript;

    services.pihole-web = {
      enable = true;
      hostName = cfg.webHostName;
      ports = [ "${cfg.webListenAddress}:${toString cfg.webPort}" ];
    };
  };
}
