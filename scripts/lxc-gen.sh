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
# Fetch available hostnames from flake
echo "Fetching available hostnames from flake..."
echo

# Get the flake outputs and extract nixosConfigurations
available_hosts=$(nix flake show --json git+https://git.montycasa.net/patrick/nix-config.git?ref=$branch 2>/dev/null | jq -r '.nixosConfigurations | keys[]' 2>/dev/null)

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
  --flake "git+https://git.montycasa.net/patrick/nix-config.git?ref=$branch#lxc-base" \
  -o "$output_dir"
echo
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
ssh "root@$container_ip" "mkdir -p /home/patrick/.config/sops/age"
scp ~/.config/sops/age/keys.txt "root@$container_ip:/home/patrick/.config/sops/age/keys.txt"

# Run nixos-rebuild in background and monitor with dinosay check
run_nixos_rebuild() {
    echo "Starting nixos-rebuild in background..."
    
    # Run nixos-rebuild in background
    nixos-rebuild switch \
        --flake "git+https://git.montycasa.net/patrick/nix-config.git?ref=$branch#$hostname" \
        --target-host "root@$container_ip" \
        --impure \
        --option connect-timeout 60 \
        --show-trace &
    
    rebuild_pid=$!
    echo "Rebuild process started with PID: $rebuild_pid"
    
    # Monitor the rebuild process
    local timeout=900  # 15 minutes
    local elapsed=0
    local check_interval=20
    
    while [ $elapsed -lt $timeout ]; do
        echo "Checking container status... (${elapsed}s elapsed)"
        
        # Check if container is responsive
        if ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 "root@$container_ip" "echo 'Container is up'" >/dev/null 2>&1; then
            echo "Container is responsive, checking if rebuild completed..."
            
            # Check if dinosay package is NOT installed (indicates successful rebuild)
            if ssh -o ConnectTimeout=10 "root@$container_ip" "nix-env -q | grep -q dinosay" 2>/dev/null; then
                echo "dinosay package found - rebuild still in progress or base template active"
            else
                echo "dinosay package NOT found - this indicates successful rebuild!"
                
                # Double-check that the system is actually ready
                if ssh -o ConnectTimeout=10 "root@$container_ip" "systemctl is-system-running --wait" >/dev/null 2>&1; then
                    echo "System is fully operational - rebuild completed successfully!"
                    
                    # Kill the background process if it's still running
                    if kill -0 $rebuild_pid 2>/dev/null; then
                        echo "Terminating background rebuild process..."
                        kill $rebuild_pid 2>/dev/null
                        wait $rebuild_pid 2>/dev/null
                    fi
                    
                    return 0
                else
                    echo "System not fully ready yet, continuing to monitor..."
                fi
            fi
        else
            echo "Container not responding (likely restarting during rebuild)"
        fi
        
        # Check if rebuild process is still running
        if ! kill -0 $rebuild_pid 2>/dev/null; then
            echo "Background rebuild process has finished"
            wait $rebuild_pid 2>/dev/null
            local exit_code=$?
            
            # Even if the process exited, do a final check
            sleep 5
            if ssh -o ConnectTimeout=15 "root@$container_ip" "echo 'Final check'" >/dev/null 2>&1; then
                if ! ssh -o ConnectTimeout=10 "root@$container_ip" "nix-env -q | grep -q dinosay" 2>/dev/null; then
                    echo "Final verification: dinosay not found - rebuild successful!"
                    return 0
                fi
            fi
            
            echo "Rebuild process exited with code $exit_code"
            return $exit_code
        fi
        
        sleep $check_interval
        elapsed=$((elapsed + check_interval))
    done
    
    # Timeout reached
    echo "Rebuild timeout reached after $timeout seconds"
    
    # Kill the background process
    if kill -0 $rebuild_pid 2>/dev/null; then
        echo "Terminating rebuild process..."
        kill $rebuild_pid 2>/dev/null
        wait $rebuild_pid 2>/dev/null
    fi
    
    # Final attempt to check if it actually completed
    echo "Performing final verification..."
    sleep 5
    if ssh -o ConnectTimeout=15 "root@$container_ip" "echo 'Final check'" >/dev/null 2>&1; then
        if ! ssh -o ConnectTimeout=10 "root@$container_ip" "nix-env -q | grep -q dinosay" 2>/dev/null; then
            echo "Final verification: dinosay not found - rebuild appears to have completed despite timeout!"
            return 0
        fi
    fi
    
    echo "Rebuild timed out and verification failed."
    return 124
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