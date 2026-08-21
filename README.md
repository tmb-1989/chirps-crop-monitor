# chirps-crop-monitor

Dekadal rainfall + soil moisture monitor over FEWS NET crop zones in eastern
and southern Africa. Scoping in [SCOPING.md](SCOPING.md).

## Data flow

USGS FEWS NET GeoEngine 5 API (pre-computed zonal means over
`fews_shapefile_cropzones`) → SQLite (`db/monitor.sqlite`) → Streamlit.

Datasets per zone: CHIRPS pentad/monthly (final + prelim), CHIRPS monthly
anom/z-score, LWRSI dekadal (data + % of median), FLDAS soil moisture
0–10 cm and 0–100 cm monthly (data + % of mean). 13 anchor zones in
`ingest/config.py` (KEN ETH TZA UGA ZMB MWI ZWE MOZ MDG).

## Usage

```bash
# full incremental update (EWX + local rasters + metrics)
./run_update.sh

# pieces:
./venv/bin/python ingest/run_ingest.py        # EWX API pull (--full: from 1981)
./venv/bin/python ingest/chirps_raster.py     # local CHIRPS v3 dekads (--full)
./venv/bin/python compute/metrics.py          # SPI / onset / dry spells
./venv/bin/python ingest/zones_geo.py         # rebuild zone geometries (one-off)

# dashboard
./venv/bin/streamlit run app/streamlit_app.py
```

## Phase 2: local CHIRPS v3 raster pipeline

Independent of the .gov endpoint: streams dekad GeoTIFFs from
`data.chc.ucsb.edu/products/CHIRPS/v3.0/` (no auth), rasterizes the zone
polygons once onto the 0.05° Africa grid (`data/zones/masks.npz`), computes
zonal means locally, and stores them as `chirps3local_dekad_data` /
`chirps3local-prelim_dekad_data`. Cross-checks against EWX to the decimal
(e.g. Uasin Gishu Aug 1–10 2026: local dekad 111.6mm = EWX pentads
53.4 + 58.2).

- Final Africa dekads are current; **prelim Africa dekads are stale on CHC —
  prelim comes from the global file windowed to the Africa grid.**
- Zone geometries in `data/zones/zones.geojson` (WFS-fetched crop zones,
  admin-1 for Malawi, plus CPI-aligned composites `ken_grain_basket`,
  `zmb_maize_belt`).
- Derived metrics in `dekad_metrics` (% of normal, SPI-1/SPI-3 gamma-fit on
  1991–2020) and `season_metrics` (onset date/delay, max dry spell).
- Raster cache: rolling ~2 years in `data/rasters/` (~300MB), older files
  deleted after processing.
- **CHC runs CrowdSec**: parallel or burst downloads from data.chc.ucsb.edu
  earn a temporary IP ban (403 "CrowdSec Ban" page). Downloads are
  sequential with a 1s delay; the pipeline is resumable, so after a ban
  just re-run once the 403s stop.

Update cadence: prelim pentads land ~2 days after the 5th/10th/15th/20th/25th/
end-of-month. Cron (installed): 07:30 on the 3rd/8th/13th/18th/23rd/28th runs
`run_update.sh`, logging to `data/cron.log`.

## API notes (reverse-engineered from the EWX viewer, Aug 2026)

- Timeseries: `https://edcintl.cr.usgs.gov/geoengine5/rest/timeseries/version/5.0/vector_dataset/{ws:layer}/raster_dataset/{ds}/periodicity/{p}/statistic/{stat}/lat/{lat}/lon/{lon}/seasons/{yrs}/zonal_stat_type/mean/mean-median/false`
- `vector_dataset` must be namespaced: `fews_shapefile_cropzones:shapefile_cropzones`
  (crop zones), `fews_shapefile_g2008_af_1:shapefile_g2008_af_1` (admin-1).
- Zone selection is point-in-polygon on lat/lon. Zone attributes (CROP,
  ADM0/1_NAME) via GeoServer WFS `https://edcintl.cr.usgs.gov/geoserver/wfs`,
  geometry column `geom`, CQL axis order **lat lon**.
- `statistic` must match the dataset suffix (`data`/`anom`/`zscore`/`pctm`).
- Colon-joining several rasters in one request silently returns only the
  first — one request per dataset.
- Dataset catalog: `https://edcintl.cr.usgs.gov/geoengine5/rest/version/5.0/config`
- Prelim datasets only contain granules not yet in final.
- **The API pads a "final" series server-side with prelim and CHIRPS-GEFS
  forecast granules** beyond the dataset's real end date. The catalog's
  `end.granule_end` (stored in `dataset_meta`) is the only way to classify
  final vs prelim vs forecast — the dashboard excludes forecast granules
  from season-to-date and draws them as a dotted extension.
- The timeseries API returns **HTTP 500 when the lat/lon misses every
  polygon** in the vector dataset — anchor points must be WFS-verified.
- The API **soft-rate-limits by returning 200 with an empty body** after
  request bursts (and sometimes for very long season ranges on complex
  polygons). Ingest backs off 90s and retries, and the client chunks
  full-history requests into 15-season blocks as a fallback.
- WFS quirk pair: CQL `INTERSECTS(geom, POINT(...))` wants **(lat lon)**,
  but returned GeoJSON geometry is standard **[lon, lat]**.

## Gotchas

- LWRSI `pctm` is % of the 1982–2021 median (100 = normal; <80 stress).
- Crop zones = crop polygons ∩ admin-1, so "zone" granularity is province.
- `revisions` table logs any value change on re-ingest (prelim→final).
- Phase 2 (planned): own CHIRPS v3 raster pipeline from data.chc.ucsb.edu as
  a hedge against .gov outages + custom CPI-aligned zones; see SCOPING.md.
