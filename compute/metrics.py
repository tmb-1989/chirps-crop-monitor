"""Derived dekadal metrics from the local CHIRPS v3 series (Phase 2).

Per zone, from observations dataset chirps3local_dekad_data (+prelim):
  - pct_normal:    dekad rainfall as % of the 1991-2020 same-dekad mean
  - spi1 / spi3:   Standardized Precipitation Index over 1 / 3 months
                   (3 / 9 dekads), gamma-fitted on 1991-2020 by dekad-of-year
  - onset:         per season-year, first dekad >= 25mm followed by two
                   dekads totalling >= 20mm (planting signal)
  - max_dry_spell: per season-year, longest run of dekads < 5mm inside the
                   season window

Writes tables dekad_metrics (zone_key, granule_start, value per metric)
and season_metrics (zone_key, season_name, season_year, onset_start,
onset_anom_dekads, max_dry_spell).

Run:  python compute/metrics.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
from config import ZONES  # noqa: E402
import db  # noqa: E402

CLIM_START, CLIM_END = 1991, 2020

SCHEMA = """
CREATE TABLE IF NOT EXISTS dekad_metrics (
    zone_key      TEXT NOT NULL,
    granule_start TEXT NOT NULL,
    rain_mm       REAL,
    pct_normal    REAL,
    spi1          REAL,
    spi3          REAL,
    source        TEXT,
    PRIMARY KEY (zone_key, granule_start)
);
CREATE TABLE IF NOT EXISTS season_metrics (
    zone_key      TEXT NOT NULL,
    season_name   TEXT NOT NULL,
    season_year   INTEGER NOT NULL,
    onset_start   TEXT,
    onset_delay_dekads REAL,
    max_dry_spell INTEGER,
    PRIMARY KEY (zone_key, season_name, season_year)
);
"""

# season windows for composites (config covers the rest)
EXTRA_SEASONS = {
    "ken_grain_basket": [("long_rains", 3, 9)],
    "zmb_maize_belt": [("main", 10, 4)],
}


def dekad_of_year(d: dt.date) -> int:
    return (d.month - 1) * 3 + {1: 1, 11: 2, 21: 3}[d.day]


def spi_from_clim(series: pd.Series, doy: pd.Series, window: int) -> pd.Series:
    """SPI: rolling sum over `window` dekads, gamma-fit per dekad-of-year
    on climatology years, transformed to standard normal."""
    roll = series.rolling(window, min_periods=window).sum()
    years = series.index.year
    out = pd.Series(np.nan, index=series.index)
    for k in range(1, 37):
        sel = doy == k
        clim = roll[sel & (years >= CLIM_START) & (years <= CLIM_END)].dropna()
        if len(clim) < 15:
            continue
        zeros = (clim <= 0).mean()
        pos = clim[clim > 0]
        if len(pos) < 10:
            continue
        a, loc, scale = stats.gamma.fit(pos, floc=0)
        vals = roll[sel]
        cdf = zeros + (1 - zeros) * stats.gamma.cdf(vals, a, loc=loc,
                                                    scale=scale)
        cdf = cdf.clip(1e-4, 1 - 1e-4)
        out[sel] = stats.norm.ppf(cdf)
    return out


def load_series(con, zone_key: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT granule_start, value, dataset FROM observations "
        "WHERE zone_key=? AND dataset IN "
        "('chirps3local_dekad_data','chirps3local-prelim_dekad_data')",
        con, params=(zone_key,))
    if df.empty:
        return df
    df["source"] = np.where(df.dataset.str.contains("prelim"), "prelim", "final")
    df = (df.sort_values("dataset")  # final < prelim alphabetically? ensure final wins
          .sort_values(["granule_start", "source"])
          .drop_duplicates("granule_start", keep="first"))  # 'final' < 'prelim'
    df["date"] = pd.to_datetime(df.granule_start)
    return df.set_index("date").sort_index()


def season_windows(zone_key):
    if zone_key in EXTRA_SEASONS:
        return EXTRA_SEASONS[zone_key]
    if zone_key in ZONES:
        return ZONES[zone_key][4]
    return []


def compute_zone(con, zone_key: str) -> int:
    df = load_series(con, zone_key)
    if df.empty:
        return 0
    doy = pd.Series([dekad_of_year(d.date()) for d in df.index], index=df.index)
    clim_mean = (df.value[(df.index.year >= CLIM_START)
                          & (df.index.year <= CLIM_END)]
                 .groupby(doy).mean())
    pct = 100 * df.value / doy.map(clim_mean)
    spi1 = spi_from_clim(df.value, doy, 3)
    spi3 = spi_from_clim(df.value, doy, 9)

    rows = [(zone_key, d.date().isoformat(), float(v),
             None if pd.isna(p) else float(p),
             None if pd.isna(s1) else float(s1),
             None if pd.isna(s3) else float(s3), src)
            for d, v, p, s1, s3, src in zip(df.index, df.value, pct, spi1,
                                            spi3, df.source)]
    con.executemany(
        "INSERT OR REPLACE INTO dekad_metrics VALUES (?,?,?,?,?,?,?)", rows)

    # season metrics
    for sname, s_start, s_end in season_windows(zone_key):
        cross = s_end < s_start
        n_dekads = ((s_end - s_start) % 12 + 1) * 3
        onset_by_year, dry_by_year = {}, {}
        for d, v in df.value.items():
            m = d.month
            in_season = (m >= s_start or m <= s_end) if cross else \
                (s_start <= m <= s_end)
            if not in_season:
                continue
            syear = d.year if (not cross or m >= s_start) else d.year - 1
            onset_by_year.setdefault(syear, []).append((d, v))
        for syear, seq in onset_by_year.items():
            seq.sort()
            vals = [v for _, v in seq]
            onset = None
            for i in range(len(seq) - 2):
                if vals[i] >= 25 and vals[i + 1] + vals[i + 2] >= 20:
                    onset = seq[i][0]
                    break
            run = best = 0
            for v in vals:
                run = run + 1 if v < 5 else 0
                best = max(best, run)
            onset_idx = None
            if onset is not None:
                onset_idx = next(i for i, (d, _) in enumerate(seq) if d == onset)
            dry_by_year[syear] = (onset, onset_idx, best, len(seq))
        # onset delay vs climatology median onset index
        clim_idx = [oi for y, (o, oi, _, _) in dry_by_year.items()
                    if oi is not None and CLIM_START <= y <= CLIM_END]
        med = float(np.median(clim_idx)) if clim_idx else None
        srows = []
        for syear, (onset, oi, dry, n) in dry_by_year.items():
            if n < n_dekads - 3 and syear != max(dry_by_year):
                continue  # incomplete historical season (series edges)
            delay = (oi - med) if (oi is not None and med is not None) else None
            srows.append((zone_key, sname, syear,
                          onset.date().isoformat() if onset is not None else None,
                          delay, dry))
        con.executemany(
            "INSERT OR REPLACE INTO season_metrics VALUES (?,?,?,?,?,?)", srows)
    con.commit()
    return len(rows)


def main() -> int:
    con = db.connect()
    con.executescript(SCHEMA)
    zones = [r[0] for r in con.execute(
        "SELECT DISTINCT zone_key FROM observations "
        "WHERE dataset='chirps3local_dekad_data'")]
    for zk in zones:
        n = compute_zone(con, zk)
        print(f"{zk}: {n} dekad metric rows")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
