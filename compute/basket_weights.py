"""Measure SECOND-staple production shares over existing zones (V3.1).

The multi-staple CPI basket (data/econ/staple_basket.csv) lets a
country's impulse aggregate more than one staple. A second commodity
only contributes an observed shock where our zones cover its growing
area — northern Nigeria's and Ghana's monitored maize belts are also
their sorghum belts, so the SAME polygons get a sorghum production
share measured from the GAEZ+ 2015 Sorghum grid (no new zones, no
double-counted land: each commodity aggregates against its own
national total and its own basket share).

Output: data/econ/basket_weights.csv — (zone_key, iso3, commodity,
share_national) rows for commodities BEYOND the zone's primary one in
production_weights.csv. compute/cpi_impulse.py unions the two files.

Usage: python compute/basket_weights.py
"""
from __future__ import annotations

import csv
import json
import pathlib

import numpy as np
import rasterio
import rasterio.features
from shapely.geometry import shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAEZ = ROOT / "data" / "raw" / "gaez2015"
NE = ROOT / "data" / "raw" / "ne_countries.geojson"
ZONES_GJ = ROOT / "data" / "zones" / "zones.geojson"
OUT = ROOT / "data" / "econ" / "basket_weights.csv"

# (iso3, commodity, GAEZ production raster): which countries get a
# second staple measured, over which grid. Zones included: that
# country's staple-sector zones (export belts excluded).
EXTRA = [
    ("NGA", "Sorghum", "Sorghum"),
    ("GHA", "Sorghum", "Sorghum"),
]

ADM0_A3 = {"NGA", "GHA"}


def grid_sum(arr, transform, geom) -> float:
    mask = rasterio.features.geometry_mask(
        [geom.__geo_interface__], out_shape=arr.shape,
        transform=transform, invert=True, all_touched=True)
    v = arr[mask]
    return float(np.nansum(np.where(v > 0, v, 0)))


def main() -> int:
    import sys
    sys.path.insert(0, str(ROOT / "ingest"))
    import db
    con = db.connect()
    staple_zones = {}
    for zk, iso in con.execute(
            "SELECT zone_key, iso3 FROM zones WHERE sector='staple'"):
        staple_zones.setdefault(iso, set()).add(zk)

    ne = json.loads(NE.read_text())
    outlines = {}
    for f in ne["features"]:
        iso = f["properties"].get("ADM0_A3") or f["properties"].get("ISO_A3")
        if iso in ADM0_A3:
            outlines[iso] = shape(f["geometry"])
    zgj = json.loads(ZONES_GJ.read_text())

    rows = []
    for iso, commodity, rname in EXTRA:
        with rasterio.open(GAEZ / f"Production_{rname}_Total.tif") as src:
            arr, tr = src.read(1), src.transform
        nat = grid_sum(arr, tr, outlines[iso])
        crows, tot = [], 0.0
        for f in zgj["features"]:
            p = f["properties"]
            if p["iso3"] != iso or p["zone_key"] not in staple_zones[iso]:
                continue
            share = grid_sum(arr, tr, shape(f["geometry"])) / nat \
                if nat else 0.0
            crows.append([p["zone_key"], iso, commodity, share])
            tot += share
        # overlapping polygons + all_touched edge cells can double-count
        # past 100% (GHA sorghum read 108%) — renormalize so the summed
        # coverage never exceeds 1
        scale = 1.0 / tot if tot > 1 else 1.0
        for r in crows:
            r[3] = round(r[3] * scale, 3)
        rows.extend(tuple(r) for r in crows)
        print(f"{iso} {commodity}: zones cover {min(tot, 1):.0%} of "
              f"national production"
              + (f" (raw {tot:.0%}, renormalized)" if tot > 1 else ""))

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone_key", "iso3", "commodity", "share_national"])
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
