#!/bin/zsh
# Incremental update: EWX API pull, local CHIRPS v3 rasters, derived metrics.
set -e
cd "$(dirname "$0")"
# sync first so code/data pushed from other machines can't break the
# end-of-run push (non-fast-forward)
git pull --rebase --autostash origin main
./venv/bin/python ingest/run_ingest.py
./venv/bin/python ingest/chirps_raster.py
./venv/bin/python ingest/flood_raster.py
# Kariba turbine discharge (Mozambique hydro layer, SCOPING-MOZ-HYDRO.md
# M1): one cheap HTTP request against the ZRA reservoir-data page, rides
# the ungated 5-daily chain.
./venv/bin/python ingest/zra_kariba.py
# DAHITI water levels (DRC + Mozambique hydro layers): weekly only — this
# script has no existing gating convention for less-frequent steps, so
# gate with a marker-file age check (>=6.5 days old or missing). The
# Mozambique stations (cahora_bassa, chicamba_reservoir, itt_tailwater)
# added for SCOPING-MOZ-HYDRO.md M1 are just more entries in
# dahiti_levels.py's STATIONS dict, so this same gate covers them
# automatically — no separate marker needed.
DAHITI_MARKER=data/.dahiti_last_run
if [ ! -f "$DAHITI_MARKER" ] || \
   [ $(( $(date +%s) - $(date -r "$DAHITI_MARKER" +%s) )) -ge 561600 ]; then
  ./venv/bin/python ingest/dahiti_levels.py && touch "$DAHITI_MARKER"
fi
./venv/bin/python compute/metrics.py
./venv/bin/python compute/flood_signals.py
# push refreshed DB so the deployed Streamlit app stays current
git add db/monitor.sqlite
git diff --cached --quiet || git commit -m "data update $(date +%F)"
git push origin main
echo "update complete: $(date)"
