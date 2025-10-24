{ config, lib, pkgs, ... }:

with lib;

let
  # Top-level cfg reference
  cfg = config.extra-services.caddy-proxy;
  
  nixosConfigurations = config._module.args.nixosConfigurations;

  # Collect services from all hosts based on tag mappings
  collectProxyServices = { nixosConfigurations, proxyName, tagMappings }:
    let
      enabledHosts = filterAttrs (name: hostConfig:
        hostConfig.config.extra-services.proxy-target.enable or false
      ) nixosConfigurations;

      servicesList = flatten (mapAttrsToList (hostName: hostConfig:
        let
          targetConfig = hostConfig.config.extra-services.proxy-target;
          hostAddress = targetConfig.hostAddress;
        in
          mapAttrsToList (serviceName: serviceConfig:
            let
              matchingTags = filter (tag: hasAttr tag tagMappings) serviceConfig.tags;
              matchedTag = if length matchingTags > 0 then head matchingTags else null;
              baseDomain = if matchedTag != null then tagMappings.${matchedTag} else null;
              domain = if baseDomain != null then
                (if hasInfix "." serviceConfig.subdomain
                 then serviceConfig.subdomain
                 else "${serviceConfig.subdomain}.${baseDomain}")
                else null;
            in
              if domain != null then [{
                inherit hostName serviceName hostAddress domain;
                inherit (serviceConfig) port protocol extraCaddyConfig subdomain;
                serviceId = "${hostName}-${serviceName}";
                tag = matchedTag;
              }] else []
          ) targetConfig.services
      ) enabledHosts);
    in
      servicesList;

  # Convert manual services to same format as collected services
  convertManualServices = manualServices:
    mapAttrsToList (serviceName: serviceConfig: {
      hostName = "manual";
      inherit serviceName;
      hostAddress = serviceConfig.address;
      domain = serviceConfig.domain;
      inherit (serviceConfig) port protocol extraCaddyConfig;
      subdomain = serviceConfig.domain;
      serviceId = "manual-${serviceName}";
      tag = "manual";
    }) manualServices;

in
{
  # Options definition
  options.extra-services.caddy-proxy = mkOption {
    type = types.attrsOf (types.submodule ({ name, config, ... }: {
      options = {
        enable = mkEnableOption "Caddy reverse proxy instance";

        tagMappings = mkOption {
          type = types.attrsOf types.str;
          default = {};
          description = ''
            Map service tags to base domain names.
          '';
        };

        manualServices = mkOption {
          type = types.attrsOf (types.submodule {
            options = {
              domain = mkOption { type = types.str; };
              address = mkOption { type = types.str; };
              port = mkOption { type = types.port; };
              protocol = mkOption { type = types.enum [ "http" "https" "tcp" "udp" ]; default = "http"; };
              extraCaddyConfig = mkOption { type = types.lines; default = ""; };
            };
          });
          default = {};
        };

        email = mkOption { type = types.nullOr types.str; default = null; };
        emailFile = mkOption { type = types.nullOr types.path; default = null; };

        dnsProvider = mkOption {
          type = types.nullOr (types.submodule {
            options = {
              name = mkOption { type = types.enum [
                "cloudflare" "route53" "digitalocean" "gandi" "namecheap"
                "duckdns" "godaddy" "hetzner" "linode" "ovh" "vultr"
              ]; };
              credentialsFile = mkOption { type = types.path; };
              extraConfig = mkOption { type = types.lines; default = ""; };
            };
          });
          default = null;
        };

        useLocalCerts = mkOption { type = types.bool; default = false; };
        extraGlobalConfig = mkOption { type = types.lines; default = ""; };
        extraServiceConfig = mkOption { type = types.attrsOf types.lines; default = {}; };
        openFirewall = mkOption { type = types.bool; default = true; };
      };
    }));
    default = {};
    description = "Caddy reverse proxy instances";
  };

  # Config: map over top-level cfg safely
  config = mkMerge (mapAttrsToList (proxyName: proxyCfg: mkIf proxyCfg.enable (
    let
      hosts = nixosConfigurations;  # safe: external
      # Read email
      emailValue = if proxyCfg.emailFile != null then readFile proxyCfg.emailFile else proxyCfg.email;
      dnsConfig = if proxyCfg.dnsProvider != null then ''
        acme_dns ${proxyCfg.dnsProvider.name} {
          ${readFile proxyCfg.dnsProvider.credentialsFile}
          ${proxyCfg.dnsProvider.extraConfig}
        }
      '' else "";

      # Collect services
      autoServices = collectProxyServices {
        inherit hosts proxyName;
        inherit (proxyCfg) tagMappings;
      };
      manualServicesList = convertManualServices proxyCfg.manualServices;
      allServices = autoServices ++ manualServicesList;

      httpServices = filter (s: s.protocol == "http" || s.protocol == "https") allServices;
      streamServices = filter (s: s.protocol == "tcp" || s.protocol == "udp") allServices;

      tlsDirective = if proxyCfg.dnsProvider != null then ''
        tls {
          dns ${proxyCfg.dnsProvider.name}
        }
      '' else "";

      # Generate Caddy configs
      httpConfig = concatMapStringsSep "\n\n" (service:
        let tagConfig = proxyCfg.extraServiceConfig.${service.tag} or ""; in ''
          ${service.domain} {
            ${tlsDirective}
            reverse_proxy ${service.hostAddress}:${toString service.port}
            ${tagConfig}
            ${service.extraCaddyConfig}
          }
      '') httpServices;

      streamConfig = if length streamServices > 0 then ''
        layer4 {
          ${concatMapStringsSep "\n  " (service: ''
            ${service.domain}:${toString service.port} {
              route {
                proxy {
                  upstream ${service.hostAddress}:${toString service.port}
                }
              }
            }
          '') streamServices}
        }
      '' else "";
    in
      {
        services.caddy = {
          enable = true;
          package = if proxyCfg.dnsProvider != null then
            pkgs.caddy.withPlugins {
              plugins = [
                "github.com/mholt/caddy-l4@latest"
                "github.com/caddy-dns/${proxyCfg.dnsProvider.name}@latest"
              ];
              hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
            }
          else
            pkgs.caddy.withPlugins {
              plugins = [ "github.com/mholt/caddy-l4@latest" ];
              hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
            };

          globalConfig = ''
            ${optionalString proxyCfg.useLocalCerts "local_certs"}
            ${optionalString (!proxyCfg.useLocalCerts) "email ${emailValue}"}
            ${dnsConfig}
            ${proxyCfg.extraGlobalConfig}
          '';

          extraConfig = httpConfig + "\n\n" + streamConfig;
        };

        networking.firewall = mkIf proxyCfg.openFirewall {
          allowedTCPPorts = [ 80 443 ] ++ map (s: s.port) (filter (s: s.protocol == "tcp") streamServices);
          allowedUDPPorts = [ 443 ] ++ map (s: s.port) (filter (s: s.protocol == "udp") streamServices);
        };
      }
  )) cfg);
}
