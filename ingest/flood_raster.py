"""Local CHIRPS v3 PENTAD pipeline over flood basins (flood-watch layer).

Same machinery as chirps_raster.py but at pentad resolution (the flood
timescale) over data/zones/basins.geojson, with its own mask cache
(basins_masks.npz). Datasets written to observations:
    chirps3local_pentad_data          (final, Africa pentads)
    chirps3local-prelim_pentad_data   (prelim, Africa pentads — current on
                                       CHC unlike the prelim dekads)

Usage:
    python flood_raster.py            # incremental (last 2 years)
    python flood_raster.py --full     # backfill 1981-present (~3300 files)
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import json
import pathlib
import re
import sys
import time

import numpy as np
import rasterio
import rasterio.features

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402
import chirps_raster as cr  # noqa: E402  (reuse fetch/list_remote/zonal_means)

BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0"
FINAL_DIR = f"{BASE}/pentads/africa/tifs"
PRELIM_DIR = f"{BASE}/prelim/pentads/africa/tifs"

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASINS_GJ = ROOT / "data" / "zones" / "basins.geojson"
MASKS_NPZ = ROOT / "data" / "zones" / "basins_masks.npz"

DS_FINAL = "chirps3local_pentad_data"
DS_PRELIM = "chirps3local-prelim_pentad_data"

PENTAD_START = {1: 1, 2: 6, 3: 11, 4: 16, 5: 21, 6: 26}


def pentad_dates(name: str) -> tuple[str, str]:
    y, m, p = map(int, re.match(
        r"chirps-v3\.0\.(\d{4})\.(\d{2})\.(\d)", name).groups())
    start = dt.date(y, m, PENTAD_START[p])
    end = dt.date(y, m, 25) if p < 6 else \
        dt.date(y, m, calendar.monthrange(y, m)[1])
    if p < 6:
        end = dt.date(y, m, PENTAD_START[p] + 4)
    return start.isoformat(), end.isoformat()


def build_masks(ref_tif_bytes: bytes):
    gj = json.loads(BASINS_GJ.read_text())
    with rasterio.open(io.BytesIO(ref_tif_bytes)) as src:
        transform, shape_ = src.transform, (src.height, src.width)
    masks, empty = {}, []
    for f in gj["features"]:
        key = f["properties"]["zone_key"]
        m = rasterio.features.rasterize(
            [(f["geometry"], 1)], out_shape=shape_, transform=transform,
            fill=0, dtype="uint8").astype(bool)
        if not m.any():
            empty.append(key)
        masks[key] = m
    if empty:
        raise RuntimeError(f"basins rasterized to zero pixels: {empty}")
    np.savez_compressed(MASKS_NPZ, **masks)
    print(f"basin masks built: {len(masks)} on grid {shape_}")
    return masks


def process(names, dir_url, dataset, masks, con, skip_existing=True) -> int:
    done = 0
    have = {r[0] for r in con.execute(
        "SELECT granule_start FROM observations WHERE dataset=? "
        "GROUP BY granule_start HAVING count(DISTINCT zone_key) >= ?",
        (dataset, len(masks)))} if skip_existing else set()
    todo = [n for n in names if pentad_dates(n)[0] not in have]
    for name in todo:
        blob = cr.fetch(f"{dir_url}/{name}")
        # CHC runs CrowdSec: stay sequential with >=1s between downloads
        time.sleep(1.0)
        if not blob:
            print(f"  missing remote: {name}", file=sys.stderr)
            continue
        start, end = pentad_dates(name)
        means = cr.zonal_means(blob, masks)
        for zone_key, v in means.items():
            if v is not None:
                db.upsert_observations(con, zone_key, dataset,
                                       [(start, end, v)])
        con.commit()
        done += 1
        if done % 200 == 0:
            print(f"  {dataset}: {done} pentads (through {start})")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    finals = cr.list_remote(FINAL_DIR)
    prelims = cr.list_remote(PRELIM_DIR)
    if not args.full:
        cutoff = f"chirps-v3.0.{dt.date.today().year - 2}"
        finals = [n for n in finals if n >= cutoff]
        prelims = [n for n in prelims if n >= cutoff]
    prelims = [n for n in prelims if n not in set(finals)]

    con = db.connect()
    if MASKS_NPZ.exists():
        z = np.load(MASKS_NPZ)
        masks = {k: z[k] for k in z.files}
    else:
        masks = build_masks(cr.fetch(f"{FINAL_DIR}/{finals[-1]}"))

    n1 = process(finals, FINAL_DIR, DS_FINAL, masks, con)
    n2 = process(prelims, PRELIM_DIR, DS_PRELIM, masks, con,
                 skip_existing=False)
    con.close()
    print(f"processed {n1} final + {n2} prelim pentads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
