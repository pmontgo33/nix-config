{ config, pkgs, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  networking.hostName = "paperless-ngx";

  # Reduce build memory usage
  nix.settings = {
    max-jobs = 1;  # Limit parallel builds to reduce memory usage
    cores = 2;     # Limit cores per build
  };

  # Disable tests for memory-intensive packages to prevent OOM during build
  nixpkgs.overlays = [
    (final: prev: {
      # Disable tests for paperless-ngx itself
      paperless-ngx = prev.paperless-ngx.overrideAttrs (old: {
        doCheck = false;
        doInstallCheck = false;
        dontCheck = true;
      });

      # Disable tests for ocrmypdf dependency
      pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
        (pyfinal: pyprev: {
          ocrmypdf = pyprev.ocrmypdf.overridePythonAttrs (old: {
            doCheck = false;
            doInstallCheck = false;
            dontCheck = true;
            # Preserve the override method for paperless-ngx to use
            passthru = (old.passthru or {}) // {
              override = args: (pyprev.ocrmypdf.override args).overridePythonAttrs (_: {
                doCheck = false;
                doInstallCheck = false;
                dontCheck = true;
              });
            };
          });
        })
      ];
    })
  ];

  services.openssh.enable = true;

  # SOPS secrets configuration
  sops = {
    secrets = {
      "paperless-ai-env" = {};
    };
  };

  # Enable PostgreSQL for paperless database
  services.postgresql = {
    enable = true;
    ensureDatabases = [ "paperless" ];
    ensureUsers = [{
      name = "paperless";
      ensureDBOwnership = true;
    }];
  };

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;
  extra-services.mount_general.enable = true;

  # Enable Podman for running paperless-ai OCI container
  virtualisation.podman = {
    enable = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  # Paperless-ngx service
  services.paperless = {
    enable = true;
    address = "0.0.0.0";
    port = 28981;

    # Use NFS-mounted media directory (same as old instance)
    mediaDir = "/mnt/general/paperless-ngx/media";
    consumptionDir = "/mnt/general/paperless-ngx/consume";

    settings = {
      PAPERLESS_URL = "https://paperless.montycasa.net";
      PAPERLESS_OCR_LANGUAGE = "eng";
      PAPERLESS_OCR_LANGUAGES = "eng";
      PAPERLESS_TIME_ZONE = "America/New_York";

      # Use PostgreSQL database (via Unix socket)
      PAPERLESS_DBHOST = "/run/postgresql";
      PAPERLESS_DBNAME = "paperless";
      PAPERLESS_DBUSER = "paperless";

      # Enable consumption directory polling
      PAPERLESS_CONSUMER_POLLING = 60;

      # Wait 30 seconds after detecting a file before processing
      # Helps with large files on NFS that take time to fully write
      PAPERLESS_CONSUMER_INOTIFY_DELAY = 30;

      # Use scratch directory on same filesystem as media/consume to avoid
      # PrivateTmp isolation issues with temp files
      PAPERLESS_SCRATCH_DIR = "/mnt/general/paperless-ngx/scratch";

      # Store files in dated folders (added date) with created-date-prefixed title filenames
      PAPERLESS_FILENAME_FORMAT = "{{ added_year }}-{{ added_month }}-{{ added_day }}/{{ created_year }}-{{ created_month }}-{{ created_day }}_{{ title }}";

      # AI configuration - points to paperless-ai container
      # Uncomment once paperless-ai is configured
      PAPERLESS_AI_ENABLED = true;
      PAPERLESS_AI_URL = "http://localhost:8000";
    };
  };

  # Paperless-ai as OCI Container
  # This provides AI-powered document classification and processing
  # Source: https://github.com/clusterzx/paperless-ai
  virtualisation.oci-containers = {
    backend = "podman";
    containers.paperless-ai = {
      image = "clusterzx/paperless-ai:latest";

      # Ports 3000 (web UI) and 8000 (RAG API) are exposed directly via --network=host

      volumes = [
        "/mnt/general/paperless-ngx/media:/app/data/media:ro"  # Read-only access to paperless documents on NFS
        "/var/lib/paperless-ai:/app/data"                     # Persistent storage for app data, models, and database
      ];

      extraOptions = [ "--network=host" ];

      environment = {
        # Paperless-ngx connection — app expects PAPERLESS_API_URL with /api suffix
        PAPERLESS_API_URL = "http://localhost:28981/api";

        # Paperless username for the API token owner
        PAPERLESS_USERNAME = "admin";

        # AI provider configuration
        AI_PROVIDER = "ollama";
        OLLAMA_API_URL = "http://192.168.86.113:11434";
        OLLAMA_MODEL = "mistral:latest";
      };

      # Use sops secret for API token
      environmentFiles = [
        config.sops.secrets.paperless-ai-env.path
      ];
    };
  };

  # Create necessary directories
  systemd.tmpfiles.rules = [
    "d /var/lib/paperless-ai 0755 paperless paperless -"
    "d /mnt/general 0750 paperless paperless -"
    "d /mnt/general/paperless-ngx/scratch 0750 paperless paperless -"
  ];

  # Paperless services require the NFS mount before starting.
  # Without this they race against the mount on boot and fail with NAMESPACE errors.
  systemd.services.paperless-scheduler.requires = [ "mnt-general.mount" ];
  systemd.services.paperless-scheduler.after = [ "mnt-general.mount" ];
  systemd.services.paperless-task-queue.requires = [ "mnt-general.mount" ];
  systemd.services.paperless-task-queue.after = [ "mnt-general.mount" ];
  systemd.services.paperless-consumer.requires = [ "mnt-general.mount" ];
  systemd.services.paperless-consumer.after = [ "mnt-general.mount" ];
  systemd.services.paperless-web.requires = [ "mnt-general.mount" ];
  systemd.services.paperless-web.after = [ "mnt-general.mount" ];

  # Allow paperless services to write to the scratch directory on NFS
  # (ProtectSystem=strict makes everything read-only except ReadWritePaths)
  systemd.services.paperless-scheduler.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-task-queue.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-consumer.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-web.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];

  # Write the .env file that paperless-ai reads for configuration.
  # The app's isConfigured() check reads this file directly rather than process env.
  systemd.services.paperless-ai-write-env = {
    description = "Write paperless-ai .env configuration file";
    wantedBy = [ "podman-paperless-ai.service" ];
    before = [ "podman-paperless-ai.service" ];
    after = [ "sops-install-secrets.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      TOKEN=$(grep PAPERLESS_API_TOKEN ${config.sops.secrets.paperless-ai-env.path} | cut -d= -f2)
      printf 'PAPERLESS_API_URL=http://localhost:28981/api\nPAPERLESS_API_TOKEN=%s\nPAPERLESS_USERNAME=admin\nAI_PROVIDER=ollama\nOLLAMA_API_URL=http://192.168.86.113:11434\nOLLAMA_MODEL=mistral:latest\n' "$TOKEN" > /var/lib/paperless-ai/.env
    '';
  };

  # Ensure paperless-ai container starts after paperless service
  systemd.services.podman-paperless-ai = {
    requires = [ "paperless-consumer.service" "paperless-ai-write-env.service" ];
    after = [ "paperless-consumer.service" "paperless-ai-write-env.service" ];
  };

  # Open firewall ports
  networking.firewall.allowedTCPPorts = [
    28981  # Paperless-ngx web UI
    3000   # Paperless-ai Web UI (setup and management)
    8000   # Paperless-ai RAG API
  ];

  system.stateVersion = "25.11";
}
