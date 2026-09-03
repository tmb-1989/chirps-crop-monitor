"""Flood-watch signals over East Africa basins (SCOPING-FLOODS.md F2/F4).

Countries: Kenya (pilot), Ethiopia, Tanzania, Rwanda, Uganda. Signals are
computed per basin; the regional alert, event catalog, backtest, and
flood-season month filter are per country (basins carry iso3 in
data/zones/basins.geojson).

Per basin, per pentad, from chirps3local_pentad_data(+prelim):
  pct_normal   pentad rain as % of the 1991-2020 same-pentad-of-year mean
               (display only — no longer drives tiers)
  ante_pct     antecedent wetness: trailing 18-pentad (~3mo) sum's empirical
               percentile within its own pentad-of-year climatology
  hot_pct      the pentad's own percentile within its pentad-of-year
               climatology (seasonality-proof heavy-rain test — replaces
               %-of-normal + fixed mm floors, which undersaw high-normal
               kiremt AND low-normal deyr months; Sep 2026 recalibration)
  tier         0 none / 1 watch / 2 alert, PARAMS[iso3]-driven signature:
    saturation: ante_pct >= arm AND `consec` consecutive pentads with
                hot_pct >= hot (each >= floor mm) -> watch;
                hot_pct >= 97 on top -> alert
    whiplash:   ante_pct <= 20 AND hot_pct >= 97 (>= floor mm) -> alert
  regional     PARAMS[iso3]["region"] = (basins armed, basins alerting)
               required in the same pentad, in flood-season months

Parameters are calibrated per country by compute/flood_calibrate.py
(in-sample grid search vs the event catalogs).

GEFS (CHIRPS-GEFS v3 05/10/15-day accumulations, fetched live) upgrades a
current watch to alert when the 10-day forecast >= 180% of the same-window
climatology; stored in flood_gefs.

Backtest: evaluates historical alert episodes against the embedded
per-country event catalogs (1999-2026, EM-DAT/FloodList/ReliefWeb-informed
month windows). Run: python compute/flood_signals.py [--backtest-only]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
import db  # noqa: E402

CLIM_START, CLIM_END = 1991, 2020
ANTE_WINDOW = 18  # pentads (~3 months)

# Per-country signature/rule parameters from compute/flood_calibrate.py
# (final, 3 Sep 2026, all 32 basins backfilled; basin-subset search
# included — see TELEMETRY_ONLY).
# arm: antecedent percentile to arm; hot: heavy-pentad percentile;
# consec: consecutive hot pentads for a watch; floor: absolute mm floor;
# region: (min armed, min alerting) basins for a country regional alert.
# Calibration (episodes 1999-2026): precision/recall/major-recall
#   KEN 88/73/100  ETH 56/64/67  TZA 40/88/100  RWA 60/50/67  UGA 30/43/75
PARAMS = {
    "KEN": dict(arm=80, hot=90, consec=2, floor=25, region=(2, 1)),
    "ETH": dict(arm=80, hot=95, consec=1, floor=25, region=(2, 1)),
    "TZA": dict(arm=90, hot=90, consec=1, floor=5, region=(2, 1)),
    "RWA": dict(arm=90, hot=90, consec=2, floor=5, region=(2, 1)),
    "UGA": dict(arm=90, hot=95, consec=1, floor=5, region=(2, 1)),
}
ALERT_HOT = 97  # alert tier: hot pentad also above this percentile

# Telemetry-only basins: computed, stored and displayed (incl. downstream
# labels) but EXCLUDED from the regional alert — the basin-subset
# calibration showed each of these degrades its country's precision
# without adding recall (arid/flash catchments with noisy percentile
# stats). Gode (ETH) and Lindi (TZA) earned alert-rule seats; these did
# not.
TELEMETRY_ONLY = {"bas_diredawa", "bas_genale", "bas_omo_low",
                  "bas_wami", "bas_rusizi", "bas_semliki"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS flood_state (
    zone_key      TEXT NOT NULL,
    granule_start TEXT NOT NULL,
    rain_mm       REAL,
    pct_normal    REAL,
    ante_pct      REAL,
    tier          INTEGER,
    signature     TEXT,
    hot_pct       REAL,
    PRIMARY KEY (zone_key, granule_start)
);
CREATE TABLE IF NOT EXISTS live.flood_gefs (
    zone_key   TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    horizon    TEXT NOT NULL,
    fcst_mm    REAL,
    pct_clim   REAL,
    PRIMARY KEY (zone_key, issue_date, horizon)
);
"""

# Major flood events per country, calibration catalogs (approximate month
# windows). Sources: EM-DAT, FloodList, ReliefWeb archives. "major" =
# national-scale disaster (deaths >~20 or mass displacement). These are
# calibration windows, not precise event dates.
EVENTS = {
    "KEN": [
        ("2001-11-01", "2001-12-15", "moderate"),
        ("2002-04-15", "2002-05-31", "moderate"),
        ("2006-10-15", "2006-12-15", "major"),
        ("2007-09-01", "2007-10-31", "moderate"),   # Nzoia/Budalangi
        ("2008-10-15", "2008-11-30", "moderate"),
        ("2010-03-01", "2010-05-15", "moderate"),
        ("2012-04-01", "2012-05-31", "major"),
        ("2013-03-15", "2013-05-15", "major"),
        ("2015-10-15", "2015-12-31", "major"),
        ("2018-03-01", "2018-05-31", "major"),
        ("2019-10-01", "2019-12-31", "major"),
        ("2020-03-15", "2020-05-31", "major"),
        ("2023-10-15", "2023-12-15", "major"),
        ("2024-03-15", "2024-05-15", "major"),
        ("2026-03-05", "2026-05-31", "major"),
    ],
    "ETH": [
        ("2003-08-01", "2003-09-15", "moderate"),
        ("2005-04-15", "2005-05-31", "moderate"),   # Somali region spring
        ("2006-08-01", "2006-09-15", "major"),      # Dire Dawa / Omo
        ("2007-08-01", "2007-09-30", "moderate"),
        ("2010-07-15", "2010-09-15", "moderate"),
        ("2016-04-01", "2016-05-31", "moderate"),   # post-El Niño whiplash
        ("2018-04-01", "2018-05-31", "major"),      # Somali region gu
        ("2019-10-01", "2019-11-30", "major"),
        ("2020-07-01", "2020-09-30", "major"),      # Awash/Afar kiremt
        ("2023-10-15", "2023-12-15", "major"),      # deyr after drought
        ("2024-04-15", "2024-05-31", "major"),
    ],
    "TZA": [
        ("2009-12-20", "2010-01-31", "moderate"),   # Kilosa/Morogoro
        ("2011-12-15", "2011-12-31", "major"),      # Dar es Salaam
        ("2014-04-01", "2014-05-15", "moderate"),
        ("2018-04-01", "2018-04-30", "moderate"),   # Dar
        ("2019-10-15", "2020-01-31", "major"),      # OND + Lindi/Mtwara
        ("2020-04-01", "2020-05-15", "moderate"),
        ("2023-11-01", "2023-12-15", "major"),      # Hanang/Manyara
        ("2024-04-01", "2024-05-15", "major"),      # El Niño MAM
    ],
    "RWA": [
        ("2016-05-01", "2016-05-31", "moderate"),   # Gakenke
        ("2018-04-01", "2018-05-31", "major"),
        ("2019-12-01", "2019-12-31", "moderate"),   # Kigali
        ("2020-04-01", "2020-05-31", "major"),
        ("2023-05-01", "2023-05-15", "major"),      # NW landslides/floods
        ("2024-05-01", "2024-05-31", "moderate"),
    ],
    "UGA": [
        ("2007-08-15", "2007-10-15", "major"),      # Teso
        ("2010-03-01", "2010-03-31", "major"),      # Bududa
        ("2013-05-01", "2013-05-31", "moderate"),   # Kasese
        ("2018-10-01", "2018-10-31", "moderate"),   # Bududa
        ("2019-10-15", "2019-12-31", "major"),
        ("2020-05-01", "2020-05-31", "major"),      # Kasese/lake levels
        ("2022-07-15", "2022-08-31", "moderate"),   # Mbale
    ],
}

# flood-season month filter for the regional alert, per country
FLOOD_MONTHS = {
    "KEN": {2, 3, 4, 5, 10, 11, 12},        # MAM long + OND short rains
    "ETH": {4, 5, 6, 7, 8, 9, 10, 11},      # belg + kiremt + deyr spillover
    "TZA": {1, 2, 3, 4, 5, 11, 12},         # Nov-May (uni/bimodal mix)
    "RWA": {3, 4, 5, 9, 10, 11, 12},        # two rainy seasons
    "UGA": {3, 4, 5, 7, 8, 9, 10, 11, 12},  # bimodal + Elgon/Rwenzori JJA
}

COUNTRY = {"KEN": "Kenya", "ETH": "Ethiopia", "TZA": "Tanzania",
           "RWA": "Rwanda", "UGA": "Uganda"}

BASINS_GJ = pathlib.Path(__file__).resolve().parent.parent / \
    "data" / "zones" / "basins.geojson"


def basin_countries() -> dict:
    """zone_key -> iso3 from basins.geojson."""
    import json
    gj = json.loads(BASINS_GJ.read_text())
    return {f["properties"]["zone_key"]: f["properties"].get("iso3", "KEN")
            for f in gj["features"]}


def pentad_of_year(d: dt.date) -> int:
    return (d.month - 1) * 6 + min((d.day - 1) // 5 + 1, 6)


def load_basin(con, zk: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT granule_start, value, dataset FROM observations WHERE "
        "zone_key=? AND dataset IN ('chirps3local_pentad_data',"
        "'chirps3local-prelim_pentad_data')", con, params=(zk,))
    df["src"] = np.where(df.dataset.str.contains("prelim"), "prelim", "final")
    df = (df.sort_values(["granule_start", "src"])
          .drop_duplicates("granule_start", keep="first"))
    df["date"] = pd.to_datetime(df.granule_start)
    return df.set_index("date").sort_index()


def compute_basin(con, zk: str, params: dict) -> pd.DataFrame:
    df = load_basin(con, zk)
    poy = pd.Series([pentad_of_year(d.date()) for d in df.index],
                    index=df.index)
    clim_years = (df.index.year >= CLIM_START) & (df.index.year <= CLIM_END)
    clim_mean = df.value[clim_years].groupby(poy[clim_years]).mean()
    pct = 100 * df.value / poy.map(clim_mean)

    ante = df.value.rolling(ANTE_WINDOW, min_periods=ANTE_WINDOW).sum()
    ante_pct = pd.Series(np.nan, index=df.index)
    hot_pct = pd.Series(np.nan, index=df.index)
    for k in range(1, 73):
        sel = poy == k
        ref = ante[sel & clim_years].dropna()
        if len(ref) >= 15:
            ante_pct[sel] = ante[sel].map(
                lambda v: float((ref <= v).mean() * 100)
                if pd.notna(v) else np.nan)
        ref_v = df.value[sel & clim_years].dropna()
        if len(ref_v) >= 15:
            hot_pct[sel] = df.value[sel].map(
                lambda v: float((ref_v <= v).mean() * 100)
                if pd.notna(v) else np.nan)

    hot = (hot_pct >= params["hot"]) & (df.value >= params["floor"])
    run = hot
    for i in range(1, params["consec"]):
        run = run & hot.shift(i, fill_value=False)
    sat_watch = (ante_pct >= params["arm"]) & run
    sat_alert = sat_watch & (hot_pct >= ALERT_HOT)
    whiplash = (ante_pct <= 20) & (hot_pct >= ALERT_HOT) & \
        (df.value >= params["floor"])

    tier = pd.Series(0, index=df.index)
    sig = pd.Series(None, index=df.index, dtype=object)
    tier[sat_watch] = 1; sig[sat_watch] = "saturation"
    tier[sat_alert] = 2; sig[sat_alert] = "saturation"
    tier[whiplash] = 2; sig[whiplash] = "whiplash"

    out = pd.DataFrame({
        "zone_key": zk, "granule_start": [d.date().isoformat() for d in df.index],
        "rain_mm": df.value.values, "pct_normal": pct.values,
        "ante_pct": ante_pct.values, "tier": tier.values,
        "signature": sig.values, "hot_pct": hot_pct.values})
    con.executemany(
        "INSERT OR REPLACE INTO flood_state VALUES (?,?,?,?,?,?,?,?)",
        [tuple(None if (isinstance(x, float) and np.isnan(x)) else x
               for x in row)
         for row in out.itertuples(index=False)])
    con.commit()
    return out


def fetch_gefs(con, basins: list) -> None:
    """Zonal means of the latest CHIRPS-GEFS accumulations per basin."""
    import re
    import requests
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "ingest"))
    import chirps_raster as cr
    import flood_raster as fr

    z = np.load(fr.MASKS_NPZ)
    masks = {k: z[k] for k in z.files}
    bounds = (-20.0, -40.000001192092896, 55.00000111758709, 40.0)  # Africa grid
    year = dt.date.today().year
    for horizon in ("05_day", "10_day", "15_day"):
        base = (f"https://data.chc.ucsb.edu/products/CHIRPS-GEFS/v3/"
                f"{horizon}/global/data/{year}")
        r = requests.get(base + "/", headers=cr.UA, timeout=60)
        names = sorted(set(re.findall(r'href="(c3g_[\d.]+\.tif)"', r.text)))
        if not names:
            continue
        latest = names[-1]
        issue = latest.replace("c3g_", "").replace(".tif", "").replace(".", "-")
        blob = cr.fetch(f"{base}/{latest}")
        means = cr.zonal_means(blob, masks, window_bounds=bounds)
        # % of climatology for the covered window: approximate with the
        # pentad climatology of the pentads starting at the issue date
        n_pent = {"05_day": 1, "10_day": 2, "15_day": 3}[horizon]
        for zk, v in means.items():
            if v is None:
                continue
            clim = con.execute(
                "SELECT avg(value) FROM observations WHERE zone_key=? AND "
                "dataset='chirps3local_pentad_data' AND "
                "CAST(strftime('%Y', granule_start) AS INT) BETWEEN ? AND ? "
                "AND CAST(strftime('%j', granule_start) AS INT) BETWEEN "
                "CAST(strftime('%j', ?) AS INT) - 2 AND "
                "CAST(strftime('%j', ?) AS INT) + 3 + (? - 1) * 5",
                (zk, CLIM_START, CLIM_END, issue, issue, n_pent)).fetchone()
            pct = 100 * v / (clim[0] * n_pent) if clim[0] else None
            con.execute("INSERT OR REPLACE INTO flood_gefs VALUES (?,?,?,?,?)",
                        (zk, issue, horizon, float(v),
                         None if pct is None else float(pct)))
    con.commit()


NTFY_TOPIC_FILE = pathlib.Path(__file__).resolve().parent.parent / \
    "data" / "ntfy_topic.txt"


def _notify(message: str) -> None:
    """Push via ntfy.sh if data/ntfy_topic.txt exists (opt-in; the file is
    gitignored — one private topic string, no account or credentials)."""
    if not NTFY_TOPIC_FILE.exists():
        return
    topic = NTFY_TOPIC_FILE.read_text().strip()
    if not topic:
        return
    try:
        import requests
        requests.post(f"https://ntfy.sh/{topic}", data=message.encode(),
                      headers={"Title": "CHIRPS flood watch",
                               "Priority": "high", "Tags": "ocean"},
                      timeout=30)
        print(f"push notification sent to ntfy topic '{topic[:8]}…'")
    except Exception as e:  # noqa: BLE001 - alerting must never kill the run
        print(f"ntfy push failed (non-fatal): {e}", file=sys.stderr)


def regional_series(states: pd.DataFrame, iso3: str) -> pd.Series:
    """Regional-alert pentads for one country's basins: PARAMS[iso3]
    ["region"] = (r1 armed, r2 alerting) in the same pentad, inside that
    country's flood-season months. Calibration record in PARAMS."""
    r1, r2 = PARAMS[iso3]["region"]
    states = states[~states.zone_key.isin(TELEMETRY_ONLY)]
    g = states.groupby("granule_start").agg(
        n1=("tier", lambda t: (t >= 1).sum()),
        n2=("tier", lambda t: (t >= 2).sum()))
    idx = pd.to_datetime(g.index)
    mask = (g.n1.values >= r1) & (g.n2.values >= r2) & \
        pd.Index(idx.month).isin(list(FLOOD_MONTHS[iso3]))
    return pd.Series(idx[mask])


def backtest(states: pd.DataFrame, iso3: str) -> None:
    """Alert episodes for one country vs its event catalog."""
    # regional-alert pentads, collapsed to episodes (gap > 30 days)
    reg = regional_series(states, iso3)
    reg = reg[reg >= pd.Timestamp("1999-01-01")]
    episodes = []
    for d in reg:
        if episodes and (d - episodes[-1][1]).days <= 30:
            episodes[-1][1] = d
        else:
            episodes.append([d, d])
    ev = [(pd.Timestamp(a), pd.Timestamp(b), sev)
          for a, b, sev in EVENTS.get(iso3, [])]
    hits, falses = [], []
    for e0, e1 in episodes:
        ok = any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                 for a, b, _ in ev)
        (hits if ok else falses).append((e0.date(), e1.date()))
    caught = [f"{a.date()}" for a, b, sev in ev
              if any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                     for e0, e1 in episodes)]
    print(f"\n=== {COUNTRY[iso3]} backtest 1999-2026: "
          f"{len(episodes)} regional-alert episodes")
    print(f"hits: {len(hits)}  false alarms: {len(falses)}  "
          f"precision {100*len(hits)/max(len(episodes),1):.0f}%")
    print(f"events caught: {len(caught)}/{len(ev)} "
          f"({', '.join(caught)})")
    print("false alarms:", ", ".join(f"{a}" for a, b in falses) or "none")
    missed = [str(a.date()) for a, b, sev in ev if str(a.date())[:7] not in
              {c[:7] for c in caught}]
    print("events missed:", ", ".join(missed) or "none")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest-only", action="store_true")
    ap.add_argument("--no-gefs", action="store_true")
    args = ap.parse_args()
    con = db.connect()
    con.executescript(SCHEMA)
    if "hot_pct" not in [r[1] for r in con.execute(
            "SELECT * FROM pragma_table_info('flood_state')")]:
        con.execute("ALTER TABLE flood_state ADD COLUMN hot_pct REAL")
    countries = basin_countries()
    basins = [r[0] for r in con.execute(
        "SELECT DISTINCT zone_key FROM observations WHERE "
        "dataset='chirps3local_pentad_data' AND zone_key LIKE 'bas_%'")]
    frames = []
    for zk in sorted(basins):
        out = compute_basin(con, zk, PARAMS[countries.get(zk, "KEN")])
        cur = out.iloc[-1]
        ante = None if pd.isna(cur.ante_pct) else round(cur.ante_pct)
        print(f"{countries.get(zk, '???')} {zk}: {len(out)} pentads; "
              f"latest {cur.granule_start} tier={cur.tier} ante={ante}")
        frames.append(out)
    if not args.backtest_only and not args.no_gefs:
        try:
            fetch_gefs(con, basins)
            print("GEFS forecasts ingested")
        except Exception as e:  # noqa: BLE001
            print(f"GEFS fetch failed (non-fatal): {e}", file=sys.stderr)
    allf = pd.concat(frames)
    allf["iso3"] = allf.zone_key.map(countries)
    latest = allf.granule_start.max()

    # persist state transitions for the cron log / downstream consumers.
    # Migrate the pre-extension Kenya-only table (PK granule_start, no
    # country column) to a per-country PK.
    cols = [r[1] for r in con.execute(
        "SELECT * FROM pragma_table_info('flood_alerts', 'live')")]
    if cols and "country" not in cols:
        con.executescript(
            "ALTER TABLE live.flood_alerts RENAME TO flood_alerts_old;"
            "CREATE TABLE live.flood_alerts (country TEXT NOT NULL, "
            "granule_start TEXT NOT NULL, state TEXT, detail TEXT, "
            "recorded_at TEXT, PRIMARY KEY (country, granule_start));"
            "INSERT INTO live.flood_alerts SELECT 'KEN', granule_start, "
            "state, detail, recorded_at FROM live.flood_alerts_old;"
            "DROP TABLE live.flood_alerts_old;")
    else:
        con.execute("CREATE TABLE IF NOT EXISTS live.flood_alerts ("
                    "country TEXT NOT NULL, granule_start TEXT NOT NULL, "
                    "state TEXT, detail TEXT, recorded_at TEXT, "
                    "PRIMARY KEY (country, granule_start))")

    summary = []
    for iso3 in sorted(set(allf.iso3.dropna())):
        cf = allf[allf.iso3 == iso3]
        reg = regional_series(cf, iso3)
        clatest = cf.granule_start.max()
        live = not reg.empty and \
            (pd.Timestamp(clatest) - reg.iloc[-1]).days <= 10
        summary.append(f"{iso3}:{'ACTIVE' if live else 'clear'}")
        print(f"\n{COUNTRY[iso3]} REGIONAL ALERT state as of {clatest}: "
              f"{'ACTIVE since ' + str(reg.iloc[-1].date()) if live else 'clear'}")
        if live:
            cur = con.execute(
                "INSERT OR IGNORE INTO flood_alerts VALUES (?,?,?,?,?)",
                (iso3, clatest, "REGIONAL_ALERT",
                 f"active since {reg.iloc[-1].date()}",
                 dt.datetime.now(dt.timezone.utc).isoformat(
                     timespec="seconds")))
            con.commit()
            print(f"!! {COUNTRY[iso3]} REGIONAL FLOOD ALERT recorded — "
                  "check the dashboard")
            if cur.rowcount > 0:  # newly recorded -> push, don't repeat
                _notify(f"REGIONAL FLOOD ALERT ({COUNTRY[iso3]} basins): "
                        f"active since {reg.iloc[-1].date()}, latest pentad "
                        f"{clatest}. See the flood-watch dashboard.")
    # heartbeat so 'no alert' is distinguishable from 'not running'
    con.execute("CREATE TABLE IF NOT EXISTS live.flood_runs (id INTEGER "
                "PRIMARY KEY CHECK (id=1), last_run TEXT, latest_pentad "
                "TEXT, regional TEXT)")
    con.execute("INSERT OR REPLACE INTO flood_runs VALUES (1,?,?,?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 latest, " ".join(summary)))
    con.commit()
    for iso3 in sorted(set(allf.iso3.dropna())):
        backtest(allf[allf.iso3 == iso3], iso3)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
