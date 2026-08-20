#!/usr/bin/env python3
"""Offline Pi-hole policy renderer and dry-run reconciler."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pihole.policy_reconcile import main


if __name__ == "__main__":
    raise SystemExit(main())
