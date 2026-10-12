# Scoping — reservoir-level hydro risk for Ethiopia, Mozambique, Uganda (V3.2)

Goal: extend the Kariba model (live reservoir state + thresholds +
catchment inflow proxy → board light) beyond the ZRA scrape. Kariba's
three format drifts in one season taught us: source reliability is the
scope question.

## 1. What the Kariba model actually is

1. **Live reservoir state** — level (m), % usable storage, weekly
   drawdown rate (ZRA weekly bulletin scrape).
2. **Engineering thresholds** — 475.5 m minimum operating level;
   calibrated rationing boundaries (478 m red / 480 m yellow,
   drawdown 0.20 / 0.15 m-per-week) from the elnino-hydro-dashboard
   sister project.
3. **Inflow proxy** — catchment rainfall percentile (V2.4, live).
4. Worst-of combination → hydro light.

Pieces 1 and 2 are the gap for new dams; piece 3 already runs for all
seven catchments.

## 2. Data source survey (verified 5 Oct 2026)

**NASA GWM** (earth.gsfc.nasa.gov/gwm — the successor to USDA
G-REALM, which retired): satellite radar altimetry lake levels. Open
HTTP, no API key, plain-text per-target files, bulk tarball
(`gwm/zip/lake.10.tar.gz`, 324 MB, 1038 targets) plus a dedicated
**HEP (hydropower) reservoir product**
(`har.gsfc.nasa.gov/pub/danu/power/Reservoir_data/GWM_HEP_reservoirs.tgz`,
5 MB, curated hydro reservoirs — the natural daily fetch).

Verified targets and freshness:

| Target | id | cadence | latest obs | level |
|---|---|---|---|---|
| Lake Victoria (3 passes) | 000314/758/1259 | 10-day | 2026-09-24 | 1136.4 m |
| Cahora Bassa | 000414 | 10-day | 2026-09-23 | 315.0 m |
| Gilgel Gibe III (ETH) | 002323 | 10-day | 2026-09-30 | 858.7 m |
| Tekeze (ETH) | 001597 | 27-day | 2026-08-28 | 1145.8 m |
| Lake Kyoga / Albert (UGA) | 000398/405 | 10d / 27d | Sep 2026 | — |
| Lake Malawi (MWI, bonus) | 000317 | 10-day | 2026-10-01 | 475.7 m |
| **Kariba (cross-check)** | 000394 | 10-day | 2026-09-30 | 480.69 m |

Kariba altimetry vs the same-week ZRA scrape (480.97 m): **0.3 m
agreement** — altimetry is accurate enough for threshold work and
gives us a free corruption sentinel for the ZRA feed.

**GERD is absent from every GWM product** (10-day, monthly, HEP) —
politically sensitive target. Alternatives: DAHITI (TU Munich — has a
GERD target, needs a free registered API key) or CNES Hydroweb
(account + API). Both are account-gated; neither verified yet.

**Operator bulletins**: HCB/ARA-Zambeze publish Cahora Bassa PDFs
(scrape-fragile, Portuguese); Uganda DWRM publishes Jinja gauge data
irregularly; Ethiopia publishes nothing for GERD. Altimetry dominates
on reliability.

## 3. Proposed model per country

All three get: GWM level history (2002– for Jason-era targets) +
current level + 8-week trend, combined worst-of with the existing
V2.4 catchment rainfall proxy.

- **Mozambique — Cahora Bassa** (2.1 GW): level vs operating band
  (MOL 295 m, FSL 326 m, published HCB figures) + level percentile vs
  own altimetry history + drawdown rate. Closest to a true Kariba
  clone.
- **Ethiopia — Gibe III** (1.87 GW, domestic swing producer) same
  treatment (MOL 800 m, FSL 892 m); **Tekeze** (0.3 GW) 27-day,
  secondary. **GERD: storage stays gray** ("no public level feed")
  unless we add a DAHITI key; its catchment rainfall proxy (cat_gerd)
  keeps carrying the rainfall side. Honest per gray-never-green.
- **Uganda — Victoria Nile cascade** (Nalubaale/Kiira, Bujagali,
  Isimba, Karuma ≈ 1.4 GW): all run-of-river off Lake Victoria —
  one monitor: Victoria level percentile + trend (the 2005-06 crisis
  level ≈ 1134.1 m and 2020 flood ≈ 1137.3 m bracket the usable
  range). No %-storage concept; percentile bands only.

## 4. Threshold philosophy (decision)

(a) **Percentile bands vs own altimetry history** — p10/p25 red/yellow,
    automatic, honest, no engineering research debt; or
(b) **Engineering anchors** — MOL/FSL/rationing levels researched per
    dam (Kariba-style), more meaningful but a research liability per
    dam and unverifiable for some; or
(c) **Hybrid (recommended)** — percentile bands always computed, plus
    the two published engineering anchors (MOL, FSL) where they exist;
    red = below p10 OR within 20% of MOL on the operating band.

## 5. Build shape

- `ingest/gwm_levels.py` — daily fetch of the HEP tarball (5 MB),
  parse target files (fixed-format, documented header), upsert
  `reservoir_levels` (target_id, date, level_m, mission); state table
  per reservoir in live.sqlite. Kariba target ingested too, as a
  sentinel against ZRA feed corruption (alert on >1 m divergence).
- `compute/country_risk.py` — generalize `_kariba_light` into a
  reservoir-light that takes (level, band, percentile, trend,
  thresholds); ZMB keeps the richer ZRA feed as primary.
- App: reservoir level chart per country in the Hydropower section
  (like the Kariba chart), catchment chart already there.
- Catchments: cat_gibe3 (Omo upstream) and cat_tekeze added to
  V2.4 generation; cat_victoria (Lake Victoria basin) for Uganda.

## 6. Decisions (5 Oct 2026, with Thaddeus)

- **Thresholds: engineering anchors only** — no percentile bands.
  Uniform rule on the anchored band: position = (level − MOL) /
  (FSL − MOL); red < 10%, yellow < 25% of the band.
- **GERD: gray** ("no public level feed"); Ethiopia's reservoir leg =
  Gibe III. **Tekeze dropped from v1**: its altimetry range
  (1124.9–1150.1) is incoherent with the published FSL 1140 m —
  datum unresolved.
- **Lake Malawi added** (MWI reservoir leg, Kamuzu Barrage band
  473.2–477.2 m). **Kariba altimetry sentinel: not included.**
- Anchor validation (altimetry datum vs published bands): Victoria's
  file range exactly brackets the 2006 crisis min (1134.28) and 2020
  flood max (1137.28) — those two in-datum event levels ARE its
  anchors; Cahora Bassa file range 304.2–325.8 vs FSL 326 ✓;
  Gibe III max 891.2 vs FSL 892 ✓; Malawi range 473.2–476.7 vs
  barrage band ✓.

## 7. Risks

- GWM is a research product: no SLA, target files can re-bias when
  missions change (bias rows are in the header — parse, don't
  hardcode). Staleness watchdog entry per reservoir.
- Altimetry datum ≠ operator datum (0.3 m at Kariba): use each
  target's own history for percentiles; engineering anchors applied
  with a ±0.5 m tolerance band.
- 10-day cadence + processing lag ≈ 2 weeks worst case — fine for
  seasonal risk, not for operational dispatch.
