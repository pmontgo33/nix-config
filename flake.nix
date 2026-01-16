{
  description = "Monty's NixOS flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    home-manager = {
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-flatpak = {
      url = "github:gmodena/nix-flatpak";
    };

    plasma-manager = {
      url = "github:nix-community/plasma-manager";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
  };

  outputs = inputs@{ self, nixpkgs, nixpkgs-unstable, home-manager, disko, sops-nix, nix-flatpak, plasma-manager, ... }: {

    ## tesseract ##
    nixosConfigurations.tesseract = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/tesseract
        disko.nixosModules.disko
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.users.patrick = import ./users/patrick/hosts/tesseract/home.nix;
          home-manager.users.lina = import ./users/lina/hosts/tesseract/home.nix;

          home-manager.sharedModules = [
            sops-nix.homeManagerModules.sops
            nix-flatpak.homeManagerModules.nix-flatpak
            plasma-manager.homeModules.plasma-manager
          ];
        }
      ];
    };

    ## hp-nixos ##
    nixosConfigurations.hp-nixos = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/hp-nixos
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.users.patrick = import ./users/patrick/hosts/hp-nixos/home.nix;
          home-manager.users.lina = import ./users/lina/hosts/hp-nixos/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## nixbook installer ##
    nixosConfigurations.nixbook-installer = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"

        ./hosts/nixbooks/nixbook-installer
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## ali-book ##
    nixosConfigurations.ali-book = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nixbooks/ali-book
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## emma-book ##
    nixosConfigurations.emma-book = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nixbooks/emma-book
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## cora-book ##
    nixosConfigurations.cora-book = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nixbooks/cora-book
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## plasma-vm-nixos ##
    nixosConfigurations.plasma-vm-nixos = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/plasma-vm-nixos
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.users.patrick = import ./users/patrick/hosts/tesseract/home.nix;
          
          home-manager.sharedModules = [
            sops-nix.homeManagerModules.sops
            nix-flatpak.homeManagerModules.nix-flatpak
            plasma-manager.homeModules.plasma-manager
          ];
        }
      ];

    };

    ## nxc-base ##
    nixosConfigurations.nxc-base = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/nxc-base
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## lxc-tailscale ##
    nixosConfigurations.lxc-tailscale = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/lxc-tailscale
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## nix-fury ##
    nixosConfigurations.nix-fury = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nix-fury
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];

    };

    ## yondu ##
    nixosConfigurations.yondu = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/yondu
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];

    };

    ## bifrost ##
    nixosConfigurations.bifrost = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/bifrost
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## local-proxy ##
    nixosConfigurations.local-proxy = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/local-proxy
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## jellyfin ##
    nixosConfigurations.jellyfin = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/jellyfin
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## audiobookshelf ##
    nixosConfigurations.audiobookshelf = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/audiobookshelf
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## immich ##
    nixosConfigurations.immich = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/dev/immich
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## homepage ##
    nixosConfigurations.homepage = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/homepage
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## omada ##
    nixosConfigurations.omada = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/omada
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## endurain ##
    nixosConfigurations.endurain = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/endurain
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## netalertx ##
    nixosConfigurations.netalertx = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/netalertx
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## mealie ##
    nixosConfigurations.mealie = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/mealie
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## ERPNext ##
    nixosConfigurations.erpnext = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/dev/erpnext
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## grist ##
    nixosConfigurations.grist = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/grist
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## pocket-id ##
    nixosConfigurations.pocket-id = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/pocket-id
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };
          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## nextcloud ##
    nixosConfigurations.nextcloud = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nextcloud
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## onlyoffice ##
    nixosConfigurations.onlyoffice = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/dev/onlyoffice
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## forgejo ##
    nixosConfigurations.forgejo = nixpkgs.lib.nixosSystem {
      specialArgs = {
        inherit inputs;
        nixosConfigurations = self.nixosConfigurations;
      };
      modules = [
        ./hosts/nxc/forgejo
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## omnitools ##
    nixosConfigurations.omnitools = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/omnitools
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## frigate ##
    nixosConfigurations.frigate = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/frigate
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## paperless-ngx ##
    nixosConfigurations.paperless-ngx = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/paperless-ngx
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## wallabag ##
    nixosConfigurations.wallabag = nixpkgs.lib.nixosSystem {
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nxc/wallabag
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";
          home-manager.extraSpecialArgs = { inherit inputs; };

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };
  };
}
