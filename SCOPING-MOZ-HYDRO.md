# Mozambique hydropower layer — scoping, 14 Sep 2026

Why: Mozambique's power story is one asset — Cahora Bassa — and that asset
is simultaneously the country's power-sector export earner (majority of
output sold to Eskom, plus ZESA), the region's most stressed reservoir
right now (~−1.3σ vs seasonal normal, the reading behind Mozambique's
"high" score in the elnino dashboard), and the flood valve for the lower
Zambezi. A hydropower layer here is mostly a Cahora Bassa layer, and the
monitor is unusually well placed to build it because Cahora sits
DOWNSTREAM of things already tracked.

## Structure: what drives Cahora Bassa

Inflows, in order: (1) Kariba turbine releases (the ZRA allocation we
track daily in the elnino dashboard — routes down the Mupata gorge in
days-to-weeks); (2) the **Luangwa** — the great unregulated tributary
(~140–150k km², eastern Zambia), flashy, wet-season dominated, joining at
Zumbo at the head of the lake, and currently tracked by NOTHING anywhere;
(3) Kafue outflows (ITT tailwater, DAHITI 38838, already curated in the
elnino dashboard); (4) local intervening rainfall. So the layer can show
storage AND its drivers: release policy upstream + rainfall over the
unregulated catchment.

Secondary asset: the Chicamba–Mavuzi cascade (~90MW, EDM, Revué river,
Beira corridor supply). Small but cheap to add given direct altimetry.

Not monitorable, stated on the page: the Songo–Apollo HVDC line (the
1,400MW export artery — its availability is mechanical/contractual), EDM's
gas fleet, and the south-region Eskom import loop. Disclosure-capturable
(D2): HCB is BVM-listed and publishes semi-annual results with generation
GWh, export volumes and reservoir levels — the same disclosure-register
pattern as the DRC layer's Ivanhoe/Glencore feed.

## Feasibility — verified

| Series | Source | Status |
|---|---|---|
| Cahora Bassa level | DAHITI 209 (+ NASA GWM cross-check) | proven: ~1,000 obs 2002→Jul 2026; GWM fresh to mid-Jul |
| Chicamba reservoir | DAHITI 40103 (−19.13, 33.11) | verified in target list today |
| Kafue outflow proxy | DAHITI 38838 (ITT tailwater) | proven in elnino dashboard (528 obs) |
| Kariba discharge | ZRA reservoir-data page (daily) | proven scraper in elnino dashboard; history 2017– reconstructed there |
| Luangwa rainfall | CHIRPS zone via HydroBASINS walk | same machinery as DRC catchment zones |

## Design (monitor idioms; shares the DRC layer's plumbing)

- **Zone**: `zmb_luangwa_basin` — HydroBASINS lev-7 upstream walk from the
  Zumbo confluence (~ −15.61, 30.44), UP_AREA sanity check (expect
  ~140–150k km²; it is a big zone — CHIRPS zonal mean is still fine at
  this scale, precedent: the elnino Kariba catchment at 683k km²).
  Season ("wet", 11, 4). Dekadal rainfall + SPI + season tracker free.
- **Stations** (extend the DRC layer's ingest/dahiti_levels.py dict):
  cahora_bassa 209, chicamba_reservoir 40103, itt_tailwater 38838.
- **Kariba discharge**: port the small ZRA reservoir-data parser (live
  page: current month × 3 years, level + turbine discharge; plausibility
  guards from the elnino version). History seed: one-time export of the
  reconstructed daily series (2017–, wayback-derived) from the elnino
  repo into data/seed/kariba_discharge_daily.csv, committed with
  provenance note; live scraper appends weekly thereafter. Duplicates a
  scrape the elnino dashboard does — accepted, because the monitor must
  be self-contained on the mini.
- **Signals (display-only until calibrated)**: Cahora level z-score and
  day-of-year percentile with empirical percentile bands (the widely
  cited FSL ≈ 326m / minimum operating ≈ 295m lines may be drawn but must
  be verified against HCB's own disclosures during build, not trusted
  from memory); Chicamba z-score; an inflow panel (Kariba 30-day
  discharge + Luangwa season rainfall vs band).
- **Streamlit**: one "Mozambique hydro" view: Cahora chart with bands,
  inflow panel, Chicamba chart, limitations note (HVDC/exports not
  observed; south imports from Eskom).

## D2 (later)

HCB disclosure register (semi-annual results scrape → generation GWh,
exports, reported levels — also the verification source for the band
constants); a "spill watch" context flag (level percentile high + inflow
surge = lower-Zambezi flood risk at Marromeu/Caia, Jan-2023 template —
natural coupling to the flood layer's idiom); episode catalog (2019 and
2024–25 lows, 2023 spills) before any alerting.

## Contract amendment

Tier automation; outbound += `www.zambezira.org` (dahiti.dgfi.tum.de
already added by the DRC layer); no new secrets; ZRA scrape rides the
existing 5-daily cron, DAHITI shares the weekly gate.

## Phases & sequencing

- **M1 (~½ day, Sonnet-able)**: Luangwa zone, station additions, ZRA
  parser + seed, Streamlit view.
- **M2 (~½ day)**: HCB register, spill watch, band verification, episode
  catalog.
- **Sequencing constraint**: the DRC D1 agent is currently editing the
  same files (zones, dahiti ingest, app, run_update.sh). M1 must start
  only after that lands, and should EXTEND its modules rather than add
  parallel ones.
