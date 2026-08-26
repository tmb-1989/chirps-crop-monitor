"""Build data/zones/basins.geojson from HydroBASINS Africa level 7.

Kenya pilot basins for the flood-watch layer (SCOPING-FLOODS.md). Anchors
are point-in-polygon selected from the local HydroBASINS shapefile
(data/raw/hybas_af_lev07_v1c.shp — download noted in README).
"""
from __future__ import annotations

import json
import pathlib

import shapefile
from shapely.geometry import shape, Point, mapping

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHP = ROOT / "data" / "raw" / "hybas_af_lev07_v1c.shp"
OUT = ROOT / "data" / "zones" / "basins.geojson"

# zone_key: (lat, lon, name)
BASINS = {
    "bas_nzoia_hw":  (1.05, 35.05, "Nzoia headwaters (Cherangani)"),
    "bas_nzoia_low": (0.12, 34.10, "Lower Nzoia (Budalangi plain)"),
    "bas_tana_hw":   (-0.45, 36.85, "Tana headwaters (Aberdares/Mt Kenya)"),
    "bas_tana_mid":  (-0.45, 39.65, "Tana at Garissa"),
    "bas_lakeshore": (-0.10, 34.75, "Lake Victoria shore (Kisumu)"),
    "bas_nairobi":   (-1.29, 36.82, "Nairobi / upper Athi"),
    "bas_ewaso":     (0.30, 37.20, "Ewaso Ng'iro upper"),
}

# headwater -> downstream pairing with approximate routing lag (days)
DOWNSTREAM = {
    "bas_nzoia_hw": ("bas_nzoia_low", "2-5"),
    "bas_tana_hw": ("bas_tana_mid", "3-7"),
}


def main() -> int:
    sf = shapefile.Reader(str(SHP))
    fields = [f[0] for f in sf.fields[1:]]
    feats, found = [], set()
    for srec, rec in zip(sf.iterShapes(), sf.iterRecords()):
        for key, (lat, lon, name) in BASINS.items():
            if key in found:
                continue
            x0, y0, x1, y1 = srec.bbox
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            g = shape(srec.__geo_interface__)
            if g.contains(Point(lon, lat)):
                d = dict(zip(fields, rec))
                ds = DOWNSTREAM.get(key)
                feats.append({
                    "type": "Feature", "geometry": mapping(g),
                    "properties": {
                        "zone_key": key, "name": name, "iso3": "KEN",
                        "hybas_id": int(d["HYBAS_ID"]),
                        "next_down": int(d["NEXT_DOWN"]),
                        "area_km2": round(d["SUB_AREA"]),
                        "downstream": ds[0] if ds else None,
                        "routing_days": ds[1] if ds else None,
                    }})
                found.add(key)
    missing = set(BASINS) - found
    if missing:
        raise SystemExit(f"anchors not found: {missing}")
    OUT.write_text(json.dumps({"type": "FeatureCollection",
                               "features": feats}))
    for f in feats:
        p = f["properties"]
        print(f"{p['zone_key']:16s} {p['area_km2']:>6}km2  -> "
              f"{p['downstream'] or '-'}")
    print(f"wrote {OUT} ({len(feats)} basins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
