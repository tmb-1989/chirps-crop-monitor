# WRSI → output shock → food-CPI impulse — scoping
*Scoped 7 Sep 2026, motivated by the 2026 Kenya drought (Trans Nzoia WRSI 57,
projected ~25M-bag shortfall vs ~75M consumption, food inflation 9.0% in
July driving a 6.5% headline). The two legs exist separately in the
literature; chaining them from in-season WRSI is the novel piece.*

## 1. Objective

Per crop zone, translate the in-season WRSI into an **implied output
shock** (% of water-limited potential yield lost, with a confidence
range); per country, aggregate to a **food-CPI impulse estimate**
(percentage points, with error bars), available 2–3 months before
harvest and further ahead of prices.

Credit read-through: inflation/monetary-policy calls (CBK, BoU, NBR),
import-bill and FX pressure (maize import parity), fiscal cost of
subsidies, IPC/food-security overlap.

## 2. Method: two legs, both presented as ranges

**Leg A — WRSI → zone yield shock (agronomy, well established).**
FAO water production function (Doorenbos & Kassam 1979 / FAO 33):
`1 − Ya/Ym = Ky × (1 − WRSI/100)`, seasonal Ky(maize) ≈ 1.25.
WRSI 60 ⇒ ~50% yield loss; WRSI 80 ⇒ ~25%; <50 ⇒ failure. Empirical
skill: r ≈ 0.75 vs sub-national yields (Verdin & Klaver 2002), R² ≈
0.52–0.61 with CHIRPS-driven WRSI (Tanzania study) — so carry a ±
band reflecting ~half the variance being non-water (pests, inputs,
management), not a point estimate. Mid-season WRSI is provisional
(can only fall); label pre-flowering estimates "ceiling so far".

**Leg B — production shock → food CPI (macro, established separately).**
- Zone shocks → national production shock: weight each zone by its
  share of national production of its crop; scale by the fraction of
  national production our zones represent (coverage factor), with the
  uncovered remainder assumed at trend ± the covered shock's sign.
- Production shock → staple price: fit a simple elasticity on our own
  history (zone WRSI vs FEWS NET national maize prices, ~20 seasons)
  and sanity-bound it with the IMF estimates (droughts ≈ +2pp staple
  inflation; global→local pass-through ≈ 1 for imported staples,
  6–12-month lag). **Import-parity cap**: once domestic price reaches
  import parity (TZA/UGA/world for KEN), further shortfall moves the
  import bill, not the price — cap the impulse there.
- Staple price → CPI: national CPI food weights (KEN ~33%, SSA ~40%)
  and the staple's share within the food basket.

**Error bars**: Monte Carlo over (Ky slope × leg-A residual s.d.,
elasticity range, coverage-factor bounds); report P10–P90. The point
of the section is an honest range, not false precision.

## 3. Data (mostly flowing already)

| Input | Source | Status |
|---|---|---|
| In-season WRSI per zone | EWX `lwrsi_africa_dekad` | have |
| Zone production weights | FAOSTAT + national ag statistics (one-off table, versioned) | **add** |
| CPI food weights + staple shares | national CPI baskets (KNBS etc., one-off) | **add** |
| Staple market prices (fit + validation) | FEWS NET price data warehouse / WFP VAM (monthly, per market) | **add** (small ingest) |
| Historical food-CPI series (validation) | national statistics offices / IMF | **add** (one-off CSV) |

## 4. Computation design

New `compute/cpi_impulse.py`, run after `metrics.py` in the daily chain:

- Per zone: latest in-season WRSI → `output_shock_pct` + [lo, hi];
  written to `zone_risk` (new columns) so the drought drill-down can
  show it beside the light.
- Per country: Monte Carlo aggregation → `cpi_impulse` table
  (country, season, output_shock, cpi_impulse_pp, p10, p90,
  computed_at, inputs_json for audit).
- Out-of-season: NULL, not zero — the board must distinguish "no
  signal yet" from "no impact".

## 5. Calibration & validation (the core of the build)

- Hindcast: reconstruct end-of-season WRSI per zone for 2000–2026,
  run the chain, compare predicted vs realized food-CPI impulses for
  the known drought years (KEN 2008-09, 2016-17, 2021-22, 2026; ETH
  2015-16; ZMB/ZWE 2015-16, 2023-24 El Niño).
- Report leg-A and leg-B fit separately (so a miss is attributable),
  plus the joint hindcast — in-sample honesty, same convention as the
  flood layer: label it calibration, not skill.
- Acceptance bar: correct sign and order of magnitude (impulse within
  ~×2) on ≥ 75% of drought seasons; directional lead of ≥ 2 months
  over realized food CPI turning points.

## 6. Outputs

- **Dashboard**: "Implied output shock" column in the drought
  drill-down (zone table); country-level "Food-CPI impulse" panel on
  the risk-board view — bar per country with P10–P90 whiskers,
  gray when out of season.
- **CSV export** per country/season for credit models.
- Risk-board cell tooltip: drought cells gain "→ est. X–Y pp food
  CPI" when an impulse is live.

## 7. Phases & effort

| Phase | Deliverable | Effort |
|---|---|---|
| C1 | Static tables (production weights, CPI weights) + FEWS NET price ingest | 1–2 days |
| C2 | cpi_impulse.py: legs A+B, Monte Carlo, hindcast report | 2–3 days |
| C3 | Dashboard column + impulse panel + CSV | 1–2 days |
| C4 | Extend beyond maize (beans, sorghum/teff for ETH) | 1–2 days each |

## 8. Known limits & gotchas

- Leg A explains ~half of yield variance; a good-water/bad-pest year
  will fool it. The band, not the midpoint, is the product.
- Our zones ≠ national production; the coverage factor is a real
  assumption and must be printed with the estimate.
- Policy breaks the price link routinely (export bans, subsidy
  programs, duty-free import windows — Kenya 2026 did all three).
  Flag known interventions in `inputs_json` rather than pretending
  the elasticity is stable.
- FX pass-through can swamp the weather signal (IMF WP/24/59); the
  impulse is *ceteris paribus* on the exchange rate, say so on the
  panel.
- WRSI is maize-parameterized in our feed; applying it to mixed-crop
  zones (RWA sorghum polygon) inherits FAO Ky mismatch — use
  crop-specific Ky where the zone's crop mask differs.
- Floods destroy crops too (the wet channel WRSI cannot see) — a
  flood-hit season can have fine WRSI and a real price shock; the
  flood layer's alerts should veto "no impulse" complacency.

## 9. Hindcast results (8 Sep 2026) — §5 executed

Run: `python compute/cpi_impulse.py --hindcast` (prior elasticity, not
fitted — no in-sample flattery). Two findings changed the chain:

1. **Absolute LWRSI is unusable for leg A**: end-of-season absolute
   values read ~55-60 in several zones every year (mask/window bias),
   calling every season a 40%+ loss — price skill was coin-flip (49%
   sign accuracy over 120 "drought" seasons). Leg A now uses LWRSI as
   % of the zone median (pctm), which centers baselines at ~100.
2. **Local-currency prices are unusable for ZWE-class histories**;
   legs now compare USD prices.

After both fixes: drought-season counts become sane (0-4 per country),
sign accuracy 13/19 = **68%** (within ×2: 5/19 = 26%), correlations
+0.3 to +0.6 for southern Africa (ZWE +0.61, ZMB +0.49, MOZ +0.41),
near zero for KEN/ETH/TZA. Interpretation: the chain has genuine skill
where our zones cover the dominant unimodal maize season; East Africa
fails the §5 acceptance bar mostly for coverage reasons (KEN zones see
only the long-rains grain basket while price spikes rode short-rains/
ASAL droughts and global markets — 2008, 2011, 2022). Next lever:
short-rains zone coverage for KEN/ETH, or a global maize price control
in leg B.
