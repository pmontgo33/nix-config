#!/bin/bash

# Clear the terminal
clear

# Welcome message
echo "This script will create a NixOS LXC container based on a host from your flake.nix"
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


# LXC Basics
echo "LXC Basic Configuration Options:"
echo "-------------------------------------"

# Handle Proxmox host
if [ -n "$ENV_PVE_HOST" ]; then
    pve_host="$ENV_PVE_HOST"
    echo "Using Proxmox host from .env file: $pve_host"
else
    read -p "Enter Proxmox VE hostname or IP: " pve_host
fi

# Get next available VMID
next_vmid=$(ssh "root@$pve_host" "pvesh get /cluster/nextid 2>/dev/null" 2>/dev/null)
read -p "Enter VMID [$next_vmid]: " vmid

# Set VMID to next_vmid if empty
if [ -z "$vmid" ]; then
    vmid="$next_vmid"
fi

# Handle Memory
if [ -n "$ENV_DEFAULT_MEMORY" ]; then
    read -p "Enter Memory (MB) [$ENV_DEFAULT_MEMORY]: " memory
    if [ -z "$memory" ]; then
        memory="$ENV_DEFAULT_MEMORY"
        echo "$memory MB"
    fi
else
    read -p "Enter Memory (MB): " memory
fi

# Handle Cores
if [ -n "$ENV_DEFAULT_CORES" ]; then
    read -p "Enter Cores [$ENV_DEFAULT_CORES]: " cores
    if [ -z "$cores" ]; then
        cores="$ENV_DEFAULT_CORES"
        echo "$cores cores"
    fi
else
    read -p "Enter Cores: " cores
fi

# Handle Disk Size
if [ -n "$ENV_DEFAULT_DISK_SIZE" ]; then
    read -p "Enter Disk Size (GB) [$ENV_DEFAULT_DISK_SIZE]: " disk_size
    if [ -z "$disk_size" ]; then
        disk_size="$ENV_DEFAULT_DISK_SIZE"
        echo "$disk_size GB"
    fi
else
    read -p "Enter Disk Size (GB): " disk_size
fi
echo
echo "================================================================"
echo

# Function to validate CIDR format
validate_cidr() {
    local ip="$1"
    # Check if it matches basic CIDR format (IP/prefix)
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        # Extract IP and prefix
        local ip_part=$(echo "$ip" | cut -d'/' -f1)
        local prefix=$(echo "$ip" | cut -d'/' -f2)
        
        # Validate IP octets (0-255)
        IFS='.' read -ra octets <<< "$ip_part"
        for octet in "${octets[@]}"; do
            if [[ $octet -lt 0 || $octet -gt 255 ]]; then
                return 1
            fi
        done
        
        # Validate prefix (0-32)
        if [[ $prefix -lt 0 || $prefix -gt 32 ]]; then
            return 1
        fi
        
        return 0
    else
        return 1
    fi
}

while true; do
    # IP Address selection with options
    echo "Network Configuration:"
    echo "---------------------------"
    echo "1) Static IP 192.168.86.$vmid/24 (Gateway: 192.168.86.1)"
    echo "2) DHCP"
    echo "Other enter custom Static IP address"
    echo
    
    # Handle default network option from .env
    if [ -n "$ENV_DEFAULT_NETWORK_OPTION" ]; then
        read -p "Select network option (1, 2) or enter custom IP address (e.g., 192.168.86.$vmid/24) [$ENV_DEFAULT_NETWORK_OPTION]: " ip_selection
        if [ -z "$ip_selection" ]; then
            ip_selection="$ENV_DEFAULT_NETWORK_OPTION"
            echo "Using default network option from .env: $ip_selection"
        fi
    else
        read -p "Select network option (1, 2) or enter custom IP address (e.g., 192.168.86.$vmid/24): " ip_selection
    fi

    case "$ip_selection" in
        1)
            ip_address="192.168.86.$vmid/24"
            gateway="192.168.86.1"
            break
            ;;
        2)
            ip_address="dhcp"
            gateway=""
            break
            ;;
        *)
            # Check if it's a custom IP address
            if validate_cidr "$ip_selection"; then
                ip_address="$ip_selection"
                # Prompt for gateway for custom IP
                read -p "Enter Gateway: " gateway
                break
            else
                echo "Invalid selection or IP format. Please enter 1, 2, or a valid CIDR address (e.g., 192.168.86.$vmid/24)"
                echo
            fi
            ;;
    esac
done

echo
echo "================================================================"
echo

# Run nixos-generate command with the base template
echo "Generating NixOS LXC Base template (this may take several minutes)..."
output_dir=~/lxc-templates/lxc-base-$(date +%Y%m%d)
nixos-generate -f proxmox-lxc \
  --flake "$flake_base_url#lxc-base" \
  -o "$output_dir"
echo
sleep 5
echo "NixOS LXC Base template generation complete!"
echo

# Find the template filename
template_filename=$(find "$output_dir/tarball" -name "*.tar.xz" -exec basename {} \; 2>/dev/null | head -n1)

if [ -z "$template_filename" ]; then
    echo "Error: Could not find template file in $output_dir/tarball"
    exit 1
fi

# Copy template to Proxmox host
echo "Copying template to Proxmox host $pve_host..."
scp "$output_dir/tarball/$template_filename" "root@$pve_host:/var/lib/vz/template/cache/lxc-base-$template_filename"
echo

# Build network configuration
net_config="name=eth0,bridge=vmbr0,ip=$ip_address"
if [[ "$ip_address" != "dhcp" && "$ip_address" != "DHCP" && -n "$gateway" ]]; then
    net_config="$net_config,gw=$gateway"
fi

# Create the container
echo "Creating container on Proxmox host $pve_host..."
ssh "root@$pve_host" "pct create $vmid /var/lib/vz/template/cache/lxc-base-$template_filename --hostname $hostname --memory $memory --cores $cores --rootfs local-zfs:$disk_size --unprivileged 1 --features nesting=1 --onboot 1 --tags nixos --net0 $net_config"
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


# Start NixOS LXC Base container
echo "Starting LXC container for configuration phase..."
ssh "root@$pve_host" "pct start $vmid"
echo

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

echo "Container is running. Beginning rebuild with $hostname configuration..."
echo

# Get the container's IP address
if [[ "$ip_address" == "dhcp" || "$ip_address" == "DHCP" ]]; then
    # For DHCP, we need to get the assigned IP from Proxmox
    container_ip=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/ip -4 addr show eth0" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
    if [ -z "$container_ip" ]; then
        echo "Warning: Could not determine container IP. You may need to check manually."
        container_ip="UNKNOWN"
        exit 1
    fi
else
    # Extract IP from CIDR notation (remove /XX)
    container_ip=$(echo "$ip_address" | cut -d'/' -f1)
fi

echo "Container IP: $container_ip"
echo

echo "Copying SOPS key to container..."
ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/mkdir -p /etc/sops/age"

# Use SOPS key path from .env or default
if [ -n "$ENV_SOPS_KEY_PATH" ]; then
    sops_key_path="$ENV_SOPS_KEY_PATH"
    echo "Using SOPS key path from .env: $sops_key_path"
else
    sops_key_path="/etc/sops/age/keys.txt"
fi

cat "$sops_key_path" | ssh root@$pve_host "pct exec $vmid -- /run/current-system/sw/bin/tee /etc/sops/age/keys.txt > /dev/null"
echo

# Get current generation number before rebuild
initial_generation=$(ssh "root@$pve_host" "pct exec $vmid -- /run/current-system/sw/bin/readlink /nix/var/nix/profiles/system | grep -o '[0-9]*'" 2>/dev/null)
echo "Initial Nix Configurtion Generation: $initial_generation"
echo

# Run nixos-rebuild
echo "Running nixos-rebuild switch on new host $hostname"
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
    echo "🎉 NixOS LXC container setup completed successfully!"
    echo "Container ID: $vmid"
    echo "Hostname: $hostname"
    echo "IP Address: $container_ip"
    echo "Template: $template_filename"
    echo
else
    echo "⚠️  Container setup completed with warnings."
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