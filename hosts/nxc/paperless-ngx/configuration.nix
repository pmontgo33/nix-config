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

      # Use scratch directory on same filesystem as media/consume to avoid
      # PrivateTmp isolation issues with temp files
      PAPERLESS_SCRATCH_DIR = "/mnt/general/paperless-ngx/scratch";

      # AI configuration - points to paperless-ai container
      # Uncomment once paperless-ai is configured
      # PAPERLESS_AI_ENABLED = true;
      # PAPERLESS_AI_URL = "http://localhost:8000";
    };
  };

  # Paperless-ai as OCI Container
  # This provides AI-powered document classification and processing
  # Source: https://github.com/clusterzx/paperless-ai
  virtualisation.oci-containers = {
    backend = "podman";
    containers.paperless-ai = {
      image = "clusterzx/paperless-ai:latest";

      ports = [
        "3000:3000"  # Paperless-ai Web UI (setup and management)
        "8000:8000"  # Paperless-ai RAG API port
      ];

      volumes = [
        "/mnt/general/paperless-ngx/media:/data/media:ro"  # Read-only access to paperless documents on NFS
        "/var/lib/paperless-ai:/data/models"               # Storage for AI models
      ];

      environment = {
        # Paperless-ngx connection
        PAPERLESS_URL = "http://localhost:28981";

        # OpenAI API configuration (if using OpenAI)
        # OPENAI_API_KEY will be loaded from environment file

        # Or use local models (ollama, etc.)
        # MODEL_BACKEND = "ollama";
        # OLLAMA_URL = "http://host.docker.internal:11434";
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

  # Allow paperless services to write to the scratch directory on NFS
  # (ProtectSystem=strict makes everything read-only except ReadWritePaths)
  systemd.services.paperless-scheduler.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-task-queue.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-consumer.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];
  systemd.services.paperless-web.serviceConfig.ReadWritePaths = [ "/mnt/general/paperless-ngx/scratch" ];

  # Ensure paperless-ai container starts after paperless service
  systemd.services.podman-paperless-ai = {
    requires = [ "paperless-consumer.service" ];
    after = [ "paperless-consumer.service" ];
  };

  # Open firewall ports
  networking.firewall.allowedTCPPorts = [
    28981  # Paperless-ngx web UI
    3000   # Paperless-ai Web UI (setup and management)
    8000   # Paperless-ai RAG API
  ];

  system.stateVersion = "25.11";
}
