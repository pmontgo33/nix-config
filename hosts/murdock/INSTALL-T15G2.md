# ThinkPad T15 Gen 2 Installation Guide
## NixOS Installation via nixos-anywhere

This guide covers installing NixOS on the Lenovo ThinkPad T15 Gen 2 (i7-1185G7, 24GB RAM, Intel Iris Xe Graphics) using nixos-anywhere for automated deployment.

---

## Prerequisites

### On Your Current Machine (Installation Host)
- [ ] NixOS configuration repository cloned and up-to-date
- [ ] SSH access configured
- [ ] nixos-anywhere installed: `nix-shell -p nixos-anywhere`

### On the T15 Gen 2 (Target Machine)
- [ ] Boot from NixOS minimal ISO
- [ ] Connect to network (ethernet recommended)
- [ ] Note the IP address

---

## Phase 1: Prepare the T15 for Installation

### Step 1: Boot T15 into NixOS Live Environment

1. Download the latest NixOS minimal ISO
2. Create a bootable USB drive:
   ```bash
   sudo dd if=nixos-minimal-XX.XX-x86_64-linux.iso of=/dev/sdX bs=4M status=progress
   sudo sync
   ```
3. Boot from USB (press F12 at boot for boot menu)

### Step 2: Configure Network on T15

```bash
# If using WiFi:
sudo systemctl start wpa_supplicant
wpa_cli
> add_network
> set_network 0 ssid "YourSSID"
> set_network 0 psk "YourPassword"
> enable_network 0
> quit

ip addr show
# Note the IP address
```

### Step 3: Enable SSH on T15

```bash
sudo passwd
sudo systemctl start sshd
```

### Step 4: Verify Hardware Details

```bash
# Check NVMe device name
lsblk
# Should show nvme0n1

# Check CPU
lscpu | grep "Model name"
# Should show: Intel(R) Core(TM) i7-1185G7

# Check RAM
free -h
# Should show approximately 24GB

# Check Intel GPU PCI Bus ID
lspci | grep VGA
# Expected: 00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2
# The config assumes PCI:0:2:0 — update hardware.graphics if different

# Check USB IDs for Bluetooth and fingerprint reader
lsusb
# Note the vendor:product for the Bluetooth adapter and fingerprint reader
# Config assumes: Bluetooth 8087:0aaa, Fingerprint 06cb:00bd
# Update udev rules in configuration.nix if different
```

---

## Phase 2: Install NixOS via nixos-anywhere

### Step 5: Run nixos-anywhere

From your installation host:

```bash
nixos-anywhere --flake .#murdock root@<T15-IP>
```

### Step 6: Set LUKS Encryption Password

During installation you will be prompted for a LUKS password. Choose a strong one and write it down — you will need it at every boot until TPM2 is enrolled.

### Step 7: Wait for Installation to Complete

The installation will:
1. Partition the NVMe drive (GPT + ESP + LUKS)
2. Create btrfs subvolumes (root, home, nix, log, swap)
3. Install NixOS
4. Reboot

---

## Phase 3: First Boot and Configuration

### Step 8: First Boot

1. Remove the USB drive after reboot
2. Enter your LUKS password at the prompt
3. The system will boot to the SDDM login screen

### Step 9: Migrate Home Directories (BEFORE first GUI login)

**Do this before logging into the desktop** to avoid Firefox lock file conflicts and KDE state collisions.

```bash
# Press Ctrl+Alt+F2 to switch to a TTY virtual console
# Log in as patrick

# Connect to WiFi
nmcli device wifi connect "YourSSID" password "YourPassword"

# Clone the config repo
git clone <your-repo-url> ~/nix-config

# Verify SSH access to the source machine
ssh patrick@tesseract

# Migrate patrick's home directory
~/nix-config/scripts/migrate-home.sh patrick tesseract /home/patrick/

# Migrate lina's home directory (run as root)
sudo ~/nix-config/scripts/migrate-home.sh lina tesseract /home/lina/

# Return to display manager
# Press Ctrl+Alt+F1
```

### Step 10: Login and Verify System

```bash
nixos-version
lspci | grep VGA
free -h
lsblk
ping -c 3 1.1.1.1
```

### Step 11: Configure TPM2 Auto-Unlock

```bash
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2

# Verify
sudo systemd-cryptenroll /dev/nvme0n1p2
# Should list "tpm2" as an enrolled method
```

### Step 12: Calculate and Set Hibernation Swap Offset

```bash
sudo btrfs inspect-internal map-swapfile -r /swap/swapfile
# Outputs a number like: 533760
```

Edit [hosts/murdock/configuration.nix](configuration.nix) — uncomment and update the `resume_offset` kernel parameter:

```nix
"resume_offset=533760"  # Replace with your actual value
```

Rebuild:

```bash
sudo nixos-rebuild switch --flake .#murdock
```

---

## Phase 4: Hardware Verification

### Step 13: Verify Intel GPU

```bash
glxinfo | grep "OpenGL renderer"
# Should show Intel Iris Xe Graphics

vainfo
# Should show iHD driver and supported profiles
```

### Step 14: Test Power Management

```bash
# Check power profile daemon
systemctl status power-profiles-daemon

# Monitor power consumption
sudo powertop

# Check thermal status
sensors

# Verify throttled service
sudo systemctl status throttled
```

### Step 15: Test Hibernate/Resume

```bash
# Test suspend
systemctl suspend
# Resume with power button — verify everything works

# Test hibernation
systemctl hibernate
# Power on, enter LUKS password if TPM fails
# Verify applications resumed correctly
```

### Step 16: Verify WiFi and Bluetooth

```bash
nmcli device status
bluetoothctl
> power on
> scan on
```

### Step 17: Enroll Fingerprint

```bash
fprintd-enroll
# Follow prompts; test by locking screen and using fingerprint
```

### Step 18: Verify Fingerprint + Bluetooth USB IDs

```bash
lsusb
# Find your Bluetooth adapter and fingerprint reader
# If vendor:product differs from 8087:0aaa (BT) or 06cb:00bd (fingerprint),
# update the udev rules and sleep hook in configuration.nix and rebuild
```

---

## Phase 5: Optional — RAM Upgrade to 40GB

If you later upgrade the RAM:

1. Update [hosts/murdock/disk-config.nix](disk-config.nix): change `size = "24G"` to `"40G"`
2. Delete the existing swapfile: `sudo rm /swap/swapfile`
3. Rebuild: `sudo nixos-rebuild switch --flake .#murdock` (disko will recreate the swapfile)
4. Recalculate resume_offset: `sudo btrfs inspect-internal map-swapfile -r /swap/swapfile`
5. Update `resume_offset` in configuration.nix and rebuild again

No partition changes needed — the swapfile is inside btrfs.

---

## Post-Installation Checklist

- [ ] System boots and prompts for LUKS password (or auto-unlocks with TPM)
- [ ] Login works (password and/or fingerprint)
- [ ] WiFi connects successfully
- [ ] Bluetooth works
- [ ] Intel Iris Xe GPU works (check with `glxinfo`, `vainfo`)
- [ ] Suspend works correctly
- [ ] Hibernate works correctly (resume_offset set)
- [ ] Lid close triggers suspend-then-hibernate
- [ ] Audio works (PipeWire)
- [ ] Touchpad gestures work
- [ ] TrackPoint works
- [ ] External displays work
- [ ] USB ports work
- [ ] Auto-updates are scheduled
- [ ] Tailscale connected
- [ ] LUKS header backed up: `sudo cryptsetup luksHeaderBackup /dev/nvme0n1p2 --header-backup-file ~/luks-header-backup-murdock.img`
- [ ] Hardware info documented

---

## Troubleshooting

### LUKS Won't Unlock with TPM
```bash
sudo systemd-cryptenroll --wipe-slot=tpm2 /dev/nvme0n1p2
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2
```

### Hibernate Fails to Resume
- Verify `resume_offset` is set correctly in configuration.nix: `cat /proc/cmdline | grep resume_offset`
- Verify `boot.resumeDevice = "/dev/mapper/cryptroot"` is set

### WiFi Not Working
- Check firmware: `dmesg | grep iwlwifi`
- Ensure `hardware.enableAllFirmware = true` is set

### Fingerprint Not Working After Resume
- Check USB IDs with `lsusb` and update udev rules in configuration.nix
