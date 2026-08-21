# CHIRPS Crop-Zone Rainfall & Soil Moisture Monitor — Scoping
*Scoped 20 Aug 2026. Eastern & Southern Africa agricultural season monitor for food-CPI / fiscal / credit read-through.*

## 1. Objective

Track dekadal (10-day) rainfall and soil moisture **inside key crop zones** of eastern and southern Africa, benchmarked against climatology, to get a 1–4 month lead on:

- **Food CPI** — feeds the Kenya and Zambia CPI models directly (food is ~33% of the Kenya basket, ~53% Zambia). Maize harvest outcomes drive the food component with a known lag.
- **Fiscal / external accounts** — failed seasons force maize imports (FX drain — Zambia 2024 template), drought relief spending, and utility losses where hydro and rainfall correlate (links to the El Niño hydro dashboard).
- **Food security → FEWS NET IPC phases** — a leading indicator for humanitarian appeals and donor-flow dynamics in Malawi, Zimbabwe, Ethiopia.

Country universe (proposed): **KEN, ETH, TZA, UGA, ZMB, MWI, ZWE, MOZ, MDG** — plus SOM/SSD monitored but not credit-relevant.

## 2. Data sources (verified 20 Aug 2026)

### 2.1 Rainfall — CHIRPS v3 (CHC UC Santa Barbara)

- **What**: 0.05° gauge+satellite blended precipitation, 1981–present. v3 is now the primary product; v2 is legacy — **build on v3 from day one**, do not anchor to v2 paths.
- **Access**: plain HTTPS directory listing at `https://data.chc.ucsb.edu/products/CHIRPS/v3.0/` — GeoTIFF/COG/NetCDF, daily/pentad/dekad/monthly, with an **Africa subdomain** (smaller files). No auth, no Akamai games (unlike the IMF scraper).
- **Latency**:
  - **Prelim**: ~2 days after each pentad (updates on the 2nd, 7th, 12th, 17th, 22nd, 27th) — this is the monitoring feed.
  - **Final**: ~3rd week of the following month — this is the modelling feed. **Store both and track prelim→final revisions**; prelim revises when late gauge data arrives.
- **Dekad convention**: days 1–10, 11–20, 21–end of month. Third dekad varies 8–11 days — always compare vs same-dekad climatology, never raw mm across dekads.

### 2.2 Crop zones overlay — USGS FEWS NET (earlywarning.usgs.gov)

- The "crop zones" on the portal are the **cropland masks used in the Croplands WRSI products** (e.g. products 890/894/924 — CHIRPS-Croplands WRSI for the different seasonal windows).
- **Key shortcut — the GeoEngine REST API**:
  - Config/catalog: `https://edcintl.cr.usgs.gov/geoengine5/rest/version/5.0/config`
  - Time series: `https://edcintl.cr.usgs.gov/rest/timeseries/version/5.0/`
  - Serves **zonal-statistic time series (mean/median) over crop zones and admin units** for CHIRPS, soil moisture, WRSI, NDVI, ET — i.e. the exact "rainfall in crop zones" number, pre-computed. API guide: `https://earlywarning.usgs.gov/fews/api/`. The EWX viewer (`/fews/ewx/index.html?region=af`) uses this same API — reverse-engineer request shapes from its network traffic.
- Raster/vector bulk downloads: `https://edcftp.cr.usgs.gov/project/fews/dekadal/africa_east/` (and `africa_south/`).
- **Supplementary zones**: FEWS NET livelihood-zone shapefiles (fews.net → data) for finer framing (e.g. "Zambia maize belt", "Kenya grain basket" aligned to CPI-model geography).

### 2.3 Soil moisture — FLDAS (CHIRPS has no soil moisture)

- **FLDAS Noah LSM** (NASA + FEWS NET): 0.1°, monthly, 1982–present, layers 0–10 cm (surface) and 10–40 cm (~root zone). NetCDF from **NASA GES DISC — requires a free Earthdata login** (credential to provision; token in `.netrc`).
- Same fields are exposed through the USGS portal/EWX API (products 935/936, incl. anomaly and %-anomaly derivatives) **without Earthdata auth** — easier path for v1.
- **WRSI (Water Requirement Satisfaction Index)** — dekadal, crop-specific (maize), already integrates rainfall + ET + soil water holding into a single 0–100 crop-stress index over the crop mask. Arguably the single best headline variable for the dashboard: WRSI < 80 = stress, < 50 = failure. Seasonal windows: East Africa Mar–Sep (belg/long rains) and Oct–Mar; Southern Africa Oct–Apr.

## 3. Architecture (mirrors existing model pattern)

```
~/claude/chirps-crop-monitor/
├── ingest/
│   ├── chirps.py        # dekadal prelim+final COGs, Africa subdomain → data/rasters/
│   ├── ewx_api.py       # GeoEngine timeseries: crop-zone zonal stats, WRSI, FLDAS SM
│   └── zones.py         # one-off: crop mask + livelihood/admin shapefiles → data/zones/
├── compute/
│   ├── zonal.py         # own zonal stats: rioxarray + exactextract over crop mask
│   ├── climatology.py   # 1991–2020 dekadal normals, percentiles, SPI-1/3
│   └── season.py        # cumulative-vs-normal, onset dating, dry-spell counter
├── db/  (SQLite: dekad_zone_stats, climatology, wrsi, fldas_sm, revisions)
├── app/ (Streamlit: season tracker per zone, anomaly map, credit read-through tab)
└── cron: mornings of 3rd/8th/13th/18th/23rd/28th (day after each prelim pentad drop)
```

**Two ingestion strategies — run both:**

| | A: EWX API zonal stats | B: own raster pipeline |
|---|---|---|
| Effort | Days — no raster work | ~1–2 wks |
| Zones | Fixed USGS crop zones / admin units | Any custom polygon (CPI-aligned regions) |
| Variables | CHIRPS, WRSI, FLDAS SM, NDVI, ET | CHIRPS only (extend later) |
| Risk | .gov endpoint stability, gov-shutdown outages, undocumented API churn | CHC endpoint is stable & simple |

**Recommendation**: Phase 1 ships on **A** (fast, includes WRSI + soil moisture for free). Phase 2 adds **B** for CHIRPS so the core rainfall series survives a USGS outage and supports custom zones; keep A as the WRSI/soil-moisture source and as a cross-check on B's zonal stats.

## 4. Core metrics per crop zone

1. **Dekadal rainfall % of normal** (vs 1991–2020) — prelim, ~2-day lag.
2. **Season-to-date cumulative vs normal** — the headline chart; season windows per zone (E Africa bimodal: MAM long rains + OND short rains, Ethiopia belg Feb–May / kiremt Jun–Sep; S Africa single Oct–Apr).
3. **SPI-1 / SPI-3** — standardized, comparable across zones.
4. **Onset of rains** (first dekad ≥ 25mm followed by 2 dekads ≥ 20mm, or similar) and **max dry-spell length** — planting failure signals that cumulative totals hide.
5. **FLDAS 10–40 cm soil moisture percentile** — monthly; slower but less noisy than rainfall.
6. **WRSI level & anomaly** — end-of-season crop outcome proxy.
7. **ENSO/IOD context strip** — reuse the El Niño dashboard's ENSO ingestion; ENSO phase conditions S-Africa Oct–Apr expectations before the season starts.

## 5. Credit read-through layer

- **Zone → CPI mapping**: Kenya grain basket (Rift Valley) → Kenya CPI model food component; Zambia maize belt (Central/Southern/Eastern) → Zambia CPI model. Expose model-ready CSV so the CPI models can pull a "season health" exogenous regressor.
- **Zone → fiscal/external**: failed S-Africa season ⇒ maize import bill (ZMB, MWI, ZWE) — track alongside reserves; drought ⇒ hydro (link ENSO dashboard for ZMB/DRC).
- **Prices cross-check**: FEWS NET staple-price series (maize, retail markets) as the transmission check between rainfall anomaly and CPI print.
- Alert rules (email/log): season-to-date < 75% of normal at mid-season; WRSI < 80 over > 30% of a zone; dry spell ≥ 2 dekads inside planting window.

## 6. Gotchas / traps

- **CHIRPS v2 deprecation**: most tutorials and old code point at v2.0 paths; use `v3.0` throughout. v2/v3 values differ — never mix versions in one climatology.
- **Prelim revisions**: keep `prelim` and `final` in separate columns; backfill final ~week 3 of following month and log revision size (matters if a prelim print triggered an alert).
- **Variable dekad length**: normalize by same-dekad climatology, not mm/day naive.
- **GES DISC Earthdata auth**: only needed if going straight to NASA for FLDAS — avoid in v1 by using the USGS API mirror.
- **USGS endpoint fragility**: .gov shutdown/maintenance outages are a real failure mode — hence the Phase-2 own-pipeline hedge; cache all API pulls raw.
- **Crop mask ≠ production weights**: the mask says where crops are, not how much; for CPI purposes weight zones by production stats (FAOSTAT / national ag ministry) not area.
- **Bimodal seasons**: a good OND short-rains print doesn't offset a failed MAM in Kenya — track seasons separately, never calendar-year aggregate.

## 7. Build phases

| Phase | Deliverable | Est. effort |
|---|---|---|
| 1 | EWX API ingestion → SQLite → basic Streamlit season tracker (rainfall + WRSI + SM per zone, ~9 countries) | 3–5 days |
| 2 | Own CHIRPS raster pipeline + custom CPI-aligned zones + climatology/SPI/onset metrics | 1–2 wks |
| 3 | Credit layer: CPI-model export, FEWS NET price cross-check, alert rules, ENSO strip | 3–5 days |
| 4 | Backtest: 2015–16 & 2023–24 El Niño droughts vs realized food CPI / maize imports — validates lead times | 2–3 days |

## Sources
- [CHC CHIRPS v3](https://www.chc.ucsb.edu/data/chirps3) · [v3 README](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt) · [CHC data index](https://www.chc.ucsb.edu/data)
- [USGS FEWS NET portal](https://earlywarning.usgs.gov/fews/) · [API guide](https://earlywarning.usgs.gov/fews/api/) · [Croplands WRSI product](https://earlywarning.usgs.gov/fews/product/890/) · [belg WRSI](https://earlywarning.usgs.gov/fews/product/894/) · [Apr–Sep WRSI](https://earlywarning.usgs.gov/fews/product/924/)
- [FLDAS soil moisture 10–40 cm](https://earlywarning.usgs.gov/fews/product/936/) · [0–10 cm](https://earlywarning.usgs.gov/fews/product/935/) · [FLDAS paper](https://www.nature.com/articles/sdata201712)
- [Digital Earth Africa CHIRPS specs](https://docs.digitalearthafrica.org/en/latest/data_specs/CHIRPS_specs.html)
