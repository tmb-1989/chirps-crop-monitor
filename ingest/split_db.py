"""One-off migration: split fast-moving tables into db/live.sqlite.

Why: run_update.sh commits the DB after every run so the deployed
Streamlit app stays current. At a daily cadence, committing the ~54MB
monitor.sqlite daily grows the GitHub repo by GBs per month (SQLite
binaries don't delta-compress). The split puts the small tables that
actually change daily — ENSO/IOD indices, Kariba, the risk board, GEFS
outlooks, flood alerts/heartbeat — into a few-hundred-KB live.sqlite
committed daily, while monitor.sqlite (observations, derived metrics,
flood_state) is committed only on dekad days.

db.connect() ATTACHes live.sqlite as `live`; unqualified table names in
queries resolve into it automatically, so only DDL needed qualifying.

Idempotent: tables already moved are skipped. Run once per machine:
    python ingest/split_db.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

LIVE_TABLES = [
    "enso", "enso_weekly", "iod_dmi", "iod_dmi_oisst",
    "kariba_level", "kariba_reservoir",
    "country_risk", "country_risk_log", "zone_risk",
    "flood_gefs", "flood_alerts", "flood_runs",
]


def main() -> int:
    con = db.connect()  # attaches live.sqlite
    main_tables = {r[0] for r in con.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}
    for t in LIVE_TABLES:
        if t not in main_tables:
            print(f"{t}: not in main (already moved or never created)")
            continue
        sql = con.execute("SELECT sql FROM main.sqlite_master WHERE "
                          "name=?", (t,)).fetchone()[0]
        qualified = sql.replace(f"TABLE {t}", f"TABLE live.{t}", 1) \
            .replace(f'TABLE "{t}"', f'TABLE live."{t}"', 1)
        con.execute(f"DROP TABLE IF EXISTS live.{t}")
        con.execute(qualified)
        con.execute(f"INSERT INTO live.{t} SELECT * FROM main.{t}")
        n = con.execute(f"SELECT count(*) FROM live.{t}").fetchone()[0]
        con.execute(f"DROP TABLE main.{t}")
        print(f"{t}: moved {n} rows to live.sqlite")
    con.commit()
    con.execute("VACUUM main")
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
