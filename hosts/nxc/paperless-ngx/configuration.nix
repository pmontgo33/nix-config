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

    # Enable local PostgreSQL database
    # Note: The service creates and manages the database automatically
    # You can customize database settings here if needed

    settings = {
      PAPERLESS_URL = "https://paperless.montycasa.com";  # Update with your domain
      PAPERLESS_OCR_LANGUAGE = "eng";
      PAPERLESS_OCR_LANGUAGES = "eng";
      PAPERLESS_TIME_ZONE = "America/New_York";

      # Enable consumption directory polling
      PAPERLESS_CONSUMER_POLLING = 60;

      # AI configuration - points to paperless-ai container
      # Uncomment once paperless-ai is configured
      # PAPERLESS_AI_ENABLED = true;
      # PAPERLESS_AI_URL = "http://localhost:8000";
    };
  };

  # Paperless-ai as OCI Container
  # This provides AI-powered document classification and processing
  virtualisation.oci-containers = {
    backend = "podman";
    containers.paperless-ai = {
      image = "ghcr.io/icereed/paperless-ai:latest";

      ports = [
        "8000:8000"  # Paperless-ai API port
      ];

      volumes = [
        "/var/lib/paperless/media:/data/media:ro"  # Read-only access to paperless documents
        "/var/lib/paperless-ai:/data/models"       # Storage for AI models
      ];

      environment = {
        # OpenAI API configuration (if using OpenAI)
        # OPENAI_API_KEY will be loaded from environment file

        # Or use local models (ollama, etc.)
        # MODEL_BACKEND = "ollama";
        # OLLAMA_URL = "http://host.docker.internal:11434";
      };

      # Uncomment and configure if using API keys
      # environmentFiles = [
      #   config.sops.secrets.paperless-ai-env.path
      # ];
    };
  };

  # SOPS secrets configuration (uncomment when ready to use)
  # sops.secrets.paperless-ai-env = {};

  # Create necessary directories
  systemd.tmpfiles.rules = [
    "d /var/lib/paperless-ai 0755 paperless paperless -"
    "d /mnt/general 0750 paperless paperless -"
  ];

  # Ensure paperless-ai container starts after paperless service
  systemd.services.podman-paperless-ai = {
    requires = [ "paperless-consumer.service" ];
    after = [ "paperless-consumer.service" ];
  };

  # Open firewall ports
  networking.firewall.allowedTCPPorts = [
    28981  # Paperless-ngx web UI
    8000   # Paperless-ai API (if you need external access)
  ];

  system.stateVersion = "25.11";
}
