Nix Configuration

Welcome to my Nix Configuration repository! This is my personal collection of Nix configurations, designed to manage and define my development environments, system configurations, and workflows using the Nix package manager and the NixOS declarative configuration system.
Overview

This repository serves as the central hub for my Nix-based system configuration. It allows me to manage my systems consistently, reproducibly, and efficiently across different environments. The configurations are tailored to my specific preferences and use cases, but they may also serve as a reference or inspiration for others exploring the power of Nix.
Repository Structure

Here's an overview of the structure of this repository:

    flake.nix: The central entry point for managing configurations using Nix flakes.
    users/: Contains user-specific configurations.
    secrets/: A directory for securely managing sensitive files or configurations, such as SSH keys or other encrypted secrets.
    hosts/: Contains host-specific configurations for different machines or environments.
    justfile: A task runner file used to simplify and streamline common commands and workflows.
    nixos-config_old/: Contains older NixOS-related configurations and scripts, including shared settings, host configurations, and deployment scripts.

Prerequisites

To make use of this repository, ensure you have the following installed and set up:

    Nix Package Manager: Installation Guide
    NixOS (optional): For system-level configurations if you’re using NixOS.
    Just (optional): Task runner for executing commands defined in the justfile.

Getting Started
1. Clone the Repository
bash

git clone https://github.com/pmontgo33/nix-config.git
cd nix-config

2. Set Up Your Environment

If you’re using NixOS, you can apply the system configurations:
bash

sudo nixos-rebuild switch --flake .

3. Customize Configurations

Edit the configurations in the users/, hosts/, or secrets/ directories to suit your needs. This setup is modular and designed to be easily extendable.

License

This project is licensed under the MIT License. See the LICENSE file for details.
Acknowledgments

This repository is a reflection of my personal journey with Nix. A big thanks to the Nix community for creating such a robust and flexible tool for managing systems and packages!
