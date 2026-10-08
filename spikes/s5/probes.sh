#!/bin/sh
# Usage (inside the sandbox): probes.sh < JSON from launch.py (stdin). One PASS/FAIL line per probe.
exec python3 /run/hz/probes.py "$@"
