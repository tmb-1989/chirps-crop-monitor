"""ENSO state ingest: NOAA CPC Oceanic Niño Index (ONI) + weekly Niño SSTs.

Fetches the full ONI table (3-month running SST anomalies in Niño 3.4,
~monthly updates, no auth) into the `enso` table, and the weekly Niño
region SSTs (from 1990, ~5-day lag) into `enso_weekly` — the weekly
Niño 3.4 anomaly leads the ONI by 1-2 months and sharpens the
'developing' call. Weekly parser ported from the elnino-hydro-dashboard
project. Phase classification (the standard NOAA convention plus a
'developing' tier for the country risk board):

  elnino active      >= 5 consecutive overlapping seasons with anom >= 0.5
  elnino developing  latest season anom >= 0.5, streak still < 5 — or ONI
                     still neutral but the last 4 weekly Niño 3.4 anomalies
                     all >= 0.5 (early detection from the weekly series)
  lanina active/developing  mirrored at <= -0.5
  neutral            otherwise

v1 limitation: no probabilistic outlook is ingested — 'developing' is
detection, not forecast, though the weekly series narrows the lag to days.

Run: python ingest/enso.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"

# ONI season label -> center month
CENTER = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
          "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}

SCHEMA = """
CREATE TABLE IF NOT EXISTS enso (
    season TEXT NOT NULL,
    year   INTEGER NOT NULL,
    center_month INTEGER NOT NULL,
    total  REAL,
    anom   REAL,
    PRIMARY KEY (season, year)
);
CREATE TABLE IF NOT EXISTS enso_weekly (
    date TEXT PRIMARY KEY,
    nino12_sst REAL, nino12_anom REAL,
    nino3_sst  REAL, nino3_anom  REAL,
    nino34_sst REAL, nino34_anom REAL,
    nino4_sst  REAL, nino4_anom  REAL
);
"""

_WEEK_RE = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})")
_NUM_RE = re.compile(r"-?\d+\.\d")


def fetch_nino_weekly() -> list[tuple]:
    """Weekly Niño region SSTs/anomalies (1990 normals file)."""
    r = requests.get(WEEKLY_URL, timeout=60)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
        m = _WEEK_RE.match(line)
        if not m:
            continue
        nums = _NUM_RE.findall(line[m.end():])
        if len(nums) != 8:
            continue
        date = dt.datetime.strptime(m.group(1).title(), "%d%b%Y").date()
        rows.append((date.isoformat(), *[float(x) for x in nums]))
    if len(rows) < 100:
        raise RuntimeError(f"weekly Niño parse looks wrong: {len(rows)} rows")
    return rows


def fetch_oni() -> list[tuple]:
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines()[1:]:
        parts = line.split()
        if len(parts) != 4 or parts[0] not in CENTER:
            continue
        seas, yr, total, anom = parts
        rows.append((seas, int(yr), CENTER[seas], float(total), float(anom)))
    if len(rows) < 100:
        raise RuntimeError(f"ONI parse looks wrong: {len(rows)} rows")
    return rows


def phase(rows: list[tuple], weekly: list[tuple] | None = None) -> dict:
    """Classify the current ENSO phase from the ONI series, sharpened by
    the weekly Niño 3.4 anomalies when given (see module docstring).
    Returns {phase, tier, anom, season, year[, weekly_anom, weekly_date]}."""
    anoms = [r[4] for r in rows]
    latest = rows[-1]
    out = {"phase": "neutral", "tier": "none", "anom": latest[4],
           "season": latest[0], "year": latest[1]}
    for sign, name in ((1, "elnino"), (-1, "lanina")):
        streak = 0
        for a in reversed(anoms):
            if sign * a >= 0.5:
                streak += 1
            else:
                break
        if streak >= 1:
            out.update(phase=name,
                       tier="active" if streak >= 5 else "developing")
            break
    if weekly:
        wk = weekly[-1]
        out["weekly_anom"], out["weekly_date"] = wk[6], wk[0]
        if out["phase"] == "neutral" and len(weekly) >= 4:
            last4 = [w[6] for w in weekly[-4:]]
            for sign, name in ((1, "elnino"), (-1, "lanina")):
                if all(sign * a >= 0.5 for a in last4):
                    out.update(phase=name, tier="developing")
    return out


def phase_from_db(con) -> dict:
    """Current phase from the stored ONI + weekly tables."""
    oni = con.execute("SELECT season, year, center_month, total, anom "
                      "FROM enso ORDER BY year, center_month").fetchall()
    weekly = con.execute(
        "SELECT date, nino12_sst, nino12_anom, nino3_sst, nino3_anom, "
        "nino34_sst, nino34_anom, nino4_sst, nino4_anom FROM enso_weekly "
        "ORDER BY date").fetchall()
    if not oni:
        return {}
    return phase(oni, weekly or None)


def main() -> int:
    con = db.connect()
    con.executescript(SCHEMA)
    rows = fetch_oni()
    con.executemany("INSERT OR REPLACE INTO enso VALUES (?,?,?,?,?)", rows)
    weekly = fetch_nino_weekly()
    con.executemany("INSERT OR REPLACE INTO enso_weekly VALUES "
                    "(?,?,?,?,?,?,?,?,?)", weekly)
    con.commit()
    p = phase(rows, weekly)
    print(f"ONI: {len(rows)} seasons through {p['season']} {p['year']} "
          f"(anom {p['anom']:+.2f}); weekly Niño3.4 {p['weekly_anom']:+.1f} "
          f"({p['weekly_date']}) -> {p['phase']} {p['tier']}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
