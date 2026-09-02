"""Lake Kariba levels from the Zambezi River Authority — ported from the
elnino-hydro-dashboard project (ingest/zra.py there).

Two pages, both scraped:
  /hydrology/lake-levels           daily level + usable storage %, trailing
                                   ~2 weeks, with a same-day-last-year column
  /hydrology/kariba-reservoir-data current month, daily, current + two prior
                                   years; current year adds live storage
                                   (BCM) and turbine discharge (m3/s)

Neither page exposes an archive, so history accrues per run (the cron's
5-day cadence is fine) — plus `--import-legacy` seeds the history the
elnino-hydro-dashboard project accrued in its parquet store (including a
Wayback Machine backfill to 2017).

Tables: kariba_level (levels page, with vintage), kariba_reservoir
(reservoir page, wide). The hydro traffic light reads kariba_reservoir
first and falls back to kariba_level.

Run: python ingest/kariba.py [--import-legacy [DIR]]
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
from difflib import get_close_matches

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

LEVELS_URL = "https://www.zambezira.org/hydrology/lake-levels"
RESERVOIR_URL = "https://www.zambezira.org/hydrology/kariba-reservoir-data"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36"}

LEGACY_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / \
    "elnino-hydro-dashboard" / "data" / "series"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kariba_level (
    date TEXT NOT NULL,
    level_m REAL,
    pct_full REAL,
    vintage TEXT,
    PRIMARY KEY (date, vintage)
);
CREATE TABLE IF NOT EXISTS kariba_reservoir (
    date TEXT PRIMARY KEY,
    level_m REAL,
    pct_full REAL,
    live_storage_bcm REAL,
    turbine_discharge_m3s REAL
);
"""

# Plausibility guards: ZRA tables contain occasional transcription typos
# (e.g. 467.47 for 476.47 on 2024-09-30). Null, don't guess.
BOUNDS = {
    "level_m": (470, 492),
    "pct_full": (0, 100),
    "live_storage_bcm": (0, 70),
    "turbine_discharge_m3s": (0, 3000),
}


def _get(url):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r


def _num(text):
    text = text.strip().replace("%", "").replace(",", "")
    if not text or text in {"-", "n/a", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bound(field, val):
    if val is None:
        return None
    lo, hi = BOUNDS.get(field, (float("-inf"), float("inf")))
    return val if lo <= val <= hi else None


def fetch_lake_levels(con, today=None) -> int:
    """Parse the levels table: Day | This Year | %Full | Last Year | %Full."""
    today = today or dt.datetime.now()
    soup = BeautifulSoup(_get(LEVELS_URL).text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("ZRA lake-levels page: no table found")
    n = 0
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 5 or cells[0] == "Day":
            continue
        # "23-Jun" belongs to the current year unless that lands in the
        # future (a December row read in January) — then roll back a year.
        try:
            day = dt.datetime.strptime(f"{cells[0]}-{today.year}", "%d-%b-%Y")
        except ValueError:
            continue
        if day > today + dt.timedelta(days=3):
            day = day.replace(year=day.year - 1)
        pairs = [(day, cells[1], cells[2], "current")]
        try:
            pairs.append((day.replace(year=day.year - 1), cells[3], cells[4],
                          "lastyear_backfill"))
        except ValueError:  # Feb 29
            pass
        for d, lv, pc, vintage in pairs:
            level = _bound("level_m", _num(lv))
            if level is None:
                continue
            con.execute("INSERT OR REPLACE INTO kariba_level VALUES "
                        "(?,?,?,?)", (d.date().isoformat(), level,
                                      _bound("pct_full", _num(pc)), vintage))
            n += 1
    if n == 0:
        raise ValueError("ZRA lake-levels page: table parsed to zero rows")
    con.commit()
    return n


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


_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def _parse_month_header(label: str):
    """ZRA hand-edits these headers and typos are routine ('Aiugust 2026'),
    so match the month fuzzily and regex the year."""
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
    return dt.date(int(year.group(0)), _MONTHS.index(match[0]) + 1, 1)


def fetch_reservoir_data(con) -> int:
    """Parse the reservoir table (current month x 3 years, colspan groups)."""
    soup = BeautifulSoup(_get(RESERVOIR_URL).text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("ZRA reservoir-data page: no table found")
    trs = table.find_all("tr")
    groups = []
    for cell in trs[0].find_all(["td", "th"])[1:]:
        label = cell.get_text(strip=True)
        month_start = _parse_month_header(label)
        if month_start is None:
            raise ValueError(f"unparseable month header {label!r}")
        groups.append((month_start, int(cell.get("colspan", 1))))
    sub = [c.get_text(strip=True).lower() for c in trs[1].find_all(["td", "th"])]
    if sub and sub[0] == "day":
        sub = sub[1:]
    col_names, i = [], 0
    for month_start, span in groups:
        col_names.append((month_start, sub[i:i + span]))
        i += span
    recs = {}
    for tr in trs[2:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or not cells[0].isdigit():
            continue
        day_no, j = int(cells[0]), 1
        for month_start, names in col_names:
            for name in names:
                val = _num(cells[j]) if j < len(cells) else None
                j += 1
                field = _field_for(name)
                if val is None or field is None:
                    continue
                try:
                    date = month_start.replace(day=day_no).isoformat()
                except ValueError:
                    continue
                recs.setdefault(date, {})[field] = _bound(field, val)
    if not recs:
        raise ValueError("ZRA reservoir-data page: table parsed to zero rows")
    for date, f in recs.items():
        con.execute(
            "INSERT INTO kariba_reservoir VALUES (?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "level_m=coalesce(excluded.level_m, level_m), "
            "pct_full=coalesce(excluded.pct_full, pct_full), "
            "live_storage_bcm=coalesce(excluded.live_storage_bcm, "
            "  live_storage_bcm), "
            "turbine_discharge_m3s=coalesce(excluded.turbine_discharge_m3s, "
            "  turbine_discharge_m3s)",
            (date, f.get("level_m"), f.get("pct_full"),
             f.get("live_storage_bcm"), f.get("turbine_discharge_m3s")))
    con.commit()
    return len(recs)


def import_legacy(con, directory: pathlib.Path) -> None:
    """One-off: seed history from the elnino-hydro-dashboard parquet store."""
    import pandas as pd
    lv = pd.read_parquet(directory / "kariba_level_zra.parquet")
    for _, r in lv.iterrows():
        if _bound("level_m", r.level_m) is None:
            continue
        con.execute("INSERT OR IGNORE INTO kariba_level VALUES (?,?,?,?)",
                    (r.date.date().isoformat(), float(r.level_m),
                     _bound("pct_full", r.pct_full), r.vintage))
    rs = pd.read_parquet(directory / "kariba_reservoir_zra.parquet")
    for _, r in rs.iterrows():
        vals = {f: _bound(f, None if pd.isna(r.get(f)) else float(r.get(f)))
                for f in ("level_m", "pct_full", "live_storage_bcm",
                          "turbine_discharge_m3s")}
        con.execute("INSERT OR IGNORE INTO kariba_reservoir VALUES "
                    "(?,?,?,?,?)",
                    (r.date.date().isoformat(), vals["level_m"],
                     vals["pct_full"], vals["live_storage_bcm"],
                     vals["turbine_discharge_m3s"]))
    con.commit()
    print(f"legacy import: {len(lv)} level rows, {len(rs)} reservoir rows "
          f"offered (INSERT OR IGNORE; live scrapes win)")


def drawdown_rate(con):
    """Rolling 4-week drawdown rate in m/week (positive = falling) from the
    merged daily level series, plus its date. Thresholds (from the source
    project, calibrated on 2017-2026): sustained >0.15 m/wk puts the 478 m
    severe-rationing boundary in play before a mid-Feb refill; >0.20 m/wk
    reaches it by mid-January regardless of the rains. Returns
    (rate, asof_date) or (None, None)."""
    import pandas as pd
    rows = con.execute(
        "SELECT date, level_m FROM kariba_reservoir WHERE level_m IS NOT "
        "NULL UNION SELECT date, level_m FROM kariba_level WHERE level_m "
        "IS NOT NULL ORDER BY date").fetchall()
    if not rows:
        return None, None
    s = pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    lvl = s.resample("D").mean().interpolate(limit=10)
    if len(lvl) < 29 or pd.isna(lvl.iloc[-29]):
        return None, str(s.index[-1].date())
    rate = float((lvl.iloc[-29] - lvl.iloc[-1]) / 4.0)
    return rate, str(lvl.index[-1].date())


def latest_state(con) -> dict:
    """Freshest Kariba reading + drawdown rate, for the risk board."""
    row = con.execute(
        "SELECT date, level_m, pct_full FROM ("
        "SELECT date, level_m, pct_full FROM kariba_reservoir WHERE "
        "level_m IS NOT NULL UNION ALL SELECT date, level_m, pct_full "
        "FROM kariba_level WHERE level_m IS NOT NULL AND vintage='current')"
        "ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        return {}
    rate, rate_asof = drawdown_rate(con)
    return {"date": row[0], "level_m": row[1], "pct_full": row[2],
            "drawdown_m_wk": rate, "rate_asof": rate_asof}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--import-legacy", nargs="?", const=str(LEGACY_DIR),
                    default=None, metavar="DIR")
    args = ap.parse_args()
    con = db.connect()
    con.executescript(SCHEMA)
    if args.import_legacy:
        import_legacy(con, pathlib.Path(args.import_legacy))
    n1 = fetch_lake_levels(con)
    n2 = fetch_reservoir_data(con)
    s = latest_state(con)
    rate = s.get("drawdown_m_wk")
    print(f"Kariba: {n1} level rows, {n2} reservoir days; latest "
          f"{s.get('date')} level {s.get('level_m')}m "
          f"({s.get('pct_full')}% usable), 4-wk drawdown "
          f"{rate if rate is None else round(rate, 3)} m/wk")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
