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
./venv/bin/python compute/metrics.py
./venv/bin/python compute/flood_signals.py
./venv/bin/python compute/cpi_impulse.py
./venv/bin/python compute/country_risk.py
# push refreshed data so the deployed Streamlit app stays current.
# live.sqlite (risk board, ENSO/IOD, Kariba — a few hundred KB) goes daily.
# The full monitor.sqlite is local-only since the V2 zone expansion put
# it past GitHub's 100MB hard limit; dekad days commit the trimmed
# app.sqlite (observations >= 2010, ingest/app_db.py) instead.
git add db/live.sqlite
case $(date +%-d) in
  3|8|13|18|23|28) ./venv/bin/python ingest/app_db.py && git add db/app.sqlite;;
esac
git diff --cached --quiet || git commit -m "data update $(date +%F)"
git push origin main
echo "update complete: $(date)"
