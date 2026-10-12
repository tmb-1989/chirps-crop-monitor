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
import iod  # noqa: E402
import kariba  # noqa: E402
from flood_signals import (FLOOD_MONTHS, PARAMS, TELEMETRY_ONLY,  # noqa: E402
                           basin_countries, pentad_end)

NAMES = {"KEN": "Kenya", "ETH": "Ethiopia", "TZA": "Tanzania",
         "RWA": "Rwanda", "UGA": "Uganda", "ZMB": "Zambia",
         "MWI": "Malawi", "ZWE": "Zimbabwe", "MOZ": "Mozambique",
         "MDG": "Madagascar", "ZAF": "South Africa",
         "NGA": "Nigeria", "GHA": "Ghana", "CIV": "Côte d'Ivoire",
         "BEN": "Benin"}
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
    # West Africa (P7). Nigeria's monitored belt is Sahel/Sudan savanna:
    # El Niño weakens the West African monsoon (JAS core), La Niña
    # strengthens it (flood side in the north). The Guinea-coast trio
    # (GHA/CIV/BEN) is deliberately ABSENT: coastal-bimodal rainfall has
    # a weak, non-stationary ENSO link, so their ENSO cell stays gray
    # ("no ENSO exposure profile") rather than inventing a loading.
    "NGA": {"elnino": ({6, 7, 8, 9}, "weak WA monsoon — drought side "
                       "(northern belt)"),
            "lanina": ({7, 8, 9}, "strong monsoon — Sahel flood side")},
}

# staleness thresholds (days) before a factor goes gray
STALE = {"wrsi": 45, "sm": 90, "spi": 30, "flood": 20, "kariba": 14,
         "iod": 90}  # DMI is a slow seasonal index; ~63d OISST lag is normal

# IOD-led countries: the OND short rains respond to the Indian Ocean
# dipole more directly than to ENSO (whose influence is largely mediated
# through it) — enhanced rains under El Niño alone are often beneficial;
# the flood years (1997, 2019) had a strongly positive IOD, and 2019 had
# no El Niño at all. DMI leads the light; ENSO phase is congruence
# context. Ethiopia stays ENSO-led: the kiremt drought channel is the
# dominant food-security link and is directly ENSO-driven.
IOD_LED = {"KEN", "TZA", "UGA", "RWA"}
IOD_COUNTRIES = IOD_LED | {"ETH"}   # DMI shown in the reason text
IOD_THRESHOLD = 0.4                 # watch level
IOD_RED = 0.75                      # flood-year territory (1997/2019 class)

# Kariba hydro thresholds. 475.50 m is the minimum operating level; the
# source project's calibration puts the severe-rationing boundary at 478 m
# and flags sustained drawdown >0.15 m/wk (boundary in play before the
# mid-Feb refill) and >0.20 m/wk (boundary reached by mid-Jan regardless).
KARIBA_RED = {"level_m": 478.0, "pct_full": 10.0, "rate": 0.20}
KARIBA_YEL = {"level_m": 480.0, "pct_full": 20.0, "rate": 0.15}

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.country_risk (
    country  TEXT NOT NULL,
    factor   TEXT NOT NULL,
    status   TEXT NOT NULL,
    reason   TEXT,
    as_of    TEXT,
    computed_at TEXT,
    PRIMARY KEY (country, factor)
);
CREATE TABLE IF NOT EXISTS live.country_risk_log (
    country  TEXT NOT NULL,
    factor   TEXT NOT NULL,
    status   TEXT NOT NULL,
    prev     TEXT,
    reason   TEXT,
    changed_at TEXT
);
CREATE TABLE IF NOT EXISTS live.zone_risk (
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
    season   TEXT,
    PRIMARY KEY (zone_key, factor)
);
"""


def _age(date_str: str | None, today: dt.date) -> int:
    if not date_str:
        return 10_000
    return (today - dt.date.fromisoformat(date_str[:10])).days


def window_of(seasons: str | None, month: int) -> tuple | None:
    """(name, start_month, end_month) of the season window containing
    `month` ('belg:2-5,kiremt:6-9'; cross-year 'main:10-4' supported).
    V2.5: bimodal zones report which season a reading belongs to."""
    for tok in (seasons or "").split(","):
        if ":" not in tok:
            continue
        name, ab = tok.split(":")
        a, b = map(int, ab.split("-"))
        if (a <= month <= b) if a <= b else (month >= a or month <= b):
            return name.replace("_", " "), a, b
    return None


def season_of(seasons: str | None, month: int) -> str | None:
    w = window_of(seasons, month)
    return w[0] if w else None


def in_season(seasons: str | None, month: int) -> bool:
    return season_of(seasons, month) is not None


# ---------------------------------------------------------------- factors
def enso_status(con, today: dt.date) -> dict:
    """iso3 -> (status, reason, as_of); one global phase (ONI sharpened by
    the weekly Niño 3.4), per-country exposure, IOD modifier for the
    countries whose OND rains the dipole loads. Impact window = current
    month..+3."""
    p = enso.phase_from_db(con)
    out = {}
    if not p:
        return {c: ("gray", "no ONI data — run ingest/enso.py", None)
                for c in ORDER}
    as_of = f"{p['season']} {p['year']}"
    wk = ""
    if p.get("weekly_anom") is not None:
        as_of = p["weekly_date"]
        if _age(p["weekly_date"], today) > 21:
            wk = f", weekly Niño3.4 stale ({p['weekly_date']})"
        else:
            wk = f", wkly {p['weekly_anom']:+.1f}"
    # ONI is a 3-month running mean; latest center month ~1-2 months back
    oni_last = con.execute("SELECT max(year*12+center_month) FROM enso"
                           ).fetchone()[0]
    stale = (today.year * 12 + today.month) - oni_last > 3
    # near-real-time OISST DMI: leads the light for IOD_LED countries,
    # congruence modifier for the rest of IOD_COUNTRIES
    dmi = iod.latest_dmi(con)
    dmi_val = dmi[1] if dmi and dmi[2] <= STALE["iod"] else None
    iod_amp = ""
    if dmi_val is not None:
        if dmi_val >= IOD_THRESHOLD:
            iod_amp = "elnino"
        elif dmi_val <= -IOD_THRESHOLD:
            iod_amp = "lanina"
    window = {(today.month - 1 + k) % 12 + 1 for k in range(4)}
    strong = p.get("strength") == "strong"
    for c in ORDER:
        if stale:
            out[c] = ("gray", f"ONI stale (through {p['season']} "
                              f"{p['year']})", as_of)
            continue
        if c not in EXPOSURE:
            out[c] = ("gray", "no ENSO exposure profile — coastal-"
                              "bimodal West Africa has a weak, non-"
                              "stationary ENSO link", as_of)
            continue

        # --- IOD-led OND short rains (EA minus Ethiopia) ---------------
        if c in IOD_LED and dmi_val is not None:
            near = bool(window & {10, 11, 12})
            pname = {"neutral": "ENSO neutral", "elnino": "El Niño",
                     "lanina": "La Niña"}[p["phase"]]
            lab = f"DMI {dmi_val:+.1f}, {pname} (ONI {p['anom']:+.1f}{wk})"
            congruent = iod_amp and iod_amp == p["phase"]
            if near and dmi_val >= IOD_RED:
                out[c] = ("red", f"{lab}: +IOD in flood-year territory — "
                                 "extreme OND short rains", as_of)
            elif near and dmi_val >= IOD_THRESHOLD:
                out[c] = ("red" if congruent else "yellow",
                          f"{lab}: +IOD loading the short rains"
                          + (" — El Niño reinforces" if congruent else ""),
                          as_of)
            elif near and dmi_val <= -IOD_THRESHOLD:
                out[c] = ("red" if congruent else "yellow",
                          f"{lab}: −IOD — failed short rains risk"
                          + (" — La Niña compounds" if congruent else ""),
                          as_of)
            elif near and p["phase"] != "neutral":
                side = ("enhanced short rains — beneficial unless IOD "
                        "turns positive" if p["phase"] == "elnino"
                        else "drier short rains — watch for −IOD")
                out[c] = ("yellow" if strong else "green",
                          f"{lab}: {side}", as_of)
            else:
                out[c] = ("green", f"{lab}: no dipole signal for the "
                                   "coming season", as_of)
            continue

        # --- ENSO-led (Ethiopia kiremt + Southern Africa) --------------
        if p["phase"] == "neutral":
            out[c] = ("green", f"ENSO neutral (ONI {p['anom']:+.1f}{wk})",
                      as_of)
            continue
        months, note = EXPOSURE[c][p["phase"]]
        name = "El Niño" if p["phase"] == "elnino" else "La Niña"
        tier = f"{p['tier']}/{p['strength']}" \
            if p.get("strength") != "weak" else p["tier"]
        lab = f"{name} {tier} (ONI {p['anom']:+.1f}{wk})"
        near = bool(window & months)
        amp = c in IOD_COUNTRIES and iod_amp == p["phase"]
        tail = ""
        if c in IOD_COUNTRIES and iod_amp:
            tail = (f"; +IOD {dmi_val:+.1f} amplifies" if iod_amp == "elnino"
                    else f"; −IOD {dmi_val:+.1f} compounds")
        if near and (p["tier"] == "active" or strong or amp):
            out[c] = ("red", f"{lab}: {note}{tail}", as_of)
        elif near:
            out[c] = ("yellow", f"{lab}: {note}{tail}", as_of)
        elif p["tier"] == "active" or strong:
            out[c] = ("yellow", f"{lab} — impact season later: {note}",
                      as_of)
        else:
            out[c] = ("green", f"{lab} — impact season months away", as_of)
    return out


def iod_status(con, today: dt.date) -> dict:
    """iso3 -> (status, reason, as_of): the dipole as its own light for
    the countries whose OND short rains it loads; gray elsewhere. Sign
    maps to hazard side: positive = wet East Africa (flood), negative =
    failed short rains (drought). Colors are hottest when the OND window
    is within the 3-month lookahead."""
    out = {c: ("gray", "IOD does not load this country's seasons", None)
           for c in ORDER}
    dmi = iod.latest_dmi(con)
    if not dmi:
        for c in IOD_COUNTRIES:
            out[c] = ("gray", "no DMI data — run ingest/iod.py", None)
        return out
    date, val, lag = dmi
    if lag > STALE["iod"]:
        for c in IOD_COUNTRIES:
            out[c] = ("gray", f"DMI stale (through {date})", date)
        return out
    near = bool({(today.month - 1 + k) % 12 + 1 for k in range(4)}
                & {10, 11, 12})
    side = ("wet East Africa — flood side" if val > 0
            else "dry East Africa — failed-rains side")
    if abs(val) >= IOD_RED:
        state = ("red" if near else "yellow",
                 f"DMI {val:+.2f} — flood-year-class dipole, {side}"
                 + ("" if near else " (OND window months away)"), date)
    elif abs(val) >= IOD_THRESHOLD:
        state = ("yellow", f"DMI {val:+.2f} — dipole event underway, "
                           f"{side}", date)
    else:
        state = ("green", f"DMI {val:+.2f} — no dipole signal", date)
    for c in IOD_COUNTRIES:
        out[c] = state
    return out


# V2.4 inflow-proxy thresholds (compute/hydro_signal.py). In season the
# trailing 3-month rainfall percentile leads; out of season the last
# completed wet season's % of normal carries the refill verdict.
HYDRO_TRAIL = {"red": 10, "yellow": 25}
HYDRO_REFILL = {"red": 60, "yellow": 80}


def _kariba_light(con, today: dt.date):
    """(status, reason, as_of) from the live Kariba level monitor."""
    s = kariba.latest_state(con)
    if not s:
        return ("gray", "no Kariba data — run ingest/kariba.py", None)
    if _age(s["date"], today) > STALE["kariba"]:
        return ("gray", f"Kariba data stale (through {s['date']})",
                s["date"])
    lvl, pct, rate = s["level_m"], s["pct_full"], s["drawdown_m_wk"]
    desc = f"Kariba {lvl:.2f}m" + \
        (f", {pct:.0f}% usable" if pct is not None else "") + \
        (f", drawdown {rate:.2f} m/wk" if rate is not None else "")
    if (lvl < KARIBA_RED["level_m"]
            or (pct is not None and pct < KARIBA_RED["pct_full"])
            or (rate is not None and rate >= KARIBA_RED["rate"])):
        return ("red", f"{desc} — severe-rationing boundary in play",
                s["date"])
    if (lvl < KARIBA_YEL["level_m"]
            or (pct is not None and pct < KARIBA_YEL["pct_full"])
            or (rate is not None and rate >= KARIBA_YEL["rate"])):
        return ("yellow", f"{desc} — approaching rationing thresholds",
                s["date"])
    return ("green", desc, s["date"])


def _catchment_light(r, today: dt.date):
    """(status, reason) from one live.hydro_catchment row (V2.4).
    Rainfall proxy only — says what fell on the catchment, not what the
    reservoir holds."""
    name = r["name"]
    if _age(r["as_of"], today) > STALE["wrsi"]:
        return ("gray", f"{name}: catchment rainfall stale "
                        f"(through {r['as_of']})")
    if r["in_season"]:
        tp = r["trail_pct"]
        if tp is None:
            return ("gray", f"{name}: trailing-rain percentile not yet "
                            "computable")
        lab = (f"{name}: 3-month catchment rain p{tp:.0f}"
               f" ({r['trail_mm']:.0f}mm), season {r['season_label']} "
               f"{r['season_pct_normal']:.0f}% of normal"
               if r["season_pct_normal"] is not None else
               f"{name}: 3-month catchment rain p{tp:.0f}")
        if tp <= HYDRO_TRAIL["red"]:
            return ("red", f"{lab} — inflow-side drought")
        if tp <= HYDRO_TRAIL["yellow"]:
            return ("yellow", f"{lab} — inflow watch")
        return ("green", lab)
    spn = r["season_pct_normal"]
    if spn is None:
        return ("gray", f"{name}: no completed-season total yet")
    lab = (f"{name}: last wet season ({r['season_label'].replace(' final', '')}) "
           f"{spn:.0f}% of normal — dry-season carryover")
    if spn < HYDRO_REFILL["red"]:
        return ("red", f"{lab}; refill failure")
    if spn < HYDRO_REFILL["yellow"]:
        return ("yellow", f"{lab}; below-normal refill")
    return ("green", lab)


def hydro_status(con, today: dt.date) -> dict:
    """iso3 -> (status, reason, as_of). Kariba keeps its live
    level/drawdown monitor (ZMB and ZWE); V2.4 adds a rainfall-side
    inflow proxy for every monitored catchment. A country's light is
    the worst of its catchments (plus the Kariba level where it
    applies); reasons name the tripping catchment."""
    out = {c: ("gray", "no hydropower monitor for this country", None)
           for c in ORDER}
    try:
        cat = con.execute(
            "SELECT zone_key, name, countries, as_of, in_season, trail_mm, "
            "trail_pct, season_pct_normal, season_label "
            "FROM live.hydro_catchment").fetchall()
        cols = ["zone_key", "name", "countries", "as_of", "in_season",
                "trail_mm", "trail_pct", "season_pct_normal",
                "season_label"]
        cat = [dict(zip(cols, r)) for r in cat]
    except Exception:  # table absent until compute/hydro_signal.py runs
        cat = []
    per_c: dict = {}
    for r in cat:
        st, reason = _catchment_light(r, today)
        for iso in r["countries"].split(","):
            per_c.setdefault(iso, []).append((st, reason, r["as_of"]))
    klight = _kariba_light(con, today)
    for iso in ("ZMB", "ZWE"):
        per_c.setdefault(iso, []).append(klight)
    # V3.2 reservoir levels (NASA GWM altimetry, ingest/gwm_levels.py):
    # engineering-anchored band position — red <10%, yellow <25%
    try:
        res = con.execute(
            "SELECT res_key, name, iso3, date, level_m, band_frac, "
            "trend_8wk, drawdown_m_wk, refill_date, proj_frac "
            "FROM live.reservoir_state").fetchall()
    except Exception:  # table absent until gwm_levels.py runs
        res = []
    for rk, name, iso, d, lvl, frac, trend, rate, rdate, pfrac in res:
        tr = "" if trend is None else f", {trend:+.1f}m/8wk"
        lab = f"{name} {lvl:.1f}m — {frac:.0%} of operating band{tr}"
        # V3.3 drawdown monitor (Kariba-rule mirror): does the current
        # 8-week drawdown rate reach the floor before the refill?
        dd = ("" if pfrac is None else
              f"; drawdown {rate:.2f} m/wk puts it at {pfrac:.0%} of "
              f"the band at refill onset (~{rdate})")
        if _age(d, today) > 35:        # ~3 missed 10-day passes
            light = ("gray", f"{name}: altimetry stale (through {d})", d)
        elif frac < 0.10:
            light = ("red", f"{lab} — at minimum-operating-level "
                            "territory", d)
        elif pfrac is not None and pfrac <= 0:
            light = ("red", f"{lab}{dd} — ON PACE TO HIT MINIMUM "
                            "OPERATING LEVEL BEFORE REFILL", d)
        elif frac < 0.25 or (pfrac is not None and pfrac < 0.10):
            light = ("yellow", f"{lab}{dd} — low on the band or "
                               "drawdown pace threatens the floor", d)
        else:
            light = ("green", lab + dd, d)
        per_c.setdefault(iso, []).append(light)
    # GERD has no public level feed (SCOPING-HYDRO-LEVELS): say so
    # rather than letting the rainfall proxy stand in silently
    per_c.setdefault("ETH", []).append(
        ("gray", "GERD storage: no public level feed — Blue Nile "
                 "catchment rainfall proxy only", None))
    for iso, lights in per_c.items():
        if iso not in out:
            continue
        worst = max(lights, key=lambda x: RANK.get(x[0], -1))
        others = [x for x in lights if x is not worst]
        tail = (f" (+{len(others)} other monitor(s) "
                f"{'/'.join(sorted({o[0] for o in others}))})"
                if others else "")
        out[iso] = (worst[0], worst[1] + tail, worst[2])
    return out


RANK = {"gray": -1, "green": 0, "yellow": 1, "red": 2}


def drought_zones(con, today: dt.date, sector: str = "staple"
                  ) -> pd.DataFrame:
    """Per-zone drought lights: one row per crop zone with status, the
    tripping reason, and the raw readings (in-season WRSI, SPI-3, SM).
    V2.3: sector selects staple zones (default) or export belts — the
    same water-stress thresholds, routed to different board columns."""
    have_sector = "sector" in [r[1] for r in con.execute(
        "SELECT * FROM pragma_table_info('zones')")]
    zones = pd.read_sql_query(
        "SELECT zone_key, iso3, name, seasons FROM zones"
        + (" WHERE coalesce(sector,'staple')=?" if have_sector else ""),
        con, params=(sector,) if have_sector else ())
    if not have_sector and sector == "export":
        return pd.DataFrame(columns=["zone_key", "country", "name",
                                     "status", "reason", "wrsi",
                                     "wrsi_in_season", "spi3", "sm",
                                     "season", "as_of", "_sev"])
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
        wr_win = None if wr_d is None else \
            window_of(z.seasons, dt.date.fromisoformat(wr_d).month)
        wr_season = wr_win[0] if wr_win else None
        # a reading in the first month of a window is not yet a season
        # signal — early dekads read as extremes (RWA Sep 2026)
        if wr_win and wr_d:
            rd = dt.date.fromisoformat(wr_d)
            start = dt.date(rd.year - (1 if wr_win[1] > rd.month else 0),
                            wr_win[1], 1)
            if (rd - start).days < 30:
                wr_season = None
        wr_ok = _age(wr_d, today) <= STALE["wrsi"] and wr is not None \
            and wr_season is not None
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
            "season": wr_season if wr_ok else None,
            "as_of": wr_d if wr_ok else (s3_d or sm_d or wr_d),
            # severity for the country rollup: worse status first, then
            # lower in-season WRSI, then lower SPI-3
            "_sev": (RANK[status],
                     -(wr if wr_ok and wr is not None else 999),
                     -(s3 if s3 is not None else 999)),
        })
    return pd.DataFrame(rows)


def drought_status(zr: pd.DataFrame,
                   empty_reason: str = "no crop zones ingested") -> dict:
    """iso3 -> (status, reason, as_of): worst zone in drought_zones wins."""
    out = {}
    for c in ORDER:
        zs = zr[zr.country == c]
        if zs.empty:
            out[c] = ("gray", empty_reason, None)
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
            out[c] = ("gray", "flood signals stale (rain through "
                              f"{pentad_end(latest)})", latest)
            continue
        cur = cf[cf.granule_start == latest]
        # telemetry-only basins inform the yellow tier but not the
        # calibrated regional (red) rule
        rule = cur[~cur.zone_key.isin(TELEMETRY_ONLY)]
        n1 = int((rule.tier >= 1).sum())
        n2 = int((rule.tier >= 2).sum())
        month = dt.date.fromisoformat(latest).month
        season = month in FLOOD_MONTHS[c]
        armed_fc = cur[(cur.ante_pct >= 90)
                       & cur.zone_key.isin(wet_fc)].zone_key.tolist()
        r1, r2 = PARAMS[c]["region"]
        n1_all = int((cur.tier >= 1).sum())
        if season and n1 >= r1 and n2 >= r2:
            out[c] = ("red", f"regional alert: {n2} basin(s) alerting, "
                             f"{n1} armed", latest)
        elif (season and n1_all >= 1) or armed_fc:
            why = (f"{n1_all} basin(s) at watch/alert" if season and
                   n1_all >= 1
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
    zx = drought_zones(con, today, sector="export")
    factors = {"enso": enso_status(con, today),
               "iod": iod_status(con, today),
               "drought": drought_status(zr),
               "export": drought_status(
                   zx, empty_reason="no export-crop belts for this "
                                    "country"),
               "flood": flood_status(con, today),
               "hydro": hydro_status(con, today)}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if "season" not in [r[1] for r in con.execute(
            "SELECT * FROM pragma_table_info('zone_risk', 'live')")]:
        con.execute("ALTER TABLE live.zone_risk ADD COLUMN season TEXT")
    con.executemany(
        "INSERT OR REPLACE INTO zone_risk VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r.zone_key, fac, r.country, r["name"], r.status, r.reason,
          r.wrsi, r.wrsi_in_season, r.spi3, r.sm, r.as_of, now, r.season)
         for fac, frame in (("drought", zr), ("export", zx))
         for _, r in frame.iterrows()])
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
