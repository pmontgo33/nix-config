{ config, pkgs, lib, modulesPath, inputs, outputs, ... }:
{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  sops = {
    secrets = {
      "homepage-dashboard-env" = {
        owner = "homepage-dashboard";
        group = "homepage-dashboard";
        mode = "0400";
        restartUnits = [ "homepage-dashboard.service" ];
      };
    };
  };

  environment.systemPackages = with pkgs; [ ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };

  services.openssh.enable = true;

  services.homepage-dashboard = {
    enable = true;
    allowedHosts = "*";
    environmentFile = config.sops.secrets."homepage-dashboard-env".path;
    openFirewall = true;

    widgets = [
      {
        logo = {
          icon = "https://1.bp.blogspot.com/-8pXESPi3igc/XoC5nUl5dcI/AAAAAAAAqwg/-iz6DADXKJQLoB78_Ri7g9637RlRZV2sgCLcBGAsYHQ/s1600/HomeLab_icon.png";
        };
      }
      {
        greeting = {
          text_size = "3xl";
          text = "Monty Casa Homelab";
        };
      }
      {
        resources = {
          cpu = true;
          memory = true;
          disk = "/";
        };
      }
      {
        search = {
          provider = "duckduckgo";
          target = "_blank";
        };
      }
    ];

    settings = {
      title = "Monty Casa Homelab";
      favicon = "https://1.bp.blogspot.com/-8pXESPi3igc/XoC5nUl5dcI/AAAAAAAAqwg/-iz6DADXKJQLoB78_Ri7g9637RlRZV2sgCLcBGAsYHQ/s1600/HomeLab_icon.png";
      quicklaunch = {
        searchDescriptions = true;
        hideInternetSearch = true;
        hideVisitURL = true;
      };
      providers = {
        openweathermap = "openweathermapapikey";
        weatherapi = "weatherapiapikey";
      };
      layout = {
        Proxmox = { tab = "Machines"; };
        Storage = { tab = "Machines"; };
        "Other Machines" = { tab = "Machines"; };
        Offsite = { tab = "Machines"; };
        "Routers, DNS, Switches" = { tab = "Networking"; };
        "Access Points" = { tab = "Networking"; };
        "Remote Access" = { tab = "Networking"; };
        "Home Automation" = { tab = "Home"; };
        NVR = { tab = "Home"; };
        "Media Servers" = { tab = "Media"; style = "row"; columns = 1; };
        Aar = { tab = "Media"; style = "row"; columns = 3; };
        Apps = { tab = "Apps"; style = "row"; columns = 3; };
        Status = { style = "row"; columns = 2; };
      };
    };

    services = [
      {
        Status = [
          {
            "Uptime Kuma" = {
              href = "https://uptime.montycasa.net";
              siteMonitor = "https://uptime.montycasa.net";
              icon = "sh-uptime-kuma.svg";
              widget = {
                type = "uptimekuma";
                url = "https://uptime.montycasa.net";
                slug = "homepage";
              };
            };
          }
          {
            MySpeed = {
              href = "https://myspeed.montycasa.net";
              siteMonitor = "https://myspeed.montycasa.net";
              icon = "sh-myspeed.png";
              widget = {
                type = "myspeed";
                url = "https://myspeed.montycasa.net";
              };
            };
          }
          {
            Dockge = {
              href = "https://dockge.montycasa.net";
              siteMonitor = "https://dockge.montycasa.net";
              icon = "sh-dockge.svg";
            };
          }
          {
            Gotify = {
              href = "https://notify.montycasa.com";
              siteMonitor = "https://notify.montycasa.com";
              icon = "sh-gotify.svg";
            };
          }
        ];
      }
      {
        Proxmox = [
          {
            Stark = {
              href = "https://stark.montycasa.net";
              siteMonitor = "https://stark.montycasa.net";
              description = "Proxmox VE";
              icon = "sh-proxmox.svg";
              widget = {
                type = "proxmox";
                url = "https://stark.montycasa.net";
                username = "{{HOMEPAGE_VAR_PROXMOX_USERNAME}}";
                password = "{{HOMEPAGE_VAR_PROXMOX_PASSWORD}}";
                node = "stark";
              };
            };
          }
          {
            Loki = {
              href = "https://loki.montycasa.net";
              siteMonitor = "https://loki.montycasa.net";
              description = "Proxmox VE";
              icon = "sh-proxmox.svg";
              widget = {
                type = "proxmox";
                url = "https://loki.montycasa.net";
                username = "{{HOMEPAGE_VAR_PROXMOX_USERNAME}}";
                password = "{{HOMEPAGE_VAR_PROXMOX_PASSWORD}}";
                node = "loki";
              };
            };
          }
          {
            Starlord = {
              href = "https://starlord.montycasa.net";
              siteMonitor = "https://starlord.montycasa.net";
              description = "Proxmox VE";
              icon = "sh-proxmox.svg";
              widget = {
                type = "proxmox";
                url = "https://starlord.montycasa.net";
                username = "{{HOMEPAGE_VAR_PROXMOX_USERNAME}}";
                password = "{{HOMEPAGE_VAR_PROXMOX_PASSWORD}}";
                node = "starlord";
              };
            };
          }
        ];
      }
      {
        Storage = [
          {
            "TrueNAS Home" = {
              href = "https://truenas.montycasa.net";
              siteMonitor = "https://truenas.montycasa.net";
              description = "TrueNAS Scale";
              icon = "sh-truenas-scale.svg";
              widget = {
                type = "truenas";
                url = "https://truenas.montycasa.net";
                username = "{{HOMEPAGE_VAR_TRUENAS_USERNAME}}";
                password = "{{HOMEPAGE_VAR_TRUENAS_PASSWORD}}";
                nasType = "scale";
              };
            };
          }
          {
            "Proxmox Backup Server" = {
              href = "https://pbs.montycasa.net";
              siteMonitor = "https://pbs.montycasa.net";
              icon = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/proxmox-light.png";
              widget = {
                type = "proxmoxbackupserver";
                url = "https://pbs.montycasa.net";
                username = "{{HOMEPAGE_VAR_PBS_USERNAME}}";
                password = "{{HOMEPAGE_VAR_PBS_PASSWORD}}";
              };
            };
          }
        ];
      }
      {
        Offsite = [
          {
            Wakanda = {
              href = "https://wakanda.skink-galaxy.ts.net:8006/";
              siteMonitor = "https://wakanda.skink-galaxy.ts.net:8006/";
              description = "Offsite Proxmox VE";
              icon = "sh-proxmox.svg";
              widget = {
                type = "proxmox";
                url = "https://wakanda.skink-galaxy.ts.net:8006";
                username = "{{HOMEPAGE_VAR_WAKANDA_USERNAME}}";
                password = "{{HOMEPAGE_VAR_WAKANDA_PASSWORD}}";
                node = "wakanda";
              };
            };
          }
          {
            "Bucky TrueNAS" = {
              href = "https://bucky.skink-galaxy.ts.net/";
              siteMonitor = "https://bucky.skink-galaxy.ts.net/";
              description = "Offsite TrueNAS Scale";
              icon = "sh-truenas-scale.svg";
              widget = {
                type = "truenas";
                url = "https://bucky.skink-galaxy.ts.net/";
                username = "{{HOMEPAGE_VAR_BUCKY_USERNAME}}";
                password = "{{HOMEPAGE_VAR_BUCKY_PASSWORD}}";
                nasType = "scale";
              };
            };
          }
        ];
      }
      {
        "Routers, DNS, Switches" = [
          {
            OPNSense = {
              href = "https://router.montycasa.net";
              siteMonitor = "https://router.montycasa.net";
              description = "Router/Firewall";
              icon = "sh-opnsense.svg";
              widget = {
                type = "opnsense";
                url = "https://router.montycasa.net";
                username = "{{HOMEPAGE_VAR_OPNSENSE_USERNAME}}";
                password = "{{HOMEPAGE_VAR_OPNSENSE_PASSWORD}}";
              };
            };
          }
          {
            "Adguard Home" = {
              href = "https://blocker.montycasa.net";
              siteMonitor = "https://blocker.montycasa.net";
              description = "Network DNS Adblocker";
              icon = "sh-adguard-home.svg";
              widget = {
                type = "adguard";
                url = "https://blocker.montycasa.net";
                username = "{{HOMEPAGE_VAR_ADGUARD_USERNAME}}";
                password = "{{HOMEPAGE_VAR_ADGUARD_PASSWORD}}";
              };
            };
          }
        ];
      }
      {
        "Access Points" = [
          {
            Omada = {
              href = "https://omada.montycasa.net";
              siteMonitor = "https://omada.montycasa.net";
              description = "Omada Controller";
              icon = "sh-omada.svg";
              widget = {
                type = "omada";
                url = "https://omada.montycasa.net";
                username = "{{HOMEPAGE_VAR_OMADA_USERNAME}}";
                password = "{{HOMEPAGE_VAR_OMADA_PASSWORD}}";
                site = "{{HOMEPAGE_VAR_OMADA_SITE}}";
              };
            };
          }
        ];
      }
      {
        "Media Servers" = [
          {
            Jellyfin = {
              href = "https://watchit.montycasa.com";
              siteMonitor = "https://watchit.montycasa.com";
              description = "Movie and TV Show Library";
              icon = "sh-jellyfin.svg";
              widget = {
                type = "jellyfin";
                url = "https://watchit.montycasa.com";
                key = "{{HOMEPAGE_VAR_JELLYFIN_KEY}}";
                enableNowPlaying = true;
                enableUser = true;
                showEpisodeNumber = true;
                expandOneStreamToTwoRows = false;
              };
            };
          }
          {
            Immich = {
              href = "https://photos.montycasa.com";
              siteMonitor = "https://photos.montycasa.com";
              description = "Photo Library";
              icon = "sh-immich.svg";
              widget = {
                type = "immich";
                url = "https://photos.montycasa.com";
                key = "{{HOMEPAGE_VAR_IMMICH_KEY}}";
                version = 2;
              };
            };
          }
          {
            Audiobookshelf = {
              href = "https://audiobooks.montycasa.com";
              siteMonitor = "https://audiobooks.montycasa.com";
              description = "Audiobook Library";
              icon = "sh-audiobookshelf.svg";
              widget = {
                type = "audiobookshelf";
                url = "https://audiobooks.montycasa.com";
                key = "{{HOMEPAGE_VAR_AUDIOBOOKSHELF_KEY}}";
              };
            };
          }
        ];
      }
      {
        Aar = [
          {
            qBittorrent = {
              href = "https://qbittorrent.montycasa.net";
              siteMonitor = "https://qbittorrent.montycasa.net";
              icon = "sh-qbittorrent.svg";
              widget = {
                type = "qbittorrent";
                url = "https://qbittorrent.montycasa.net";
                username = "{{HOMEPAGE_VAR_QBITTORRENT_USERNAME}}";
                password = "{{HOMEPAGE_VAR_QBITTORRENT_PASSWORD}}";
              };
            };
          }
          {
            Radarr = {
              href = "https://radarr.montycasa.net";
              siteMonitor = "https://radarr.montycasa.net";
              icon = "sh-radarr.svg";
              widget = {
                type = "radarr";
                url = "https://radarr.montycasa.net";
                key = "{{HOMEPAGE_VAR_RADARR_KEY}}";
                enableQueue = true;
              };
            };
          }
          {
            Sonarr = {
              href = "https://sonarr.montycasa.net";
              siteMonitor = "https://sonarr.montycasa.net";
              icon = "sh-sonarr.svg";
              widget = {
                type = "sonarr";
                url = "https://sonarr.montycasa.net";
                key = "{{HOMEPAGE_VAR_SONARR_KEY}}";
                enableQueue = true;
              };
            };
          }
          {
            Bazarr = {
              href = "https://bazarr.montycasa.net";
              siteMonitor = "https://bazarr.montycasa.net";
              icon = "sh-bazarr.png";
              widget = {
                type = "bazarr";
                url = "https://bazarr.montycasa.net";
                key = "{{HOMEPAGE_VAR_BAZARR_KEY}}";
              };
            };
          }
          {
            Prowlarr = {
              href = "https://prowlarr.montycasa.net";
              siteMonitor = "https://prowlarr.montycasa.net";
              icon = "sh-prowlarr.svg";
              widget = {
                type = "prowlarr";
                url = "https://prowlarr.montycasa.net";
                key = "{{HOMEPAGE_VAR_PROWLARR_KEY}}";
              };
            };
          }
          {
            Transmission = {
              href = "http://192.168.86.114:9092/";
              siteMonitor = "http://192.168.86.114:9092/";
              description = "NO VPN";
              icon = "sh-transmission.svg";
              widget = {
                type = "transmission";
                url = "http://192.168.86.114:9092";
                username = "";
                password = "{{HOMEPAGE_VAR_TRANSMISSION_KEY}}";
                rpcUrl = "/transmission/";
              };
            };
          }
        ];
      }
    ];
  };

  system.stateVersion = "25.05";
}
