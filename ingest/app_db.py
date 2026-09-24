"""Build db/app.sqlite — the deploy-facing copy of monitor.sqlite.

The full archive (observations from 1981, 150+ MB after the V2 zone
expansion) exceeds GitHub's 100 MB hard limit, so it stays local-only
(gitignored). The deployed dashboard instead reads this trimmed copy:
every table in full EXCEPT observations, which is cut to >= CUTOFF.

CUTOFF = 2010: the app's oldest observation-based comparisons are the
2015-16 / 2023-24 El Niño episode windows; long-run flood history
renders from flood_state, which is kept complete. Climatologies (SPI,
pctm, flood percentiles) are computed upstream into dekad_metrics /
flood_state before this split, so trimming observations does not
touch any baseline.

Run by run_update.sh on dekad days, before the git add.
"""
from __future__ import annotations

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "db" / "monitor.sqlite"
DST = ROOT / "db" / "app.sqlite"
CUTOFF = "2010-01-01"


def main() -> int:
    if DST.exists():
        DST.unlink()
    con = sqlite3.connect(SRC)
    con.execute("ATTACH DATABASE ? AS app", (str(DST),))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?",
                          (t,)).fetchone()[0]
        sql = sql.replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ")
        con.execute(sql.replace(f"CREATE TABLE {t}",
                                f"CREATE TABLE app.{t}", 1))
        where = f" WHERE granule_start >= '{CUTOFF}'" \
            if t == "observations" else ""
        con.execute(f"INSERT INTO app.{t} SELECT * FROM {t}{where}")
    con.commit()
    n = con.execute("SELECT count(*) FROM app.observations").fetchone()[0]
    con.execute("VACUUM app")
    con.close()
    print(f"app.sqlite: {n:,} observations (>= {CUTOFF}), "
          f"{DST.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
