#!/bin/zsh
# Incremental update: EWX API pull, local CHIRPS v3 rasters, derived metrics.
set -e
cd "$(dirname "$0")"
# sync first so code/data pushed from other machines can't break the
# end-of-run push (non-fast-forward)
git pull --rebase --autostash origin main
./venv/bin/python ingest/run_ingest.py
# climate-index ingests are non-fatal: an upstream outage (e.g. NOAA THREDDS
# 503s) should leave that index one day stale, not block the whole update
./venv/bin/python ingest/enso.py || echo "WARN: enso ingest failed, continuing with stale data"
./venv/bin/python ingest/iod.py || echo "WARN: iod ingest failed, continuing with stale data"
./venv/bin/python ingest/kariba.py || echo "WARN: kariba ingest failed, continuing with stale data"
./venv/bin/python ingest/prices.py || echo "WARN: price ingest failed, continuing with stale data"
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
./venv/bin/python compute/cpi_impulse.py
./venv/bin/python compute/country_risk.py
# push refreshed data so the deployed Streamlit app stays current.
# live.sqlite (risk board, ENSO/IOD, Kariba — a few hundred KB) goes daily;
# the ~54MB monitor.sqlite only on dekad days, since sqlite binaries don't
# delta-compress and daily commits would grow the repo by GBs/month.
git add db/live.sqlite
case $(date +%-d) in
  3|8|13|18|23|28) git add db/monitor.sqlite;;
esac
git diff --cached --quiet || git commit -m "data update $(date +%F)"
git push origin main
echo "update complete: $(date)"
