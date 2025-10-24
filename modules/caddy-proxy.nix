{ config, pkgs, lib, ... }:

with lib;

let
  cfg = config.extra-services.caddy-proxy;

  caddyWithPlugins = pkgs.caddy.withPlugins {
    plugins = [
      "github.com/caddy-dns/cloudflare@v1.3.0"
      "github.com/mholt/caddy-l4@v0.0.0-20250530154005-4d3c80e89c5f"
    ];
    hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  };

  cfTls = ''
    tls {
      dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
  '';

  # Generate config based on protocol type
  mkConfig = { protocol, upstream }: 
    if protocol == "http" then ''
      ${cfTls}
      reverse_proxy ${upstream}
    ''
    else if protocol == "https" then ''
      ${cfTls}
      reverse_proxy ${upstream} {
        transport http {
          tls
          tls_insecure_skip_verify
        }
      }
    ''
    else throw "Protocol ${protocol} should be handled in layer4, not virtualHosts";

  # Generate layer4 config for SNI-based services
  mkLayer4SniConfig = domain: { protocol, upstream }: ''
    @${builtins.replaceStrings ["."] ["_"] domain} tls sni ${domain}
    route @${builtins.replaceStrings ["."] ["_"] domain} {
      proxy ${upstream}
    }
  '';

  # Filter HTTP/HTTPS services for virtualHosts
  httpServices = filterAttrs (n: v: v.protocol == "http" || v.protocol == "https") cfg.services;

  # Check if we have SNI services
  hasSniServices = cfg.layer4SniServices != {};
in
{
  options.extra-services.caddy-proxy = {
    enable = mkEnableOption "Caddy reverse proxy with Cloudflare and L4 plugins";

    cloudflareTokenFile = mkOption {
      type = types.path;
      default = "/var/lib/caddy/cloudflare.env";
      description = "Path to file containing CLOUDFLARE_API_TOKEN=your_token";
    };

    services = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          protocol = mkOption {
            type = types.enum [ "http" "https" ];
            description = "Protocol type for the service";
          };
          upstream = mkOption {
            type = types.str;
            description = "Upstream server address (e.g., host.internal:8080)";
          };
        };
      });
      default = {};
      description = "HTTP/HTTPS services to proxy";
      example = literalExpression ''
        {
          "service1.example.com" = { 
            protocol = "https"; 
            upstream = "host1.internal:443"; 
          };
          "service2.example.com" = { 
            protocol = "http"; 
            upstream = "host2.internal:3001"; 
          };
        }
      '';
    };

    layer4SniServices = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          protocol = mkOption {
            type = types.enum [ "tcp" "udp" ];
            description = "Protocol type for the layer4 service";
          };
          upstream = mkOption {
            type = types.str;
            description = "Upstream server address (e.g., host.internal:22)";
          };
        };
      });
      default = {};
      description = "TCP/UDP services accessible via SNI on port 443";
      example = literalExpression ''
        {
          "ssh.example.com" = { 
            protocol = "tcp"; 
            upstream = "host1.internal:22"; 
          };
        }
      '';
    };

    extraGlobalConfig = mkOption {
      type = types.lines;
      default = "";
      description = "Extra configuration to add to Caddy's global config";
    };
  };

  config = mkIf cfg.enable {
    services.caddy = {
      enable = true;
      package = caddyWithPlugins;
      environmentFile = cfg.cloudflareTokenFile;
      
      globalConfig = ''
        ${optionalString hasSniServices ''
          layer4 {
            :443 {
              ${concatStringsSep "\n          " (mapAttrsToList mkLayer4SniConfig cfg.layer4SniServices)}
            }
          }
        ''}
        ${cfg.extraGlobalConfig}
      '';
      
      virtualHosts = mapAttrs (domain: config: {
        extraConfig = mkConfig config;
      }) httpServices;
    };

    networking.firewall.allowedTCPPorts = [ 80 443 ];

    systemd.services.caddy.preStart = ''
      if [ ! -f ${cfg.cloudflareTokenFile} ]; then
        echo "Warning: ${cfg.cloudflareTokenFile} not found!"
        echo "Create it with: echo 'CLOUDFLARE_API_TOKEN=your_token' > ${cfg.cloudflareTokenFile}"
      fi
    '';
  };
}