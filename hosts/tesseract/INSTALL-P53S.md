# ThinkPad P53s Installation Guide
## NixOS Installation via nixos-anywhere

This guide covers installing NixOS on the Lenovo ThinkPad P53s (i7-8665U, 32GB RAM, 1TB NVMe, NVIDIA Quadro P520) using nixos-anywhere for automated deployment.

---

## Prerequisites

### On Your Current Machine (Installation Host)
- [ ] NixOS configuration repository cloned and up-to-date
- [ ] SSH access configured
- [ ] nixos-anywhere installed: `nix-shell -p nixos-anywhere`

### On the P53s (Target Machine)
- [ ] Boot from NixOS minimal ISO (download from https://nixos.org/download.html)
- [ ] Connect to network (ethernet recommended for stability)
- [ ] Note the IP address assigned to the P53s

---

## Phase 1: Prepare the P53s for Installation

### Step 1: Boot P53s into NixOS Live Environment

1. Download the latest NixOS minimal ISO
2. Create a bootable USB drive:
   ```bash
   # On your current machine
   sudo dd if=nixos-minimal-XX.XX-x86_64-linux.iso of=/dev/sdX bs=4M status=progress
   sudo sync
   ```
3. Boot the P53s from the USB drive (press F12 at boot for boot menu)
4. Select "NixOS" from the boot menu

### Step 2: Configure Network on P53s

Once booted into the live environment:

```bash
# If using WiFi (ethernet preferred):
sudo systemctl start wpa_supplicant
wpa_cli

# In wpa_cli:
> add_network
> set_network 0 ssid "YourSSID"
> set_network 0 psk "YourPassword"
> enable_network 0
> quit

# Verify network connectivity
ping -c 3 google.com

# Get the P53s IP address
ip addr show
# Note the IP address (e.g., 192.168.1.100)
```

### Step 3: Enable SSH on P53s

```bash
# Set a temporary root password
sudo passwd

# Start SSH service
sudo systemctl start sshd

# Verify SSH is running
sudo systemctl status sshd
```

### Step 4: Verify Hardware Details (IMPORTANT)

Before proceeding, verify the actual hardware configuration:

```bash
# Check PCI Bus IDs for GPUs
lspci | grep -E "VGA|3D"
# Expected output similar to:
# 00:02.0 VGA compatible controller: Intel Corporation ...
# 01:00.0 3D controller: NVIDIA Corporation ...

# Note the bus IDs (00:02.0 and 01:00.0 in example above)
# These will be needed if different from configuration

# Check NVMe device name
lsblk
# Should show nvme0n1 (or similar)

# Check WiFi chipset
lspci | grep -i network
# Note the WiFi model

# Check CPU
lscpu | grep "Model name"
# Should show: Intel(R) Core(TM) i7-8665U

# Check RAM
free -h
# Should show approximately 32GB
```

**IMPORTANT:** If the PCI Bus IDs are different from what's in the configuration, note them down. You'll need to update the configuration before proceeding.

---

## Phase 2: Update Configuration (If Needed)

### Step 5: Update PCI Bus IDs (On Installation Host)

If the PCI Bus IDs from Step 4 are different, update the configuration:

```bash
# On your current machine
cd ~/nix-config
nano hosts/tesseract/configuration.nix
```

Find lines 171-172 and update with actual values from lspci:
```nix
intelBusId = "PCI:0:2:0";      # Use format PCI:X:Y:Z from lspci XX:YY.Z
nvidiaBusId = "PCI:1:0:0";     # Update if different
```

### Step 6: Commit Configuration Updates

```bash
# On your current machine
cd ~/nix-config
git add hosts/tesseract/configuration.nix
git commit -m "Update tesseract config for P53s hardware"
git push
```

---

## Phase 3: Install NixOS via nixos-anywhere

### Step 7: Run nixos-anywhere

From your current machine, run the installation:

```bash
# Replace 192.168.1.100 with the P53s IP address from Step 2
# Replace /path/to/ssh-key with your SSH key path if needed

nixos-anywhere --flake .#tesseract root@192.168.1.100

# OR if using SSH key authentication:
nixos-anywhere --flake .#tesseract --ssh-key ~/.ssh/id_ed25519 root@192.168.1.100
```

### Step 8: Set LUKS Encryption Password

During installation, you'll be prompted to enter a LUKS encryption password:
- Choose a strong password
- **WRITE IT DOWN** - you'll need it on every boot
- This password will unlock the encrypted root partition

### Step 9: Wait for Installation to Complete

The installation process will:
1. Partition the 1TB NVMe drive
2. Set up LUKS encryption
3. Create btrfs subvolumes
4. Install NixOS with your configuration
5. Reboot the system

This typically takes 10-30 minutes depending on network speed.

---

## Phase 4: First Boot Configuration

### Step 10: First Boot

1. After installation completes, the P53s will reboot
2. Remove the USB drive
3. At the LUKS prompt, enter your encryption password
4. The system will boot into the login screen

### Step 11: Login and Verify System

Login as your user (patrick) and verify basic functionality:

```bash
# Check NixOS version
nixos-version

# Verify hardware
lspci | grep -E "VGA|3D"
lscpu | grep "Model name"
free -h

# Check disk layout
lsblk
df -h

# Verify network
ping -c 3 google.com
```

### Step 12: Configure TPM2 Auto-Unlock

This allows the system to automatically unlock LUKS on boot using the TPM chip:

```bash
# Enroll TPM2 for auto-unlock
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2

# Verify enrollment
sudo systemd-cryptenroll /dev/nvme0n1p2

# You should see "tpm2" listed as an enrolled method
```

**Important:** Password fallback is automatically enabled. If TPM unlock fails (e.g., after BIOS update), you can still enter your password manually.

### Step 13: Calculate and Set Hibernation Swap Offset

```bash
# Calculate the swap file offset
sudo btrfs inspect-internal map-swapfile -r /swap/swapfile

# This will output a number like: 46005252
# Copy this number
```

Now update the configuration with the correct offset:

```bash
# On the P53s or your installation host
cd ~/nix-config
nano hosts/tesseract/configuration.nix
```

Find line 47 and update with your actual offset:
```nix
"resume_offset=XXXXX"    # Replace XXXXX with the number from above
```

Apply the change:
```bash
# Commit and push
git add hosts/tesseract/configuration.nix
git commit -m "Update resume_offset for P53s swap file"
git push  # If on P53s and git configured

# Rebuild system
sudo nixos-rebuild switch --flake .#tesseract
```

---

## Phase 5: Hardware Verification and Testing

### Step 14: Verify GPU Configuration

```bash
# Check Intel GPU
glxinfo | grep "OpenGL renderer"
# Should show Intel UHD Graphics 620

# Check NVIDIA GPU with offload
nvidia-offload glxinfo | grep "OpenGL renderer"
# Should show NVIDIA Quadro P520

# Check NVIDIA status
nvidia-smi

# Verify NVIDIA Prime configuration
nvidia-settings
# Go to "PRIME Profiles" - should show "NVIDIA On-Demand" mode
```

### Step 15: Test Power Management

```bash
# Check TLP status
sudo tlp-stat -s

# Monitor power consumption
sudo powertop

# Check thermal status
sensors

# Verify throttled service is running
sudo systemctl status throttled

# Check for BD PROCHOT throttling (should be disabled by throttled)
sudo journalctl -u throttled | tail -20
```

### Step 16: Test Hibernate/Resume

**CRITICAL:** Test this thoroughly as NVIDIA power management was enabled.

```bash
# Test suspend first
systemctl suspend
# Wake the system (press power button or keyboard)
# Verify system resumes correctly

# Test hibernation
systemctl hibernate
# Power on the system
# Enter LUKS password if TPM unlock fails
# Verify all applications restored correctly

# Test suspend-then-hibernate (what happens on lid close)
# Close the lid and wait 2+ hours
# Open lid and verify system hibernated and resumes
```

**If hibernation fails:**
1. Check resume_offset is correct: `cat /proc/cmdline | grep resume_offset`
2. Check swap file exists: `ls -lh /swap/swapfile`
3. Check logs: `sudo journalctl -b -1` (previous boot)
4. Consider disabling NVIDIA power management if issues persist

### Step 17: Verify WiFi and Bluetooth

```bash
# Check WiFi
nmcli device status
nmcli device wifi list

# Connect to WiFi if needed
nmcli device wifi connect "SSID" password "PASSWORD"

# Check Bluetooth
bluetoothctl
> power on
> scan on
> devices
> quit

# Verify firmware loaded
dmesg | grep -i iwlwifi
```

### Step 18: Test Fingerprint Sensor

```bash
# Enroll fingerprint
fprintd-enroll

# Follow the prompts to scan your finger multiple times

# Test fingerprint login
# Lock the screen and try logging in with fingerprint
```

### Step 19: Verify Automatic Updates

```bash
# Check auto-upgrade service status
sudo systemctl status nixos-upgrade.timer

# View recent upgrade logs
sudo journalctl -u nixos-upgrade.service
```

### Step 20: Test Tailscale (If Used)

```bash
# Check Tailscale status
sudo tailscale status

# Authenticate if needed
sudo tailscale up

# Test connectivity to other machines on your tailnet
```

---

## Phase 6: Optional Optimizations

### Step 21: Optimize NVIDIA Power Management (Optional)

**Current Configuration:** Power management is DISABLED by default for stability.

Once you've confirmed hibernate/resume works reliably, you can enable advanced power management for better battery life:

```bash
cd ~/nix-config
nano hosts/tesseract/configuration.nix
```

Enable NVIDIA power management (lines 150-151):
```nix
powerManagement.enable = true;
powerManagement.finegrained = true;
```

And update kernel parameter (line 49):
```nix
"nvidia.NVreg_PreserveVideoMemoryAllocations=1"
```

Rebuild and test thoroughly:
```bash
sudo nixos-rebuild switch --flake .#tesseract

# Test hibernate multiple times
systemctl hibernate
# Power on and verify resume works

# If issues occur, revert the changes above
```

### Step 22: Test NVMe Power Management

Monitor for any NVMe issues:

```bash
# Check NVMe logs
sudo dmesg | grep nvme

# Monitor NVMe health
sudo nvme smart-log /dev/nvme0n1

# If experiencing NVMe stability issues, disable power saving:
# Edit configuration.nix line 48:
"nvme_core.default_ps_max_latency_us=0"  # 0 = disabled
```

### Step 23: Tune Undervoltaging (Advanced/Optional)

The throttled service is configured with defaults. For advanced tuning:

```bash
# Check current throttled configuration
cat /etc/throttled.conf

# Monitor temperatures under load
sudo watch -n 1 sensors

# For undervoltaging configuration, refer to:
# https://github.com/erpalma/throttled
# Note: i7-8665U may have locked voltage controls
```

---

## Phase 7: Final Checks

### Step 24: Verify All Services

```bash
# Check for failed services
systemctl --failed

# Check overall system status
systemctl status

# Check disk health
sudo btrfs filesystem show
sudo btrfs filesystem usage /
sudo fstrim -av
```

### Step 25: Backup LUKS Header

**IMPORTANT:** Backup the LUKS header in case of corruption:

```bash
# Backup LUKS header
sudo cryptsetup luksHeaderBackup /dev/nvme0n1p2 \
  --header-backup-file ~/luks-header-backup-tesseract.img

# Copy this file to a secure location (NOT on the encrypted drive)
# Store it on another machine or USB drive
```

### Step 26: Document Your Settings

Create a note with your configuration details:

```bash
# PCI Bus IDs
lspci | grep -E "VGA|3D" > ~/tesseract-hardware-info.txt

# Swap offset
echo "Swap offset: $(sudo btrfs inspect-internal map-swapfile -r /swap/swapfile)" \
  >> ~/tesseract-hardware-info.txt

# WiFi chipset
lspci | grep -i network >> ~/tesseract-hardware-info.txt

# LUKS UUID
sudo blkid /dev/nvme0n1p2 >> ~/tesseract-hardware-info.txt

# Save this file somewhere safe
```

---

## Troubleshooting

### LUKS Won't Unlock with TPM
- **Solution:** Enter password manually, then re-enroll TPM:
  ```bash
  sudo systemd-cryptenroll --wipe-slot=tpm2 /dev/nvme0n1p2
  sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2
  ```

### Hibernate Fails to Resume
- **Check:** Correct resume_offset in kernel parameters
- **Check:** Resume device is set to `/dev/mapper/cryptroot`
- **Try:** Disable NVIDIA power management (Step 21)

### NVIDIA GPU Not Working
- **Check:** PCI Bus IDs are correct
- **Check:** NVIDIA driver loaded: `lsmod | grep nvidia`
- **Try:** Switch to sync mode instead of offload mode

### WiFi Not Working
- **Check:** Firmware loaded: `dmesg | grep iwlwifi`
- **Try:** Different kernel version (uncomment LTS kernel in config)

### System Throttling Despite throttled Service
- **Check:** Service is running: `systemctl status throttled`
- **Check:** Logs: `journalctl -u throttled`
- **Note:** Some i7-8665U models have locked undervoltaging

### Poor Battery Life
- **Check:** TLP is running: `systemctl status tlp`
- **Monitor:** Power usage with `powertop`
- **Optimize:** Run `sudo powertop --auto-tune` (temporary)
- **Consider:** Reducing zram further or disabling NVIDIA when not needed

---

## Post-Installation Checklist

- [ ] System boots and prompts for LUKS password (or auto-unlocks with TPM)
- [ ] Login works (password and/or fingerprint)
- [ ] WiFi connects successfully
- [ ] Bluetooth works
- [ ] Intel GPU works (default graphics)
- [ ] NVIDIA GPU works with `nvidia-offload` command
- [ ] Suspend works correctly
- [ ] Hibernate works correctly
- [ ] Lid close triggers suspend-then-hibernate
- [ ] Audio works (PipeWire)
- [ ] Touchpad gestures work
- [ ] TrackPoint works
- [ ] External displays work
- [ ] USB ports work
- [ ] Thermal management is appropriate (no excessive throttling)
- [ ] Auto-updates are scheduled
- [ ] Tailscale connected (if used)
- [ ] LUKS header backed up to external storage
- [ ] Hardware info documented and saved

---

## Performance Baseline

After setup, run these benchmarks to establish a performance baseline:

```bash
# CPU performance
sysbench cpu --threads=8 run

# Memory performance
sysbench memory --threads=8 run

# Disk performance
sudo fio --name=random-write --ioengine=libaio --iodepth=4 \
  --rw=randwrite --bs=4k --direct=1 --size=1G --numjobs=4 --runtime=60 \
  --group_reporting --filename=/tmp/fio-test

# GPU performance
glxgears -info  # Intel
nvidia-offload glxgears -info  # NVIDIA
```

Save these results for comparison if you experience performance issues later.

---

## Regular Maintenance

### Weekly
- Check for failed systemd services: `systemctl --failed`
- Monitor disk usage: `df -h` and `btrfs filesystem usage /`

### Monthly
- Review system logs: `journalctl -p 3 -b`
- Check SMART status: `sudo nvme smart-log /dev/nvme0n1`
- Verify backups (if configured)

### Quarterly
- Test hibernate/resume functionality
- Run btrfs scrub: `sudo btrfs scrub start /`
- Update BIOS/firmware via `fwupdmgr` if available

---

## Congratulations!

Your ThinkPad P53s is now running NixOS with:
- ✅ Full disk encryption with TPM2 auto-unlock
- ✅ NVIDIA Quadro P520 on-demand graphics
- ✅ Optimized power management and thermals
- ✅ Hibernation support
- ✅ Automated system updates
- ✅ Security hardening

Enjoy your new system!
