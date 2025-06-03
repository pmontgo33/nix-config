{
  description = "Monty's NixOS flake";

  inputs = {
    # NixOS official package source, using the nixos-24.11 branch here
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    # home-manager, used for managing user configuration
    home-manager = {
      url = "github:nix-community/home-manager/release-25.05";
      # The `follows` keyword in inputs is used for inheritance.
      # Here, `inputs.nixpkgs` of home-manager is kept consistent with
      # the `inputs.nixpkgs` of the current flake,
      # to avoid problems caused by different versions of nixpkgs.
      inputs.nixpkgs.follows = "nixpkgs";
    };

    agenix.url = "github:ryantm/agenix";
  };

  outputs = inputs@{ self, nixpkgs, home-manager, agenix, ... }: {
    
    ## hp-nixos ##
    nixosConfigurations.hp-nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/hp-nixos
        agenix.nixosModules.default

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

          home-manager.users.patrick = import ./users/patrick/hosts/hp-nixos/home.nix;
          home-manager.users.lina = import ./users/lina/hosts/hp-nixos/home.nix;

          # Optionally, use home-manager.extraSpecialArgs to pass arguments to home.nix
        }
      ];
    };

    ## plasma-vm-nixos ##
    nixosConfigurations.plasma-vm-nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/plasma-vm-nixos
        agenix.nixosModules.default

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

          home-manager.users.patrick = import ./users/patrick/hosts/hp-nixos/home.nix;

          # Optionally, use home-manager.extraSpecialArgs to pass arguments to home.nix
        }
      ];

    };

    ## nix-fury ##
    nixosConfigurations.nix-fury = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./hosts/nix-fury
        agenix.nixosModules.default

        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.backupFileExtension = "backup";

   #       home-manager.users.patrick = import ./users/patrick/home.nix;

          # Optionally, use home-manager.extraSpecialArgs to pass arguments to home.nix
        }
      ];

    };
  };
}
