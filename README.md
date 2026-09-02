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

## Country risk board

Traffic-light matrix (green/yellow/red/gray) per country × risk factor —
**ENSO, drought, flood, hydropower** — the dashboard's landing view.
`ingest/enso.py` pulls NOAA CPC's ONI + weekly Niño SSTs (the weekly
Niño 3.4 anomaly sharpens the 'developing' call by 1-2 months); a
per-country exposure table converts the global phase into country lights
(El Niño ≈ OND flood risk in East Africa, main-season drought in
southern Africa). `ingest/iod.py` computes a near-real-time Indian Ocean
Dipole index from NOAA OISST (validated against PSL's HadISST DMI); an
aligned |DMI| ≥ 0.4 amplifies East African ENSO cells. `ingest/kariba.py`
scrapes Lake Kariba level/storage/discharge from the Zambezi River
Authority and computes the 4-week drawdown rate, feeding the Zambia
hydropower light (thresholds: 478m severe-rationing boundary, drawdown
0.15/0.20 m/wk). ENSO/IOD/Kariba ingest + thresholds ported from the
sibling elnino-hydro-dashboard project, which also seeded the Kariba
history (2017–) via `ingest/kariba.py --import-legacy`.
Drought aggregates in-season WRSI / SPI-3 / soil moisture over crop
zones; flood reads the flood-watch layer. Worst-case aggregation — the
most stressed zone/basin colors the country and is named in the cell.
Gray = inputs missing or stale, never "safe". Snapshot in `country_risk`,
transitions in `country_risk_log`, per-zone drought lights (the board's
drill-down expander) in `zone_risk` (`compute/country_risk.py`).

## Flood-watch layer

Basin-level flood signals over 24 HydroBASINS level-7 basins in **Kenya
(pilot), Ethiopia, Tanzania, Rwanda, Uganda** — scoping in
[SCOPING-FLOODS.md](SCOPING-FLOODS.md). Pentad CHIRPS v3 zonal means per
basin (`ingest/flood_raster.py`), saturation/whiplash tiers + per-country
regional alerts and backtests (`compute/flood_signals.py`), "Flood watch"
dashboard view with a country selector.

```bash
# one-off: basin polygons (needs data/raw/hybas_af_lev07_v1c.shp from
# https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_af_lev07_v1c.zip)
./venv/bin/python ingest/flood_zones.py

# pentad backfill over basins (--full: 1981-present, ~4-5h at CHC's rate limit;
# delete data/zones/basins_masks.npz first if basins changed)
./venv/bin/python ingest/flood_raster.py --full

# signals + per-country regional alerts + backtests
./venv/bin/python compute/flood_signals.py
```

Calibration status: Kenya's regional-alert rule is backtested at 71%
precision / all major events caught (1999–2026). The extension countries
reuse the same thresholds and rule; their event catalogs are approximate
EM-DAT/FloodList-informed windows and each country's backtest prints its
own hit/false-alarm record — check it before leaning on those alerts.

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
- Derived metrics in `dekad_metrics` (% of normal, SPI-1/SPI-3) and
  `season_metrics` (onset date/delay, max dry spell) — precise definitions
  below.

### Metric definitions (as implemented in `compute/metrics.py`)

All derived metrics run on the merged local dekad series (final CHIRPS v3
where available, prelim otherwise; final wins on overlap).

**pct_normal** — dekad rainfall as % of the 1991–2020 mean for the *same
dekad-of-year* (36 classes/year). Handles variable dekad length (8–11 days,
incl. leap-year Feb 21–29) by construction: dekads are only ever compared
to themselves.

**SPI-1 / SPI-3** — Standardized Precipitation Index over rolling sums of
**3 and 9 dekads** (≈1 and ≈3 months; the names follow the conventional
monthly labels, but the windows are dekad-based). Full window required
(`min_periods = window`) — no partial sums. Distributions are fitted
**separately for each of the 36 dekad-of-year classes** on the 1991–2020
rolling sums ending in that class. Zero/trace handling: a mixed
distribution — the zero fraction q₀ is estimated empirically, a 2-parameter
gamma (location fixed at 0) is fitted to the positive values only, and
CDF = q₀ + (1−q₀)·Gamma(x). The CDF is clipped to [1e-4, 1−1e-4]
(bounding SPI at ≈±3.7) before the normal-quantile transform. Classes with
<15 climatology observations or <10 positive values return NaN.

**Onset of rains** — per season-year, the first in-season dekad with
**≥25mm** whose two following dekads sum to **≥20mm** (the follow-up
requirement is the false-onset guard; there is no separate re-onset rule).
Earliest eligible date is the first dekad of the season window's start
month. `onset_delay_dekads` is the offset in dekads vs the median onset
position over 1991–2020 season-years where an onset was found.

**Max dry spell** — per season-year, the longest run of consecutive
in-season dekads with **<5mm**. Runs may cross month and calendar-year
boundaries within a season (e.g. Dec→Jan inside an Oct–Apr season) but
never cross season boundaries — the counter exists only within one
season-year's window. Threshold is on the zonal-mean dekad total, so
localized in-zone rain can mask a spell.

**Cross-year seasons & edges** — a season-year is labeled by its start
year (the 2025-26 Oct–Apr season is `season_year=2025`). Historical
seasons missing more than 3 dekads (series edges) are dropped from
season_metrics; the current season is always kept.
### Zonal aggregation (as implemented in `ingest/chirps_raster.py`)

- **Cell selection**: zone polygons are rasterized onto the 0.05° CHIRPS
  Africa grid with `rasterio.features.rasterize` at default settings —
  a cell belongs to a zone iff its **center point** falls inside the
  polygon (not all-touched, not fractional overlap). Polygon holes and
  multi-part geometries are honored.
- **Statistic**: simple **unweighted arithmetic mean** of member cells.
  No latitude (cos φ) area weighting — on a geographic grid cell area
  varies with latitude, but across a single zone's ≤5° span the bias is
  <1% and is accepted; do not reuse this for continental aggregates.
- **NoData**: the file's nodata value (−9999) and non-finite cells are
  excluded per dekad; a zone with zero valid cells in a dekad yields no
  row (never a zero). Mask build fails loudly if any zone rasterizes to
  zero cells (guards against sliver polygons).
- **What the zones are**: FEWS NET "crop zones" are **crop-type polygons
  intersected with admin-1 units** — a cartographic crop map, *not* a
  satellite-derived cropland mask. Zonal means therefore include
  non-cropped cells inside the polygon. Three zones (Malawi Lilongwe,
  Mozambique Zambezia, SA Mpumalanga) use admin-1 boundaries on the EWX
  side because their crop-zone polygons have no server-side stats; of
  these, Mpumalanga's *local* series still uses the crop polygon, so its
  local-vs-EWX cross-check differs by construction.
- **EWX side**: the API's zonal means are computed server-side by USGS
  (method not published); our local means reproduce them to the decimal
  where both exist, which is the standing cross-check.
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
