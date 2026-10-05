"""Reservoir levels from NASA GWM satellite altimetry (V3.2).

Source: the GWM HEP (hydropower) reservoir product — a 5 MB tarball of
curated per-target altimetry files (successor to USDA G-REALM, which
retired in 2025). Column 15 of each target file is the water level in
meters above mean sea level (EGM2008), one datum across the whole
record (TOPEX 1992 → Sentinel-6A today, ~10-day cadence).

Engineering anchors (SCOPING-HYDRO-LEVELS §6): each reservoir carries
its published operating band (MOL–FSL), validated against the
altimetry record's own range. Lake Victoria's band is anchored to two
documented in-datum events instead (2006 generation-crisis minimum,
2020 flood maximum) — a run-of-river cascade has no storage band.

Tables: reservoir_levels (monitor.sqlite, full history) and
live.reservoir_state (latest reading + band position + 8-week trend).

Run on dekad days by run_update.sh (the source tarball is 324 MB and
the altimetry cadence is ~10 days — daily fetching buys nothing).
Usage: python ingest/gwm_levels.py [--from-file lake.10.tar.gz]
"""
from __future__ import annotations

import datetime as dt
import io
import pathlib
import re
import sys
import tarfile
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

# the curated HEP tarball lives on har.gsfc.nasa.gov, which serves an
# EXPIRED certificate (checked 5 Oct 2026) — so we fetch the full
# 10-day tarball (324 MB) from the valid-cert main host instead, and
# only on dekad days: the altimetry itself moves every ~10 days.
URL = "https://earth.gsfc.nasa.gov/gwm/zip/lake.10.tar.gz"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}
# level must fall within [MOL - SLACK, FSL + SLACK] to be accepted —
# a corrupted archive or re-biased target fails closed
PLAUSIBLE_SLACK = 20.0

# res_key: (GWM target id, name, iso3 lit, MOL, FSL, anchor note)
RESERVOIRS = {
    "res_cahora": ("000414", "Cahora Bassa", "MOZ", 295.0, 326.0,
                   "HCB published operating band"),
    "res_gibe3": ("002323", "Gilgel Gibe III", "ETH", 800.0, 892.0,
                  "EEP/Salini design MOL-FSL"),
    "res_victoria": ("000314", "Lake Victoria (Victoria Nile cascade)",
                     "UGA", 1134.28, 1137.28,
                     "in-datum anchors: 2006 crisis min / 2020 flood max"),
    "res_malawi": ("000317", "Lake Malawi (Shire cascade)", "MWI",
                   473.2, 477.2, "Kamuzu Barrage regulation band"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservoir_levels (
    res_key   TEXT NOT NULL,
    date      TEXT NOT NULL,
    level_m   REAL NOT NULL,         -- EGM2008 meters above msl
    mission   TEXT,
    PRIMARY KEY (res_key, date)
);
DROP TABLE IF EXISTS live.reservoir_state;  -- derived, replaced per run
CREATE TABLE live.reservoir_state (
    res_key    TEXT PRIMARY KEY,
    name       TEXT,
    iso3       TEXT,
    date       TEXT,                 -- latest observation
    level_m    REAL,
    mol_m      REAL,
    fsl_m      REAL,
    band_frac  REAL,                 -- (level-MOL)/(FSL-MOL)
    trend_8wk  REAL,                 -- level change over ~8 weeks (m)
    -- V3.3 drawdown monitor (mirrors the Kariba drawdown rule):
    drawdown_m_wk REAL,              -- 8-week linear rate (+=falling)
    refill_doy REAL,                 -- climatological refill onset
    refill_date TEXT,                -- next onset after `date`
    weeks_to_refill REAL,
    proj_level_m REAL,               -- level projected at refill onset
    proj_frac  REAL,                 -- band position at refill onset
    anchor_note TEXT,
    fetched_at TEXT
);
"""


def refill_onset_doy(rows: list[tuple[str, float, str]]) -> float | None:
    """Climatological refill onset = circular median day-of-year of the
    annual minimum level (the trough is where drawdown ends and refill
    begins). Circular mean handles southern reservoirs whose trough
    straddles the new year."""
    import math
    mins: dict[int, tuple[float, int]] = {}
    for d, v, _ in rows:
        dd = dt.date.fromisoformat(d)
        # hydrological year: shift by 6 months so a Dec-Jan trough
        # lands mid-"year" and min() is well defined per year
        hy = (dd + dt.timedelta(days=182)).year
        doy = dd.timetuple().tm_yday
        if hy not in mins or v < mins[hy][0]:
            mins[hy] = (v, doy)
    doys = [doy for _, doy in mins.values()]
    if len(doys) < 5:
        return None
    ang = [2 * math.pi * d / 365.25 for d in doys]
    x = sum(math.cos(a) for a in ang) / len(ang)
    y = sum(math.sin(a) for a in ang) / len(ang)
    mean = math.atan2(y, x) % (2 * math.pi)
    return mean * 365.25 / (2 * math.pi)


def drawdown_rate(rows: list[tuple[str, float, str]]) -> float | None:
    """8-week least-squares slope, meters per week (positive = falling)."""
    last = dt.date.fromisoformat(rows[-1][0])
    pts = [((dt.date.fromisoformat(d) - last).days / 7.0, v)
           for d, v, _ in rows
           if (last - dt.date.fromisoformat(d)).days <= 56]
    if len(pts) < 4:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den < 1e-9:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    return -slope  # positive = falling


def parse_target(text: str) -> list[tuple[str, float, str]]:
    rows = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) >= 16 and re.match(r"^\d{8}$", p[2]) \
                and p[14] != "9999.99":
            d = f"{p[2][:4]}-{p[2][4:6]}-{p[2][6:]}"
            rows.append((d, float(p[14]), p[0]))
    return rows


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", help="parse a local lake.10.tar.gz "
                    "instead of downloading (one-time backfills)")
    args = ap.parse_args()
    if args.from_file:
        blob = pathlib.Path(args.from_file).read_bytes()
    else:
        blob = urllib.request.urlopen(
            urllib.request.Request(URL, headers=UA), timeout=900).read()
    con = db.connect()
    con.executescript(SCHEMA)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    wanted = {f"lake{tid}": (rk, *rest)
              for rk, (tid, *rest) in RESERVOIRS.items()}
    found = set()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        for m in tf.getmembers():
            stem = m.name.split(".")[0]
            if stem not in wanted or not m.name.endswith(".txt"):
                continue
            rk, name, iso3, mol, fsl, note = wanted[stem]
            rows = parse_target(tf.extractfile(m).read().decode(
                "utf-8", "replace"))
            # plausibility gate: drop any reading outside the anchored
            # band ±PLAUSIBLE_SLACK (corrupt archive / re-biased target)
            rows = [r for r in rows
                    if mol - PLAUSIBLE_SLACK <= r[1] <= fsl + PLAUSIBLE_SLACK]
            if not rows:
                print(f"[{rk}] no plausible rows — skipped",
                      file=sys.stderr)
                continue
            found.add(rk)
            con.executemany(
                "INSERT OR REPLACE INTO reservoir_levels VALUES (?,?,?,?)",
                [(rk, d, v, mi) for d, v, mi in rows])
            d_last, lvl, _ = rows[-1]
            dd_last = dt.date.fromisoformat(d_last)
            cut = (dd_last - dt.timedelta(days=56)).isoformat()
            older = [v for d, v, _ in rows if d <= cut]
            trend = lvl - older[-1] if older else None
            frac = (lvl - mol) / (fsl - mol)
            # V3.3 drawdown monitor: project the current rate forward
            # to the climatological refill onset (Kariba-rule mirror)
            rate = drawdown_rate(rows)
            rdoy = refill_onset_doy(rows)
            rdate = wks = proj = pfrac = None
            if rdoy is not None:
                rdate = dt.date(dd_last.year, 1, 1) + \
                    dt.timedelta(days=rdoy - 1)
                if rdate <= dd_last:
                    rdate = dt.date(dd_last.year + 1, 1, 1) + \
                        dt.timedelta(days=rdoy - 1)
                wks = (rdate - dd_last).days / 7.0
                if rate is not None and rate > 0:
                    proj = lvl - rate * wks
                    pfrac = (proj - mol) / (fsl - mol)
            con.execute(
                "INSERT OR REPLACE INTO live.reservoir_state VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rk, name, iso3, d_last, lvl, mol, fsl, round(frac, 3),
                 None if trend is None else round(trend, 2),
                 None if rate is None else round(rate, 3),
                 None if rdoy is None else round(rdoy, 1),
                 None if rdate is None else rdate.isoformat(),
                 None if wks is None else round(wks, 1),
                 None if proj is None else round(proj, 2),
                 None if pfrac is None else round(pfrac, 3),
                 note, now))
            print(f"[{rk}] {len(rows)} obs; latest {d_last} {lvl:.2f}m "
                  f"({frac:.0%} of band"
                  + (f", {trend:+.2f}m/8wk" if trend is not None else "")
                  + (f"; drawdown {rate:.2f}m/wk -> {pfrac:.0%} of band "
                     f"at refill ~{rdate}" if pfrac is not None else
                     f"; refill ~{rdate}" if rdate else "")
                  + ")")
    con.commit()
    con.close()
    missing = set(RESERVOIRS) - found
    if missing:
        print(f"MISSING targets: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
