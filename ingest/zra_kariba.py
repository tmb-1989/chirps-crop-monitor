"""Zambezi River Authority: Lake Kariba turbine discharge (Mozambique
hydro layer, SCOPING-MOZ-HYDRO.md M1).

Kariba turbine releases are the first-order inflow driver for Cahora
Bassa (routes down the Mupata gorge in days-to-weeks). This module ports
parse_reservoir_html from elnino-hydro-dashboard/ingest/zra.py verbatim
(same plausibility guards) and stores into a new kariba_flows table
(ingest/db.py), which is seeded once from that dashboard's already-
reconstructed 2017- daily series (data/seed/kariba_discharge_daily.csv)
and then continued by this scraper. Cheap, single-page, ungated —
SCOPING-MOZ-HYDRO.md puts it in the 5-daily cron chain.

The reservoir-data page shows the current month x 3 years (current +
prior two), so a run today also backfills the same calendar days from
the two prior years — same idiom as the elnino version, cheap insurance
against a missed run.

Usage:
    python ingest/zra_kariba.py            # scrape the live ZRA page
    python ingest/zra_kariba.py --seed      # one-time: load the seed CSV
                                             # (source='seed'; live rows
                                             # from a prior run are never
                                             # overwritten)
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys
from datetime import datetime
from difflib import get_close_matches

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED_CSV = ROOT / "data" / "seed" / "kariba_discharge_daily.csv"
RESERVOIR_URL = "https://www.zambezira.org/hydrology/kariba-reservoir-data"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}

_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def _num(text):
    text = text.strip().replace("%", "").replace(",", "")
    if not text or text in {"-", "n/a", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _field_for(name: str):
    if "turbine" in name:
        return "turbine_discharge_m3s"
    if "storage" in name:
        return "live_storage_bcm"
    if "percent" in name or name == "%full":
        return "pct_full"
    if name.startswith("lake") or "level" in name:
        return "level_m"
    return None


def _parse_month_header(label: str):
    """Parse a 'August 2026' month-group header to a month-start datetime.

    ZRA hand-edits these headers each month and typos are routine (e.g.
    'Aiugust 2026'), so match the month name fuzzily rather than by
    strptime and pull the year out with a regex."""
    year = re.search(r"\b(19|20)\d{2}\b", label)
    word = re.search(r"[A-Za-z]+", label)
    if not year or not word:
        return None
    name = word.group(0).lower()
    match = [m for m in _MONTHS if m.startswith(name[:3]) and len(name) >= 3]
    if not match:
        match = get_close_matches(name, _MONTHS, n=1, cutoff=0.6)
    if not match:
        return None
    return datetime(int(year.group(0)), _MONTHS.index(match[0]) + 1, 1)


def parse_reservoir_html(html: str) -> pd.DataFrame:
    """Parse the reservoir-data table (current month x 3 years) to a wide
    frame. Ported verbatim from elnino-hydro-dashboard/ingest/zra.py —
    header row 0 carries month-year labels with colspans, e.g.
    Day | July 2024 (2 cols) | July 2025 (2 cols) | July 2026 (4 cols);
    a subheader row names the columns within each year group."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("ZRA reservoir-data page: no table found")
    trs = table.find_all("tr")

    header = trs[0].find_all(["td", "th"])
    groups = []  # (month_start: datetime, n_cols)
    for cell in header[1:]:
        label = cell.get_text(strip=True)
        span = int(cell.get("colspan", 1))
        month_start = _parse_month_header(label)
        if month_start is None:
            raise ValueError(
                f"ZRA reservoir-data: unparseable month header {label!r}")
        groups.append((month_start, span))

    sub = [c.get_text(strip=True).lower() for c in trs[1].find_all(["td", "th"])]
    if sub and sub[0] == "day":
        sub = sub[1:]
    col_names, i = [], 0
    for month_start, span in groups:
        names = sub[i:i + span]
        i += span
        col_names.append((month_start, names))

    rows = []
    for tr in trs[2:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or not cells[0].isdigit():
            continue
        day_no = int(cells[0])
        j = 1
        for month_start, names in col_names:
            for name in names:
                val = _num(cells[j]) if j < len(cells) else None
                j += 1
                if val is None:
                    continue
                try:
                    date = month_start.replace(day=day_no)
                except ValueError:
                    continue
                field = _field_for(name)
                if field:
                    rows.append({"date": date, "field": field, "value": val})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("ZRA reservoir-data page: table parsed to zero rows")
    wide = df.pivot_table(index="date", columns="field", values="value").reset_index()
    wide.columns.name = None
    # Plausibility guards (elnino version, unchanged): the ZRA tables
    # contain occasional transcription typos. Null, don't guess.
    bounds = {
        "level_m": (470, 492),
        "pct_full": (0, 100),
        "live_storage_bcm": (0, 70),
        "turbine_discharge_m3s": (0, 3000),
    }
    for col, (lo, hi) in bounds.items():
        if col in wide.columns:
            wide.loc[(wide[col] < lo) | (wide[col] > hi), col] = None
    return wide


def fetch_reservoir_html() -> str:
    resp = requests.get(RESERVOIR_URL, headers=UA, timeout=60)
    resp.raise_for_status()
    return resp.text


def run() -> dict:
    html = fetch_reservoir_html()
    wide = parse_reservoir_html(html)
    rows = [
        (d.date().isoformat(),
         None if pd.isna(r.get("level_m")) else float(r["level_m"]),
         None if pd.isna(r.get("turbine_discharge_m3s"))
         else float(r["turbine_discharge_m3s"]))
        for d, r in zip(wide["date"], wide.to_dict("records"))
    ]
    rows = [r for r in rows if r[1] is not None or r[2] is not None]
    con = db.connect()
    n_new = db.upsert_kariba_flows(con, rows, source="zra_live")
    con.commit()
    con.close()
    latest = max(rows, key=lambda r: r[0]) if rows else None
    out = {"rows": len(rows), "new": n_new,
           "latest": latest[0] if latest else None,
           "latest_level_m": latest[1] if latest else None,
           "latest_turbine_m3s": latest[2] if latest else None}
    print(f"[zra_kariba] {len(rows)} rows scraped ({n_new} new); "
          f"latest {out['latest']}: level={out['latest_level_m']}, "
          f"turbine={out['latest_turbine_m3s']} m3/s")
    return out


def seed() -> dict:
    """One-time load of the reconstructed 2017- seed CSV (source='seed';
    never overwrites an existing 'zra_live' row for the same date)."""
    if not SEED_CSV.exists():
        raise SystemExit(f"seed CSV not found: {SEED_CSV}")
    rows = []
    with SEED_CSV.open() as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    reader = csv.DictReader(lines)
    for r in reader:
        level = float(r["level_m"]) if r["level_m"] else None
        turb = float(r["turbine_m3s"]) if r["turbine_m3s"] else None
        if level is None and turb is None:
            continue
        rows.append((r["date"], level, turb))
    con = db.connect()
    n_new = db.upsert_kariba_flows(con, rows, source="seed")
    con.commit()
    con.close()
    print(f"[zra_kariba --seed] loaded {len(rows)} seed rows ({n_new} new) "
          f"from {SEED_CSV}")
    return {"rows": len(rows), "new": n_new}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true",
                    help="one-time load of data/seed/kariba_discharge_daily.csv")
    args = ap.parse_args()
    try:
        if args.seed:
            seed()
        else:
            run()
    except Exception as exc:  # noqa: BLE001
        print(f"[zra_kariba] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
