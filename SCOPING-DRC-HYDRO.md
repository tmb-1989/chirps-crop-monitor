# DRC hydropower layer — scoping, 14 Sep 2026

Why: DRC copper/cobalt is the credit exposure; power is the binding
constraint on the mining belt; the monitor should watch the *hydrological*
channels of that constraint the way it watches crop rainfall. Prior
analysis (elnino-hydro-dashboard, Jul 2026) established the structure and
this scoping inherits it.

## The structure: three sub-systems, two of them monitorable

1. **Inga (lower Congo; Inga I+II ≈ 1.8GW nameplate, chronically derated).**
   Run-of-river on ~40,000 m³/s — annual rainfall volume is never the
   constraint and the Congo basin is only weakly ENSO-sensitive. The one
   hydrological channel is the **étiage**: the Jul–Sep (secondary Feb–Mar)
   low-water season, when intake head can curtail output. Everything else
   about Inga risk (turbine availability, the Nkokolo canal silting,
   transmission) is mechanical and NOT observable from climate data — the
   layer must say so on the page.
2. **The Katanga cascade — the underappreciated piece.** Nzilo (108MW) +
   Nseke (260MW) on the upper Lualaba, Mwadingusha (68MW) + Koni (42MW) on
   the Lufira, Busanga (240MW, Sicomines, 2021) — ~700MW of RESERVOIR hydro
   in Katanga-highlands catchments that share the Zambezi rainfall regime
   (Nov–Apr wet season), directly feeding the Kolwezi/Likasi mining belt.
   Unlike Inga, these are rainfall- and ENSO-relevant, and they sit in the
   same climate the monitor already tracks next door (zmb zones, Mweru).
3. **Outcome data.** BCC monthly bulletin Tableau I.6 carries national
   electricity production/consumption monthly from 2015 (~1,100 GWh/mo);
   the parser pattern for that exact PDF already exists
   (elnino-hydro-dashboard ingest/bcc.py parses Tableau I.2 from the same
   cached file). Cu/Co production is already ingested there too.

Mechanical availability is not sensor-monitorable but IS disclosure-
capturable, and the layer must capture it (user requirement, 14 Sep):
**Inga II at unit level** and **Mwadingusha** are both tied to mining
counterparties that publish — Ivanhoe/Kamoa funds and reports the Inga II
turbine refurbishments (Turbine 5 for Kamoa's supply; later units under
discussion), Glencore/KCC rehabilitated and draws Mwadingusha (and Koni
next on the Lufira). Their news releases and quarterlies are the de facto
plant-status feed for exactly the two assets that matter to copper. See
"unit status register" below. Still not covered: transmission outages and
the Zambia import contracts for Kolwezi (CEC + Kalumbila–Kolwezi) — the
Zambian surplus side lives in the elnino dashboard and is cross-referenced,
not duplicated.

## Feasibility — verified today (DAHITI v2, live checks)

| Station | id | Obs | Span | Range | Role |
|---|---|---|---|---|---|
| Congo at Inga (6km from dam) | 13226 | 156 | 2002 – **4 Sep 2026** | 38.3–66.5m (28m swing!) | étiage monitor |
| **Nzilo Reservoir (Lac Delcommune)** | 13235 | 120 | 2008 – Jul 2026 | 1264.0–1268.2m | Lualaba cascade storage |
| Lufira below Tshangalele | 21410 | 126 | 2016 – Aug 2026 | 878.5–879.6m | Mwadingusha outflow proxy |

The Nzilo hit is the scoping's key find: direct altimetry on the reservoir
that regulates the Lualaba cascade. **Lac Tshangalele (Mwadingusha's
reservoir) has NO DAHITI target** — verified against all 1,059 DRC entries;
the downstream Lufira station is the interim proxy (narrow 1.1m range —
flow indicator, not storage). Two follow-ups: submit a DAHITI new-target
request for Tshangalele (~400 km², likely track-crossed; DAHITI's API
supports user-requested targets), and pick it up via Hydroweb/SWOT when
the Theia account lands. Busanga: no altimetry, no public data —
nameplate note only. Current Inga reading
44.2m sits in the low-water zone of its 38–67m range, as expected for
September; the layer's job is to convert that into a day-of-year
percentile.

## Design (monitor idioms, maximum reuse)

- **Zones**: two hydro-catchment zones so Mwadingusha carries its own
  rainfall signal rather than being averaged into the Lualaba's:
  `drc_lualaba_hydro` (upper Lualaba above Nzilo/Busanga, box ≈
  24.8–26.5E, 12.2–10.5S) and `drc_lufira_hydro` (upper Lufira above
  Tshangalele, box ≈ 26.6–28.2E, 11.8–10.4S); verify both against
  HydroBASINS lev-5 during build. Registered like crop zones ⇒ dekadal
  CHIRPS, SPI, anomaly tracking, season metrics free. Season: ("wet", 11, 4).
- **Unit status register** (the Inga II turbine / Mwadingusha capture):
  a small `hydro_units` table — asset, unit, mw, status
  (operating/refurb/outage), effective_date, source_url, quality — seeded
  during build from Ivanhoe (Inga II turbines, Kamoa supply MW) and
  Glencore/KCC (Mwadingusha, Koni) disclosures, then maintained by a
  monthly `--if-due` scraper over both companies' news-release indexes
  that flags new Inga/Mwadingusha/Koni mentions for review rather than
  auto-parsing MW into the table (extraction-only, provenance kept,
  no LLM in the cron path). Seed values must come from the actual
  disclosures at build time, not from memory. The Streamlit view shows
  the register as a status strip above the level charts: nameplate vs
  currently-operating MW per asset.
- **Ingest `dahiti.py`**: port from elnino dashboard (v2 API, wse fields,
  curated station dict = the 3 above). Weekly `--if-due` in the cron
  chain; sequential, polite.
- **Ingest `bcc_power.py`**: port the bcc.py pattern for Tableau I.6;
  monthly `--if-due`; ~18MB PDF, very slow server (~5 min), cache under
  data/; parse-integrity check (monthly sum vs annual row) as in the
  copper parser.
- **Signals** (display-first; NO alerting until calibrated):
  - Inga étiage: level percentile within day-of-year climatology
    (2002–2025 base, ~24 yrs). Proposed bands: <20th pct = "low-water
    watch", <10th = "low-water alert" — but ship as display-only until we
    can line the series up against ≥3 documented SNEL curtailment
    episodes; the calibration catalog does not exist yet and inventing
    thresholds without it is how false alarms get built.
  - Nzilo: level z-score vs same-calendar-month climatology (the elnino
    regional-panel method verbatim; ~18 yrs of history is ample).
  - Katanga zone rainfall: season cumulative vs climatology band —
    existing machinery.
- **Streamlit**: one "DRC hydro" view — three level charts with bands,
  the zone rainfall tracker, monthly GWh bars, and a plain-language
  limitations note (mechanical risk not covered; Kolwezi also imports
  from Zambia).

## Mini contract amendment

Tier `automation` (unchanged). Outbound additions:
`dahiti.dgfi.tum.de`, `www.bcc.cd`. New secret: `CHIRPS_DAHITI_API_KEY`
in the mini Keychain, read via secrets.py (MacBook dev may keep the
existing elnino .env key). No browser, no new recipients. Cadence fits
inside the existing 5-daily cron with `--if-due` gates (DAHITI weekly,
BCC monthly).

## Phases

- **D1 (~½ day, Sonnet-able)**: zone registration (both), dahiti ingest,
  three level series into SQLite, Streamlit view with climatology bands,
  cron wiring. Deliverable: the layer visible, display-only.
- **D2 (~½–1 day)**: BCC Tableau I.6 power ingest + GWh panel; étiage
  percentile computation; **hydro_units register seeded from Ivanhoe and
  Glencore/KCC disclosures + monthly mention-scraper**; DAHITI new-target
  request for Tshangalele; assemble the SNEL curtailment episode catalog
  (news/IMF Article IV/SNEL statements) and only then decide alert
  thresholds.
- Explicitly out of scope: Grand Inga anything, SNEL plant-level data
  (doesn't exist publicly), Busanga (no data), Zambia import modelling
  (elnino dashboard's job).

Cross-feed: étiage percentile + Nzilo z + GWh trend are inputs the
elnino-hydro-dashboard's DRC risk row could later consume in place of its
current Mweru/Tanganyika proxy — one-way street, monitor → dashboard.
