# Example configuration for Proxmox Storage Monitor
#
# OPTION 1: Using SOPS for secrets management (RECOMMENDED)
#
# extra-services.proxmox-storage-monitor = {
#   enable = true;
#
#   # List of Proxmox hosts to monitor
#   proxmoxHosts = [
#     {
#       name = "pve1";
#       host = "pve1.local";  # or IP address
#       user = "root@pam";
#       tokenId = "monitoring";
#       tokenSecretFile = config.sops.secrets."proxmox/pve1/token".path;
#       port = 8006;  # Default Proxmox port
#       verifySsl = false;  # Set to true if using valid SSL cert
#     }
#     {
#       name = "pve2";
#       host = "pve2.local";
#       user = "root@pam";
#       tokenId = "monitoring";
#       tokenSecretFile = config.sops.secrets."proxmox/pve2/token".path;
#     }
#   ];
#
#   # Gotify notification settings
#   gotify = {
#     url = "https://gotify.example.com";
#     tokenFile = config.sops.secrets."gotify/token".path;
#   };
#
#   # Alert when storage usage exceeds this percentage
#   storageThreshold = 80;
#
#   # How often to check (systemd timer format)
#   checkInterval = "hourly";  # or "daily", "*:0/30" for every 30 min, etc.
# };
#
# # SOPS secrets configuration (add to your configuration.nix):
# sops.secrets = {
#   "proxmox/pve1/token" = {
#     sopsFile = ./secrets.yaml;
#     owner = "root";
#     mode = "0400";
#   };
#   "proxmox/pve2/token" = {
#     sopsFile = ./secrets.yaml;
#     owner = "root";
#     mode = "0400";
#   };
#   "gotify/token" = {
#     sopsFile = ./secrets.yaml;
#     owner = "root";
#     mode = "0400";
#   };
# };
#
#
# OPTION 2: Direct secrets (NOT RECOMMENDED for production)
#
# extra-services.proxmox-storage-monitor = {
#   enable = true;
#
#   proxmoxHosts = [
#     {
#       name = "pve1";
#       host = "pve1.local";
#       user = "root@pam";
#       tokenId = "monitoring";
#       tokenSecret = "your-secret-token-here";  # Direct secret (not recommended)
#     }
#   ];
#
#   gotify = {
#     url = "https://gotify.example.com";
#     token = "your-gotify-app-token";  # Direct secret (not recommended)
#   };
#
#   storageThreshold = 80;
#   checkInterval = "hourly";
# };
#
# SETUP INSTRUCTIONS:
#
# 1. Create Proxmox API Token:
#    - Log into Proxmox web UI
#    - Go to Datacenter → Permissions → API Tokens
#    - Click "Add" and create a token (e.g., "monitoring")
#    - Save the token secret (you won't be able to see it again)
#    - Grant the token appropriate permissions:
#      * VM.Audit - Required to list VMs and LXCs
#      * VM.Monitor - Required to access QEMU guest agent (for VM disk monitoring)
#      * Datastore.Audit - Optional, for storage info
#
# 1b. Enable QEMU Guest Agent on VMs (required for VM disk monitoring):
#    - Install qemu-guest-agent inside each VM:
#      * Debian/Ubuntu: apt install qemu-guest-agent
#      * RHEL/CentOS: yum install qemu-guest-agent
#      * Windows: Install from VirtIO ISO
#    - Enable agent in VM options (Proxmox UI):
#      VM → Options → QEMU Guest Agent → Edit → Enable
#    - Note: LXC containers don't need guest agent, they work automatically
#
# 2. Set up Gotify:
#    - Install/access your Gotify server
#    - Create a new application for Proxmox alerts
#    - Copy the application token
#
# 3. Configure SOPS (if using SOPS):
#    a. Create or edit your secrets.yaml file:
#       sops secrets.yaml
#
#    b. Add your secrets in this format:
#       proxmox:
#         pve1:
#           token: "your-pve1-token-secret-here"
#         pve2:
#           token: "your-pve2-token-secret-here"
#       gotify:
#         token: "your-gotify-token-here"
#
#    c. The secrets will be decrypted at runtime to /run/secrets/
#       and the module will read them from there
#
# 4. Add the configuration above to your NixOS configuration
#
# 5. Rebuild your system:
#    sudo nixos-rebuild switch
#
# 6. Test the service manually:
#    sudo systemctl start proxmox-storage-monitor
#    sudo journalctl -u proxmox-storage-monitor -f
#
#    Check that secrets are loaded:
#    sudo cat /run/proxmox-storage-monitor/env  # Should show environment variables
#
# 7. Check timer status:
#    sudo systemctl status proxmox-storage-monitor.timer
#    sudo systemctl list-timers | grep proxmox
#
# ENVIRONMENT VARIABLES:
# The module creates environment variables for each host:
# - PVE_TOKEN_PVE1 (for host named "pve1")
# - PVE_TOKEN_PVE2 (for host named "pve2")
# - GOTIFY_TOKEN
#
# Host names are converted to uppercase and special characters (-, ., space)
# are replaced with underscores for environment variable names.

{ }
