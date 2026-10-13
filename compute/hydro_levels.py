"""DRC hydro level signals — display-only (SCOPING-DRC-HYDRO.md D1).

Pure functions over the hydro_levels table (DAHITI altimetry): a
same-calendar-month climatology band per station, and a day-of-year
low-water percentile for the Inga etiage monitor. No new tables, no
alerting — D1 ships display-first per the scoping doc; D2 is where
calibrated thresholds get decided (against a documented SNEL curtailment
catalog, which doesn't exist yet).

Run standalone to print a summary (used for end-to-end verification):
    python compute/hydro_levels.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
import db  # noqa: E402


def load_levels(con, station_key: str) -> pd.DataFrame:
    """Empty frame (never an exception) when the table is absent — the
    deploy-facing app.sqlite may predate the hydro ingest on a given
    machine, and a missing table must render as 'no data yet', not a 500
    (Streamlit Cloud outage of 2026-10-13)."""
    try:
        df = pd.read_sql_query(
            "SELECT date, level_m, error_m FROM hydro_levels WHERE "
            "station_key=? ORDER BY date", con, params=(station_key,))
    except pd.errors.DatabaseError:
        return pd.DataFrame(columns=["date", "level_m", "error_m"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def monthly_climatology(df: pd.DataFrame) -> pd.DataFrame:
    """Same-calendar-month mean +/- 1 sigma, using the station's full
    available history (records are short — 10-24 yrs — so no separate
    baseline window is carved out, unlike the 1991-2020 CHIRPS climatology
    used elsewhere in this monitor)."""
    g = df.assign(month=df["date"].dt.month).groupby("month")["level_m"]
    return g.agg(mean="mean", std="std", n="count").reset_index()


def latest_vs_band(df: pd.DataFrame) -> dict:
    """Latest observation plus its calendar-month climatology band."""
    if df.empty:
        return {}
    clim = monthly_climatology(df)
    last = df.iloc[-1]
    row = clim[clim["month"] == last["date"].month]
    if row.empty:
        return {"date": last["date"], "level_m": last["level_m"]}
    r = row.iloc[0]
    return {
        "date": last["date"], "level_m": last["level_m"],
        "clim_mean": r["mean"], "clim_std": r["std"], "clim_n": int(r["n"]),
        "z": (None if pd.isna(r["std"]) or r["std"] == 0
              else (last["level_m"] - r["mean"]) / r["std"]),
    }


def etiage_percentile(df: pd.DataFrame, window_days: int = 15) -> dict:
    """Latest reading's percentile within a +/-window_days day-of-year
    climatology (circular, wraps year-end). Display only — uncalibrated:
    there is no documented catalog of SNEL curtailment episodes yet to
    check candidate low-water thresholds against (D2 per the scoping
    doc), so this is a descriptive percentile, not an alert level."""
    if df.empty:
        return {}
    doy = df["date"].dt.dayofyear
    last = df.iloc[-1]
    last_doy = int(doy.iloc[-1])
    # circular distance in day-of-year space (handles wraparound near Jan 1)
    delta = (doy - last_doy).abs()
    delta = np.minimum(delta, 365 - delta)
    window = df[delta <= window_days]
    if len(window) < 8:
        return {"date": last["date"], "level_m": last["level_m"],
                "percentile": None, "n": len(window),
                "note": "too few historical obs in window"}
    pctile = float((window["level_m"] <= last["level_m"]).mean() * 100)
    return {"date": last["date"], "level_m": last["level_m"],
            "percentile": pctile, "n": len(window)}


def main() -> int:
    con = db.connect()
    for station_key in ("inga_congo", "nzilo_reservoir",
                        "lufira_tshangalele"):
        df = load_levels(con, station_key)
        if df.empty:
            print(f"{station_key}: no data")
            continue
        band = latest_vs_band(df)
        z = band.get("z")
        print(f"{station_key}: {len(df)} obs, {df['date'].min().date()} - "
              f"{df['date'].max().date()}; latest {band['date'].date()} = "
              f"{band['level_m']:.2f}m vs month-clim "
              f"{band.get('clim_mean', float('nan')):.2f}"
              f"+/-{band.get('clim_std', float('nan')):.2f}m "
              f"(n={band.get('clim_n', 0)})"
              + (f", z={z:+.2f}" if z is not None else ""))
        if station_key == "inga_congo":
            e = etiage_percentile(df)
            if e.get("percentile") is not None:
                print(f"  low-water percentile (display only — "
                      f"uncalibrated): {e['percentile']:.0f}th "
                      f"(n={e['n']} obs within +/-15d of day-of-year)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
