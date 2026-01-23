{ config, lib, pkgs, ... }:
with lib;
let
  cfg = config.extra-services.flutter;
  androidComposition = pkgs.androidenv.composeAndroidPackages {
    toolsVersion = "26.1.1";
    platformToolsVersion = "35.0.1";
    buildToolsVersions = [
      "30.0.3"
      "33.0.1"
      "34.0.0"
    ];
    platformVersions = [
      "31"
      "33"
      "34"
    ];
    abiVersions = [ "x86_64" ];
    includeEmulator = true;
    emulatorVersion = "35.1.4";
    includeSystemImages = true;
    systemImageTypes = [ "google_apis_playstore" ];
    includeSources = false;
    extraLicenses = [
      "android-googletv-license"
      "android-sdk-arm-dbt-license"
      "android-sdk-license"
      "android-sdk-preview-license"
      "google-gdk-license"
      "intel-android-extra-license"
      "intel-android-sysimage-license"
      "mips-android-sysimage-license"
    ];
  };
  androidSdk = androidComposition.androidsdk;
  buildToolsVersion = "33.0.1";
in {
  options.extra-services.flutter = {
    enable = mkEnableOption "Flutter development environment";
    addToKvmGroup = mkEnableOption "Add user to KVM group for hardware acceleration";
    enableAdb = mkEnableOption "Enable ADB and add user to adbusers group";
    user = mkOption {
      type = types.str;
      description = "Username for Flutter development";
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = with pkgs; [
      flutter
      androidSdk
      # android-studio
      jdk17
      firebase-tools
    ];

    environment.variables = {
      ANDROID_SDK_ROOT = "${androidSdk}/libexec/android-sdk";
      ANDROID_HOME = "${androidSdk}/libexec/android-sdk";
      JAVA_HOME = "${pkgs.jdk17}";
      GRADLE_OPTS = "-Dorg.gradle.project.android.aapt2FromMavenOverride=${androidSdk}/libexec/android-sdk/build-tools/34.0.0/aapt2";
    };

    nixpkgs.config = {
      android_sdk.accept_license = true;
      allowUnfree = true;
    };

    environment.shellInit = ''
      export PATH=$PATH:${androidSdk}/libexec/android-sdk/platform-tools
      export PATH=$PATH:${androidSdk}/libexec/android-sdk/cmdline-tools/latest/bin
      export PATH=$PATH:${androidSdk}/libexec/android-sdk/emulator
      export PATH="$PATH":"$HOME/.pub-cache/bin"
    '';

    programs.adb.enable = cfg.enableAdb;

    users.users.${cfg.user}.extraGroups =
      (optional cfg.addToKvmGroup "kvm") ++ (optional cfg.enableAdb "adbusers");
  };
}