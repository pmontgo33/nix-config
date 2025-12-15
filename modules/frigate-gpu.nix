# Frigate GPU Acceleration Module
#
# This module provides a clean interface for configuring Frigate NVR
# with GPU-accelerated object detection.
#
# Usage:
#   extra-services.frigate-gpu = {
#     enable = true;
#     detectorType = "cpu";  # or "openvino" or "onnx"
#     detectorDevice = "AUTO";
#     enableLPR = false;
#     enableFaceRecognition = true;
#   };
#
# Note: OpenVINO and ONNX detectors require additional Python packages
# that are not included in the default NixOS Frigate package.
# The module will warn you if you try to use them.
#
# To enable OpenVINO support, you would need to override the Frigate package:
#   services.frigate.package = pkgs.frigate.overrideAttrs (oldAttrs: {
#     propagatedBuildInputs = oldAttrs.propagatedBuildInputs ++ [
#       pkgs.python3Packages.openvino
#     ];
#   });

{ config, lib, pkgs, ... }:

with lib; let
  cfg = config.extra-services.frigate-gpu;

in {
  options.extra-services.frigate-gpu = {
    enable = mkEnableOption "Frigate NVR with GPU acceleration configuration";

    detectorType = mkOption {
      type = types.enum [ "cpu" "openvino" "onnx" ];
      default = "cpu";
      description = "Detector type to use for object detection";
    };

    detectorDevice = mkOption {
      type = types.str;
      default = "AUTO";
      description = ''
        Device to use for detection.
        For OpenVINO: AUTO, GPU, CPU, MYRIAD, etc.
        For ONNX: AUTO, CUDA, CPU, etc.
      '';
    };

    enableLPR = mkOption {
      type = types.bool;
      default = false;
      description = "Enable license plate recognition (may cause issues in LXC)";
    };

    enableFaceRecognition = mkOption {
      type = types.bool;
      default = true;
      description = "Enable face recognition";
    };

    faceRecognitionModelSize = mkOption {
      type = types.enum [ "small" "medium" "large" ];
      default = "small";
      description = "Face recognition model size";
    };

    enableBirdClassification = mkOption {
      type = types.bool;
      default = true;
      description = "Enable bird classification";
    };
  };

  config = mkIf cfg.enable {
    # Override Frigate package with OpenVINO support when using openvino detector
    services.frigate.package = mkIf (cfg.detectorType == "openvino")
      (pkgs.frigate.overrideAttrs (oldAttrs: {
        propagatedBuildInputs = (oldAttrs.propagatedBuildInputs or []) ++ [
          pkgs.python3Packages.openvino
        ];
      }));

    # Configure detector based on type
    services.frigate.settings.detectors = mkMerge [
      (mkIf (cfg.detectorType == "cpu") {
        cpu = {
          type = "cpu";
        };
      })
      (mkIf (cfg.detectorType == "openvino") {
        ov = {
          type = "openvino";
          device = cfg.detectorDevice;
        };
      })
      (mkIf (cfg.detectorType == "onnx") {
        onnx = {
          type = "onnx";
          device = cfg.detectorDevice;
        };
      })
    ];

    # Configure features
    services.frigate.settings = {
      lpr.enabled = cfg.enableLPR;

      face_recognition = mkIf cfg.enableFaceRecognition {
        enabled = true;
        model_size = cfg.faceRecognitionModelSize;
      };

      classification.bird.enabled = cfg.enableBirdClassification;
    };

    # Add warnings for potential issues
    warnings =
      optional (cfg.detectorType == "onnx") ''
        extra-services.frigate-gpu.detectorType is set to "onnx" but ONNX requires a model
        file path to be configured. The detector will fail without an explicit model path.
      '' ++
      optional cfg.enableLPR ''
        extra-services.frigate-gpu.enableLPR is enabled. This may cause segmentation
        faults in LXC containers due to thread affinity issues. Consider disabling if crashes occur.
      '';
  };
}
