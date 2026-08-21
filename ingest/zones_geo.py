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


def main() -> int:
    feats, geoms = [], {}
    for zone_key, (iso3, name, lat, lon, _seasons) in ZONES.items():
        vector = VECTOR_OVERRIDES.get(zone_key, CROPZONE_VECTOR)
        geom = fetch_polygon(vector, lat, lon)
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
