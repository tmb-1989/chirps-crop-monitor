"""Fetch zone geometries (WFS) and build the local zones GeoJSON.

Per configured zone: the crop-zone polygon containing its anchor point
(admin-1 polygon for VECTOR_OVERRIDES zones). Adds two CPI-aligned
composites: kenya_grain_basket and zambia_maize_belt (unions).

Output: data/zones/zones.geojson with properties zone_key, iso3, name.
NOTE: this server's WFS returns GeoJSON coordinates as [lon, lat]
(standard), but CQL INTERSECTS filters use (lat lon) axis order.
"""
from __future__ import annotations

import json
import pathlib
import sys

import requests
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from config import ZONES, VECTOR_OVERRIDES, CROPZONE_VECTOR, ADMIN1_VECTOR, WFS_BASE  # noqa: E402

UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "zones" / "zones.geojson"

COMPOSITES = {
    "ken_grain_basket": ("KEN", "Kenya grain basket (composite)",
                         ["ken_uasin_gishu", "ken_trans_nzoia"]),
    "zmb_maize_belt": ("ZMB", "Zambia maize belt (composite)",
                       ["zmb_central", "zmb_southern", "zmb_eastern"]),
    "zaf_maize_triangle": ("ZAF", "South Africa maize triangle (composite)",
                           ["zaf_free_state", "zaf_north_west",
                            "zaf_mpumalanga"]),
}


def fetch_polygon(vector: str, lat: float, lon: float):
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": vector, "outputFormat": "application/json", "count": "1",
        "cql_filter": f"INTERSECTS(geom,POINT({lat} {lon}))",
    }
    r = requests.get(WFS_BASE, params=params, headers=UA, timeout=120)
    r.raise_for_status()
    feats = r.json().get("features") or []
    if not feats:
        raise RuntimeError(f"no polygon at ({lat}, {lon}) in {vector}")
    return shape(feats[0]["geometry"])


# GAEZ harvested-area threshold for export-belt clipping: a 5-arcmin
# cell counts as belt when it holds >= this fraction of the belt's max
# cell. Chosen so Ethiopia's belt becomes the coffee highlands, not all
# of Oromia (the unclipped admin-1 polygon diluted everything — see
# SCOPING-V2.3 / the 5 Oct 2026 Hydrology mockup).
BELT_CELL_FRAC = 0.05
EXPORT_RASTER = {"coffee": "Stimulants", "tea": "Stimulants",
                 "tobacco": "Tobacco", "vanilla": "CropsNES",
                 "cashew": "CropsNES"}


def clip_to_crop_footprint(geom, crop: str):
    """Intersect an export belt's admin-1 polygon with the GAEZ
    harvested-area footprint of its crop (cells >= BELT_CELL_FRAC of
    the in-polygon max, polygonized). Falls back to the unclipped
    polygon when the footprint is empty."""
    import numpy as np
    import rasterio
    import rasterio.features
    from shapely.geometry import shape as _shape
    from shapely.ops import unary_union as _union
    path = (pathlib.Path(__file__).resolve().parent.parent / "data" /
            "raw" / "gaez2015" / f"HarvArea_{EXPORT_RASTER[crop]}_Total.tif")
    with rasterio.open(path) as src:
        arr = src.read(1)
        tr = src.transform
        mask = rasterio.features.geometry_mask(
            [geom.__geo_interface__], out_shape=arr.shape, transform=tr,
            invert=True, all_touched=True)
    vals = np.where(mask & np.isfinite(arr) & (arr > 0), arr, 0)
    if vals.max() <= 0:
        return geom
    keep = (vals >= vals.max() * BELT_CELL_FRAC).astype(np.uint8)
    pieces = [_shape(g) for g, v in
              rasterio.features.shapes(keep, transform=tr) if v == 1]
    if not pieces:
        return geom
    # buffer(0) heals self-intersections in the hand-digitized admin
    # polygons (mdg_vanilla hit a GEOS side-location conflict)
    clipped = geom.buffer(0).intersection(_union(pieces).buffer(0))
    return clipped if not clipped.is_empty else geom


def main() -> int:
    from config import EXPORT_CROP
    feats, geoms = [], {}
    for zone_key, (iso3, name, lat, lon, _seasons) in ZONES.items():
        vector = VECTOR_OVERRIDES.get(zone_key, CROPZONE_VECTOR)
        geom = fetch_polygon(vector, lat, lon)
        if zone_key in EXPORT_CROP:
            raw = geom.area
            geom = clip_to_crop_footprint(geom, EXPORT_CROP[zone_key])
            print(f"  {zone_key}: clipped {raw:.2f} -> {geom.area:.2f} deg2")
        geoms[zone_key] = geom
        feats.append({"type": "Feature", "geometry": mapping(geom),
                      "properties": {"zone_key": zone_key, "iso3": iso3,
                                     "name": name}})
        print(f"{zone_key}: {geom.geom_type}, area {geom.area:.2f} deg2")
    for ckey, (iso3, name, members) in COMPOSITES.items():
        geom = unary_union([geoms[m] for m in members])
        feats.append({"type": "Feature", "geometry": mapping(geom),
                      "properties": {"zone_key": ckey, "iso3": iso3,
                                     "name": name}})
        print(f"{ckey}: union of {members}, area {geom.area:.2f} deg2")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"wrote {OUT} ({len(feats)} zones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
