# Flood-watch layer — scoping
*Scoped 23 Aug 2026, motivated by the 2026 Kenya floods backtest (see README
and strict-trigger audit: 50% precision on disaster-grade events, misses
driven by geography, not thresholds).*

## 1. Objective

Flag, per river basin, the two states from which East/Southern Africa's
rainfall-driven flood disasters historically emerge, with 1–3 weeks of lead:

- **Saturation floods** — sustained excess rain loads a catchment until it
  has no absorption capacity (Kenya 2018, 2019, 2020, 2026).
- **Dry-whiplash flash floods** — extreme rain onto a parched, crusted
  catchment at season onset, often after a failed season (Horn 2023 OND,
  desert-margin events).

Credit read-through: humanitarian appeals and fiscal reallocation (KEN, MWI,
MOZ), infrastructure/transport disruption, crop destruction in flood plains
(a *wet* damage channel WRSI cannot see), and hydro/dam management.

## 2. Geography: basins, not crop zones

The 2024 Mai Mahiu miss showed one headwater crop zone cannot see storms
elsewhere. Replace crop polygons with hydrological basins:

- **Source**: HydroSHEDS / HydroBASINS level 5–6 polygons (free, standard,
  Pfafstetter-coded so upstream→downstream relations are encoded in the
  basin id). One-off download, ~MBs per country.
- **Pilot universe (Kenya)**: Nzoia (headwaters = our existing grain-basket
  zones → Budalangi flood plain — the 2007-documented causal chain), Tana
  (headwaters Aberdares/Mt Kenya → Garissa flood plain + hydro cascade),
  Lake Victoria shore basins, Nairobi urban catchment (Athi upper), Ewaso
  Ng'iro. ~8–10 basin polygons.
- **Extension countries by flood-credit relevance**: Malawi (Shire — Lower
  Shire flood plain), Mozambique (Limpopo, Zambezi, Pungwe — note cyclone
  rainfall is CHIRPS-visible but cyclone warning itself is a different
  system), Tanzania north (bimodal OND floods), Ethiopia (Awash).
- Headwater→downstream pairs carried as a static table with an approximate
  routing lag (Nzoia headwater→Budalangi ≈ 2–5 days) from Pfafstetter
  topology + literature; v1 uses the pair only to label alerts
  ("downstream: Budalangi"), not to model hydrographs.

## 3. Data (all already flowing, plus one addition)

| Input | Source | Status |
|---|---|---|
| Pentad rainfall (obs, ~2-day lag) | EWX API / local CHIRPS | have |
| Dekad rainfall + % of normal | local pipeline | have |
| Antecedent wetness: SPI-3, FLDAS SM percentile | metrics / EWX | have |
| Forecast: CHIRPS-GEFS 5/10/15-day | EWX API (`chirps-gefs*_global_pentad_data`) | **add** (ingest per basin) |
| Basin polygons | HydroBASINS | **add** (one-off) |

Zonal stats over basins reuse `chirps_raster.py` unchanged (new GeoJSON in,
same masks machinery). GEFS per basin comes from the EWX timeseries API the
same way as any other dataset.

## 4. Signal design

Two signatures, each with watch/alert tiers, evaluated per basin per pentad:

**A. Saturation (the 2018/2020/2026 signature)**
- *Arm*: antecedent SPI-3 ≥ +1 **or** FLDAS SM ≥ 90th percentile.
- *Watch*: armed + 2 consecutive pentads ≥ 150% of pentad normal (with an
  absolute floor, e.g. ≥ 25mm/pentad, to kill dry-season % noise).
- *Alert*: armed + observed ≥ 200% pentad **or** GEFS 10-day ≥ 180% of
  normal on top of a watch. GEFS converts concurrent detection into
  ~1–2 weeks of lead.

**B. Dry-whiplash (the 2023-OND signature)**
- *Arm*: SPI-3 ≤ −1 **or** SM ≤ 20th percentile (parched catchment).
- *Alert*: GEFS 5/10-day ≥ 250% of normal or ≥ seasonal-onset extreme
  threshold. Observed confirmation upgrades severity.

Headwater basins propagate their state to the paired downstream basin with
the routing-lag label. Absolute floors and percentile arms are per-basin
(calibrated), not global constants.

## 5. Calibration & backtest (the core of the build)

- Event catalog: EM-DAT + FloodList + ReliefWeb, Kenya 1999–2026 (~12–15
  major events + localized set) — assembled once, versioned in the repo.
- Grid-search thresholds per basin to maximize F1 against the catalog;
  report precision/recall per basin and per signature, in-sample honesty
  (28 years, ~15 events — no holdout pretensions, label it calibration).
- Acceptance bar (from the crop-zone baseline): ≥ 70% precision on
  major events at ≤ 1 alert/basin/year, recall ≥ 80% including 2006, 2023,
  2024-class events. If Nairobi urban misses stay unresolved, say so —
  urban flash floods may be honestly out of scope for 0.05° rainfall.

## 6. Outputs

- **Dashboard**: third view "Flood watch" — basin table (state, tier, days
  armed, GEFS outlook, downstream pair), stripes-style history strip per
  basin, map optional later.
- **Cron alerts**: tier changes appended to a `flood_alerts` table + line
  in cron log; optional email/ntfy hook later.
- **CSV export** per basin for credit models (like the season-health
  regressor).

## 7. Phases & effort

| Phase | Deliverable | Effort |
|---|---|---|
| F1 | HydroBASINS ingest, Kenya basins, GEFS per basin, backfill | 2–3 days |
| F2 | Event catalog + calibration/backtest report | 2–3 days |
| F3 | Dashboard view + cron alert wiring | 2 days |
| F4 | Extend to more countries | 1–2 days each |

F4 status (Sep 2026): extended to **Ethiopia, Tanzania, Rwanda, Uganda**
(17 new basins, per-country event catalogs / flood-season months / regional
alerts / backtests). Malawi and Mozambique remain candidates — note the
cyclone caveat in §8 for Mozambique.

## 8. Known limits & gotchas

- CHIRPS underestimates convective extremes (gauge-sparse, IR-based) — the
  % thresholds absorb some of this; don't read absolute mm as gospel.
- Dekad/pentad latency means the *observed* leg is 3–7 days behind; GEFS
  is the lead — and GEFS skill decays after day ~7; treat 10/15-day as
  direction, not magnitude.
- Storm-scale (hours) triggers are invisible at pentad resolution — this
  layer flags the vulnerable window, it does not replace met-service
  nowcasts. 2024 Mai Mahiu-class events (dam/localized) may miss any
  rainfall-only system.
- Lake/lake-shore floods (Victoria 2020, Baringo) are level-driven with
  months of memory — optional later add: DAHITI/G-REALM altimetry lake
  levels as a slow-moving arm condition.
- Cyclone-driven Mozambique floods: CHIRPS sees the rain only ~1–2 days
  ahead via GEFS; cyclone track warnings are the real lead there.
