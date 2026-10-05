"""Build data/zones/catchments.geojson — hydropower catchments (V2.4).

Each catchment is the union of HydroBASINS Africa level-7 polygons
upstream of the dam: find the polygon containing the dam point, then
walk the NEXT_DOWN graph upstream (every basin whose outflow chain
reaches the dam). Cahora Bassa is stored as the INCREMENTAL catchment —
its upstream set minus Kariba's and Kafue's, so the three signals stay
independent (Kariba and Kafue Gorge sit upstream of Cahora Bassa).

Catchments are hydrological objects and cross borders by design (the
upper Zambezi above Kariba is mostly Angolan). `countries` lists the
ISO3s whose hydro light each catchment feeds.

Run once per change (the output is committed); needs shapely +
pyshp, so it never runs on Streamlit Cloud. Label points are
precomputed here for the same reason.

Usage: python ingest/catchments.py
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict, deque

import shapefile
from shapely.geometry import shape, Point, mapping
from shapely.ops import unary_union

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHP = ROOT / "data" / "raw" / "hybas_af_lev07_v1c.shp"
OUT = ROOT / "data" / "zones" / "catchments.geojson"

# zone_key: (dam lat, dam lon, name, plant, countries lit by it)
CATCHMENTS = {
    "cat_kariba": (-16.522, 28.761, "Kariba (upper Zambezi)",
                   "Kariba North+South ~2.1 GW", "ZMB,ZWE"),
    "cat_kafue": (-15.800, 28.417, "Kafue (Itezhi-Tezhi / Gorge)",
                  "Kafue Gorge ~1.6 GW", "ZMB"),
    "cat_cahora": (-15.586, 32.707, "Cahora Bassa incremental",
                   "Cahora Bassa ~2.1 GW", "MOZ"),
    "cat_shire": (-15.883, 34.773, "Shire / Lake Malawi",
                  "Nkula/Tedzani/Kapichira ~0.4 GW", "MWI"),
    "cat_rufiji": (-7.808, 37.993, "Rufiji (Julius Nyerere)",
                   "JNHPP ~2.1 GW", "TZA"),
    "cat_tana": (-0.657, 37.880, "Tana cascade (Seven Forks)",
                 "Masinga..Kiambere ~0.6 GW", "KEN"),
    "cat_gerd": (11.214, 35.093, "Blue Nile (GERD)",
                 "GERD ~5.2 GW", "ETH"),
}
# incremental: subtract these catchments' upstream sets
SUBTRACT = {"cat_cahora": ("cat_kariba", "cat_kafue")}


def main() -> int:
    sf = shapefile.Reader(str(SHP))
    fields = [f[0] for f in sf.fields[1:]]
    ix = {n: i for i, n in enumerate(fields)}
    recs = sf.records()
    shapes = sf.shapes()
    upstream = defaultdict(list)       # next_down id -> [ids]
    rec_of = {}
    for i, r in enumerate(recs):
        hid = int(r[ix["HYBAS_ID"]])
        rec_of[hid] = i
        upstream[int(r[ix["NEXT_DOWN"]])].append(hid)

    def find_dam_basin(lat, lon):
        pt = Point(lon, lat)
        for i, s in enumerate(shapes):
            x0, y0, x1, y1 = s.bbox
            if x0 <= lon <= x1 and y0 <= lat <= y1 and \
                    shape(s.__geo_interface__).contains(pt):
                return int(recs[i][ix["HYBAS_ID"]])
        raise SystemExit(f"no level-7 basin contains ({lat}, {lon})")

    def walk_up(root):
        seen, q = {root}, deque([root])
        while q:
            for u in upstream.get(q.popleft(), []):
                if u not in seen:
                    seen.add(u)
                    q.append(u)
        return seen

    sets = {}
    for key, (lat, lon, *_rest) in CATCHMENTS.items():
        sets[key] = walk_up(find_dam_basin(lat, lon))
    for key, subs in SUBTRACT.items():
        for s in subs:
            sets[key] -= sets[s]

    feats = []
    for key, (lat, lon, name, plant, countries) in CATCHMENTS.items():
        geoms = [shape(shapes[rec_of[h]].__geo_interface__)
                 for h in sets[key]]
        # buffer(0) heals the slivers unary_union leaves on shared edges
        g = unary_union(geoms).buffer(0).simplify(0.01,
                                                  preserve_topology=True)
        area = sum(float(recs[rec_of[h]][ix["SUB_AREA"]])
                   for h in sets[key])
        rp = g.representative_point()
        feats.append({
            "type": "Feature", "geometry": mapping(g),
            "properties": {
                "zone_key": key, "name": name, "plant": plant,
                "countries": countries, "dam_lat": lat, "dam_lon": lon,
                "area_km2": round(area), "n_basins": len(sets[key]),
                "label_lon": round(rp.x, 4), "label_lat": round(rp.y, 4),
            }})
        print(f"{key:12s} {len(sets[key]):4d} basins  {area:>9,.0f} km2  "
              f"{name}")
    OUT.write_text(json.dumps({"type": "FeatureCollection",
                               "features": feats}))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
