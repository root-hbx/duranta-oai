#!/usr/bin/env python3
"""Bracket CLOCK_REALTIME against CLOCK_MONOTONIC without rounded /proc/uptime."""
import json
from pathlib import Path
import sys
import time

before = time.monotonic_ns()
epoch = time.time_ns()
after = time.monotonic_ns()
record = {"label": sys.argv[2], "epoch_ns": epoch, "monotonic_before_ns": before,
          "monotonic_after_ns": after, "boottime_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME),
          "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
with open(sys.argv[1], "a") as stream:
  stream.write(json.dumps(record) + "\n")
