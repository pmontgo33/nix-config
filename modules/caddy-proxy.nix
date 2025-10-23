{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.extra-services.caddy-proxy;

  # Collect services from all configured hosts based on tag mappings
  collectProxyServices = { nixosConfigurations, proxyName, tagMappings }:
    let
      # Filter hosts that have proxy-target enabled
      enabledHosts = filterAttrs (name: hostConfig:
        hostConfig.config.extra-services.proxy-target.enable or false
      ) nixosConfigurations;
      
      # Extract services that match our tag mappings
      servicesList = flatten (mapAttrsToList (hostName: hostConfig:
        let
          targetConfig = hostConfig.config.extra-services.proxy-target;
          hostAddress = targetConfig.hostAddress;
        in
          mapAttrsToList (serviceName: serviceConfig:
            let
              # Check if any of this service's tags match our tag mappings
              matchingTags = filter (tag: hasAttr tag tagMappings) serviceConfig.tags;
              # Use the first matching tag to determine the base domain
              matchedTag = if length matchingTags > 0 then head matchingTags else null;
              baseDomain = if matchedTag != null then tagMappings.${matchedTag} else null;
              
              # Build full domain: if subdomain contains dots, use as-is, otherwise prepend to base domain
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

in
{
  options.extra-services.caddy-proxy = mkOption {
    type = types.attrsOf (types.submodule ({ name, ... }: {
      options = {
        enable = mkEnableOption "Caddy reverse proxy instance";

        nixosConfigurations = mkOption {
          type = types.unspecified;
          description = "The flake's nixosConfigurations attrset to scan for proxy targets";
        };

        tagMappings = mkOption {
          type = types.attrsOf types.str;
          default = {};
          description = ''
            Map service tags to base domain names.
            Service subdomains will be prepended to these base domains.
          '';
          example = literalExpression ''
            {
              "public" = "example.com";
              "internal" = "local";
            }
          '';
        };

        email = mkOption {
          type = types.str;
          description = "Email for Let's Encrypt certificate registration";
          example = "admin@example.com";
        };

        useLocalCerts = mkOption {
          type = types.bool;
          default = false;
          description = "Use local CA certificates instead of Let's Encrypt";
        };

        extraGlobalConfig = mkOption {
          type = types.lines;
          default = "";
          description = "Extra global Caddy configuration";
        };

        extraServiceConfig = mkOption {
          type = types.attrsOf types.lines;
          default = {};
          description = "Extra Caddy configuration per tag";
          example = literalExpression ''
            {
              "api" = '''
                rate_limit {
                  zone api_limit {
                    key {remote_host}
                    events 100
                    window 1m
                  }
                }
              ''';
            }
          '';
        };

        openFirewall = mkOption {
          type = types.bool;
          default = true;
          description = "Automatically open firewall ports";
        };
      };
    }));
    default = {};
    description = "Caddy reverse proxy instances";
  };

  config = mkMerge (mapAttrsToList (proxyName: proxyCfg: mkIf proxyCfg.enable {
    services.caddy = {
      enable = true;
      
      # Enable the layer4 plugin for TCP proxying
      package = pkgs.caddy.withPlugins {
        plugins = [ "github.com/mholt/caddy-l4@latest" ];
        hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
      };

      globalConfig = ''
        ${optionalString proxyCfg.useLocalCerts "local_certs"}
        email ${proxyCfg.email}
        ${proxyCfg.extraGlobalConfig}
      '';

      extraConfig = 
        let
          services = collectProxyServices {
            nixosConfigurations = proxyCfg.nixosConfigurations;
            inherit proxyName;
            inherit (proxyCfg) tagMappings;
          };
          
          # Separate HTTP/HTTPS services from TCP/UDP services
          httpServices = filter (s: s.protocol == "http" || s.protocol == "https") services;
          streamServices = filter (s: s.protocol == "tcp" || s.protocol == "udp") services;
          
          # Generate HTTP/HTTPS virtual host configs
          httpConfig = concatMapStringsSep "\n\n" (service: 
            let
              tagConfig = proxyCfg.extraServiceConfig.${service.tag} or "";
            in ''
              ${service.domain} {
                reverse_proxy ${service.hostAddress}:${toString service.port}
                ${tagConfig}
                ${service.extraCaddyConfig}
              }
            '') httpServices;
          
          # Generate layer4 configs for TCP/UDP
          streamConfig = if (length streamServices > 0) then ''
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
          httpConfig + "\n\n" + streamConfig;
    };

    # Open firewall ports if requested
    networking.firewall = mkIf proxyCfg.openFirewall (
      let
        services = collectProxyServices {
          nixosConfigurations = proxyCfg.nixosConfigurations;
          inherit proxyName;
          inherit (proxyCfg) tagMappings;
        };
        streamServices = filter (s: s.protocol == "tcp" || s.protocol == "udp") services;
      in {
        allowedTCPPorts = [ 80 443 ] ++ 
          map (s: s.port) (filter (s: s.protocol == "tcp") streamServices);
        allowedUDPPorts = [ 443 ] ++  # QUIC
          map (s: s.port) (filter (s: s.protocol == "udp") streamServices);
      }
    );
  }) cfg);
}