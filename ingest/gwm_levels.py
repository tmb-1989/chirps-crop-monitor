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
CREATE TABLE IF NOT EXISTS live.reservoir_state (
    res_key    TEXT PRIMARY KEY,
    name       TEXT,
    iso3       TEXT,
    date       TEXT,                 -- latest observation
    level_m    REAL,
    mol_m      REAL,
    fsl_m      REAL,
    band_frac  REAL,                 -- (level-MOL)/(FSL-MOL)
    trend_8wk  REAL,                 -- level change over ~8 weeks (m)
    anchor_note TEXT,
    fetched_at TEXT
);
"""


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
            cut = (dt.date.fromisoformat(d_last)
                   - dt.timedelta(days=56)).isoformat()
            older = [v for d, v, _ in rows if d <= cut]
            trend = lvl - older[-1] if older else None
            frac = (lvl - mol) / (fsl - mol)
            con.execute(
                "INSERT OR REPLACE INTO live.reservoir_state VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?)",
                (rk, name, iso3, d_last, lvl, mol, fsl, round(frac, 3),
                 None if trend is None else round(trend, 2), note, now))
            print(f"[{rk}] {len(rows)} obs; latest {d_last} {lvl:.2f}m "
                  f"({frac:.0%} of band"
                  + (f", {trend:+.2f}m/8wk" if trend is not None else "")
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
