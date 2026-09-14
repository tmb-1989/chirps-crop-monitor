"""SQLite storage for zone metadata and observation time series."""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "monitor.sqlite"

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
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def upsert_zone(con, zone_key, iso3, name, lat, lon, info, seasons):
    con.execute(
        "INSERT OR REPLACE INTO zones VALUES (?,?,?,?,?,?,?,?,?)",
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
