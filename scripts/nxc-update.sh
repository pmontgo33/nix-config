#!/bin/bash

# Clear the terminal
clear

# Welcome message
echo "This script will update an existing NixOS LXC container based on a host from your flake.nix"
echo "----------------------------------------------------------------------------------"
echo "Prerequisites:"
echo "  1. This script must run on an existing NixOS system"
echo "  2. This script must run on a system with ssh access to the Proxmox VE host"
echo
echo "Press Enter to continue..."
read
echo "================================================================"
echo

# Function to load .env file
load_env_file() {
    # Get the directory where the script is located
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local env_file="$script_dir/.env"
    if [ -f "$env_file" ]; then
        # Source the .env file, but only export specific variables we care about
        while IFS='=' read -r key value; do
            # Skip empty lines and comments
            [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
            
            # Trim whitespace from key
            key=$(echo "$key" | xargs)
            
            # Remove quotes from value if present and trim whitespace
            value=$(echo "$value" | xargs)
            value=$(echo "$value" | sed 's/^"\(.*\)"$/\1/' | sed "s/^'\(.*\)'$/\1/")
            
            case "$key" in
                "REPOSITORY_URL")
                    ENV_REPOSITORY_URL="$value"
                    ;;
                "BRANCH")
                    ENV_BRANCH="$value"
                    ;;
                "PVE_HOST")
                    ENV_PVE_HOST="$value"
                    ;;
                "DEFAULT_MEMORY")
                    ENV_DEFAULT_MEMORY="$value"
                    ;;
                "DEFAULT_CORES")
                    ENV_DEFAULT_CORES="$value"
                    ;;
                "DEFAULT_DISK_SIZE")
                    ENV_DEFAULT_DISK_SIZE="$value"
                    ;;
                "DEFAULT_NETWORK_OPTION")
                    ENV_DEFAULT_NETWORK_OPTION="$value"
                    ;;
                "DEFAULT_GATEWAY")
                    ENV_DEFAULT_GATEWAY="$value"
                    ;;
                "SOPS_KEY_PATH")
                    ENV_SOPS_KEY_PATH="$value"
                    ;;
            esac
        done < "$env_file"
        return 0
    else
        return 1
    fi
}

# Load environment variables from .env file
if load_env_file; then
    echo "Found .env file, checking for configuration..."
    echo
else
    echo "No .env file found in current directory, continuing with user prompts..."
    echo
fi
echo "================================================================"
echo
echo "Define Flake Repository"
echo "-------------------------"

# Handle repository URL
if [ -n "$ENV_REPOSITORY_URL" ]; then
    repo_url="$ENV_REPOSITORY_URL"
    echo "Using repository URL from .env file: $repo_url"
    echo
else
    # Fallback to git repository detection
    remote_name=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | cut -d'/' -f1)
    if [ -z "$remote_name" ]; then
        echo "No repository found in current working directory or .env file"
        echo
        echo "Enter full url for nix flake git repository (example: https://github.com/user-name/nix-config.git)"
        read -p ": " repo_url
    else
        remote_url=$(git config --get remote.$remote_name.url)

        if [[ $remote_url == https://* ]]; then
            # Already https, leave as is
            https_url=$remote_url
        elif [[ $remote_url == ssh://git@* ]]; then
            # Convert ssh://git@host/path to https://host/path
            https_url=$(echo "$remote_url" | sed -E 's#^ssh://git@([^/]+)(/.+)#https://\1\2#')
        else
            echo "Error: Remote URL is not https or ssh://git@ format" >&2
            exit 1
        fi

        echo "Repository with remote $https_url found in current working directory"
        echo
        echo "Press Enter to use this repo, or enter full url for the nix flake repository you would like to use"
        echo "  (example: https://github.com/user-name/nix-config.git)"
        read -p ": " repo_url
        
        # Set flake url to remote url if nothing entered
        if [ -z "$repo_url" ]; then
            repo_url=$https_url
        fi
    fi
    echo "Using flake repository $repo_url"
    echo
fi


# Handle branch selection
if [ -n "$ENV_BRANCH" ]; then
    branch="$ENV_BRANCH"
    echo "Using branch from .env file: $branch"
    echo
else
    # Select branch to use in repo
    read -p "Enter Branch to use in repository [master]: " branch
    echo
    # Set default branch to master if empty
    if [ -z "$branch" ]; then
        branch="master"
    fi
    echo "Branch $branch"
    echo
fi

# Get the latest commit hash to ensure the latest is pulled by nix
echo "Fetching latest commit hash for branch $branch..."
latest_commit=$(git ls-remote $repo_url "refs/heads/$branch" | cut -f1)
# latest_commit=$(git ls-remote "$repo_url" "refs/heads/$branch" | cut -f1)

echo

if [ -z "$latest_commit" ]; then
    echo "Error: Could not fetch latest commit for branch $branch"
    exit 1
fi

echo "Using commit: $latest_commit"
flake_base_url="git+$repo_url?rev=$latest_commit"
echo

# Fetch available hostnames from flake
echo "Fetching available hostnames from flake..."
echo
echo "================================================================"
echo

# Get the flake outputs and extract nixosConfigurations
available_hosts=$(nix flake show --json $flake_base_url 2>/dev/null | jq -r '.nixosConfigurations | keys[]' 2>/dev/null)

if [ -n "$available_hosts" ]; then
    echo "Available hostnames:"
    echo "-------------------------"
    echo "$available_hosts" | nl -w2 -s'. '
    echo
fi

# Prompt for hostname
read -p "Select a host by number or hostname: " selection

# Validate selection is not empty
if [ -z "$selection" ]; then
    echo "Error: Hostname cannot be empty"
    exit 1
fi

# Check if the input is a number (matches a line number)
if [[ "$selection" =~ ^[0-9]+$ ]]; then
    # Extract the corresponding hostname based on the number
    hostname=$(echo "$available_hosts" | sed -n "${selection}p")
else
    # Assume input is the hostname itself
    hostname="$selection"
fi
# Check if the hostname actually exists in the list
if ! echo "$available_hosts" | grep -Fxq "$hostname"; then
    echo "Invalid selection."
    exit 1
fi

echo "You selected host $hostname."
echo
echo "================================================================"
echo


# LXC Identification
echo "Select NXC to Update:"
echo "-------------------------------------"

# Handle Proxmox host
if [ -n "$ENV_PVE_HOST" ]; then
    pve_host="$ENV_PVE_HOST"
    echo "Using Proxmox host from .env file: $pve_host"
else
    read -p "Enter Proxmox VE hostname or IP: " pve_host
fi
echo

echo "NixOS LXCs on this Proxmox host:"
nixos_lxcs=()
valid_vmids=()

for vmid in $(ssh "root@$pve_host" "pct list" | awk 'NR>1 {print $1}'); do
    if ssh "root@$pve_host" "pct config '$vmid'" | grep -q "ostype: nixos"; then
        name=$(ssh "root@$pve_host" "pct list" | awk -v id="$vmid" '$1==id {print $3}')
        nixos_lxcs+=("$vmid:$name")
        valid_vmids+=("$vmid")
    fi
done

for lxc in "${nixos_lxcs[@]}"; do
    vmid_display="${lxc%%:*}"
    name="${lxc#*:}"
    echo "$vmid_display. $name"
done

# Prompt user for VMID selection
while true; do
    echo
    read -p "Enter the VMID for the NXC to be updated: " vmid
    
    # Check if input is blank
    if [[ -z "$vmid" ]]; then
        echo "Error: VMID cannot be blank. Please try again."
        continue
    fi
    
    # Check if VMID exists in our valid list
    if [[ " ${valid_vmids[*]} " == *" $vmid "* ]]; then
        break
    else
        echo "Error: '$vmid' is not a valid VMID from the list. Please try again."
    fi
done

echo "You selected VMID $vmid"
echo
echo "================================================================"
echo

echo "Checking if $hostname has Tailscale enabled..."
    
tailscale_check=$(nix eval --json "$flake_base_url#nixosConfigurations.$hostname.config.services.tailscale.enable" 2>/dev/null || echo "false")

if [ "$tailscale_check" = "true" ]; then
    echo "✓ Tailscale is enabled for $hostname"
    # Add TUN device configuration
    echo "Configuring TUN device access..."
    ssh "root@$pve_host" "grep -q 'lxc.cgroup2.devices.allow: c 10:200 rwm' /etc/pve/lxc/$vmid.conf || echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> /etc/pve/lxc/$vmid.conf; grep -q 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' /etc/pve/lxc/$vmid.conf || echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/$vmid.conf"
else
    echo "✗ Tailscale is not enabled for $hostname"
    echo "Skipping TUN device passthrough"
fi
echo
echo "================================================================"
echo


# Ensure NixOS LXC container is running
echo "Ensure LXC container is started for configuration update..."
if ssh "root@$pve_host" "pct status $vmid | grep -q running"; then
    echo "LXC is running!"
else
    echo "LXC is not running! Staring LXC......."
    ssh "root@$pve_host" "pct start $vmid"
    # Wait for container to be ready
    echo "Waiting for container to start..."
    sleep 10
    echo

    # Check if container is running
    while ! ssh "root@$pve_host" "pct status $vmid | grep -q running"; do
        echo "Waiting for container to be ready..."
        echo
        sleep 5
    done
    echo "LXC is running!"
fi

echo
echo "Container is running. Beginning rebuild with $hostname configuration..."
echo

# Get the container's IP address
container_ip=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/ip -4 addr show eth0" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
if [ -z "$container_ip" ]; then
    echo "Warning: Could not determine container IP. You may need to check manually."
    container_ip="UNKNOWN"
    exit 1
fi

# Get current generation number before rebuild
initial_generation=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/readlink /nix/var/nix/profiles/system | grep -o '[0-9]*'" 2>/dev/null)
echo "Initial Nix Configurtion Generation: $initial_generation"
echo

# Run nixos-rebuild
echo "Running nixos-rebuild switch on host $vmid $hostname"
ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/bash -c 'export PATH=\"/run/current-system/sw/bin:/nix/var/nix/profiles/system/sw/bin:\$PATH\" && nixos-rebuild switch --flake \"$flake_base_url#$hostname\" --impure --show-trace'"
# ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/source /etc/set-environment && nixos-rebuild switch --flake '$flake_base_url#$hostname' --impure --show-trace"
# pct exec 128 -- /run/current-system/sw/bin/nixos-rebuild switch --flake 'git+https://git.montycasa.net/patrick/nix-config.git#$omnitools' --impure --show-trace --option extra-sandbox-paths '/run/current-system/sw'
# pct exec 128 -- /run/current-system/sw/bin/bash -c 'export PATH="/run/current-system/sw/bin:/nix/var/nix/profiles/system/sw/bin:$PATH" && nixos-rebuild switch --flake "git+https://git.montycasa.net/patrick/nix-config.git#omnitools" --impure --show-trace'
echo
echo "================================================================"
echo

# Final verification
echo "Performing final verification..."
if ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/systemctl is-system-running" 2>/dev/null; then
    system_status=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/systemctl is-system-running" 2>/dev/null)
    echo "System status: $system_status"
    echo
fi

if ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/hostname" 2>/dev/null | grep -q "$hostname"; then
    echo "✓ Hostname verification successful"
    final_success=true
else
    echo "✗ Hostname verification failed"
    final_success=false
fi

# Get current generation
current_generation=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/readlink /nix/var/nix/profiles/system | grep -o '[0-9]*'" 2>/dev/null)
if [ -n "$current_generation" ] && [ "$current_generation" -gt "$initial_generation" ]; then
    echo "✓ Generation changed from $initial_generation to $current_generation - rebuild successful"
elif [ -n "$current_generation" ]; then
    echo "Generation check $check_count: Still at generation $current_generation (waiting for change from $initial_generation)"
    final_success=false
else
    echo "Generation check $check_count: Could not read generation"
fi

# Verify the container is still running on Proxmox
if ssh "root@$pve_host" "pct status $vmid | grep -q running"; then
    echo "✓ Container is running on Proxmox"
else
    echo "✗ Warning: Container may not be running properly"
    final_success=false
fi
echo
echo "================================================================"
echo

if [ "$final_success" = true ]; then
    echo "🎉 NixOS LXC container update completed successfully!"
    echo "Container ID: $vmid"
    echo "Hostname: $hostname"
    echo "IP Address: $container_ip"
    echo "Template: $template_filename"
    echo
else
    echo "⚠️  Container update completed with warnings."
    echo "Please check the container manually:"
    echo "Container ID: $vmid"
    echo "Expected hostname: $hostname"
    echo "IP Address: $container_ip"
    echo
fi
echo
echo "======================================================================"
echo
exit 1