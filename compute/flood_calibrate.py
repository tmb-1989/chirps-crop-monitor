"""Per-country calibration of the flood-watch signature and regional rule.

Motivated by the 2 Sep 2026 backtest review: with Kenya's thresholds
applied unchanged, Ethiopia scored 29% precision / 2 of 11 events and
Uganda 20% / 2 of 7. Diagnosis (see README):
  - %-of-normal + fixed mm floors undersee high-normal kiremt months
    (extreme volume, modest %) AND low-normal deyr months (1000% of
    normal but under the 25mm floor);
  - several countries' disasters are single-basin, so the Kenya-style
    ">=2 basins armed" regional rule discards true basin alerts.

Signature searched here (per basin, per pentad):
  hot      pentad value >= `hot`th percentile of its pentad-of-year
           1991-2020 climatology AND >= `floor` mm
  watch    ante_pct >= `arm` and `consec` consecutive hot pentads
  alert    watch and pentad >= 97th percentile (or whiplash: ante <= 20
           and >= 97th percentile — unchanged in spirit)
  regional `r1` basins armed and `r2` alerting in the same pentad,
           inside the country's flood-season months

Grid: arm {80,90} x hot {90,95,97} x consec {1,2} x floor {5,15,25}mm
x rule {(1,1),(2,1)} = 72 combos/country, scored on alert-episode F1
vs the country's event catalog (episodes 1999+, 30-day merge, 10-day
lead grace), tie-broken by recall on major events then precision.
IN-SAMPLE calibration — ~28 years and 6-15 events per country leaves no
honest holdout; label it calibration, not skill.

Basins with an incomplete backfill (< MIN_PENTADS) are excluded and
listed, so a partial run is visibly partial.

Run: python compute/flood_calibrate.py [--iso3 ETH]
"""
from __future__ import annotations

import argparse
import itertools
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402
from flood_signals import (CLIM_START, CLIM_END, ANTE_WINDOW, EVENTS,  # noqa: E402
                           FLOOD_MONTHS, COUNTRY, basin_countries,
                           load_basin, pentad_of_year)

MIN_PENTADS = 3000
GRID = {
    "arm": (80, 90),
    "hot": (90, 95, 97),
    "consec": (1, 2),
    "floor": (5, 15, 25),
    "region": ((1, 1), (2, 1)),
}
ALERT_HOT = 97  # alert tier: hot pentad also above this percentile


def basin_series(con, zk: str) -> pd.DataFrame | None:
    """value, ante_pct, hot_pct per pentad for one basin (vectorized once;
    the grid search then only does boolean algebra)."""
    df = load_basin(con, zk)
    if len(df) < MIN_PENTADS:
        return None
    poy = pd.Series([pentad_of_year(d.date()) for d in df.index],
                    index=df.index)
    clim_years = (df.index.year >= CLIM_START) & (df.index.year <= CLIM_END)
    ante = df.value.rolling(ANTE_WINDOW, min_periods=ANTE_WINDOW).sum()
    ante_pct = pd.Series(np.nan, index=df.index)
    hot_pct = pd.Series(np.nan, index=df.index)
    for k in range(1, 73):
        sel = poy == k
        ref_a = ante[sel & clim_years].dropna()
        if len(ref_a) >= 15:
            ante_pct[sel] = ante[sel].map(
                lambda v: float((ref_a <= v).mean() * 100)
                if pd.notna(v) else np.nan)
        ref_v = df.value[sel & clim_years].dropna()
        if len(ref_v) >= 15:
            hot_pct[sel] = df.value[sel].map(
                lambda v: float((ref_v <= v).mean() * 100)
                if pd.notna(v) else np.nan)
    return pd.DataFrame({"value": df.value, "ante_pct": ante_pct,
                         "hot_pct": hot_pct})


def tiers(s: pd.DataFrame, arm, hot, consec, floor) -> pd.Series:
    """0/1/2 tier series for one basin under one parameter combo."""
    hotp = (s.hot_pct >= hot) & (s.value >= floor)
    run = hotp
    for i in range(1, consec):
        run = run & hotp.shift(i, fill_value=False)
    armed = s.ante_pct >= arm
    watch = armed & run
    alert = watch & (s.hot_pct >= ALERT_HOT)
    whiplash = (s.ante_pct <= 20) & (s.hot_pct >= ALERT_HOT) & \
        (s.value >= floor)
    t = pd.Series(0, index=s.index)
    t[watch] = 1
    t[alert | whiplash] = 2
    return t


def score(tier_df: pd.DataFrame, iso3: str, r1: int, r2: int) -> dict:
    """Episode precision/recall for a country's stacked basin tiers."""
    n1 = (tier_df >= 1).sum(axis=1)
    n2 = (tier_df >= 2).sum(axis=1)
    months = pd.Index(tier_df.index.month)
    reg = tier_df.index[(n1 >= r1) & (n2 >= r2)
                        & months.isin(list(FLOOD_MONTHS[iso3]))]
    reg = reg[reg >= pd.Timestamp("1999-01-01")]
    episodes = []
    for d in reg:
        if episodes and (d - episodes[-1][1]).days <= 30:
            episodes[-1][1] = d
        else:
            episodes.append([d, d])
    ev = [(pd.Timestamp(a), pd.Timestamp(b), sev)
          for a, b, sev in EVENTS[iso3]]
    hits = sum(any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                   for a, b, _ in ev) for e0, e1 in episodes)
    caught = [sev for a, b, sev in ev
              if any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                     for e0, e1 in episodes)]
    majors = sum(1 for *_, sev in ev if sev == "major")
    prec = hits / len(episodes) if episodes else 0.0
    rec = len(caught) / len(ev) if ev else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"episodes": len(episodes), "hits": hits,
            "false": len(episodes) - hits, "precision": prec,
            "recall": rec, "f1": f1,
            "major_recall": (sum(1 for s in caught if s == "major") / majors
                             if majors else 1.0)}


def calibrate(con, iso3: str, zones: list[str]) -> None:
    series, skipped = {}, []
    for zk in zones:
        s = basin_series(con, zk)
        (series.__setitem__(zk, s) if s is not None
         else skipped.append(zk))
    print(f"\n=== {COUNTRY[iso3]}: {len(series)} basins"
          + (f" (EXCLUDED, backfill incomplete: {', '.join(skipped)})"
             if skipped else ""))
    if not series:
        print("nothing to calibrate")
        return
    results = []
    for arm, hot, consec, floor, (r1, r2) in itertools.product(*GRID.values()):
        t = pd.DataFrame({zk: tiers(s, arm, hot, consec, floor)
                          for zk, s in series.items()})
        r = score(t, iso3, r1, r2)
        r.update(arm=arm, hot=hot, consec=consec, floor=floor,
                 region=f"{r1}/{r2}")
        results.append(r)
    res = (pd.DataFrame(results)
           .sort_values(["f1", "major_recall", "precision"],
                        ascending=False))
    cols = ["arm", "hot", "consec", "floor", "region", "episodes",
            "hits", "false", "precision", "recall", "major_recall", "f1"]
    with pd.option_context("display.width", 120):
        print(res[cols].head(6).to_string(
            index=False, float_format=lambda v: f"{v:.2f}"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso3", default=None)
    args = ap.parse_args()
    con = db.connect()
    bc = basin_countries()
    by = {}
    for zk, c in sorted(bc.items()):
        by.setdefault(c, []).append(zk)
    for iso3 in (args.iso3.split(",") if args.iso3 else sorted(by)):
        calibrate(con, iso3, by[iso3])
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
