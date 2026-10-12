# Global federation — scoping
*Scoped 29 Sep 2026 with three decisions taken: coverage = the CHIRPS
band (50°S–50°N), architecture = template + federated instances, and
the minimum bar for a new country = drought + CPI. This document is
the plan a colleague follows; the procedure they execute per country
is docs/SCOPING-PROCESS.md.*

## 1. Objective

Let a colleague stand up a monitor for a new country (or region) in
days, not weeks, without touching shared code — and roll every
instance up into one global risk board.

## 2. Architecture: template + federation

- **monitor-template** (new repo): this codebase with every
  country-specific fact extracted into a *country pack*. Instances
  fork/depend on it; template fixes flow to instances by merge, never
  by hand-copy.
- **One instance per region** (owner-operated, mini-contract layout:
  manifest, uv, Keychain secrets, healthchecks, git-only transport).
  An instance owns its cron, its full local DB, and its pushed
  live.sqlite + app.sqlite, exactly as this repo does today.
- **global-board** (new, thin): an aggregator dashboard that clones
  each registered instance's repo shallowly and reads ONLY the small
  pushed live.sqlite files (board rows, impulses, alerts, feed_watch).
  No heavy history crosses instances. Registry = one TOML listing
  repo URLs. The board renders: world map of country lights, the
  worst-N table, CPI impulse bars, and a federation health strip
  (each instance's last push + watchdog state — a silent instance is
  a gray region, never a green one).

## 3. The country pack (the unit of replication)

One directory, declarative, no code:

```
packs/<iso3>/
  zones.csv        zone_key, anchor lat/lon, seasons, crop, sector,
                   vector override — the output of the zone_expand /
                   export_zones tooling, editable by hand
  drivers.toml     which climate-driver columns apply and how:
                   enso = {phase->impact months, sign, notes}
                   iod|nao|monsoon = {...}  (registry below)
  flood.toml       basins + per-country rule params (absent = no
                   flood layer yet)
  events.csv       flood/drought event catalog + damage peaks,
                   confidence-graded, source-cited
  econ.toml        CPI food weight, staple + share, price source
                   (HDX slug or n/a), elasticity prior overrides
  hydro.toml       reservoir assets + thresholds + scraper id
                   (absent = hydro gray)
```

The template's loaders replace today's in-code dicts (EXPOSURE,
IOD_LED, FLOOD_MONTHS, EVENTS, EVENT_PEAKS, PARAMS, Kariba constants,
cpi_weights.csv). Migrating THIS repo's knowledge into packs/ is the
proof the format is sufficient.

## 4. Driver registry (the ENSO framing does not travel)

Today's board hard-codes an East/Southern-Africa El Niño/IOD story;
Morocco already broke it. The template ships a registry of driver
columns — ENSO (sign + impact window per country), IOD, NAO,
monsoon-onset, AMO — and a pack declares which apply. A country
never inherits another region's teleconnection; an undeclared driver
column renders gray with "not applicable", not green.

## 5. Drought leg A outside the WRSI domain

EWX WRSI exists for FEWS NET domains only (Africa, Central America +
Hispaniola, Afghanistan, Yemen). For the rest of the CHIRPS band:

- **Tier 1 (FEWS domains)**: WRSI pctm exactly as here.
- **Tier 2 (elsewhere)**: leg A runs on weighted SPI-3 over the crop
  season; the elasticity is fitted directly on SPI (no Ky step), and
  the dashboard labels the shock "rainfall-based estimate". The
  hindcast harness decides per country whether tier 2 clears the
  acceptance bar; where it does not, the country ships drought lights
  without a CPI impulse rather than a fake one.
- Crop-area weighting (GAEZ) and season calendars work identically in
  both tiers.

## 6. Onboarding procedure for a colleague

1. Read docs/SCOPING-PROCESS.md (the per-layer procedure) and
   docs/LANDMINES.md (new — the failure catalog: pctm not absolute
   WRSI; USD not local prices; admin-1 fallbacks for all-null EWX
   polygons; sliver-polygon FID resolution; early-season WRSI guard;
   app.sqlite vs the 100MB limit; scraper drift sentinels; SQLite
   single-writer discipline).
2. Run the pack generators (zone_expand, export_zones) against the
   new country; hand-correct anchors where the proxy misleads — the
   tooling prints its confidence, the human owns the judgment.
3. Backfill, calibrate with the shipped harness, and publish the
   hindcast/backtest report IN the pack (a pack without calibration
   numbers does not merge).
4. Register the instance in the global-board registry.

Effort observed here: drought+CPI pack ≈ 3–4 days; +flood ≈ +3–4
(catalog curation dominates); +hydro/export as the country warrants.

## 7. Phases & effort (template side)

| Phase | Deliverable | Effort |
|---|---|---|
| G1 | Extract packs/ format; migrate THIS repo onto it (11 countries, no behavior change — the regression test is identical board output) | 4–6 days |
| G2 | Driver registry + tier-2 SPI leg A + LANDMINES.md | 3–4 days |
| G3 | monitor-template repo split + instance bootstrap script | 2–3 days |
| G4 | global-board aggregator (registry, map, health strip) | 3–4 days |
| G5 | First external pack by a colleague (pilot = the real test of the docs) | their 1–2 weeks, our review |

## 8. Known limits

- Federation trusts instance owners: a badly calibrated pack shows on
  the global board with the same authority as a good one. The merge
  rule (no calibration numbers, no pack) is the only gate — keep it.
- CHIRPS undercatches outside the tropics band edges and says nothing
  about snowpack, irrigation withdrawal, or temperate heat stress —
  the coverage decision explicitly excludes those geographies rather
  than serving them badly.
- Tier-2 CPI impulses will be weaker than tier-1; the label must say
  so on every surface, per the house rule that the band is the
  product.
- The aggregator adds a new failure mode — stale *instances* — which
  its health strip must surface with the same gray-is-never-green
  discipline as feeds.
