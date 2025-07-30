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

# Run nixos-generate command with the provided hostname
echo "Generating NixOS LXC Base template for hostname: $hostname (this may take several minutes)..."
output_dir=~/lxc-templates/$hostname-$(date +%Y%m%d)
nixos-generate -f proxmox-lxc \
  --flake "git+https://git.montycasa.net/patrick/nix-config.git?ref=$branch#$hostname" \
  -o "$output_dir"
echo
echo "NixOS LXC template generation complete!"
echo

# Find the template filename
template_filename=$(find "$output_dir/tarball" -name "*.tar.xz" -exec basename {} \; 2>/dev/null | head -n1)

if [ -z "$template_filename" ]; then
    echo "Error: Could not find template file in $output_dir/tarball"
    exit 1
fi

# Copy template to Proxmox host
echo "Copying template to Proxmox host..."
scp "$output_dir/tarball/$template_filename" "root@$pve_host:/var/lib/vz/template/cache/$hostname-$template_filename"
echo

# Build network configuration
net_config="name=eth0,bridge=vmbr0,ip=$ip_address"
if [[ "$ip_address" != "dhcp" && "$ip_address" != "DHCP" && -n "$gateway" ]]; then
    net_config="$net_config,gw=$gateway"
fi

# Create the container
echo "Creating container on Proxmox host..."
ssh "root@$pve_host" "pct create $vmid /var/lib/vz/template/cache/$hostname-$template_filename --hostname $hostname --memory $memory --cores $cores --rootfs local-zfs:$disk_size --unprivileged 1 --features nesting=1 --onboot 1 --tags nixos --net0 $net_config"
echo

# Add TUN device configuration
echo "Configuring TUN device access..."
ssh "root@$pve_host" "grep -q 'lxc.cgroup2.devices.allow: c 10:200 rwm' /etc/pve/lxc/$vmid.conf || echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> /etc/pve/lxc/$vmid.conf; grep -q 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' /etc/pve/lxc/$vmid.conf || echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/$vmid.conf"
echo

echo "NixOS LXC container setup complete!"
echo "Container ID: $vmid"
echo "Hostname: $hostname"
echo "Template: $template_filename"