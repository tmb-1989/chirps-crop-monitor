#!/bin/zsh
# Incremental update: EWX API pull, local CHIRPS v3 rasters, derived metrics.
set -e
cd "$(dirname "$0")"
# sync first so code/data pushed from other machines can't break the
# end-of-run push (non-fast-forward)
git pull --rebase origin main
./venv/bin/python ingest/run_ingest.py
./venv/bin/python ingest/chirps_raster.py
./venv/bin/python compute/metrics.py
# push refreshed DB so the deployed Streamlit app stays current
git add db/monitor.sqlite
git diff --cached --quiet || git commit -m "data update $(date +%F)"
git push origin main
echo "update complete: $(date)"
