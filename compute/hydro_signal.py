"""Hydropower inflow proxy per catchment (V2.4, SCOPING-V2).

For each cat_* catchment (data/zones/catchments.geojson, rainfall in
observations as chirps3local_dekad_data / -prelim boolean pixel means):

- trailing 3-month (9-dekad) rainfall sum, and its percentile against
  rolling sums ending at the same dekad-of-year across all prior years
  (1981-present) — the inflow-side drought signal while the wet season
  runs;
- wet-season-to-date cumulative vs the 1991-2020 climatology — and,
  out of season, the LAST completed season's total % of normal (the
  refill question a reservoir carries through the dry months).

Writes live.hydro_catchment (replaced wholesale each run); the country
board (compute/country_risk.py) turns these into hydro lights. This is
a RAINFALL proxy: it says what the sky delivered to the catchment, not
what the reservoir holds — only Kariba has a live level feed.

Usage: python compute/hydro_signal.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
import db  # noqa: E402

CATCH_GJ = ROOT / "data" / "zones" / "catchments.geojson"
TRAIL = 9           # dekads (~3 months)
CLIM = (1991, 2021)  # range() end-exclusive -> 1991..2020

# wet-season windows (month start, end; cross-year allowed) per
# catchment. Tana is bimodal — each window is a season of its own.
WET = {
    "cat_kariba": [(11, 4)], "cat_kafue": [(11, 4)],
    "cat_cahora": [(11, 4)], "cat_shire": [(11, 4)],
    "cat_rufiji": [(11, 5)],
    "cat_tana": [(3, 5), (10, 12)],
    "cat_gerd": [(6, 9)],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.hydro_catchment (
    zone_key   TEXT PRIMARY KEY,
    name       TEXT,
    countries  TEXT,
    as_of      TEXT,               -- last dekad start used
    in_season  INTEGER,
    trail_mm   REAL,               -- trailing 9-dekad sum
    trail_pct  REAL,               -- percentile vs same window, all years
    season_pct_normal REAL,        -- season-to-date (or last season) vs clim
    season_label TEXT,             -- e.g. "2026-27 to date" / "2025-26 final"
    computed_at TEXT
);
"""


def _in(m: int, a: int, b: int) -> bool:
    return (a <= m <= b) if a <= b else (m >= a or m <= b)


def pick_window(month: int, windows):
    """The window containing `month`, else the most recently ended one."""
    for a, b in windows:
        if _in(month, a, b):
            return (a, b), True
    best, gap = windows[0], 13
    for a, b in windows:
        g = (month - b) % 12          # months since this window ended
        if g < gap:
            best, gap = (a, b), g
    return best, False


def start_year(d: dt.date, a: int, b: int, ins: bool) -> int:
    """Season start-year containing d (in season) or last completed."""
    cross = a > b
    if ins:
        return d.year if (not cross or d.month >= a) else d.year - 1
    if cross:
        return d.year - 1             # Nov-Apr season seen from the dry gap
    return d.year if d.month > b else d.year - 1


def main() -> int:
    con = db.connect()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.executescript(SCHEMA)
    props = {f["properties"]["zone_key"]: f["properties"]
             for f in json.loads(CATCH_GJ.read_text())["features"]}
    today = dt.date.today()
    rows = []
    for zk, p in props.items():
        obs = con.execute(
            "SELECT granule_start, value FROM observations WHERE zone_key=? "
            "AND dataset IN ('chirps3local_dekad_data',"
            "'chirps3local-prelim_dekad_data') ORDER BY granule_start",
            (zk,)).fetchall()
        if not obs:
            print(f"[{zk}] no rainfall rows yet — backfill pending",
                  file=sys.stderr)
            continue
        dates = [dt.date.fromisoformat(d) for d, _ in obs]
        vals = np.array([v for _, v in obs], float)
        as_of = dates[-1]

        def doy(d: dt.date) -> int:          # dekad-of-year 0..35
            return (d.month - 1) * 3 + min(2, (d.day - 1) // 10)

        # trailing 9-dekad sum, ranked against rolling sums ending at
        # the same dekad-of-year in every prior year
        trail_mm = trail_pct = None
        if len(vals) >= TRAIL:
            trail_mm = float(vals[-TRAIL:].sum())
            roll = np.convolve(vals, np.ones(TRAIL), "valid")
            ends = dates[TRAIL - 1:]
            same = np.array([s for s, d in zip(roll, ends)
                             if doy(d) == doy(as_of)
                             and d.year < as_of.year])
            if same.size >= 10:
                trail_pct = float(100.0 * (same < trail_mm).mean())

        # season-to-date (in season) or last completed season vs clim
        (a, b), ins = pick_window(today.month, WET[zk])
        cross = a > b
        sy = start_year(as_of if ins else today, a, b, ins)

        def season_sum(y: int, until: dt.date | None) -> float:
            s0 = dt.date(y, a, 1)
            s1 = dt.date(y + 1 if cross else y, b, 28)
            if until is not None:
                uy = y + 1 if (cross and until.month <= b) else y
                s1 = min(s1, dt.date(uy, until.month, min(until.day, 28)))
            return float(sum(v for d, v in zip(dates, vals)
                             if s0 <= d <= s1))

        until = as_of if ins else None
        cur = season_sum(sy, until)
        clims = [season_sum(y, until) for y in range(*CLIM)]
        clim = float(np.mean(clims)) if clims else 0.0
        spn = (100.0 * cur / clim) if clim > 0 else None
        label = (f"{sy}-{str(sy + 1)[2:]}" if cross else f"{sy}") + \
            (" to date" if ins else " final")

        rows.append((zk, p["name"], p["countries"], as_of.isoformat(),
                     int(ins), trail_mm, trail_pct, spn, label, now))
        tp = "—" if trail_pct is None else f"p{trail_pct:.0f}"
        sp = "—" if spn is None else f"{spn:.0f}%"
        print(f"[{zk}] through {as_of}: trail "
              f"{0 if trail_mm is None else trail_mm:.0f}mm ({tp}), "
              f"season {label}: {sp} of normal"
              f"{' [in season]' if ins else ''}")

    if rows:
        con.execute("DELETE FROM live.hydro_catchment")
        con.executemany(
            "INSERT INTO live.hydro_catchment VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows)
        con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
