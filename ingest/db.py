"""SQLite storage for zone metadata and observation time series."""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "monitor.sqlite"
# Fast-moving small tables live in a separate file so the daily cron can
# commit it without committing the ~54MB history DB (see ingest/split_db.py).
LIVE_PATH = DB_PATH.parent / "live.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
    zone_key   TEXT PRIMARY KEY,
    iso3       TEXT NOT NULL,
    name       TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    crop       TEXT,
    adm0       TEXT,
    adm1       TEXT,
    seasons    TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    zone_key      TEXT NOT NULL,
    dataset       TEXT NOT NULL,
    granule_start TEXT NOT NULL,
    granule_end   TEXT NOT NULL,
    value         REAL NOT NULL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (zone_key, dataset, granule_start)
);
CREATE TABLE IF NOT EXISTS dataset_meta (
    dataset     TEXT PRIMARY KEY,
    granule_end TEXT,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    zone_key      TEXT NOT NULL,
    dataset       TEXT NOT NULL,
    granule_start TEXT NOT NULL,
    old_value     REAL NOT NULL,
    new_value     REAL NOT NULL,
    revised_at    TEXT NOT NULL
);
-- DAHITI satellite-altimetry water levels (SCOPING-DRC-HYDRO.md D1).
CREATE TABLE IF NOT EXISTS hydro_levels (
    station_key TEXT NOT NULL,
    date        TEXT NOT NULL,
    level_m     REAL NOT NULL,
    error_m     REAL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (station_key, date)
);
-- Kariba (ZRA) level + turbine discharge, seeded from the elnino
-- dashboard's reconstructed 2017- series then continued by the live ZRA
-- scraper (SCOPING-MOZ-HYDRO.md M1). Live rows win on conflict with seed
-- rows for the same date (see upsert_kariba_flows).
CREATE TABLE IF NOT EXISTS kariba_flows (
    date        TEXT NOT NULL PRIMARY KEY,
    level_m     REAL,
    turbine_m3s REAL,
    source      TEXT NOT NULL,   -- 'seed' | 'zra_live'
    fetched_at  TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 60s busy timeout: long backfills and ad-hoc tools (or a .backup for
    # the data-update commit) share this file; the default 5s killed an
    # hours-long backfill mid-run when a backup held the lock.
    con = sqlite3.connect(DB_PATH, timeout=60)
    # attach the live DB: unqualified names resolve into it for tables
    # that exist only there, so readers don't need qualifying
    con.execute("ATTACH DATABASE ? AS live", (str(LIVE_PATH),))
    con.executescript(SCHEMA)
    return con


def upsert_zone(con, zone_key, iso3, name, lat, lon, info, seasons):
    # a failed WFS lookup (info=None) must not wipe a known crop/admin
    # label — INSERT OR REPLACE did, leaving rwa_northern unlabelled
    con.execute(
        "INSERT INTO zones (zone_key, iso3, name, lat, lon, "
        "crop, adm0, adm1, seasons) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(zone_key) DO UPDATE SET iso3=excluded.iso3, "
        "name=excluded.name, lat=excluded.lat, lon=excluded.lon, "
        "crop=COALESCE(excluded.crop, zones.crop), "
        "adm0=COALESCE(excluded.adm0, zones.adm0), "
        "adm1=COALESCE(excluded.adm1, zones.adm1), "
        "seasons=excluded.seasons",
        (zone_key, iso3, name, lat, lon,
         (info or {}).get("crop"), (info or {}).get("adm0"),
         (info or {}).get("adm1"), seasons))


def upsert_dataset_meta(con, ends: dict):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for dataset, end in ends.items():
        con.execute("INSERT OR REPLACE INTO dataset_meta VALUES (?,?,?)",
                    (dataset, end, now))


def upsert_hydro_levels(con, station_key: str, rows) -> int:
    """Insert/refresh DAHITI level rows [(date_iso, level_m, error_m), ...]."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    new = 0
    for date, level_m, error_m in rows:
        cur = con.execute(
            "SELECT 1 FROM hydro_levels WHERE station_key=? AND date=?",
            (station_key, date))
        if cur.fetchone() is None:
            new += 1
        con.execute(
            "INSERT OR REPLACE INTO hydro_levels VALUES (?,?,?,?,?)",
            (station_key, date, level_m, error_m, now))
    return new


def upsert_kariba_flows(con, rows, source: str) -> int:
    """Insert/refresh kariba_flows rows [(date_iso, level_m, turbine_m3s), ...].

    'zra_live' rows always win; a 'seed' row is only written if no row
    (of either source) already exists for that date — so re-running the
    one-time seed never clobbers live data, and the live scraper always
    supersedes the seed's reconstructed history."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    new = 0
    for date, level_m, turbine_m3s in rows:
        cur = con.execute(
            "SELECT source FROM kariba_flows WHERE date=?", (date,))
        hit = cur.fetchone()
        if hit is None:
            new += 1
        elif source == "seed" and hit[0] == "zra_live":
            continue  # live already has this date — never overwrite it
        con.execute(
            "INSERT OR REPLACE INTO kariba_flows VALUES (?,?,?,?,?)",
            (date, level_m, turbine_m3s, source, now))
    return new


def upsert_observations(con, zone_key: str, dataset: str, rows) -> int:
    """Insert/refresh rows, logging value changes to revisions. Returns #new."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    new = 0
    for start, end, value in rows:
        cur = con.execute(
            "SELECT value FROM observations WHERE zone_key=? AND dataset=? "
            "AND granule_start=?", (zone_key, dataset, start))
        hit = cur.fetchone()
        if hit is None:
            new += 1
        elif abs(hit[0] - value) > 1e-6:
            con.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?,?)",
                (zone_key, dataset, start, hit[0], value, now))
        con.execute(
            "INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?)",
            (zone_key, dataset, start, end, value, now))
    return new
