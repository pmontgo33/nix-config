{ config, lib, pkgs, inputs, ... }:
with lib; let
  cfg = config.extra-services.nookbridge;
  nookbridge = pkgs.callPackage ../packages/nookbridge.nix { inherit inputs; };
  serviceConfig = builtins.fromJSON (builtins.readFile ./nookbridge/service.json);
  credentialPath = config.sops.secrets."nookbridge-db-key".path;
in {
  options.extra-services.nookbridge = {
    enable = mkEnableOption "NookBridge read-only Unix-socket service";
  };

  config = mkIf cfg.enable {
    users.groups.nookbridge = {};
    users.groups.nookbridge-clients = {};
    users.users.nookbridge = {
      isSystemUser = true;
      group = "nookbridge";
      home = "/var/lib/nookbridge";
      createHome = false;
      shell = "${pkgs.shadow}/bin/nologin";
      extraGroups = [ "nookbridge-clients" ];
    };

    # The source secret is root-readable only. systemd reads it as the
    # manager, then presents a private copy through $CREDENTIALS_DIRECTORY;
    # the daemon never receives the sops-nix path or a credential in argv/env.
    sops.secrets."nookbridge-db-key" = {
      owner = "root";
      group = "root";
      mode = "0400";
      restartUnits = [ "nookd.service" ];
    };

    environment.etc."nookbridge/service.json" = {
      source = ./nookbridge/service.json;
      mode = "0644";
    };

    systemd.services.nookd = {
      description = "NookBridge read-only Unix-socket service";
      wantedBy = [ "multi-user.target" ];
      wants = [ "sops-install-secrets.service" ];
      after = [ "sops-install-secrets.service" ];
      restartTriggers = [ nookbridge (builtins.readFile ./nookbridge/service.json) ];
      serviceConfig = {
        Type = "simple";
        User = "nookbridge";
        Group = "nookbridge-clients";
        WorkingDirectory = "/var/lib/nookbridge";
        Environment = "HOME=/var/lib/nookbridge";
        # The source credential is intentionally root-only. systemd's
        # LoadCredential= reads it as the manager and presents the daemon's
        # private copy; checking credentialPath here would run as nookbridge
        # and reject the correctly protected 0400 source file.
        ExecStartPre = [
          "${pkgs.coreutils}/bin/test -r /etc/nookbridge/service.json"
        ];
        ExecStart = "${nookbridge}/bin/nookd --config /etc/nookbridge/service.json";
        LoadCredential = [ "nookbridge-db-key:${credentialPath}" ];
        StateDirectory = "nookbridge";
        StateDirectoryMode = "0750";
        RuntimeDirectory = "nookbridge";
        RuntimeDirectoryMode = "0750";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [ "AF_UNIX" ];
        RestrictNamespaces = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        LockPersonality = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        CapabilityBoundingSet = "";
        AmbientCapabilities = "";
        SystemCallArchitectures = "native";
        ReadWritePaths = [ "/var/lib/nookbridge" "/run/nookbridge" ];
        # Keep the socket private from other users while allowing the
        # nookbridge-clients group to connect.
        UMask = "0007";
        Restart = "on-failure";
        RestartSec = "5s";
        TimeoutStopSec = "15s";
      };
    };

    assertions = [
      {
        assertion = serviceConfig.stateDir == "/var/lib/nookbridge";
        message = "NookBridge stateDir is fixed to /var/lib/nookbridge";
      }
      {
        assertion = serviceConfig.socketPath == "/run/nookbridge/nookbridge.sock";
        message = "NookBridge socketPath is fixed to /run/nookbridge/nookbridge.sock";
      }
      {
        assertion = serviceConfig.socketGroup == "nookbridge-clients";
        message = "NookBridge socketGroup is fixed to nookbridge-clients";
      }
      {
        assertion = serviceConfig.backend == "systemd-credential";
        message = "NookBridge backend is fixed to systemd-credential";
      }
      {
        assertion = serviceConfig.credentialName == "nookbridge-db-key";
        message = "NookBridge credentialName is fixed to nookbridge-db-key";
      }
      {
        assertion = serviceConfig.readPolicy == [ "notes.search" "notes.status" "notes.list_notebooks" "notes.get" ];
        message = "NookBridge readPolicy is fixed to the four read-only MCP RPC methods";
      }
    ];
  };
}
