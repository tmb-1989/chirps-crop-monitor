"""Build db/app.sqlite — the deploy-facing copy of monitor.sqlite.

The full archive (observations from 1981, 150+ MB after the V2 zone
expansion) exceeds GitHub's 100 MB hard limit, so it stays local-only
(gitignored). The deployed dashboard instead reads this trimmed copy:
every table in full EXCEPT observations, which is cut to >= CUTOFF.

CUTOFF = 2015 (was 2010; bumped with the P7 West Africa expansion —
108 zones put the 2010 cut at 109 MB, over the 100 MB limit; 2015 is
the earliest year any app panel reads: the 2015-16 El Niño benchmark
episode starts 2015-07, and the southern cross-year "2015" comparator
season starts Oct 2015): the app's oldest
observation-based comparisons are the 2015-16 / 2023-24 El Niño
episode windows; long-run flood history renders from flood_state,
which is kept complete. Climatologies (SPI, pctm, flood percentiles)
are computed upstream into dekad_metrics / flood_state before this
split, so trimming observations does not touch any baseline.

Run by run_update.sh on dekad days, before the git add.
"""
from __future__ import annotations

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "db" / "monitor.sqlite"
DST = ROOT / "db" / "app.sqlite"
CUTOFF = "2015-01-01"


def main() -> int:
    if DST.exists():
        DST.unlink()
    con = sqlite3.connect(SRC)
    con.execute("ATTACH DATABASE ? AS app", (str(DST),))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")]
    # per-table trims — what the app actually renders (P6 pushed the
    # untrimmed copy to 111 MB, over GitHub's hard limit):
    # - observations: >= CUTOFF, minus raw basin pentads (every flood
    #   panel draws from flood_state, never the raw zonal means)
    # - flood_state: >= 1998 (the app's oldest flood view is the
    #   'Full history 1998-2026' expander; percentile climatologies
    #   are computed upstream, so the trim touches no baseline)
    # - dekad_metrics: >= CUTOFF (SPI panel shows the last 5 years)
    # - revisions: audit trail, never queried by the app — dropped
    WHERE = {
        "observations": (f" WHERE granule_start >= '{CUTOFF}' AND "
                         "dataset NOT IN ('chirps3local_pentad_data',"
                         "'chirps3local-prelim_pentad_data')"),
        "flood_state": " WHERE granule_start >= '1998-01-01'",
        "dekad_metrics": f" WHERE granule_start >= '{CUTOFF}'",
        "revisions": None,   # skipped entirely
    }
    for t in tables:
        if t in WHERE and WHERE[t] is None:
            continue
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?",
                          (t,)).fetchone()[0]
        sql = sql.replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ")
        con.execute(sql.replace(f"CREATE TABLE {t}",
                                f"CREATE TABLE app.{t}", 1))
        con.execute(f"INSERT INTO app.{t} SELECT * FROM {t}"
                    f"{WHERE.get(t, '')}")
    con.commit()
    n = con.execute("SELECT count(*) FROM app.observations").fetchone()[0]
    con.execute("VACUUM app")
    con.close()
    print(f"app.sqlite: {n:,} observations (>= {CUTOFF}), "
          f"{DST.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
