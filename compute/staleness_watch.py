"""Feed staleness watchdog.

The dashboard already turns cells gray when a feed goes stale, but
nothing escalated — the Sep 2026 ZRA format drift left Kariba silently
gray for 17 days. This step runs at the end of the daily update and
emails (via the flood-alert Migadu sender) once when a feed crosses
its staleness threshold, and once when it recovers. State in
live.feed_watch keeps it one email per episode, not one per day.

Thresholds mirror the dashboard's gray logic (compute/country_risk.py
STALE) with feed-level granularity.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "ingest"))
import db  # noqa: E402
from flood_signals import _email, _notify  # noqa: E402

# (feed name, latest-date query, threshold days)
FEEDS = [
    ("EWX CHIRPS pentads", "SELECT max(granule_start) FROM observations "
     "WHERE dataset='chirps_global_pentad_data'", 15),
    ("EWX LWRSI dekads", "SELECT max(granule_start) FROM observations "
     "WHERE dataset='lwrsi_africa_dekad_data'", 45),
    ("FLDAS soil moisture", "SELECT max(granule_start) FROM observations "
     "WHERE dataset='soilmoisture-0-100cm_global_month_data'", 90),
    ("Local CHIRPS pentads (flood)",
     "SELECT max(granule_start) FROM flood_state", 20),
    ("CHIRPS-GEFS forecast",
     "SELECT max(issue_date) FROM flood_gefs", 7),
    ("ENSO weekly Nino3.4", "SELECT max(date) FROM enso_weekly", 21),
    ("IOD DMI (OISST)", "SELECT max(date) FROM iod_dmi_oisst", 90),
    ("Kariba levels (ZRA)", "SELECT max(date) FROM kariba_level "
     "WHERE vintage='current'", 14),
    # discharge went stale independently of levels (Sep 2026 layout
    # drift filled dates with null values) — watch the value, not the row
    ("Kariba turbine discharge (ZRA)", "SELECT max(date) FROM "
     "kariba_reservoir WHERE turbine_discharge_m3s IS NOT NULL", 14),
    ("WFP staple prices", "SELECT max(month) FROM staple_prices", 75),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.feed_watch (
    feed        TEXT PRIMARY KEY,
    last_date   TEXT,
    stale_since TEXT,             -- set while a stale episode is open
    notified_at TEXT
);
"""


def main() -> int:
    today = dt.date.today()
    con = db.connect()
    con.executescript(SCHEMA)
    went_stale, recovered = [], []
    for feed, q, thr in FEEDS:
        try:
            latest = con.execute(q).fetchone()[0]
        except Exception as e:  # noqa: BLE001 — a missing table is stale
            latest = None
            print(f"[{feed}] query failed: {e}", file=sys.stderr)
        age = None if latest is None else \
            (today - dt.date.fromisoformat(latest[:10])).days
        stale = age is None or age > thr
        prev = con.execute("SELECT stale_since FROM feed_watch WHERE "
                           "feed=?", (feed,)).fetchone()
        open_episode = prev is not None and prev[0] is not None
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if stale and not open_episode:
            went_stale.append(f"{feed}: latest {latest or 'none'} "
                              f"({'no data' if age is None else f'{age}d'} "
                              f"vs {thr}d threshold)")
            con.execute("INSERT OR REPLACE INTO feed_watch VALUES "
                        "(?,?,?,?)", (feed, latest, today.isoformat(), now))
        elif not stale and open_episode:
            recovered.append(f"{feed}: current through {latest} "
                             f"(was stale since {prev[0]})")
            con.execute("INSERT OR REPLACE INTO feed_watch VALUES "
                        "(?,?,NULL,?)", (feed, latest, now))
        else:
            con.execute("INSERT OR REPLACE INTO feed_watch VALUES "
                        "(?,?,?,?)",
                        (feed, latest,
                         prev[0] if open_episode else None,
                         now))
        state = "STALE" if stale else "ok"
        print(f"[{feed}] {state}: latest {latest}, "
              f"age {age}d, threshold {thr}d")
    con.commit()
    con.close()
    if went_stale or recovered:
        lines = []
        if went_stale:
            lines.append("Feeds newly STALE:\n  " + "\n  ".join(went_stale))
        if recovered:
            lines.append("Feeds recovered:\n  " + "\n  ".join(recovered))
        body = ("\n\n".join(lines)
                + "\n\nA stale feed means its dashboard cells are gray "
                "and its scraper/source needs a look. One email per "
                "episode; recovery is confirmed when it arrives.")
        subj = "CHIRPS monitor — feed staleness: " + ", ".join(
            ([f"{len(went_stale)} stale"] if went_stale else [])
            + ([f"{len(recovered)} recovered"] if recovered else []))
        _email(subj, body)
        _notify(subj)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
