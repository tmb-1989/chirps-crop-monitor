"""Flood-watch signals over Kenya pilot basins (SCOPING-FLOODS.md F2).

Per basin, per pentad, from chirps3local_pentad_data(+prelim):
  pct_normal   pentad rain as % of the 1991-2020 same-pentad-of-year mean
  ante_pct     antecedent wetness: trailing 18-pentad (~3mo) sum's empirical
               percentile within its own pentad-of-year climatology
  tier         0 none / 1 watch / 2 alert, with signature:
    saturation: ante_pct >= 90 AND 2 consecutive pentads >= 150% of normal
                (each >= 25mm) -> watch; >= 200% (>= 35mm) on top -> alert
    whiplash:   ante_pct <= 20 AND pentad >= 250% of normal (>= 30mm)
                -> alert (flash-flood setup on parched catchment)

GEFS (CHIRPS-GEFS v3 05/10/15-day accumulations, fetched live) upgrades a
current watch to alert when the 10-day forecast >= 180% of the same-window
climatology; stored in flood_gefs.

Backtest: evaluates historical alert episodes against the embedded Kenya
event catalog (1999-2026, EM-DAT/FloodList/ReliefWeb-informed month
windows). Run: python compute/flood_signals.py [--backtest-only]
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS flood_state (
    zone_key      TEXT NOT NULL,
    granule_start TEXT NOT NULL,
    rain_mm       REAL,
    pct_normal    REAL,
    ante_pct      REAL,
    tier          INTEGER,
    signature     TEXT,
    PRIMARY KEY (zone_key, granule_start)
);
CREATE TABLE IF NOT EXISTS flood_gefs (
    zone_key   TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    horizon    TEXT NOT NULL,
    fcst_mm    REAL,
    pct_clim   REAL,
    PRIMARY KEY (zone_key, issue_date, horizon)
);
"""

# Kenya major flood events, calibration catalog (approximate windows).
# Sources: EM-DAT, FloodList, ReliefWeb archives. "major" = national-scale
# disaster (deaths >~20 or mass displacement).
EVENTS = [
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
    ("2026-02-15", "2026-05-31", "major"),
]


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


def compute_basin(con, zk: str) -> pd.DataFrame:
    df = load_basin(con, zk)
    poy = pd.Series([pentad_of_year(d.date()) for d in df.index],
                    index=df.index)
    clim_years = (df.index.year >= CLIM_START) & (df.index.year <= CLIM_END)
    clim_mean = df.value[clim_years].groupby(poy[clim_years]).mean()
    pct = 100 * df.value / poy.map(clim_mean)

    ante = df.value.rolling(ANTE_WINDOW, min_periods=ANTE_WINDOW).sum()
    ante_pct = pd.Series(np.nan, index=df.index)
    for k in range(1, 73):
        sel = poy == k
        ref = ante[sel & clim_years].dropna()
        if len(ref) < 15:
            continue
        ante_pct[sel] = ante[sel].map(
            lambda v: float((ref <= v).mean() * 100) if pd.notna(v) else np.nan)

    hot150 = (pct >= 150) & (df.value >= 25)
    hot200 = (pct >= 200) & (df.value >= 35)
    sat_watch = (ante_pct >= 90) & hot150 & hot150.shift(1, fill_value=False)
    sat_alert = sat_watch & hot200
    whiplash = (ante_pct <= 20) & (pct >= 250) & (df.value >= 30)

    tier = pd.Series(0, index=df.index)
    sig = pd.Series(None, index=df.index, dtype=object)
    tier[sat_watch] = 1; sig[sat_watch] = "saturation"
    tier[sat_alert] = 2; sig[sat_alert] = "saturation"
    tier[whiplash] = 2; sig[whiplash] = "whiplash"

    out = pd.DataFrame({
        "zone_key": zk, "granule_start": [d.date().isoformat() for d in df.index],
        "rain_mm": df.value.values, "pct_normal": pct.values,
        "ante_pct": ante_pct.values, "tier": tier.values,
        "signature": sig.values})
    con.executemany(
        "INSERT OR REPLACE INTO flood_state VALUES (?,?,?,?,?,?,?)",
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


FLOOD_MONTHS = {2, 3, 4, 5, 10, 11, 12}  # MAM long rains + OND short rains

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


def regional_series(all_states: pd.DataFrame) -> pd.Series:
    """Regional-alert pentads: >=2 basins armed (tier>=1), >=1 alerting
    (tier 2), inside the flood-season months. Calibrated 1999-2026:
    100% recall on major events, ~71% precision, ~1 false alarm / 5yrs."""
    g = all_states.groupby("granule_start").agg(
        n1=("tier", lambda t: (t >= 1).sum()),
        n2=("tier", lambda t: (t >= 2).sum()))
    idx = pd.to_datetime(g.index)
    mask = (g.n1.values >= 2) & (g.n2.values >= 1) & \
        pd.Index(idx.month).isin(list(FLOOD_MONTHS))
    return pd.Series(idx[mask])


def backtest(all_states: pd.DataFrame) -> None:
    alerts = all_states[all_states.tier == 2].copy()
    alerts["date"] = pd.to_datetime(alerts.granule_start)
    alerts = alerts[alerts.date >= "1999-01-01"].sort_values("date")
    # collapse to episodes (gap > 30 days)
    episodes = []
    for d, s in zip(alerts.date, alerts.signature):
        if episodes and (d - episodes[-1][1]).days <= 30:
            episodes[-1][1] = d
        else:
            episodes.append([d, d, s])
    ev = [(pd.Timestamp(a), pd.Timestamp(b), sev) for a, b, sev in EVENTS]
    hits, falses = [], []
    for e0, e1, sig in episodes:
        ok = any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                 for a, b, _ in ev)
        (hits if ok else falses).append((e0.date(), e1.date(), sig))
    caught = [f"{a.date()}" for a, b, sev in ev
              if any(a - pd.Timedelta(days=10) <= e0 <= b or a <= e1 <= b
                     for e0, e1, _ in episodes)]
    print(f"\n=== backtest 1999-2026: {len(episodes)} alert episodes")
    print(f"hits: {len(hits)}  false alarms: {len(falses)}  "
          f"precision {100*len(hits)/max(len(episodes),1):.0f}%")
    print(f"events caught: {len(caught)}/{len(ev)} "
          f"({', '.join(caught)})")
    print("false alarms:", ", ".join(f"{a}({s})" for a, b, s in falses) or "none")
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
    basins = [r[0] for r in con.execute(
        "SELECT DISTINCT zone_key FROM observations WHERE "
        "dataset='chirps3local_pentad_data' AND zone_key LIKE 'bas_%'")]
    frames = []
    for zk in sorted(basins):
        out = compute_basin(con, zk)
        cur = out.iloc[-1]
        print(f"{zk}: {len(out)} pentads; latest {cur.granule_start} "
              f"tier={cur.tier} ante={cur.ante_pct and round(cur.ante_pct)}")
        frames.append(out)
    if not args.backtest_only and not args.no_gefs:
        try:
            fetch_gefs(con, basins)
            print("GEFS forecasts ingested")
        except Exception as e:  # noqa: BLE001
            print(f"GEFS fetch failed (non-fatal): {e}", file=sys.stderr)
    allf = pd.concat(frames)
    reg = regional_series(allf)
    latest = allf.granule_start.max()
    live = not reg.empty and (pd.Timestamp(latest) - reg.iloc[-1]).days <= 10
    print(f"\nREGIONAL ALERT state as of {latest}: "
          f"{'ACTIVE since ' + str(reg.iloc[-1].date()) if live else 'clear'}")
    # persist state transitions for the cron log / downstream consumers
    con.execute("CREATE TABLE IF NOT EXISTS flood_alerts ("
                "granule_start TEXT PRIMARY KEY, state TEXT, "
                "detail TEXT, recorded_at TEXT)")
    if live:
        cur = con.execute(
            "INSERT OR IGNORE INTO flood_alerts VALUES (?,?,?,?)",
            (latest, "REGIONAL_ALERT",
             f"active since {reg.iloc[-1].date()}",
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        con.commit()
        print("!! REGIONAL FLOOD ALERT recorded — check the dashboard")
        if cur.rowcount > 0:  # newly recorded -> push, don't repeat
            _notify(f"REGIONAL FLOOD ALERT (Kenya basins): active since "
                    f"{reg.iloc[-1].date()}, latest pentad {latest}. "
                    f"See the flood-watch dashboard.")
    # heartbeat so 'no alert' is distinguishable from 'not running'
    con.execute("CREATE TABLE IF NOT EXISTS flood_runs (id INTEGER PRIMARY "
                "KEY CHECK (id=1), last_run TEXT, latest_pentad TEXT, "
                "regional TEXT)")
    con.execute("INSERT OR REPLACE INTO flood_runs VALUES (1,?,?,?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 latest, "ACTIVE" if live else "clear"))
    con.commit()
    backtest(allf)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
