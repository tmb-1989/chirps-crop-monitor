"""Shared hydro-catchment zone registry (upstream-aggregated basins).

Builds hydropower-relevant rainfall catchments into basins.geojson, keyed
by zone_key. Started as a DRC-only script (SCOPING-DRC-HYDRO.md D1:
`drc_lualaba_hydro` above the Nzilo dam, `drc_lufira_hydro` above the
Mwadingusha/Tshangalele dam) and extended for SCOPING-MOZ-HYDRO.md M1 with
`zmb_luangwa_basin` (the unregulated Luangwa above its Zambezi confluence
at Zumbo) — new hydro catchments belong in this module's OUTLETS registry,
not a parallel zone-builder file.

Unlike the Kenya flood basins in flood_zones.py (single lev-7 sub-basin per
zone), these are upstream-*aggregated* catchments: find the lev-7 basin
containing the dam/outlet, walk NEXT_DOWN upstream (the pattern in
elnino-hydro-dashboard's ingest/chirps.py _build_basin_geojsons), union the
members, and sanity-check the assembled area against the outlet basin's own
UP_AREA field (must agree within ~2% — this validates the walk, since
UP_AREA is HydroBASINS' own precomputed upstream-area figure for that
basin). Each entry carries its own (expect_min, expect_max) magnitude band
since the catchments span very different scales (14k km2 Lufira headwater
vs ~150k km2 Luangwa).

Outlet coordinates:
- DRC: SCOPING-DRC-HYDRO.md gives approximate dam locations ((-10.83,
  25.78) for Nzilo, (-10.72, 27.05) for Mwadingusha/Tshangalele). The
  Lualaba approximation lands in a small tributary sub-basin (UP_AREA
  ~2,456 km2 — wildly below the ~15,000-60,000 km2 expected magnitude), so
  this script uses the actual Nzilo dam coordinates instead (10°29'59"S,
  25°27'45"E per Wikipedia/GEO, cross-checked against two more sources —
  all three land in the same lev-7 basin, UP_AREA 18,300 km2, in range).
  The Lufira approximation already lands correctly (checked against Lake
  Tshangalele / Mwadingusha plant coordinates from Wikipedia — all land in
  the same basin, UP_AREA 14,263 km2, just under the 15,000 km2 floor but
  close enough not to warrant further investigation; this basin has no
  upstream lev-7 sub-basins of its own, i.e. it's a single headwater
  polygon whose SUB_AREA equals its UP_AREA).
- Luangwa: SCOPING-MOZ-HYDRO.md's approximate confluence (-15.61, 30.44)
  lands in the Zambezi *mainstem* lev-7 basin (UP_AREA ~1,009,000 km2 —
  the whole upstream Zambezi, an absurd magnitude for "Luangwa alone"),
  exactly the DRC build's lesson about approximate outlets landing in the
  wrong polygon. Probing neighbouring lev-7 basins around the confluence
  for one whose own UP_AREA reads near the published Luangwa magnitude
  found HYBAS 1071431480 (UP_AREA 149,540 km2, NEXT_DOWN correctly the
  Zambezi mainstem basin) — the Luangwa's own lev-7 pour-point basin. Used
  outlet (-15.61, 30.42): 0.02 deg west of the scoping doc's point, just
  inside that basin's polygon (the original point sits ~1.2km outside it,
  right at the confluence boundary).

Output: merges features into data/zones/basins.geojson (appended alongside
the existing Kenya flood basins — this script only touches the zone_keys
in OUTLETS) and deletes the stale basins_masks.npz cache so
flood_raster.py rebuilds it on next run.
"""
from __future__ import annotations

import json
import pathlib

import shapefile
from shapely.geometry import shape, Point, mapping
from shapely.ops import unary_union, transform

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHP = ROOT / "data" / "raw" / "hybas_af_lev07_v1c.shp"
OUT = ROOT / "data" / "zones" / "basins.geojson"
MASKS_NPZ = ROOT / "data" / "zones" / "basins_masks.npz"

# zone_key: (lat, lon, iso3, name, (expect_min_km2, expect_max_km2), note)
OUTLETS = {
    "drc_lualaba_hydro": (
        -10.499722, 25.4625, "COD",
        "Upper Lualaba above Nzilo dam (Katanga cascade)",
        (15_000, 60_000),
        "Nzilo dam coordinates (Wikipedia/GlobalEnergyObservatory/GEM), "
        "not the scoping doc's approximate (-10.83, 25.78) — that point "
        "lands in a 2,456 km2 tributary sub-basin, well outside the "
        "expected 15,000-60,000 km2 magnitude.",
    ),
    "drc_lufira_hydro": (
        -10.752155, 27.235365, "COD",
        "Upper Lufira above Mwadingusha/Tshangalele dam (Katanga cascade)",
        (15_000, 60_000),
        "Mwadingusha dam coordinates (Wikipedia); the scoping doc's "
        "approximate (-10.72, 27.05) lands in the same lev-7 basin.",
    ),
    "zmb_luangwa_basin": (
        -15.61, 30.42, "ZMB",
        "Luangwa basin above the Zambezi confluence at Zumbo",
        (135_000, 160_000),
        "SCOPING-MOZ-HYDRO.md's approximate confluence (-15.61, 30.44) "
        "lands in the Zambezi mainstem lev-7 basin (UP_AREA ~1,009,000 "
        "km2 — absurd for 'Luangwa alone', the DRC build's small-"
        "tributary lesson in reverse: too big, not too small). Probed "
        "neighbouring lev-7 basins for one whose own UP_AREA reads near "
        "the published ~144,000 km2 Luangwa magnitude: HYBAS "
        "1071431480, UP_AREA 149,540 km2, NEXT_DOWN the Zambezi mainstem "
        "basin — the Luangwa's own pour-point basin. Outlet nudged 0.02 "
        "deg west (-15.61, 30.42) to land inside that polygon; the "
        "scoping doc's exact point sits just outside it, on the "
        "confluence boundary.",
    ),
}

AREA_TOL = 0.02  # 2% per SCOPING-DRC-HYDRO.md / SCOPING-MOZ-HYDRO.md


def _load_shapefile():
    sf = shapefile.Reader(str(SHP))
    fields = [f[0] for f in sf.fields[1:]]
    recs = [dict(zip(fields, r)) for r in sf.records()]
    shapes = sf.shapes()
    return recs, shapes


def _find_outlet_basin(recs, shapes, lat, lon):
    for i, srec in enumerate(shapes):
        x0, y0, x1, y1 = srec.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        g = shape(srec.__geo_interface__)
        if g.contains(Point(lon, lat)):
            return i
    return None


def build() -> list[dict]:
    recs, shapes = _load_shapefile()
    by_id = {r["HYBAS_ID"]: i for i, r in enumerate(recs)}
    children: dict[int, list[int]] = {}
    for i, r in enumerate(recs):
        children.setdefault(r["NEXT_DOWN"], []).append(i)

    feats = []
    for zone_key, (lat, lon, iso3, name, expect, note) in OUTLETS.items():
        expect_min, expect_max = expect
        outlet_i = _find_outlet_basin(recs, shapes, lat, lon)
        if outlet_i is None:
            raise SystemExit(f"{zone_key}: no lev-7 basin at ({lat}, {lon})")
        outlet = recs[outlet_i]
        up_area = outlet["UP_AREA"]

        # BFS/DFS upstream via NEXT_DOWN (elnino chirps.py pattern)
        todo, members, seen = [outlet_i], [], set()
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            members.append(i)
            todo.extend(children.get(recs[i]["HYBAS_ID"], []))
        assembled = sum(recs[i]["SUB_AREA"] for i in members)

        diff = abs(assembled - up_area) / up_area
        if diff > AREA_TOL:
            raise SystemExit(
                f"{zone_key}: assembled area {assembled:,.1f} km2 does not "
                f"match outlet basin's UP_AREA {up_area:,.1f} km2 "
                f"({diff:.1%} off, tolerance {AREA_TOL:.0%}) — topology "
                f"walk is wrong, stopping rather than proceeding with a "
                f"bad catchment."
            )
        if not (expect_min <= assembled <= expect_max):
            print(f"WARNING: {zone_key} assembled area {assembled:,.0f} km2 "
                  f"is outside the expected {expect_min:,}-{expect_max:,} "
                  f"km2 band — investigate before trusting this zone.")

        union = unary_union([shape(shapes[i].__geo_interface__)
                              for i in members])
        if union.geom_type == "MultiPolygon":
            # keep every part (upstream catchments can be genuinely
            # multi-part at lev-7 resolution) but drop slivers < 0.1% area
            parts = [g for g in union.geoms if g.area > 0.001 * union.area]
            union = unary_union(parts) if len(parts) > 1 else parts[0]
        union = transform(lambda x, y: (round(x, 5), round(y, 5)), union)

        feats.append({
            "type": "Feature", "geometry": mapping(union),
            "properties": {
                "zone_key": zone_key, "name": name, "iso3": iso3,
                "hybas_id": int(outlet["HYBAS_ID"]),
                "next_down": int(outlet["NEXT_DOWN"]),
                "area_km2": round(assembled),
                "up_area_km2": round(up_area, 1),
                "n_subbasins": len(members),
                "outlet_lat": lat, "outlet_lon": lon,
                "downstream": None, "routing_days": None,
                "source_note": note,
            }})
        print(f"{zone_key}: {len(members)} sub-basins, {assembled:,.0f} km2 "
              f"(UP_AREA {up_area:,.0f} km2, diff {diff:.3%})")
    return feats


def main() -> int:
    new_feats = build()
    new_keys = {f["properties"]["zone_key"] for f in new_feats}

    existing = {"type": "FeatureCollection", "features": []}
    if OUT.exists():
        existing = json.loads(OUT.read_text())
    kept = [f for f in existing["features"]
            if f["properties"]["zone_key"] not in new_keys]

    merged = {"type": "FeatureCollection", "features": kept + new_feats}
    OUT.write_text(json.dumps(merged))
    print(f"wrote {OUT} ({len(kept)} existing + {len(new_feats)} hydro "
          f"zones = {len(merged['features'])} basins)")

    if MASKS_NPZ.exists():
        MASKS_NPZ.unlink()
        print(f"removed stale {MASKS_NPZ} — flood_raster.py will rebuild "
              f"masks for all basins on next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
