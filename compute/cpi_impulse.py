"""WRSI -> implied output shock -> food-CPI impulse (SCOPING-CPI.md).

Leg A (agronomy): per zone, FAO water production function applied to
the LWRSI as a percent of its own zone median (pctm):
    yield_shock = clip(Ky * (1 - pctm/100), 0, 1)
with crop-specific Ky. The anomaly form, not absolute WRSI, is
deliberate: end-of-season absolute LWRSI reads ~55-60 in several zones
EVERY year (mask/window bias), which made every season a 40%+ "loss" —
the Sep 2026 hindcast showed the absolute form has zero price skill
(49% sign accuracy) while the pctm form reaches ~70% with sane drought
counts. WRSI explains ~half of yield variance (R^2 0.52-0.61
CHIRPS-driven), so the shock carries an uncertainty band, not a point.
Mid-season values are a ceiling-so-far, labelled provisional.

Prices are compared in USD (usd_kg): local-currency series are unusable
for ZWE-class inflation/redenomination histories.

Leg B (macro): production-weighted national shock (data/econ/
production_weights.csv), price response via an elasticity fitted on our
own history (end-of-season shock vs post-harvest YoY staple price
change, bounded to [0.5, 3] and defaulting to the literature prior when
the fit is unusable), capped at import parity (+50%), mapped to CPI
through food weight x staple share (data/econ/cpi_weights.csv).

Monte Carlo over (per-zone shock noise, elasticity range, uncovered-
production behaviour) -> P10/P50/P90. Writes live.zone_output_shock and
live.cpi_impulse. The band is the product; the midpoint is not.

Usage:
    python compute/cpi_impulse.py               # current season
    python compute/cpi_impulse.py --calibrate   # hindcast + elasticity fit report
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
import db  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ECON = ROOT / "data" / "econ"

KY = {"Maize": 1.25, "Wheat": 1.05, "Rice": 1.10}   # FAO 33/66 seasonal
E_PRIOR, E_LO, E_HI = 1.5, 0.5, 3.0   # price elasticity to shortfall
PARITY_CAP = 0.50                     # import parity: max sustained rise
SHOCK_SD_FRAC, SHOCK_SD_FLOOR = 0.35, 0.05   # leg-A noise (R^2 ~ 0.55)
DRAWS = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.zone_output_shock (
    zone_key    TEXT PRIMARY KEY,
    iso3        TEXT NOT NULL,
    commodity   TEXT NOT NULL,
    wrsi        REAL,
    in_season   INTEGER,
    provisional INTEGER,           -- 1 = pre-season-end (ceiling so far)
    shock       REAL,              -- fraction of potential yield lost
    shock_p10   REAL,
    shock_p90   REAL,
    as_of       TEXT,
    computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live.cpi_impulse (
    country     TEXT PRIMARY KEY,
    season      TEXT,
    coverage    REAL,              -- share of national production in zones
    out_shock   REAL,              -- national production shock, P50
    out_p10     REAL,
    out_p90     REAL,
    cpi_pp      REAL,              -- food-CPI impulse, percentage points, P50
    cpi_p10     REAL,
    cpi_p90     REAL,
    elasticity  REAL,
    provisional INTEGER,
    inputs_json TEXT,
    computed_at TEXT NOT NULL
);
"""


def load_econ():
    with open(ECON / "production_weights.csv") as f:
        pw = list(csv.DictReader(f))
    with open(ECON / "cpi_weights.csv") as f:
        cw = {r["iso3"]: r for r in csv.DictReader(f)}
    return pw, cw


def season_windows(seasons: str) -> list[tuple[str, int, int]]:
    """'long_rains:3-9,second:8-11' -> [(name, start_month, end_month)]."""
    out = []
    for part in seasons.split(","):
        name, ab = part.split(":")
        a, b = ab.split("-")
        out.append((name, int(a), int(b)))
    return out


def season_span(year: int, a: int, b: int) -> tuple[dt.date, dt.date]:
    """Calendar span of a season labelled by its END year."""
    end = dt.date(year, b, 28)
    start = dt.date(year if a <= b else year - 1, a, 1)
    return start, end


def wrsi_at_season_end(con, zk: str, a: int, b: int,
                       year: int) -> float | None:
    """Last in-season LWRSI reading of the season ending month b of
    `year` (dekadal; the final dekad's value is the season outcome)."""
    start, end = season_span(year, a, b)
    row = con.execute(
        "SELECT value FROM observations WHERE zone_key=? AND dataset="
        "'lwrsi_africa_dekad_pctm' AND granule_start BETWEEN ? AND ? "
        "ORDER BY granule_start DESC LIMIT 1",
        (zk, start.isoformat(), end.isoformat())).fetchone()
    return row[0] if row else None


def current_wrsi(con, zk: str, seasons: str,
                 today: dt.date) -> tuple[float, str, int] | None:
    """(wrsi, as_of, provisional) for the season containing today, or
    the season that ended within the last 60 days (harvest read)."""
    for name, a, b in season_windows(seasons):
        for year in (today.year, today.year + (1 if a > b else 0)):
            start, end = season_span(year, a, b)
            if start <= today <= end + dt.timedelta(days=60):
                row = con.execute(
                    "SELECT value, granule_start FROM observations WHERE "
                    "zone_key=? AND dataset='lwrsi_africa_dekad_pctm' AND "
                    "granule_start BETWEEN ? AND ? "
                    "ORDER BY granule_start DESC LIMIT 1",
                    (zk, start.isoformat(), end.isoformat())).fetchone()
                if row:
                    return row[0], row[1], int(today <= end)
    return None


def leg_a(wrsi: float, ky: float, rng=None):
    shock = float(np.clip(ky * (1 - wrsi / 100.0), 0, 1))
    sd = max(SHOCK_SD_FRAC * shock, SHOCK_SD_FLOOR)
    if rng is None:
        return shock, sd
    return np.clip(rng.normal(shock, sd, DRAWS), 0, 1), sd


def season_pairs(con, iso3: str, zones: list[dict]) -> list[tuple]:
    """[(year, shock, realized_dprice)] per season: end-of-season
    production-weighted shock vs the post-harvest YoY staple price
    change (3-month window starting the month after season end)."""
    px = pd.read_sql_query(
        "SELECT month, usd_kg FROM staple_prices WHERE iso3=? AND "
        "commodity=? AND pricetype='Wholesale' ORDER BY month",
        con, params=(iso3, zones[0]["commodity"]))
    if px.empty:
        px = pd.read_sql_query(
            "SELECT month, usd_kg FROM staple_prices WHERE iso3=? AND "
            "commodity=? ORDER BY month", con,
            params=(iso3, zones[0]["commodity"]))
    if px.empty:
        return []
    px["month"] = pd.to_datetime(px.month)
    px = px.set_index("month").usd_kg
    pts = []
    zmeta = {z["zone_key"]: z for z in zones}
    seasons = {r[0]: r[1] for r in con.execute(
        "SELECT zone_key, seasons FROM zones")}
    for year in range(2007, dt.date.today().year + 1):
        num = den = 0.0
        end_m = None
        for zk, z in zmeta.items():
            if zk not in seasons:
                continue
            # main season = the longest window
            name, a, b = max(season_windows(seasons[zk]),
                             key=lambda s: (s[2] - s[1]) % 12)
            w = wrsi_at_season_end(con, zk, a, b, year)
            if w is None:
                continue
            share = float(z["share_national"])
            num += share * min(KY.get(z["commodity"], 1.25)
                               * (1 - w / 100.0), 1.0)
            den += share
            end_m = b
        if not den or end_m is None:
            continue
        shock = max(num / den, 0.0)
        # post-harvest window: 3 months starting the month after season end
        t0 = pd.Timestamp(year, end_m, 1) + pd.offsets.MonthBegin(1)
        cur = px[t0:t0 + pd.offsets.MonthBegin(3)].mean()
        prev = px[t0 - pd.offsets.MonthBegin(12):
                  t0 - pd.offsets.MonthBegin(9)].mean()
        if np.isnan(cur) or np.isnan(prev) or prev <= 0:
            continue
        pts.append((year, shock, cur / prev - 1))
    return pts


def fit_elasticity(con, iso3: str, zones: list[dict]) -> tuple[float, int]:
    """Regress post-harvest YoY staple price change on the end-of-season
    production-weighted shock, through the origin. Returns (elasticity,
    n_seasons); falls back to the prior when n < 6 or the fit leaves
    [E_LO, E_HI]."""
    pts = [(s, d) for _, s, d in season_pairs(con, iso3, zones)]
    if len(pts) < 6:
        return E_PRIOR, len(pts)
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    # de-trend inflation: subtract the median YoY change (baseline drift)
    y = y - np.median(y)
    if (x ** 2).sum() < 1e-6:
        return E_PRIOR, len(pts)
    e = float((x * y).sum() / (x ** 2).sum())
    return (e, len(pts)) if E_LO <= e <= E_HI else (E_PRIOR, len(pts))


def country_impulse(con, iso3: str, zones: list[dict], cw: dict,
                    today: dt.date, rng) -> dict | None:
    """Monte Carlo the chain for one country; None when out of season."""
    live = []
    for z in zones:
        cur = current_wrsi(con, z["zone_key"], z["seasons"], today)
        if cur:
            live.append((z, *cur))
    if not live:
        return None
    coverage = sum(float(z["share_national"]) for z, *_ in live)
    e_fit, n_fit = fit_elasticity(con, iso3, zones)
    shocks = np.zeros(DRAWS)
    for z, wrsi, as_of, prov in live:
        draws, _ = leg_a(wrsi, KY.get(z["commodity"], 1.25), rng)
        shocks += float(z["share_national"]) * draws
    covered = shocks / max(coverage, 1e-6)
    # uncovered production: between unaffected and half the covered shock
    shocks += (1 - coverage) * covered * rng.uniform(0, 0.5, DRAWS)
    elast = rng.triangular(E_LO, e_fit, E_HI, DRAWS)
    dp = np.minimum(elast * shocks, PARITY_CAP)
    cpi = dp * float(cw["staple_share_food"]) \
        * float(cw["food_cpi_weight"]) * 100
    provisional = int(any(p for *_, p in live))
    q = lambda a, p: float(np.percentile(a, p))  # noqa: E731
    return {
        "season": ", ".join(sorted({a[:7] for _, _, a, _ in live})),
        "coverage": round(coverage, 3),
        "out_shock": q(shocks, 50), "out_p10": q(shocks, 10),
        "out_p90": q(shocks, 90),
        "cpi_pp": q(cpi, 50), "cpi_p10": q(cpi, 10), "cpi_p90": q(cpi, 90),
        "elasticity": round(e_fit, 2), "provisional": provisional,
        "inputs_json": json.dumps({
            "zones": {z["zone_key"]: {"wrsi": w, "as_of": a,
                                      "share": z["share_national"]}
                      for z, w, a, _ in live},
            "elasticity_fit_seasons": n_fit,
            "assumptions": "Ky FAO33; parity cap 50%; uncovered 0-50% "
                           "of covered shock; ceteris paribus FX/policy"}),
    }


def hindcast(con, pw: list[dict]) -> None:
    """SCOPING-CPI §5: predicted (prior-elasticity — deliberately NOT
    the fitted one, to avoid judging in-sample) vs realized de-trended
    post-harvest price change, per country per season. 'Drought season'
    = model shock >= 15%. Realized changes are de-trended by the
    country's median YoY change so baseline inflation drift drops out."""
    print("hindcast: predicted (prior e=%.1f, capped) vs realized "
          "de-trended post-harvest price move\n" % E_PRIOR)
    agg_sign = agg_n = 0
    for iso3 in sorted({z["iso3"] for z in pw}):
        zs = [z for z in pw if z["iso3"] == iso3 and z["seasons"]]
        pairs = season_pairs(con, iso3, zs) if zs else []
        if len(pairs) < 6:
            print(f"=== {iso3}: only {len(pairs)} usable seasons — skipped")
            continue
        yrs = [y for y, _, _ in pairs]
        shocks = np.array([s for _, s, _ in pairs])
        realized = np.array([d for _, _, d in pairs])
        realized = realized - np.median(realized)     # de-trend
        pred = np.minimum(E_PRIOR * shocks, PARITY_CAP)
        corr = float(np.corrcoef(pred, realized)[0, 1]) \
            if pred.std() > 1e-9 else float("nan")
        dr = shocks >= 0.15
        hit = (realized[dr] > 0).sum()
        within2 = ((realized[dr] > 0)
                   & (pred[dr] / np.maximum(realized[dr], 1e-9) < 2)
                   & (pred[dr] / np.maximum(realized[dr], 1e-9) > 0.5)).sum()
        agg_sign += hit
        agg_n += int(dr.sum())
        print(f"=== {iso3}: {len(pairs)} seasons {min(yrs)}-{max(yrs)}, "
              f"corr {corr:+.2f}; drought seasons (shock>=15%): "
              f"{dr.sum()} — sign correct {hit}/{dr.sum()}, "
              f"within x2 {within2}/{dr.sum()}")
        for (y, s, _), p, r in zip(pairs, pred, realized):
            flag = " <-- drought" if s >= 0.15 else ""
            print(f"    {y}: shock {s:4.0%}  pred {p:+5.0%}  "
                  f"realized {r:+5.0%}{flag}")
    if agg_n:
        print(f"\nALL: sign correct in {agg_sign}/{agg_n} drought "
              f"seasons ({100 * agg_sign / agg_n:.0f}%)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--hindcast", action="store_true")
    args = ap.parse_args()
    today = dt.date.today()
    rng = np.random.default_rng(42)
    con = db.connect()
    con.executescript(SCHEMA)
    pw, cw = load_econ()
    seasons = {r[0]: r[1] for r in con.execute(
        "SELECT zone_key, seasons FROM zones")}
    for z in pw:
        z["seasons"] = seasons.get(z["zone_key"], "")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if args.hindcast:
        hindcast(con, pw)
        return 0

    if args.calibrate:
        for iso3 in sorted({z["iso3"] for z in pw}):
            zs = [z for z in pw if z["iso3"] == iso3 and z["seasons"]]
            e, n = fit_elasticity(con, iso3, zs) if zs else (E_PRIOR, 0)
            if e != E_PRIOR:
                src = f"fitted on {n} seasons"
            elif n < 6:
                src = f"prior ({n} usable seasons — too few)"
            else:
                src = f"prior (fit on {n} seasons fell outside " \
                      f"[{E_LO}, {E_HI}])"
            print(f"{iso3}: elasticity {e:.2f} [{src}]")
        return 0

    # ---- leg A per zone ---------------------------------------------------
    for z in pw:
        if not z["seasons"]:
            continue
        cur = current_wrsi(con, z["zone_key"], z["seasons"], today)
        if cur is None:
            con.execute("INSERT OR REPLACE INTO zone_output_shock VALUES "
                        "(?,?,?,NULL,0,0,NULL,NULL,NULL,NULL,?)",
                        (z["zone_key"], z["iso3"], z["commodity"], now))
            continue
        wrsi, as_of, prov = cur
        shock, sd = leg_a(wrsi, KY.get(z["commodity"], 1.25))
        con.execute(
            "INSERT OR REPLACE INTO zone_output_shock VALUES "
            "(?,?,?,?,1,?,?,?,?,?,?)",
            (z["zone_key"], z["iso3"], z["commodity"], wrsi, prov, shock,
             max(shock - 1.282 * sd, 0), min(shock + 1.282 * sd, 1),
             as_of, now))
        print(f"[{z['zone_key']}] WRSI {wrsi:.0f} -> shock "
              f"{shock:.0%} ({'prov' if prov else 'final'})")

    # ---- leg B per country ------------------------------------------------
    for iso3 in sorted({z["iso3"] for z in pw}):
        zs = [z for z in pw if z["iso3"] == iso3 and z["seasons"]]
        r = country_impulse(con, iso3, zs, cw[iso3], today, rng) if zs \
            else None
        if r is None:
            con.execute("DELETE FROM cpi_impulse WHERE country=?", (iso3,))
            print(f"{iso3}: out of season — no impulse")
            continue
        con.execute(
            "INSERT OR REPLACE INTO cpi_impulse VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso3, r["season"], r["coverage"], r["out_shock"], r["out_p10"],
             r["out_p90"], r["cpi_pp"], r["cpi_p10"], r["cpi_p90"],
             r["elasticity"], r["provisional"], r["inputs_json"], now))
        print(f"{iso3}: output shock {r['out_shock']:.0%} "
              f"[{r['out_p10']:.0%}-{r['out_p90']:.0%}] -> food CPI "
              f"+{r['cpi_pp']:.1f}pp [{r['cpi_p10']:.1f}-{r['cpi_p90']:.1f}]"
              f" (cov {r['coverage']:.0%}, e={r['elasticity']})")
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
