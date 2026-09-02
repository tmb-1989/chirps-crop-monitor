"""Country risk traffic lights: ENSO / Drought / Flood per country.

Aggregates existing zone- and basin-level signals into one status per
country per factor — green / yellow / red, plus gray when the inputs are
missing or stale (a stale green is worse than no light). Worst-case
aggregation: any tripping zone/basin colors its country, and the reason
string names it.

  ENSO    ONI phase (ingest/enso.py) passed through a per-country
          exposure table (direction + impact months) — a global El Niño
          only lights a country in/near its impact season.
  Drought crop zones: in-season WRSI, SPI-3, FLDAS soil moisture.
  Flood   basins: the flood-watch regional rule + armed/forecast tiers.

Writes the `country_risk` snapshot (PK country+factor) and appends status
changes to `country_risk_log`. Run: python compute/country_risk.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "compute"))
import db  # noqa: E402
import enso  # noqa: E402
from flood_signals import FLOOD_MONTHS, basin_countries  # noqa: E402

NAMES = {"KEN": "Kenya", "ETH": "Ethiopia", "TZA": "Tanzania",
         "RWA": "Rwanda", "UGA": "Uganda", "ZMB": "Zambia",
         "MWI": "Malawi", "ZWE": "Zimbabwe", "MOZ": "Mozambique",
         "MDG": "Madagascar", "ZAF": "South Africa"}
ORDER = list(NAMES)

# ENSO impact per country and phase: (impact months, short note).
# East Africa: El Niño loads the OND short rains (flood side), La Niña
# fails them plus MAM. Ethiopia: El Niño weakens kiremt and wets the
# south/east in OND. Southern Africa: El Niño dries the Nov-Mar main
# season; La Niña brings wet years/cyclone-heavy seasons.
EA_EN = ({10, 11, 12}, "wet OND short rains — flood side")
EA_LN = ({3, 4, 5, 10, 11, 12}, "failed rains risk — drought side")
SA_EN = ({11, 12, 1, 2, 3}, "dry main season — drought side")
SA_LN = ({11, 12, 1, 2, 3}, "wet season / cyclone-heavy — flood side")
EXPOSURE = {
    "KEN": {"elnino": EA_EN, "lanina": EA_LN},
    "TZA": {"elnino": EA_EN, "lanina": EA_LN},
    "UGA": {"elnino": EA_EN, "lanina": EA_LN},
    "RWA": {"elnino": EA_EN, "lanina": EA_LN},
    "ETH": {"elnino": ({6, 7, 8, 9, 10, 11, 12},
                       "weak kiremt (drought) + wet OND south/east"),
            "lanina": ({2, 3, 4, 5, 10, 11, 12},
                       "failed belg/deyr — drought side")},
    "ZMB": {"elnino": SA_EN, "lanina": SA_LN},
    "MWI": {"elnino": SA_EN, "lanina": SA_LN},
    "ZWE": {"elnino": SA_EN, "lanina": SA_LN},
    "MOZ": {"elnino": SA_EN, "lanina": SA_LN},
    "MDG": {"elnino": SA_EN, "lanina": SA_LN},
    "ZAF": {"elnino": SA_EN, "lanina": SA_LN},
}

# staleness thresholds (days) before a factor goes gray
STALE = {"wrsi": 45, "sm": 90, "spi": 30, "flood": 20}

SCHEMA = """
CREATE TABLE IF NOT EXISTS country_risk (
    country  TEXT NOT NULL,
    factor   TEXT NOT NULL,
    status   TEXT NOT NULL,
    reason   TEXT,
    as_of    TEXT,
    computed_at TEXT,
    PRIMARY KEY (country, factor)
);
CREATE TABLE IF NOT EXISTS country_risk_log (
    country  TEXT NOT NULL,
    factor   TEXT NOT NULL,
    status   TEXT NOT NULL,
    prev     TEXT,
    reason   TEXT,
    changed_at TEXT
);
CREATE TABLE IF NOT EXISTS zone_risk (
    zone_key TEXT NOT NULL,
    factor   TEXT NOT NULL,
    country  TEXT NOT NULL,
    name     TEXT,
    status   TEXT NOT NULL,
    reason   TEXT,
    wrsi     REAL,
    wrsi_in_season INTEGER,
    spi3     REAL,
    sm       REAL,
    as_of    TEXT,
    computed_at TEXT,
    PRIMARY KEY (zone_key, factor)
);
"""


def _age(date_str: str | None, today: dt.date) -> int:
    if not date_str:
        return 10_000
    return (today - dt.date.fromisoformat(date_str[:10])).days


def in_season(seasons: str | None, month: int) -> bool:
    """True if `month` falls in any of the zone's season windows
    ('belg:2-5,kiremt:6-9'; cross-year like 'main:10-4' supported)."""
    for tok in (seasons or "").split(","):
        if not tok:
            continue
        a, b = map(int, tok.split(":")[1].split("-"))
        if (a <= month <= b) if a <= b else (month >= a or month <= b):
            return True
    return False


# ---------------------------------------------------------------- factors
def enso_status(con, today: dt.date) -> dict:
    """iso3 -> (status, reason, as_of); one global phase, per-country
    exposure. Impact window = current month..+3."""
    rows = con.execute("SELECT season, year, center_month, total, anom "
                       "FROM enso ORDER BY year, center_month").fetchall()
    out = {}
    if not rows:
        return {c: ("gray", "no ONI data — run ingest/enso.py", None)
                for c in ORDER}
    p = enso.phase(rows)
    as_of = f"{p['season']} {p['year']}"
    # ONI is a 3-month running mean; latest center month ~1-2 months back
    last = dt.date(rows[-1][1], rows[-1][2], 1)
    stale = (today - last).days > 100
    window = {(today.month - 1 + k) % 12 + 1 for k in range(4)}
    for c in ORDER:
        if stale:
            out[c] = ("gray", f"ONI stale (through {as_of})", as_of)
            continue
        if p["phase"] == "neutral":
            out[c] = ("green", f"ENSO neutral (ONI {p['anom']:+.1f})", as_of)
            continue
        months, note = EXPOSURE[c][p["phase"]]
        name = "El Niño" if p["phase"] == "elnino" else "La Niña"
        lab = f"{name} {p['tier']} (ONI {p['anom']:+.1f})"
        near = bool(window & months)
        if p["tier"] == "active" and near:
            out[c] = ("red", f"{lab}: {note}", as_of)
        elif near:
            out[c] = ("yellow", f"{lab}: {note}", as_of)
        elif p["tier"] == "active":
            out[c] = ("yellow", f"{lab} — impact season later: {note}", as_of)
        else:
            out[c] = ("green", f"{lab} — impact season months away", as_of)
    return out


RANK = {"gray": -1, "green": 0, "yellow": 1, "red": 2}


def drought_zones(con, today: dt.date) -> pd.DataFrame:
    """Per-zone drought lights: one row per crop zone with status, the
    tripping reason, and the raw readings (in-season WRSI, SPI-3, SM)."""
    zones = pd.read_sql_query(
        "SELECT zone_key, iso3, name, seasons FROM zones", con)
    latest = pd.read_sql_query(
        "SELECT zone_key, dataset, granule_start, value FROM observations "
        "WHERE dataset IN ('lwrsi_africa_dekad_pctm',"
        "'soilmoisture-0-100cm_global_month_pctm') "
        "GROUP BY zone_key, dataset HAVING granule_start=max(granule_start)",
        con)
    spi = pd.read_sql_query(
        "SELECT zone_key, granule_start, spi3 FROM dekad_metrics "
        "WHERE spi3 IS NOT NULL GROUP BY zone_key "
        "HAVING granule_start=max(granule_start)", con)
    rows = []
    for _, z in zones.iterrows():
        zl = latest[latest.zone_key == z.zone_key].set_index("dataset")
        wr = zl["value"].get("lwrsi_africa_dekad_pctm")
        wr_d = zl["granule_start"].get("lwrsi_africa_dekad_pctm")
        sm = zl["value"].get("soilmoisture-0-100cm_global_month_pctm")
        sm_d = zl["granule_start"].get(
            "soilmoisture-0-100cm_global_month_pctm")
        sp = spi[spi.zone_key == z.zone_key]
        s3 = sp.spi3.iloc[0] if not sp.empty else None
        s3_d = sp.granule_start.iloc[0] if not sp.empty else None
        wr_ok = _age(wr_d, today) <= STALE["wrsi"] and wr is not None \
            and in_season(z.seasons, dt.date.fromisoformat(wr_d).month)
        sm_ok = sm is not None and _age(sm_d, today) <= STALE["sm"]
        s3_ok = s3 is not None and _age(s3_d, today) <= STALE["spi"]
        status, why = "green", "no stress"
        if not (wr_ok or sm_ok or s3_ok):
            status, why = "gray", "inputs stale"
        elif (wr_ok and wr < 80) or \
                (s3_ok and s3 <= -1.5 and sm_ok and sm < 85):
            status = "red"
            why = (f"WRSI {wr:.0f}" if wr_ok and wr < 80 else
                   f"SPI-3 {s3:.1f} + SM {sm:.0f}%")
        elif (wr_ok and wr < 95) or (s3_ok and s3 <= -1) or \
                (sm_ok and sm < 85):
            status = "yellow"
            why = (f"WRSI {wr:.0f}" if wr_ok and wr < 95 else
                   f"SPI-3 {s3:.1f}" if s3_ok and s3 <= -1 else
                   f"SM {sm:.0f}%")
        rows.append({
            "zone_key": z.zone_key, "country": z.iso3, "name": z["name"],
            "status": status, "reason": why,
            "wrsi": wr if wr is not None else None,
            "wrsi_in_season": int(bool(wr_ok)),
            "spi3": s3, "sm": sm,
            "as_of": wr_d if wr_ok else (s3_d or sm_d or wr_d),
            # severity for the country rollup: worse status first, then
            # lower in-season WRSI, then lower SPI-3
            "_sev": (RANK[status],
                     -(wr if wr_ok and wr is not None else 999),
                     -(s3 if s3 is not None else 999)),
        })
    return pd.DataFrame(rows)


def drought_status(zr: pd.DataFrame) -> dict:
    """iso3 -> (status, reason, as_of): worst zone in drought_zones wins."""
    out = {}
    for c in ORDER:
        zs = zr[zr.country == c]
        if zs.empty:
            out[c] = ("gray", "no crop zones ingested", None)
            continue
        live = zs[zs.status != "gray"]
        if live.empty:
            out[c] = ("gray", "all drought inputs stale", None)
            continue
        worst = live.sort_values("_sev", ascending=False).iloc[0]
        if worst.status == "green":
            out[c] = ("green", "no zone stressed", worst.as_of)
        else:
            out[c] = (worst.status, f"{worst['name']}: {worst.reason}",
                      worst.as_of)
    return out


def flood_status(con, today: dt.date) -> dict:
    """iso3 -> (status, reason, as_of) from flood_state basins."""
    bc = basin_countries()
    fs = pd.read_sql_query(
        "SELECT zone_key, granule_start, ante_pct, tier FROM flood_state",
        con)
    fs["iso3"] = fs.zone_key.map(bc)
    gefs = pd.read_sql_query(
        "SELECT zone_key, pct_clim FROM flood_gefs WHERE horizon='10_day' "
        "AND issue_date=(SELECT max(issue_date) FROM flood_gefs)", con)
    wet_fc = set(gefs[gefs.pct_clim >= 150].zone_key)
    out = {}
    for c in ORDER:
        cf = fs[fs.iso3 == c]
        if c not in set(bc.values()):
            out[c] = ("gray", "no flood layer for this country", None)
            continue
        if cf.empty:
            out[c] = ("gray", "no flood signals yet (backfill pending)",
                      None)
            continue
        latest = cf.granule_start.max()
        if _age(latest, today) > STALE["flood"]:
            out[c] = ("gray", f"flood signals stale (through {latest})",
                      latest)
            continue
        cur = cf[cf.granule_start == latest]
        n1 = int((cur.tier >= 1).sum())
        n2 = int((cur.tier >= 2).sum())
        month = dt.date.fromisoformat(latest).month
        season = month in FLOOD_MONTHS[c]
        armed_fc = cur[(cur.ante_pct >= 90)
                       & cur.zone_key.isin(wet_fc)].zone_key.tolist()
        if season and n1 >= 2 and n2 >= 1:
            out[c] = ("red", f"regional alert: {n2} basin(s) alerting, "
                             f"{n1} armed", latest)
        elif (season and n1 >= 1) or armed_fc:
            why = (f"{n1} basin(s) at watch/alert" if season and n1 >= 1
                   else f"armed + wet GEFS 10-day: {len(armed_fc)} basin(s)")
            out[c] = ("yellow", why, latest)
        else:
            out[c] = ("green", "no basin armed or alerting", latest)
    return out


def main() -> int:
    today = dt.date.today()
    con = db.connect()
    con.executescript(SCHEMA)
    zr = drought_zones(con, today)
    factors = {"enso": enso_status(con, today),
               "drought": drought_status(zr),
               "flood": flood_status(con, today)}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.executemany(
        "INSERT OR REPLACE INTO zone_risk VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r.zone_key, "drought", r.country, r["name"], r.status, r.reason,
          r.wrsi, r.wrsi_in_season, r.spi3, r.sm, r.as_of, now)
         for _, r in zr.iterrows()])
    prev = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT country, factor, status FROM country_risk")}
    for fac, states in factors.items():
        for c, (status, reason, as_of) in states.items():
            old = prev.get((c, fac))
            if old is not None and old != status:
                con.execute(
                    "INSERT INTO country_risk_log VALUES (?,?,?,?,?,?)",
                    (c, fac, status, old, reason, now))
                print(f"CHANGE {c} {fac}: {old} -> {status} ({reason})")
            con.execute("INSERT OR REPLACE INTO country_risk VALUES "
                        "(?,?,?,?,?,?)", (c, fac, status, reason, as_of, now))
    con.commit()
    for c in ORDER:
        cells = "  ".join(f"{f}:{factors[f][c][0]:6s}" for f in factors)
        print(f"{NAMES[c]:13s} {cells}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
