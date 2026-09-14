# SEAS5 seasonal rainfall forecasts — scoping, 14 Sep 2026

Question: would ECMWF SEAS5 (51-member, monthly inits, leads 1–6 months)
usefully predict rainfall levels for the monitor's crop zones?

Short answer: **probably yes for roughly half the (zone, season) book, no
for the rest, and we already own the machinery to find out which is which
for ~a day's work.** Do the backtest first; build the display only for
combinations that pass. The backtest is Phase S1 and is also the kill gate.

## Prior evidence (measured, not assumed)

The elnino-hydro-dashboard already runs SEAS5 (`ingest/seas5.py`) and has a
hindcast skill harness (`seas5_skill.py`): 1993–2016, 25 members/year,
ensemble median vs observed CHIRPS, scored on correlation / tercile hit
rate / driest-8 capture. Results for the two Zambezi basins:

| Init | Lead to season | Kariba r | tercile | Kafue r |
|---|---|---|---|---|
| Jul | 4–6 mo | +0.19 | 21% (chance 33%) | +0.05 |
| Sep | 2–6 mo | +0.17 | 46% | −0.05 |
| Oct | 1–5 mo | **+0.43** | **50%** | +0.24 |

Three lessons that shape this scoping:
1. **Skill is a property of (region, season, init month), not of SEAS5.**
   At long lead the model returns its own climatology (hindcast forecast
   range was ~¼ of the observed range) — a number that looks like a
   forecast and contains nothing.
2. Skill turns on at short lead. Oct-init for a Nov–Mar season is real but
   modest (r≈0.4 ≈ 18% of variance).
3. Read tercile probabilities, never the median (chronic under-dispersion).

## Expected skill by season type (hypotheses for the backtest)

- **Southern "main"/msimu, Oct/Nov–Apr — 11 zones** (zmb×3, mwi, zwe, moz×2,
  mdg, zaf×3, tza_mbeya). ENSO-forced DJF core; obs coupling to ONI is
  strong in exactly these belts (Mashonaland −0.64, S Zambia −0.58 measured
  in the dashboard). Expect Oct/Nov inits to pass, Aug/Sep marginal.
  Madagascar likely weakest (cyclone-dominated).
- **Ethiopian kiremt/meher, Jun–Sep — eth_arsi, eth_gojjam, eth_oromia_maize.**
  ENSO-forced; literature and 2015 hindcasts suggest May/Jun inits carry
  moderate skill. Testable identically.
- **Kenya long rains (MAM) — ken_uasin_gishu, ken_trans_nzoia; Uganda
  "first"; belg.** The MAM "predictability desert". Expect fail at all
  inits. If the backtest confirms, SEAS5 simply doesn't cover the monitor's
  Kenya grain-basket season — worth stating loudly, because it's the
  counter-intuitive half of the answer.
- **Uganda "second" (Aug–Nov).** OND-adjacent, IOD/ENSO-forced; expect
  Jul/Aug inits to pass. NOTE the awkward gap: the single most predictable
  East African season (OND short rains) is barely in the zone book — only
  via Uganda-second and the Kenya *flood* basins. Two cheap wins if SEAS5
  earns its place: (a) an OND-tracked marginal-agriculture zone (the
  dashboard's ken_asal box is importable as-is), (b) SEAS5 OND terciles
  over the 7 flood basins as an early-arming layer months ahead of
  CHIRPS-GEFS's 15-day ceiling.

## What it would add (and not add)

The monitor is a nowcast: dekadal observed rainfall, SPI, onset, dry
spells, soil moisture, and 15 days of CHIRPS-GEFS. It answers "how is the
season going." SEAS5 adds the only missing axis — "how is the season
likely to END" — at 1–5 month leads. The right product is therefore not a
forecast panel but a **season-outcome projection**: observed season-to-date
from the existing pipeline + SEAS5-conditioned remainder ⇒ P(final season
total lands in dry / normal / wet tercile), one row per passing zone,
refreshed monthly. During the season the observed share grows and the
forecast share shrinks, converging on the nowcast — same logic as the
dashboard's rainfall tracker, extended forward.

Not added: intra-season structure. SEAS5 monthly means say nothing about
onset timing or dry-spell placement, which season_metrics has shown is the
crop damage mechanism (Feb-2024 pattern). Totals terciles only.

## Method decisions (inherited, cheap)

- Bias handling: member totals as ratios to the model's own 1993–2016
  hindcast climatology for that init month (existing method), applied to
  each zone's CHIRPS v3 climatology. Avoids v2/v3 base issues entirely.
- Zonal means at 1°: coarse vs crop polygons — use the zone bounding boxes
  (registry anchors + WFS extents); at seasonal-total scale the box mean is
  the same approximation already accepted for the dashboard crop boxes.
- Verification target: the monitor's own local CHIRPS v3 zonal series, so
  forecast and verification share a base.
- Gate to display (mirrors APPLICABILITY): pass = hindcast tercile hit
  ≥ 45% AND r ≥ +0.35 at that init; publish the gate table in the app.

## Build shape

Phase S1 — the decision gate (~1 day, mostly CDS queue time):
port `seas5_skill.py`, one continental hindcast box (12°N–35°S, 10–52°E)
per init month (Feb, May, Jun, Jul, Aug, Sep, Oct, Nov to cover all season
starts; ~8 cached netCDFs), score every (zone, season, init). Output:
`SEAS5-SKILL.md` table. Kill criterion: fewer than ~6 passing combinations
⇒ stop, record the negative result, done.

Phase S2 — only if S1 passes (~1 day): monthly fetch of the live 51-member
init (+ hindcast cache), season-outcome projection into SQLite
(`seas5_outlook` table), Streamlit block on the zone pages + a gate table,
`--if-due` monthly step in run_update.sh (init published ~5th–13th; fetch
after the 13th).

## Mini deployment contract answers

Amendment to the existing chirps-crop-monitor deployment (which predates
the contract: cron + venv; not migrated here — noted, not fixed).

1. Tier: `automation` (deterministic pipeline, no untrusted-content
   decisions, no logins).
2. Schedule: monthly, gated `--if-due` inside the existing 5-daily cron
   chain (runs when a new init month is available after the 13th).
3. Secrets: CDS token — on the mini as Keychain `CHIRPS_CDSAPI_KEY` read
   via secrets.py and exported as `CDSAPI_KEY`/`CDSAPI_URL` env (cdsapi
   supports env config; do NOT write ~/.cdsapirc on the mini). MacBook dev
   keeps its existing ~/.cdsapirc.
4. Browser: none.
5. Outbound (additions): `cds.climate.copernicus.eu`,
   `object-store.os-api.cci2.ecmwf.int` (download host; DNS-flaky, retries
   needed — seen in the dashboard build). No new recipients.
   Licence note: `seasonal-monthly-single-levels` licence is accepted per
   CDS account; if the mini uses a separate CDS account it must accept once
   on the website (403 "required licences not accepted" otherwise).

## Recommendation

Run Phase S1. The current regime raises the stakes in both directions: a
very strong El Niño + positive IOD is exactly when seasonal forecasts carry
their most signal (strongly forced years), and the monitor's southern zones
+ Uganda-second + kiremt are the seasons where that signal should land —
but the dashboard's July lesson (confident-looking climatology at long
lead) says none of it is displayable until the hindcast gate proves it,
zone by zone. Expected outcome: ~8–12 passing combos (southern zones at
Oct/Nov, kiremt at May/Jun, uga_second at Jul/Aug), a documented fail for
the Kenya grain basket's MAM season, and a monthly outlook table the
food-CPI models can consume.
