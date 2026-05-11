# Disk Configuration for ThinkPad T15 Gen 2
# 1TB NVMe with LUKS encryption, TPM2 auto-unlock, btrfs, and hibernation
# Swap sized to 24GB RAM. If RAM is upgraded to 40GB:
#   1. Delete /swap/swapfile
#   2. Change swap size below to "40G"
#   3. Rebuild and recalculate resume_offset

{
  disko.devices = {
    disk = {
      main = {
        type = "disk";
        device = "/dev/nvme0n1";  # Adjust if your disk is different (check with lsblk)
        content = {
          type = "gpt";
          partitions = {
            ESP = {
              priority = 1;
              name = "ESP";
              size = "1G";  # Larger for multiple kernel versions
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [
                  "defaults"
                  "umask=0077"
                ];
              };
            };

            luks = {
              size = "100%";
              content = {
                type = "luks";
                name = "cryptroot";

                settings = {
                  allowDiscards = true;
                  bypassWorkqueues = true;
                };

                extraOpenArgs = [
                  "--allow-discards"
                  "--perf-no_read_workqueue"
                  "--perf-no_write_workqueue"
                ];

                content = {
                  type = "btrfs";
                  extraArgs = [ "-f" "-L" "nixos" ];

                  subvolumes = {
                    "/root" = {
                      mountpoint = "/";
                      mountOptions = [
                        "compress=zstd"
                        "noatime"
                        "space_cache=v2"
                      ];
                    };

                    "/home" = {
                      mountpoint = "/home";
                      mountOptions = [
                        "compress=zstd"
                        "noatime"
                        "space_cache=v2"
                      ];
                    };

                    "/nix" = {
                      mountpoint = "/nix";
                      mountOptions = [
                        "compress=zstd:1"
                        "noatime"
                        "space_cache=v2"
                      ];
                    };

                    "/log" = {
                      mountpoint = "/var/log";
                      mountOptions = [
                        "compress=zstd"
                        "noatime"
                        "space_cache=v2"
                      ];
                    };

                    "/swap" = {
                      mountpoint = "/swap";
                      mountOptions = [ "noatime" ];
                      swap = {
                        swapfile = {
                          size = "24G";  # Match 24GB RAM
                        };
                      };
                    };
                  };
                };
              };
            };
          };
        };
      };
    };
  };

  boot.initrd.luks.devices."cryptroot" = {
    allowDiscards = true;
    bypassWorkqueues = true;

    # TPM2 unlock — enroll after first boot with:
    # sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 /dev/nvme0n1p2
    crypttabExtraOpts = [
      "tpm2-device=auto"
      "tpm2-pcrs=0+7"
    ];
  };

  swapDevices = [ ];  # Managed by disko
}
