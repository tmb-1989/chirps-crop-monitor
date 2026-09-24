"""V2.2 zone expansion (SCOPING-V2.md).

Per country: pull every FEWS crop-zone polygon (WFS), rank by GAEZ+
2015 staple production, and greedily add polygons until the monitored
share of national production reaches TARGET (70%), the marginal
polygon adds < MIN_GAIN (2%), or MAX_NEW zones were added. Polygons
already containing an existing zone anchor count toward the baseline.

Outputs:
  ingest/zones_auto.py            generated AUTO_ZONES dict (merged
                                  into ZONES by ingest/config.py)
  data/econ/production_weights.csv  measured GAEZ shares, all zones

The staple raster per country: Rice for MDG, Maize elsewhere. Season
calendars for new zones follow the country defaults below; Kenya and
northern Tanzania get bimodal calendars (the audit showed the missing
production sits in bimodal areas).

Usage: python compute/zone_expand.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

import numpy as np
import rasterio
import rasterio.features
import requests
from shapely.geometry import shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAEZ = ROOT / "data" / "raw" / "gaez2015"
NE = ROOT / "data" / "raw" / "ne_countries.geojson"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}
WFS = "https://edcintl.cr.usgs.gov/geoserver/wfs"
LAYER = "fews_shapefile_cropzones:shapefile_cropzones"

TARGET, MIN_GAIN, MAX_NEW = 0.70, 0.02, 8

ADM0 = {"KEN": "Kenya", "ETH": "Ethiopia",
        "TZA": "United Republic of Tanzania", "RWA": "Rwanda",
        "UGA": "Uganda", "ZMB": "Zambia", "MWI": "Malawi",
        "ZWE": "Zimbabwe", "MOZ": "Mozambique", "MDG": "Madagascar",
        "ZAF": "South Africa"}
STAPLE = {"MDG": "Rice"}          # default Maize
# season calendar for NEW zones (existing zones keep theirs)
SEASONS = {"KEN": "long_rains:3-9,short_rains:10-1",
           "ETH": "meher:3-11",
           "RWA": "season_a:9-1,season_b:2-6",
           "UGA": "first:3-6,second:8-11",
           "ZMB": "main:10-4", "MWI": "main:10-4", "ZWE": "main:10-4",
           "MOZ": "main:10-4", "MDG": "main:10-4", "ZAF": "main:10-4"}


def tza_seasons(lat: float) -> str:
    """Southern Tanzania is unimodal (msimu); the north is bimodal."""
    return "msimu:11-5" if lat <= -6 else "masika:3-6,vuli:10-1"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24]


def grid_sum(arr, transform, geom) -> float:
    mask = rasterio.features.geometry_mask(
        [geom.__geo_interface__], out_shape=arr.shape,
        transform=transform, invert=True, all_touched=True)
    v = arr[mask]
    return float(np.nansum(np.where(v > 0, v, 0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / "ingest"))
    import db
    from config import ZONES
    con = db.connect()
    zcrop = {r[0]: (r[1] or "maize").lower() for r in
             con.execute("SELECT zone_key, crop FROM zones")}

    ne = json.loads(NE.read_text())
    outlines = {}
    for f in ne["features"]:
        iso = f["properties"].get("ADM0_A3") or f["properties"].get("ISO_A3")
        outlines[iso] = shape(f["geometry"])

    rasters = {}
    for name in {"Maize", "Rice", "Wheat", "Sorghum"}:
        with rasterio.open(GAEZ / f"Production_{name}_Total.tif") as src:
            rasters[name] = (src.read(1), src.transform)

    auto, weight_rows = {}, []
    for iso, adm0 in ADM0.items():
        staple = STAPLE.get(iso, "Maize")
        arr, tr = rasters[staple]
        nat = grid_sum(arr, tr, outlines[iso])
        r = requests.get(WFS, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": LAYER, "outputFormat": "application/json",
            "cql_filter": f"ADM0_NAME='{adm0}'"}, headers=UA, timeout=300)
        feats = r.json().get("features", [])
        polys = []
        for f in feats:
            g = shape(f["geometry"])
            polys.append({"g": g, "p": f["properties"],
                          "prod": grid_sum(arr, tr, g)})
        polys.sort(key=lambda x: -x["prod"])

        # existing anchors -> polygons already monitored
        anchors = [(zk, v[2], v[3]) for zk, v in ZONES.items()
                   if v[0] == iso]
        from shapely.geometry import Point
        covered_ids, base = set(), 0.0
        for pl in polys:
            if any(pl["g"].contains(Point(lon, lat))
                   for _, lat, lon in anchors):
                covered_ids.add(id(pl))
                base += pl["prod"] / nat if nat else 0
        cum, added = base, []
        for pl in polys:
            if id(pl) in covered_ids or cum >= TARGET or \
                    len(added) >= MAX_NEW:
                continue
            gain = pl["prod"] / nat if nat else 0
            if gain < MIN_GAIN:
                break
            added.append(pl)
            cum += gain
        print(f"{iso} ({staple}): base {base:.0%} -> {cum:.0%} with "
              f"{len(added)} new zone(s)"
              + ("" if cum >= TARGET else "  [target not reachable: "
                 "remaining polygons each < 2%]"))
        for pl in added:
            adm1 = pl["p"].get("ADM1_NAME") or f"z{pl['p'].get('ZONE_ID')}"
            zk = f"{iso.lower()}_{slug(adm1)}"
            n = 2
            while zk in ZONES or zk in auto:
                zk = f"{iso.lower()}_{slug(adm1)}{n}"
                n += 1
            pt = pl["g"].representative_point()
            seas = tza_seasons(pt.y) if iso == "TZA" else SEASONS[iso]
            auto[zk] = (iso, f"{ADM0[iso].split(' ')[-1]} {adm1} (auto)",
                        round(pt.y, 4), round(pt.x, 4), seas)
            share = pl["prod"] / nat if nat else 0
            weight_rows.append((zk, iso, staple, round(share, 3),
                                "GAEZ+2015 measured; auto-added V2.2"))
            print(f"   + {zk:26s} {share:5.1%}  {adm1}")

        # measured weights for existing zones (from zones.geojson)
        zones_gj = json.loads((ROOT / "data/zones/zones.geojson").read_text())
        for f in zones_gj["features"]:
            p = f["properties"]
            if p["iso3"] != iso or p["zone_key"] in (
                    "ken_grain_basket", "zmb_maize_belt",
                    "zaf_maize_triangle"):
                continue
            crop = zcrop.get(p["zone_key"], "maize")
            rname = {"maize": "Maize", "small grains": "Wheat",
                     "sorghum": "Sorghum"}.get(crop, staple)
            a2, t2 = rasters[rname]
            nat2 = grid_sum(a2, t2, outlines[iso])
            share = grid_sum(a2, t2, shape(f["geometry"])) / nat2 \
                if nat2 else 0
            weight_rows.append((p["zone_key"], iso,
                                rname, round(share, 3),
                                "GAEZ+2015 measured"))

    if args.dry_run:
        print(f"\ndry run: {len(auto)} zones would be added")
        return 0

    gen = ["# GENERATED by compute/zone_expand.py — do not hand-edit.",
           "# Regenerate after changing targets or the production grid.",
           "AUTO_ZONES = {"]
    for zk, (iso, name, lat, lon, seas) in auto.items():
        seasons = [(n, int(a), int(b)) for n, ab in
                   (p.split(":") for p in seas.split(","))
                   for a, b in [ab.split("-")]]
        gen.append(f'    "{zk}": ("{iso}", "{name}", {lat}, {lon}, '
                   f'{seasons}),')
    gen.append("}")
    (ROOT / "ingest" / "zones_auto.py").write_text("\n".join(gen) + "\n")

    with open(ROOT / "data/econ/production_weights.csv", "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone_key", "iso3", "commodity", "share_national",
                    "note"])
        seen = set()
        for row in weight_rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            w.writerow(row)
    print(f"\nwrote ingest/zones_auto.py ({len(auto)} zones) and "
          f"production_weights.csv ({len(seen)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
