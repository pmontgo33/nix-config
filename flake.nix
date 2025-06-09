{
  description = "Monty's NixOS flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    home-manager = {
      url = "github:nix-community/home-manager/release-25.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = inputs@{ self, nixpkgs, home-manager, sops-nix, ... }: {
    
    ## hp-nixos ##
    nixosConfigurations.hp-nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/hp-nixos
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

          home-manager.users.patrick = import ./users/patrick/hosts/hp-nixos/home.nix;
          home-manager.users.lina = import ./users/lina/hosts/hp-nixos/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];
    };

    ## plasma-vm-nixos ##
    nixosConfigurations.plasma-vm-nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/plasma-vm-nixos
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

          home-manager.users.patrick = import ./users/patrick/hosts/hp-nixos/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];

    };

    ## nix-fury ##
    nixosConfigurations.nix-fury = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nix-fury
        sops-nix.nixosModules.sops

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          home-manager.sharedModules = [ sops-nix.homeManagerModules.sops ];
        }
      ];

    };
  };
}
