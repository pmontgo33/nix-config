# ThinkPad T15 Gen 2 Installation Guide
## NixOS Installation via nixos-anywhere

This guide covers installing NixOS on the Lenovo ThinkPad T15 Gen 2 (i7-1185G7, 24GB RAM, Intel Iris Xe Graphics) using nixos-anywhere. The T15 is booted from the `rescue` NixOS ISO host and connected via ethernet.

---

## Prerequisites

### On Your Installation Host (tesseract)
- [ ] NixOS configuration repository cloned and up-to-date
- [ ] SSH access to `rescue` working (Tailscale or local network)
- [ ] nix flakes enabled (already the case)

### On the T15 Gen 2
- [ ] Connected via ethernet
- [ ] Booted into the `rescue` NixOS ISO

---

## Phase 1: Prepare the T15

### Step 1: Boot T15 into the Rescue ISO

Boot the T15 from the rescue NixOS ISO. It should acquire an IP via DHCP over ethernet automatically.

### Step 2: Get the T15's IP Address and Enable SSH

On the T15 (or check your router/Tailscale admin for the lease):

```bash
ip addr show
# Note the ethernet IP (e.g. 192.168.1.100)

# Set a temporary root password so nixos-anywhere can connect
sudo passwd
```

SSH should already be running on the rescue ISO. Verify from tesseract:

```bash
ssh root@<T15-IP>
```

### Step 3: Verify Hardware Details (from the T15)

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
# The config assumes PCI:0:2:0 — update configuration.nix if different

# Check USB IDs for Bluetooth and fingerprint reader
lsusb
# Config assumes: Bluetooth 8087:0aaa, Fingerprint 06cb:00bd
# Note actual values — you'll update configuration.nix after install if they differ
```

---

## Phase 2: Install NixOS via nixos-anywhere

Run all of these commands **on tesseract** (your installation host).

### Step 4: Stage the SOPS Age Key

nixos-anywhere's `--extra-files` flag copies files onto the target after partitioning, before activation. This is how the age key gets onto murdock so sops-nix can decrypt secrets on first boot.

```bash
mkdir -p /tmp/host-secrets/etc/sops/age
sudo cp /etc/sops/age/keys.txt /tmp/host-secrets/etc/sops/age/keys.txt
sudo chmod 600 /tmp/host-secrets/etc/sops/age/keys.txt
```

### Step 5: Run nixos-anywhere

```bash
nix run github:nix-community/nixos-anywhere -- \
  --flake ~/nix-config#murdock \
  --extra-files /tmp/host-secrets \
  root@<T15-IP>
```

You will be prompted to set the LUKS encryption password during the install. Choose a strong one and write it down — you will need it at every boot until TPM2 is enrolled.

### Step 6: Clean Up the Staged Key

```bash
rm -rf /tmp/host-secrets
```

### Step 7: Wait for Installation to Complete

nixos-anywhere will:
1. Partition the NVMe drive (GPT + 1G ESP + LUKS)
2. Create btrfs subvolumes (root, home, nix, log, swap)
3. Copy the age key into place
4. Install NixOS and reboot

---

## Phase 3: First Boot and Configuration

### Step 8: First Boot

1. The T15 will reboot automatically after install
2. Enter your LUKS password at the prompt
3. The system will boot to the SDDM login screen

### Step 9: Migrate Home Directories (BEFORE first GUI login)

**Do this before logging into the desktop** to avoid Firefox lock file conflicts and KDE state collisions. The migration script runs on murdock and pulls files from tesseract over SSH via ethernet.

```bash
# At the SDDM screen, press Ctrl+Alt+F2 to switch to a TTY
# Log in as patrick

# The network is already up via ethernet — verify connectivity
ping -c 3 tesseract

# Clone the config repo
git clone <your-repo-url> ~/nix-config

# Verify SSH access to the source machine
ssh patrick@tesseract

# Migrate patrick's home directory
~/nix-config/scripts/migrate-home.sh patrick tesseract /home/patrick/

# Migrate lina's home directory
sudo ~/nix-config/scripts/migrate-home.sh lina tesseract /home/lina/

# Return to display manager
# Press Ctrl+Alt+F1, then log in graphically
```

### Step 10: Verify System

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

Commit, push, and rebuild:

```bash
sudo nixos-rebuild switch --flake .#murdock
```

### Step 13: Update Fingerprint + Bluetooth USB IDs (if needed)

```bash
lsusb
# Find your Bluetooth adapter and fingerprint reader
# Config assumes: Bluetooth 8087:0aaa, Fingerprint 06cb:00bd
# If different, update the udev rules and sleep hook in configuration.nix and rebuild
```

---

## Phase 4: Hardware Verification

### Step 14: Verify Intel GPU

```bash
glxinfo | grep "OpenGL renderer"
# Should show Intel Iris Xe Graphics

vainfo
# Should show iHD driver and supported profiles
```

### Step 15: Test Power Management

```bash
systemctl status power-profiles-daemon
sudo powertop
sensors
sudo systemctl status throttled
```

### Step 16: Test Hibernate/Resume

```bash
# Test suspend first
systemctl suspend
# Resume with power button — verify everything works

# Test hibernation (requires resume_offset to be set)
systemctl hibernate
# Power on, enter LUKS password if TPM fails
# Verify applications resumed correctly
```

### Step 17: Verify WiFi and Bluetooth

```bash
nmcli device status
bluetoothctl
> power on
> scan on
```

### Step 18: Enroll Fingerprint

```bash
fprintd-enroll
# Follow prompts; test by locking screen and using fingerprint
```

---

## Phase 5: Optional — RAM Upgrade to 40GB

If you later upgrade the RAM:

1. Update [hosts/murdock/disk-config.nix](disk-config.nix): change `size = "24G"` to `"40G"`
2. Delete the existing swapfile: `sudo rm /swap/swapfile`
3. Rebuild: `sudo nixos-rebuild switch --flake .#murdock` (disko will recreate the swapfile)
4. Recalculate resume_offset: `sudo btrfs inspect-internal map-swapfile -r /swap/swapfile`
5. Update `resume_offset` in configuration.nix and rebuild again

No partition changes needed — the swapfile lives inside btrfs.

---

## Post-Installation Checklist

- [ ] System boots and prompts for LUKS password (or auto-unlocks with TPM)
- [ ] Login works (password and/or fingerprint)
- [ ] Ethernet works
- [ ] WiFi connects successfully
- [ ] Bluetooth works
- [ ] Intel Iris Xe GPU works (`glxinfo`, `vainfo`)
- [ ] Suspend works correctly
- [ ] Hibernate works correctly (resume_offset set and verified)
- [ ] Lid close triggers suspend-then-hibernate
- [ ] Audio works (PipeWire)
- [ ] Touchpad gestures work
- [ ] TrackPoint works
- [ ] External displays work
- [ ] USB ports work
- [ ] Auto-updates are scheduled
- [ ] Tailscale connected
- [ ] LUKS header backed up: `sudo cryptsetup luksHeaderBackup /dev/nvme0n1p2 --header-backup-file ~/luks-header-backup-murdock.img`

---

## Troubleshooting

### LUKS Won't Unlock with TPM
```bash
sudo systemd-cryptenroll --wipe-slot=tpm2 /dev/nvme0n1p2
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2
```

### Hibernate Fails to Resume
- Verify `resume_offset` is set: `cat /proc/cmdline | grep resume_offset`
- Verify `boot.resumeDevice = "/dev/mapper/cryptroot"` is set in configuration.nix

### WiFi Not Working
- Check firmware: `dmesg | grep iwlwifi`
- Ensure `hardware.enableAllFirmware = true` is set

### Fingerprint Not Working After Resume
- Check USB IDs with `lsusb` and update udev rules in configuration.nix
- The sleep hook in configuration.nix handles stopping/restarting fprintd around suspend
