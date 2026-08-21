#!/bin/zsh
# Incremental update: EWX API pull, local CHIRPS v3 rasters, derived metrics.
set -e
cd "$(dirname "$0")"
./venv/bin/python ingest/run_ingest.py
./venv/bin/python ingest/chirps_raster.py
./venv/bin/python compute/metrics.py
echo "update complete: $(date)"
