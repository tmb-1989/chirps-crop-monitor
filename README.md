# chirps-crop-monitor

Dekadal rainfall + soil moisture monitor over FEWS NET crop zones in eastern
and southern Africa. Scoping in [SCOPING.md](SCOPING.md).

## Data flow

USGS FEWS NET GeoEngine 5 API (pre-computed zonal means over
`fews_shapefile_cropzones`) → SQLite → Streamlit. Storage is split:
`db/monitor.sqlite` (~54MB) holds the observation/metrics/flood history
and is committed on dekad days; `db/live.sqlite` (~700KB: risk board,
ENSO/IOD, Kariba, GEFS, alerts) is committed daily. `db.connect()` and
the app ATTACH live.sqlite, so table names are unqualified everywhere;
`ingest/split_db.py` is the one-off migration.

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

WRSI rules (29 Oct 2026): a reading counts — board light, status
emails and the CPI impulse alike — when its dekad falls inside one of the
zone's configured windows **and** the window is at least
`config.WRSI_MIN_SEASON_DAYS` (60) days old (one gate,
`config.wrsi_reading_counts`, used by `country_risk` and `cpi_impulse`;
`tests/test_wrsi_gate.py` checks they agree). The 60 days are evidence-
based: over 2010–26 a pctm <80 in a window's first two dekads ended the
season <80 only 4–5% of the time (base rate 7.9%), and early readings
were uncorrelated with the end-of-season value until ~dekad 6. Readings
inside the window but before the gate are stored with
`zone_risk.wrsi_early=1` and drawn pale blue ("early season — not yet a
signal") on the maps, so an open season is visibly open without
driving a light. Kenya's grain-basket zones carry
both `long_rains:3-9` and `short_rains:10-1`. **Export belts (coffee,
tea, cocoa, tobacco, cashew, vanilla, cotton) carry no WRSI at all** —
the index models an annual cereal's growth-stage water requirement and
has no meaning for perennials — so their light is SPI-3 + soil moisture
only, and they are omitted from the Overview's WRSI map.

The Overview also carries three Africa-wide maps (shared helpers at the
top of `app/streamlit_app.py`), drawn over a dissolved Natural Earth
continent outline (`data/zones/africa_outline.geojson`) and the
monitored countries' outlines (`data/zones/country_outlines.geojson`):
every staple zone shaded by its latest in-season WRSI; every export belt
shaded by FLDAS root-zone soil moisture (% of mean, the soil-moisture
chart bands — the water buffer a tree crop draws on); and every
flood-watch basin shaded by its current tier.

The former standalone "DRC hydro" and "Mozambique hydro" views are
rendered at the foot of the Country tab — Mozambique's under Mozambique,
the DRC Katanga/Inga panels under Zambia (the DRC copper belt draws on
the Zambian grid and DRC has no crop zones in this monitor).

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

Calibration status (final, 3 Sep 2026, all 32 basins backfilled;
`compute/flood_calibrate.py` — percentile-based heavy-pentad test
replacing %-of-normal + fixed mm floors, parameters in
`flood_signals.PARAMS`; ~28yr in-sample backtest against approximate
EM-DAT/FloodList event windows):

| Country | Precision | Recall | Majors | vs Kenya-thresholds baseline |
|---|---|---|---|---|
| Kenya | 88% | 11/15 | all | up from 71%; 2 false alarms in 28yrs |
| Ethiopia | 56% | 7/11 | 2/3 gu+deyr, kiremt partial | up from 29/2-of-11 |
| Tanzania | 40% | 7/8 | all | up from 38/6-of-8; only Kilosa'10 missed |
| Rwanda | 60% | 3/6 | 2/4 | precision up from 36; landslides stay hard |
| Uganda | 30% | 3/7 | 3/4 | up from 20/2-of-7; Bududa'10 caught |

The basin-subset search also produced a telemetry/alert split
(`flood_signals.TELEMETRY_ONLY`): Gode (ETH) and Lindi (TZA) earned
seats in the regional rule, while the arid flash catchments added for
coverage (Dire Dawa, Genale, lower Omo, Wami, Rusizi, Semliki) are
monitored and displayed — ‡ in the dashboard — but excluded from the
alert rule, where each degraded precision without adding recall.
Uganda's remaining misses (Bududa'18, Kasese, Mbale) are short-burst
single-catchment events at the edge of pentad-scale resolution — treat
its regional alert as weak evidence either way. The ≥2-basin rule
survived everywhere; the 1-basin variant always lost on false alarms.

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
end-of-month; ZRA Kariba and CHIRPS-GEFS update daily; weekly Niño weekly.
Cron (installed): **daily 07:30** runs `run_update.sh`, logging to
`data/cron.log` (CHIRPS granules between updates are skipped as already
ingested, so off-dekad days are cheap).

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

- LWRSI `pctm` is the **Landscape** WRSI (generic crop, not maize) as % of
  the 1982–2021 median for the same dekad (base confirmed in the EWX
  catalog). 100 = normal. The 95/80/60 cut-offs are applied to this
  anomaly, so the bands are labelled near normal / below normal / well
  below normal / severe deficit — not the FEWS NET absolute-WRSI
  crop-condition classes (<50 failure). Map hovers show the absolute
  LWRSI beside it.
- Crop zones = crop polygons ∩ admin-1, so "zone" granularity is province.
- `revisions` table logs any value change on re-ingest (prelim→final).
- Phase 2 (planned): own CHIRPS v3 raster pipeline from data.chc.ucsb.edu as
  a hedge against .gov outages + custom CPI-aligned zones; see SCOPING.md.
