#!/usr/bin/env bash
# Live status of the Spark from the laptop. Read-only.   scripts/status.sh [ssh-host]
ssh -o ConnectTimeout=10 "${1:-spark}" '~/bin/spark-status'
