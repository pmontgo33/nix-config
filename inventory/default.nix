# Concrete desired-state inventory for network identity and service generation.
#
# Real MAC/client identifiers are intentionally not stored here. Each network
# record references identityRef, which is resolved from an encrypted runtime
# mapping by the future OPNsense/Pi-hole reconciler.
{
  inventory = {
    schemaVersion = 1;
    identitySource = "sops-runtime";

    ownership = {
      dhcpv4 = "opnsense-dnsmasq";
      dhcpv6 = "opnsense";
      routerAdvertisements = "opnsense";
      rdnss = "opnsense";
      dnsDuringPhase1 = "adguard";
      localDns = "opnsense-unbound";
    };

    networkProfiles = {
      lan = {
        interface = "lan";
        subnet = "192.168.86.0/24";
        gateway = "192.168.86.1";
        dhcpRange = {
          from = "192.168.86.210";
          to = "192.168.86.254";
        };
        dnsDuringPhase1 = [ "192.168.86.1" ];
      };

      iot = {
        interface = "opt1";
        subnet = "192.168.10.0/24";
        gateway = "192.168.10.1";
        dhcpRange = {
          from = "192.168.10.150";
          to = "192.168.10.250";
        };
        dnsDuringPhase1 = [ "192.168.10.1" ];
      };

      guest = {
        interface = "opt2";
        subnet = "192.168.20.0/24";
        gateway = "192.168.20.1";
        dhcpRange = {
          from = "192.168.20.150";
          to = "192.168.20.250";
        };
        dnsDuringPhase1 = [ "192.168.20.1" ];
      };
    };

    staticGuests = {
      pihole1 = {
        network = {
          hostname = "pihole1";
          address = "192.168.86.101";
          interface = "lan";
        };
        placement = {
          preferredNode = "loki";
          fallbackNodes = [ "stark" ];
        };
      };

      pihole2 = {
        network = {
          hostname = "pihole2";
          address = "192.168.86.102";
          interface = "lan";
        };
        placement = {
          preferredNode = "starlord";
          fallbackNodes = [ "stark" ];
        };
      };

    };

    devices = {
      poe-switch-basement = {
        network = {
          hostname = "poe-switch-basement";
          address = "192.168.86.5";
          identityRef = "lan-poe-switch-basement";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      esp32s3-3af8e0 = {
        network = {
          hostname = "esp32s3-3AF8E0";
          address = "192.168.86.70";
          identityRef = "lan-esp32s3-3af8e0";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      esp32s3-9c5358 = {
        network = {
          hostname = "esp32s3-9C5358";
          address = "192.168.86.71";
          identityRef = "lan-esp32s3-9c5358";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      reservation-lan-197 = {
        network = {
          hostname = null;
          address = "192.168.86.197";
          identityRef = "lan-reservation-197";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      roborock-vacuum-a65 = {
        network = {
          hostname = "roborock-vacuum-a65";
          address = "192.168.86.201";
          identityRef = "lan-roborock-vacuum-a65";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      reservation-lan-202 = {
        network = {
          hostname = null;
          address = "192.168.86.202";
          identityRef = "lan-reservation-202";
          interface = "lan";
          piholeGroup = "normal";
        };
      };

      wled-cabinets = {
        network = {
          hostname = "wled-cabinets";
          address = "192.168.10.17";
          identityRef = "iot-wled-cabinets";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      wled-patio = {
        network = {
          hostname = "wled-patio";
          address = "192.168.10.19";
          identityRef = "iot-wled-patio";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      emma-cam = {
        network = {
          hostname = "emma_cam";
          address = "192.168.10.50";
          identityRef = "iot-emma-cam";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      ali-cam = {
        network = {
          hostname = "ali_cam";
          address = "192.168.10.51";
          identityRef = "iot-ali-cam";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      nursery-cam = {
        network = {
          hostname = "nursery_cam";
          address = "192.168.10.52";
          identityRef = "iot-nursery-cam";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      back-door-cam = {
        network = {
          hostname = "back_door_cam";
          address = "192.168.10.53";
          identityRef = "iot-back-door-cam";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      front-door-cam = {
        network = {
          hostname = "front_door_cam";
          address = "192.168.10.54";
          identityRef = "iot-front-door-cam";
          interface = "iot";
          piholeGroup = "normal";
        };
      };

      elegoo-cc2 = {
        network = {
          hostname = "elegoo-cc2";
          address = "192.168.20.20";
          identityRef = "guest-elegoo-cc2";
          interface = "guest";
          piholeGroup = "normal";
        };
      };

      reservation-guest-80 = {
        network = {
          hostname = null;
          address = "192.168.20.80";
          identityRef = "guest-reservation-80";
          interface = "guest";
          piholeGroup = "normal";
        };
      };

      reservation-guest-193 = {
        network = {
          hostname = null;
          address = "192.168.20.193";
          identityRef = "guest-reservation-193";
          interface = "guest";
          piholeGroup = "normal";
        };
      };
    };

    localDns = {
      zones = { };
    };
  };
}
