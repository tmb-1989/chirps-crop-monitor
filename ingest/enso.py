"""ENSO state ingest: NOAA CPC Oceanic Niño Index (ONI).

Fetches the full ONI table (3-month running SST anomalies in Niño 3.4,
~monthly updates, no auth) into the `enso` table. Phase classification
(the standard NOAA convention plus a 'developing' tier for the country
risk board):

  elnino active      >= 5 consecutive overlapping seasons with anom >= 0.5
  elnino developing  latest season anom >= 0.5, streak still < 5
  lanina active/developing  mirrored at <= -0.5
  neutral            otherwise

v1 limitation: no probabilistic outlook is ingested — 'developing' is
detection, not forecast, so it lags a true watch by a month or two.

Run: python ingest/enso.py
"""
from __future__ import annotations

import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

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
"""


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


def phase(rows: list[tuple]) -> dict:
    """Classify the current ENSO phase from the ONI series (see module
    docstring). Returns {phase, tier, anom, season, year}."""
    anoms = [r[4] for r in rows]
    latest = rows[-1]
    for sign, name in ((1, "elnino"), (-1, "lanina")):
        streak = 0
        for a in reversed(anoms):
            if sign * a >= 0.5:
                streak += 1
            else:
                break
        if streak >= 1:
            return {"phase": name,
                    "tier": "active" if streak >= 5 else "developing",
                    "anom": latest[4], "season": latest[0],
                    "year": latest[1]}
    return {"phase": "neutral", "tier": "none", "anom": latest[4],
            "season": latest[0], "year": latest[1]}


def main() -> int:
    con = db.connect()
    con.executescript(SCHEMA)
    rows = fetch_oni()
    con.executemany("INSERT OR REPLACE INTO enso VALUES (?,?,?,?,?)", rows)
    con.commit()
    p = phase(rows)
    print(f"ONI: {len(rows)} seasons through {p['season']} {p['year']} "
          f"(anom {p['anom']:+.2f}) -> {p['phase']} {p['tier']}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
