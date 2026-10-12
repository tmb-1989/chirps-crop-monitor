"""V2.1 production ground-truth audit (SCOPING-V2.md).

For every crop zone: what share of national production of the zone's
crop lies inside the polygon? Grid: GAEZ+ 2015 actual production
(Total = irrigated + rainfed, metric tons, 5-arcmin; Harvard Dataverse
doi:10.7910/DVN/KJFUO1 — chosen over MapSPAM SPAM2020, which is
guestbook-gated against scripted download). Country totals come from
Natural Earth admin-0 polygons on the same grid.

Crop mapping: maize -> Maize; small grains (Arsi/Gojjam) -> Wheat
(barley not downloaded; Arsi is wheat-led); sorghum -> Sorghum.
GAEZ has no teff raster — Ethiopian teff sits in Othercereals, used
when a teff zone exists.

Output: per-zone and per-country-crop coverage table, with the
hand-estimated share from data/econ/production_weights.csv beside the
measured value. Read-only: updating the CSV stays a human decision.

Usage: python compute/production_audit.py
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

import numpy as np
import rasterio
import rasterio.features
from shapely.geometry import shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAEZ = ROOT / "data" / "raw" / "gaez2015"
NE = ROOT / "data" / "raw" / "ne_countries.geojson"
ZONES_GJ = ROOT / "data" / "zones" / "zones.geojson"
ECON = ROOT / "data" / "econ" / "production_weights.csv"

CROP_RASTER = {"maize": "Maize", "small grains": "Wheat",
               "sorghum": "Sorghum", "rice": "Rice", "teff": "Othercereals",
               "tobacco": "Tobacco", "stimulants": "Stimulants"}
COMPOSITES = {"ken_grain_basket", "zmb_maize_belt", "zaf_maize_triangle"}


def grid_sum(raster, geom) -> float:
    """Sum of raster values inside geom (all-touched rasterization)."""
    mask = rasterio.features.geometry_mask(
        [geom.__geo_interface__], out_shape=raster.shape,
        transform=raster_transform, invert=True, all_touched=True)
    vals = raster[mask]
    return float(np.nansum(np.where(vals > 0, vals, 0)))


def main() -> int:
    sys.path.insert(0, str(ROOT / "ingest"))
    import db
    con = db.connect()
    zcrop = {r[0]: (r[1] or "maize").lower() for r in
             con.execute("SELECT zone_key, crop FROM zones")}

    hand = {}
    with open(ECON) as f:
        for r in csv.DictReader(f):
            hand[r["zone_key"]] = float(r["share_national"])

    ne = json.loads(NE.read_text())
    outlines = {}
    for f in ne["features"]:
        iso = f["properties"].get("ADM0_A3") or f["properties"].get("ISO_A3")
        outlines[iso] = shape(f["geometry"])

    zones = json.loads(ZONES_GJ.read_text())
    rasters = {}
    global raster_transform
    for crop, name in CROP_RASTER.items():
        p = GAEZ / f"Production_{name}_Total.tif"
        if not p.exists():
            continue
        with rasterio.open(p) as src:
            rasters[crop] = src.read(1)
            raster_transform = src.transform

    print(f"{'zone':22s} {'crop':12s} {'measured':>9s} {'hand-est':>9s}")
    country_cover: dict = {}
    for f in zones["features"]:
        zk = f["properties"]["zone_key"]
        if zk in COMPOSITES:
            continue
        iso = f["properties"]["iso3"]
        crop = zcrop.get(zk, "maize")
        if crop not in rasters:
            print(f"{zk:22s} {crop:12s} {'no raster':>9s}")
            continue
        geom = shape(f["geometry"])
        z = grid_sum(rasters[crop], geom)
        nat = grid_sum(rasters[crop], outlines[iso])
        share = z / nat if nat else float("nan")
        h = hand.get(zk)
        print(f"{zk:22s} {crop:12s} {share:8.1%} "
              f"{'' if h is None else f'{h:8.1%}'}")
        key = (iso, crop)
        country_cover.setdefault(key, 0.0)
        country_cover[key] += share

    print("\ncountry coverage (sum of zone shares; overlap not deduped):")
    for (iso, crop), tot in sorted(country_cover.items()):
        flag = "" if tot >= 0.70 else "   <-- below 70% target"
        print(f"  {iso} {crop:12s} {tot:6.1%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
