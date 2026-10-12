# V2 expansion — scoping
*Scoped 24 Sep 2026 from a ten-item directive: production-grounded
zones, export crops, hydropower catchments, season splits, flood
extension, crop-weighted rainfall, three new countries, map fixes.
Ordered here by dependency, not by the original list.*

## V2.1 Production ground-truth — foundation, do first (DONE 24 Sep)

- **Data**: GAEZ+ 2015 actual production rasters (Harvard Dataverse
  doi:10.7910/DVN/KJFUO1, Total = irrigated+rainfed, mt, 5-arcmin),
  in data/raw/gaez2015 (gitignored, ~26 MB). MapSPAM SPAM2020 was the
  first choice but its Dataverse deposit is guestbook-gated against
  scripted download — swap in SPAM2020 later by filling the guestbook
  once by hand; the audit script only needs the file paths changed.
- **Crop availability**: maize, rice, sorghum, wheat, tobacco are
  explicit GAEZ crops. Coffee/tea/cocoa sit in one "Stimulants"
  raster — separable per country because each stimulant belt is
  dominated by one crop (ETH=coffee, KEN Kericho=tea, CIV/GHA=cocoa).
  Teff sits in Othercereals. Vanilla and cashew sit in CropsNES —
  admin-region proxies, flagged as approximations.
- **Audit (compute/production_audit.py, run 24 Sep 2026)**: every
  country is below the 70% target. Measured share of national
  production inside current zones, vs the hand estimate:
  KEN 7% (hand said 22), TZA 5% (12), ZWE 12% (20), MDG 12%, ZMB 18%
  (54!), ETH maize 21% (30), UGA 29% (27), RWA 31%, MOZ 35% (32),
  MWI 51% (15 — admin-1 polygon is far bigger than assumed),
  ZAF 59% (78). The hand-estimated CSV substantially overstated
  concentration; the CPI-impulse coverage factors were too generous
  for ZMB/KEN/TZA and too stingy for MWI. Follow-up: refresh
  data/econ/production_weights.csv from these measurements when V2.2
  re-draws the zones (doing both at once avoids re-fitting the
  elasticity twice).

## V2.2 Zone expansion to a target production share

- Target: **70%** of national production of the zone's staple inside
  monitored polygons, per country (set per directive; ZAF likely
  already passes, KEN worst).
- Method: rank the country's FEWS crop-zone polygons (full WFS layer
  pull, one-off) by SPAM production sum; add polygons until the
  cumulative share ≥ 70% or the marginal polygon adds < 2%.
- Each added polygon becomes a normal zone (anchor = polygon
  representative point) with the country's season calendar; EWX +
  local backfills run per new zone. Expect ~10-20 new zones,
  dominated by KEN/ETH/TZA.
- Acceptance: post-expansion audit table ≥ 70% per country, printed
  in the calibration log.

## V2.3 Export-crop zones

- Crops: coffee (ETH Sidama/Jimma, UGA Rwenzori/central, RWA, KEN),
  tea (KEN Kericho, MWI Thyolo, RWA), tobacco (ZWE, MWI, TZA, MOZ),
  cocoa (CIV, GHA — see V2.7), vanilla (MDG Sava — admin proxy),
  cashew (TZA Mtwara, MOZ Nampula — rest-of-crops proxy).
- Zones located from SPAM production density peaks; polygon = FEWS
  crop zone containing the peak, else admin-1.
- New zone attribute `sector` (staple | export) — export zones feed a
  separate "export-crop stress" board column and the FX/receipts
  read-through, NOT the food-CPI impulse (export crops are not in the
  food basket; their channel is export receipts).
- Ky values: perennials (coffee, tea, cocoa, vanilla) do not follow
  the FAO annual-crop water function — use WRSI/rainfall anomaly as a
  stress *indicator* only, no yield-loss percentage. Say so on the
  dashboard.

## V2.4 Hydropower catchments

- Catchments: Kariba (Zambezi upstream), Kafue, Cahora Bassa
  (incremental below Kariba), Shire (Lake Malawi outlet), Rufiji
  (Julius Nyerere dam), Tana cascade (Seven Forks), Blue Nile (GERD).
- Build each as the union of HydroBASINS level-5/6 polygons upstream
  of the dam (Pfafstetter next_down walk) — reuse basins machinery,
  new file data/zones/catchments.geojson, dataset
  `chirps3local_catchment_dekad`.
- Signal: rolling wet-season inflow proxy = catchment rainfall
  anomaly vs climatology + trailing 3-month percentile; pairs with
  the reservoir-level monitors (Kariba live; others need level
  sources — scoped separately, gray until wired).
- This turns the hydro column from "Zambia only" to seven countries
  with at least a rainfall-side light.

## V2.5 Bimodal season split

- Schema: zone_risk and cpi_impulse keyed by (zone, season_name)
  instead of zone; dashboard shows "uga_eastern first" and
  "uga_eastern second" as separate rows with separate lights.
- Affects KEN (add short rains as its own tracked season where zones
  gain OND calendars), UGA, RWA, ETH belg/kiremt/meher.
- This is the fix for the CPI hindcast's East-Africa blind spot: the
  short-rains season becomes a first-class monitored object.

## V2.6 Flood extension: MOZ, MWI, MDG

- Same F-procedure as SCOPING-FLOODS: basins (Limpopo, Zambezi delta,
  Pungwe/Buzi, Incomati; Shire valley + Lake Malawi shore; MDG
  Betsiboka/Ikopa around Antananarivo, SAVA cyclone coast), event
  catalogs 1999-2026, per-country calibration, damage-peak dates.
- Cyclone caveat (Idai/Freddy class) stays: CHIRPS sees the rain
  ~1-2 days ahead at best; the layer flags rain-driven flooding, not
  storm surge. Print this on the country panel.

## V2.7 New countries: CIV, GHA, MAR

- CIV/GHA: cocoa is the point (export channel + global price
  relevance). Staple zones (yam/maize belt) secondary. Flood: Abidjan
  + Accra urban basins are candidates, phase 2.
- MAR: wheat (Casablanca-Settat/Fes-Meknes) — Mediterranean winter
  season (Nov-May), NAO-driven rather than ENSO: the ENSO column does
  not apply; needs an NAO driver column (gray until built).
- CHIRPS/LWRSI cover all three (Africa domain). WFP prices: CIV, GHA
  via HDX; MAR has no WFP feed (use FAO GIEWS or leave prices out).

## V2.8 Crop-area-weighted rainfall

- Replace boolean pixel masks with SPAM harvested-area weights
  (regridded 5-arcmin -> 0.05°): zonal mean = Σ(rain×area)/Σ(area).
- Applies to crop zones only (flood basins stay hydrological pixel
  means). New masks file zones/weights.npz; metrics recomputed;
  **SPI climatology must be rebuilt from the weighted series** so the
  anomaly baseline matches.
- Expected effect: small for compact zones, material for large mixed
  polygons (eth_oromia_maize, zmb provinces).

## V2.9 Map/dashboard fixes (done first, trivial)

- Coverage map: season text moves to a caption line under each panel
  title; city labels get collision offsets. Script committed as
  compute/coverage_map.py.

## Order of work and effort

| Phase | Items | Effort | Depends on |
|---|---|---|---|
| P1 | V2.1 audit + V2.9 map | 1 day | SPAM download |
| P2 | V2.2 expansion + V2.5 season split | 3-4 days | P1 |
| P3 | V2.8 weighted rainfall + metrics rebuild | 2 days | P1 |
| P4 | V2.3 export crops | 2-3 days | P1 |
| P5 | V2.4 hydro catchments | 2 days | — |
| P6 | V2.6 flood extension | 3-4 days | — |
| P7 | V2.7 new countries | 3-4 days | P2 pattern |

## Known limits added by V2

- SPAM2020 is a 2020 snapshot; production geography drifts (ETH wheat
  expansion, MOZ cashew rehab). Re-audit when SPAM2025 lands.
- Vanilla/cashew proxies are admin/aggregate approximations, not crop
  masks — label them on every surface.
- Perennial export crops have no defensible water-yield function
  here; stress indicator only.
- Morocco breaks the ENSO framing; do not force it into the El Niño
  column.
