# Obsidian Headless sync daemon using the official obsidian-headless npm CLI
# First-time setup per vault: ob-sync-setup <vaultName> <localPath>
# Then: systemctl start obsidian-headless-<vaultName>
# obsidian-env must contain: OBSIDIAN_EMAIL, OBSIDIAN_PASSWORD, OBSIDIAN_SYNC_PASSWORD (E2E key)

{ config, lib, pkgs, ... }:
with lib; let
  cfg = config.extra-services.obsidian-headless;
  ob = pkgs.callPackage ../packages/obsidian-headless.nix {};

  # When running as an existing user (e.g. hermes), we do NOT create a
  # system user. Detection is by name only — host config decides.
  isInternalUser = cfg.user == "obsidian-headless";

  setupScript = pkgs.writeShellScriptBin "ob-sync-setup" ''
    set -euo pipefail
    VAULT_NAME="''${1:?Usage: ob-sync-setup <vaultName> <localPath>}"
    VAULT_PATH="''${2:?Usage: ob-sync-setup <vaultName> <localPath>}"
    set -a; source /run/secrets/obsidian-env; set +a
    sudo -u ${cfg.user} env HOME=${cfg.dataDir} ${ob}/bin/ob sync-setup \
      --path "$VAULT_PATH" --vault "$VAULT_NAME" --password "$OBSIDIAN_SYNC_PASSWORD"
    echo "Done. Run: systemctl start obsidian-headless-''${VAULT_NAME}"
  '';

  mkVaultService = name: vault: {
    description = "Obsidian Headless sync daemon (${name})";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    startLimitIntervalSec = 600;
    startLimitBurst = 5;
    serviceConfig = {
      Type = "simple";
      User = cfg.user;
      Group = cfg.group;
      WorkingDirectory = cfg.dataDir;
      # Keep auth and sync state in the configured data directory. Without
      # this, systemd's login environment uses /var/lib/<user> as HOME and
      # the service cannot see setup performed with HOME=cfg.dataDir.
      Environment = "HOME=${cfg.dataDir}";
      EnvironmentFile = config.sops.secrets.obsidian-env.path;
      # Exit 0 (not a failure) if vault hasn't been configured via ob-sync-setup yet.
      ExecStart = "${pkgs.bash}/bin/bash -c '${ob}/bin/ob sync-status --path ${vault.path} &>/dev/null || { echo \"Vault not configured -- run ob-sync-setup ${name} ${vault.path}\"; exit 0; }; exec ${ob}/bin/ob sync --path ${vault.path} --continuous'";
      UMask = "0002";
      Restart = "on-failure";
      RestartSec = "60s";
    };
  };
in {
  options.extra-services.obsidian-headless = {
    enable = mkEnableOption "obsidian-headless sync daemon";

    user = mkOption {
      type = types.str;
      default = "obsidian-headless";
      description = ''
        Unix user the sync daemon runs as. When set to a pre-existing
        user (e.g. "hermes"), the module skips user/group creation
        and the running identity matches the writer of vault files,
        eliminating the cross-user permission contract. When set to
        "obsidian-headless" (the default), the module creates the
        system user on activation.
      '';
    };

    group = mkOption {
      type = types.str;
      default = "obsidian-headless";
      description = ''
        Unix group the sync daemon runs as. Default "obsidian-headless"
        pairs with the internal default user. Configure to "users" or
        another existing group when running as a pre-existing user.
      '';
    };

    vaults = mkOption {
      type = types.attrsOf (types.submodule {
        options.path = mkOption {
          type = types.str;
          description = "Local path where this vault will be synced";
        };
      });
      default = {};
      description = "Obsidian vaults to sync, keyed by vault name";
      example = literalExpression ''
        {
          MontyVault.path = "/var/lib/hermes/vault/MontyVault";
        }
      '';
    };

    dataDir = mkOption {
      type = types.str;
      default = if isInternalUser then "/var/lib/obsidian-headless"
               else "/var/lib/${cfg.user}/.obsidian-headless";
      description = ''
        Home/state directory for the sync daemon (stores auth session).
        Falls under the configured user so the same identity owns
        credentials and writes.
      '';
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ ob setupScript ];

    # Only create the dedicated system user when running as the
    # internal identity. For other configurations (e.g. user = "hermes")
    # we rely on the existing account and skip creation entirely.
    users.users = mkIf isInternalUser {
      obsidian-headless = {
        isSystemUser = true;
        group = "obsidian-headless";
        extraGroups = [ "users" ];
        home = cfg.dataDir;
      };
      groups.obsidian-headless = {};
    };

    systemd.tmpfiles.rules = [
      "d  ${cfg.dataDir}  0750 ${cfg.user} ${cfg.group} -"
      "e  ${cfg.dataDir}  0750 ${cfg.user} ${cfg.group} -"
    ] ++ lib.concatLists (lib.mapAttrsToList (_name: vault: [
      # Directory + existing files: setgid + group-rwx for the configured
      # user/group. When running as hermes this is hermes:users.
      "d  ${vault.path} 2770 ${cfg.user} ${cfg.group} -"
      "e  ${vault.path} 2770 ${cfg.user} ${cfg.group} -"
      # Default ACL: every new file/dir inside inherits group=cfg.group
      # with read(+X for dirs) regardless of creator's umask. This is the
      # belt-and-suspenders fix for the case where a different process
      # (e.g. git, editor, MCP subprocess) writes into the vault with a
      # restrictive umask and leaves an unreadable file behind.
      #
      # NOTE: systemd-tmpfiles refuses to apply this rule when the parent
      # path has an "unsafe" ownership transition (e.g. /var/lib/hermes
      # owned by hermes -> /var/lib/hermes/vault owned by obsidian-headless).
      # The activationScript below applies the same ACL via setfacl and
      # works regardless of path ownership.
      "a+ ${vault.path} - - - - d:u::rwx,d:g::rwx,d:o::-"
    ]) cfg.vaults);

    # Backup path: apply default ACLs via activation script. systemd-tmpfiles
    # skips rules on unsafe path transitions; activation scripts run as root
    # and call setfacl directly, so they work in all cases.
    # -R applies recursively so existing subdirs (created before the parent
    # got its default ACL) also inherit group-read for new files inside them.
    system.activationScripts.obsidian-headless-vault-acls = {
      deps = [ "users" "groups" ];
      text = lib.concatStringsSep "\n" (lib.mapAttrsToList (_name: vault: ''
        if [ -d "${vault.path}" ]; then
          ${pkgs.acl}/bin/setfacl -R -d -m u::rwx,g::rwx,o::- "${vault.path}" || true
        fi
      '') cfg.vaults);
    };

    sops.secrets.obsidian-env = {
      owner = cfg.user;
      mode = "0400";
    };

    systemd.services = lib.mapAttrs' (name: vault:
      lib.nameValuePair "obsidian-headless-${name}" (mkVaultService name vault)
    ) cfg.vaults;

    networking.firewall.allowedTCPPorts = [
      8080  # TaskNotes MCP server (Obsidian plugin)
    ];
  };
}

