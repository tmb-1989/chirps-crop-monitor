"""Full ingest: all zones x dataset groups -> SQLite.

First run backfills 1981-present (one API call per zone x group, ~2-8s each).
Subsequent runs re-pull recent seasons only (--recent, default last 2 years)
so prelim->final revisions are captured in the revisions table.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from config import (ZONES, DATASETS, CROPZONE_VECTOR, VECTOR_OVERRIDES,
                    FIRST_SEASON)
import db
import ewx_api


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="backfill from 1981 (default: last 2 seasons only)")
    ap.add_argument("--zones", nargs="*", help="subset of zone keys")
    args = ap.parse_args()

    year = dt.date.today().year
    first = FIRST_SEASON if args.full else year - 1

    con = db.connect()
    ends = {}
    try:
        ends = ewx_api.dataset_end_dates([d for d, _, _ in DATASETS])
        db.upsert_dataset_meta(con, ends)
        con.commit()
    except Exception as e:  # noqa: BLE001
        print(f"catalog end-dates fetch failed: {e}", file=sys.stderr)
    zone_items = [(k, v) for k, v in ZONES.items()
                  if not args.zones or k in args.zones]
    failures = []

    for zone_key, (iso3, name, lat, lon, seasons) in zone_items:
        try:
            info = ewx_api.zone_info(lat, lon)
        except Exception as e:  # noqa: BLE001
            print(f"[{zone_key}] WFS zone lookup failed: {e}", file=sys.stderr)
            info = None
        if info is None:
            print(f"[{zone_key}] WARNING: anchor point not inside a crop zone",
                  file=sys.stderr)
        db.upsert_zone(con, zone_key, iso3, name, lat, lon, info,
                       ",".join(f"{n}:{a}-{b}" for n, a, b in seasons))
        con.commit()

        vector = VECTOR_OVERRIDES.get(zone_key, CROPZONE_VECTOR)
        for raster, periodicity, statistic in DATASETS:
            try:
                series = ewx_api.fetch_timeseries(
                    vector, [raster], periodicity, statistic,
                    lat, lon, first_season=first)
                # the API soft-rate-limits by returning 200 with an empty
                # body after request bursts — back off and retry once.
                # No retry when the catalog itself advertises no granules
                # (e.g. monthly prelim outside its brief publication window).
                if not any(series.values()) and ends.get(raster) is not None:
                    time.sleep(90)
                    series = ewx_api.fetch_timeseries(
                        vector, [raster], periodicity, statistic,
                        lat, lon, first_season=first)
            except Exception as e:  # noqa: BLE001
                failures.append((zone_key, raster, str(e)))
                print(f"[{zone_key}] {raster} FAILED: {e}", file=sys.stderr)
                continue
            if not any(series.values()):
                if ends.get(raster) is None:
                    continue  # catalog says no granules exist — not an error
                failures.append((zone_key, raster, "empty response x2"))
                print(f"[{zone_key}] {raster} EMPTY twice, skipping",
                      file=sys.stderr)
                continue
            for dataset, rows in series.items():
                n = db.upsert_observations(con, zone_key, dataset, rows)
                print(f"[{zone_key}] {dataset}: {len(rows)} rows ({n} new)")
            con.commit()
            time.sleep(3)  # be polite to the .gov endpoint

    con.close()
    if failures:
        print(f"\n{len(failures)} fetch failures", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
