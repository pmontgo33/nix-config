#!/bin/bash

# Clear the terminal
clear

# Welcome message
echo "This script will create a NixOS LXC container based on a host from your flake.nix"
echo "Press Enter to continue..."
read
echo

# Select branch to use in repo
read -p "Enter Branch to use in Repository [master]: " branch
echo
# Set default branch to master if empty
if [ -z "$branch" ]; then
    branch="master"
fi
echo "Branch $branch"
echo

# Get the latest commit hash to ensure the latest is pulled by nix
echo "Fetching latest commit hash for branch $branch..."
latest_commit=$(git ls-remote https://git.montycasa.net/patrick/nix-config.git "refs/heads/$branch" | cut -f1)
echo

if [ -z "$latest_commit" ]; then
    echo "Error: Could not fetch latest commit for branch $branch"
    exit 1
fi

echo "Using commit: $latest_commit"
flake_base_url="git+https://git.montycasa.net/patrick/nix-config.git?rev=$latest_commit"
echo

# Fetch available hostnames from flake
echo "Fetching available hostnames from flake..."
echo

# Get the flake outputs and extract nixosConfigurations
available_hosts=$(nix flake show --json $flake_base_url 2>/dev/null | jq -r '.nixosConfigurations | keys[]' 2>/dev/null)

if [ -n "$available_hosts" ]; then
    echo "Available hostnames:"
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

# Get next available VMID
next_vmid=$(ssh "root@stark" "pvesh get /cluster/nextid 2>/dev/null" 2>/dev/null)

# Additional prompts
read -p "Enter Proxmox VE hostname or IP: " pve_host
read -p "Enter VMID [$next_vmid]: " vmid

# Set VMID to next_vmid if empty
if [ -z "$vmid" ]; then
    vmid="$next_vmid"
fi

read -p "Enter Memory (MB): " memory
read -p "Enter Cores: " cores
read -p "Enter Disk Size (GB): " disk_size
read -p "Enter IPv4 CIDR Address (e.g. 192.168.86.$vmid/24) [default: dhcp]: " ip_address
echo

# Set default IP to dhcp if empty
if [ -z "$ip_address" ]; then
    ip_address="dhcp"
fi

# Prompt for gateway if IP is not dhcp
gateway=""
if [[ "$ip_address" != "dhcp" && "$ip_address" != "DHCP" ]]; then
    read -p "Enter Gateway: " gateway
fi

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
echo "Copying template to Proxmox host..."
scp "$output_dir/tarball/$template_filename" "root@$pve_host:/var/lib/vz/template/cache/lxc-base-$template_filename"
echo

# Build network configuration
net_config="name=eth0,bridge=vmbr0,ip=$ip_address"
if [[ "$ip_address" != "dhcp" && "$ip_address" != "DHCP" && -n "$gateway" ]]; then
    net_config="$net_config,gw=$gateway"
fi

# Create the container
echo "Creating container on Proxmox host..."
ssh "root@$pve_host" "pct create $vmid /var/lib/vz/template/cache/lxc-base-$template_filename --hostname $hostname --memory $memory --cores $cores --rootfs local-zfs:$disk_size --unprivileged 1 --features nesting=1 --onboot 1 --tags nixos --net0 $net_config"
echo

# Add TUN device configuration
echo "Configuring TUN device access..."
ssh "root@$pve_host" "grep -q 'lxc.cgroup2.devices.allow: c 10:200 rwm' /etc/pve/lxc/$vmid.conf || echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> /etc/pve/lxc/$vmid.conf; grep -q 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' /etc/pve/lxc/$vmid.conf || echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/$vmid.conf"
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

echo "Container is running. Beginning rebuild with hostname configuration..."
echo

# Get the container's IP address for nixos-rebuild target
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
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@$container_ip" "mkdir -p /home/patrick/.config/sops/age"
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null /home/patrick/.config/sops/age/keys.txt "root@$container_ip:/home/patrick/.config/sops/age/keys.txt"

# Run nixos-rebuild in background and monitor with generation check
run_nixos_rebuild() {
    echo "Starting nixos-rebuild in background..."
    
    # Get current generation number before rebuild
    echo "Getting current generation number..."
    initial_generation=$(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@$container_ip" "readlink /nix/var/nix/profiles/system | grep -o '[0-9]*'" 2>/dev/null)
    
    if [ -z "$initial_generation" ]; then
        echo "Warning: Could not determine initial generation number"
        initial_generation=0
    else
        echo "Initial generation: $initial_generation"
    fi
    
    # Run nixos-rebuild in background
    nixos-rebuild switch \
        --flake "$flake_base_url#$hostname" \
        --target-host "root@$container_ip" \
        --impure \
        --option connect-timeout 60 \
        --option ssh-config-options "StrictHostKeyChecking=no UserKnownHostsFile=/dev/null" \
        --show-trace &
    
    rebuild_pid=$!
    echo "Rebuild process started with PID: $rebuild_pid"
    echo "Monitoring generation changes every 20 seconds..."
    echo
    
    # Monitor the rebuild process
    check_count=0
    max_checks=30  # 10 minutes max (30 * 20 seconds)
    
    while kill -0 $rebuild_pid 2>/dev/null; do
        sleep 20
        check_count=$((check_count + 1))
        
        # Get current generation
        current_generation=$(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@$container_ip" "readlink /nix/var/nix/profiles/system | grep -o '[0-9]*'" 2>/dev/null)
        
        if [ -n "$current_generation" ] && [ "$current_generation" -gt "$initial_generation" ]; then
            echo "✓ Generation changed from $initial_generation to $current_generation - rebuild likely completed"
            break
        elif [ -n "$current_generation" ]; then
            echo "Generation check $check_count: Still at generation $current_generation (waiting for change from $initial_generation)"
        else
            echo "Generation check $check_count: Could not read generation (container may be rebooting)"
        fi
        
        # Safety timeout
        if [ $check_count -ge $max_checks ]; then
            echo "Warning: Reached maximum monitoring time (10 minutes)"
            break
        fi
    done
    
    # Wait for the background process to complete and get exit status
    wait $rebuild_pid
    return $?
}

# Run the rebuild
if run_nixos_rebuild; then
    rebuild_success=true
else
    rebuild_success=false
fi

echo

# Final verification
echo "Performing final verification..."
if ssh -o ConnectTimeout=15 "root@$container_ip" "systemctl is-system-running" 2>/dev/null; then
    system_status=$(ssh -o ConnectTimeout=15 "root@$container_ip" "systemctl is-system-running" 2>/dev/null)
    echo "System status: $system_status"
fi

if ssh -o ConnectTimeout=15 "root@$container_ip" "hostname" 2>/dev/null | grep -q "$hostname"; then
    echo "✓ Hostname verification successful"
    final_success=true
else
    echo "✗ Hostname verification failed"
    final_success=false
fi

# Verify the container is still running on Proxmox
if ssh "root@$pve_host" "pct status $vmid | grep -q running"; then
    echo "✓ Container is running on Proxmox"
else
    echo "✗ Warning: Container may not be running properly"
    final_success=false
fi

echo
if [ "$final_success" = true ]; then
    echo "🎉 NixOS LXC container setup completed successfully!"
    echo "Container ID: $vmid"
    echo "Hostname: $hostname"
    echo "IP Address: $container_ip"
    echo "Template: $template_filename"
    echo
    echo "You can access it with: ssh root@$container_ip"
else
    echo "⚠️  Container setup completed with warnings."
    echo "Please check the container manually:"
    echo "Container ID: $vmid"
    echo "Expected hostname: $hostname"
    echo "IP Address: $container_ip"
    echo
    echo "You can try accessing it with: ssh root@$container_ip"
    echo "Or check via Proxmox: ssh root@$pve_host 'pct enter $vmid'"
fi
exit 1