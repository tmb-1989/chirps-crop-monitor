"""Build data/zones/basins.geojson from HydroBASINS Africa level 7.

Flood-watch basins (SCOPING-FLOODS.md): Kenya pilot plus the Ethiopia /
Tanzania / Rwanda / Uganda extension. Anchors are point-in-polygon
selected from the local HydroBASINS shapefile
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

# zone_key: (lat, lon, name, iso3)
BASINS = {
    # --- Kenya (pilot) ---
    "bas_nzoia_hw":  (1.05, 35.05, "Nzoia headwaters (Cherangani)", "KEN"),
    "bas_nzoia_low": (0.12, 34.10, "Lower Nzoia (Budalangi plain)", "KEN"),
    "bas_tana_hw":   (-0.45, 36.85, "Tana headwaters (Aberdares/Mt Kenya)",
                      "KEN"),
    "bas_tana_mid":  (-0.45, 39.65, "Tana at Garissa", "KEN"),
    "bas_lakeshore": (-0.10, 34.75, "Lake Victoria shore (Kisumu)", "KEN"),
    "bas_nairobi":   (-1.29, 36.82, "Nairobi / upper Athi", "KEN"),
    "bas_ewaso":     (0.30, 37.20, "Ewaso Ng'iro upper", "KEN"),
    # --- Ethiopia ---
    "bas_awash_hw":  (8.85, 38.55, "Awash headwaters (Addis highlands)",
                      "ETH"),
    "bas_awash_mid": (9.40, 40.15, "Middle Awash (Amibara/Gewane plain)",
                      "ETH"),
    "bas_baro":      (8.25, 34.60, "Baro at Gambela", "ETH"),
    "bas_laketana":  (11.85, 37.70, "Lake Tana east (Fogera plain)", "ETH"),
    "bas_shabelle_hw": (7.05, 39.90, "Shabelle headwaters (Bale)", "ETH"),
    "bas_diredawa":  (9.60, 41.85, "Dire Dawa (Dechatu)", "ETH"),
    "bas_omo_low":   (5.30, 36.10, "Lower Omo flood plain", "ETH"),
    "bas_shabelle_mid": (6.00, 43.40, "Shabelle at Gode/Kelafo", "ETH"),
    "bas_genale":    (4.35, 41.95, "Genale-Dawa at Dolo Ado", "ETH"),
    # --- Tanzania ---
    "bas_dar":       (-6.85, 39.20, "Dar es Salaam (Msimbazi)", "TZA"),
    "bas_kilombero": (-8.25, 36.30, "Kilombero valley", "TZA"),
    "bas_rufiji_low": (-7.85, 38.90, "Lower Rufiji flood plain", "TZA"),
    "bas_pangani_hw": (-3.35, 37.35, "Pangani headwaters (Kilimanjaro/Moshi)",
                       "TZA"),
    "bas_mwanza":    (-2.55, 32.90, "Lake Victoria south shore (Mwanza)",
                      "TZA"),
    # --- Rwanda ---
    "bas_nyabarongo": (-1.95, 30.00, "Nyabarongo (Kigali)", "RWA"),
    "bas_sebeya":    (-1.65, 29.30, "Sebeya / NW highlands (Rubavu)", "RWA"),
    "bas_akanyaru":  (-2.45, 29.90, "Akanyaru / Bugesera south", "RWA"),
    # --- Uganda ---
    "bas_elgon":     (0.95, 34.30, "Mt Elgon west (Manafwa/Bududa)", "UGA"),
    "bas_kampala":   (0.35, 32.60, "Kampala / Lake Victoria north", "UGA"),
    "bas_nyamwamba": (0.18, 30.10, "Nyamwamba / Rwenzori east (Kasese)",
                      "UGA"),
    "bas_kyoga":     (1.65, 33.55, "Lake Kyoga lowlands (Teso)", "UGA"),
}

# headwater -> downstream pairing with approximate routing lag (days)
DOWNSTREAM = {
    "bas_nzoia_hw": ("bas_nzoia_low", "2-5"),
    "bas_tana_hw": ("bas_tana_mid", "3-7"),
    "bas_awash_hw": ("bas_awash_mid", "2-5"),
    "bas_shabelle_hw": ("bas_shabelle_mid", "4-8"),
    "bas_kilombero": ("bas_rufiji_low", "3-7"),
}


def main() -> int:
    sf = shapefile.Reader(str(SHP))
    fields = [f[0] for f in sf.fields[1:]]
    feats, found = [], set()
    for srec, rec in zip(sf.iterShapes(), sf.iterRecords()):
        for key, (lat, lon, name, iso3) in BASINS.items():
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
                        "zone_key": key, "name": name, "iso3": iso3,
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
        print(f"{p['iso3']} {p['zone_key']:16s} {p['area_km2']:>6}km2  -> "
              f"{p['downstream'] or '-'}")
    print(f"wrote {OUT} ({len(feats)} basins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
