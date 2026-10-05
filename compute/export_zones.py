"""V2.3 export-crop zones (SCOPING-V2.md).

Per (country, export crop): find the peak GAEZ+ 2015 production cell
inside the country, resolve the admin-1 unit containing it (FEWS
g2008 layer — the staple crop masks are wrong footprints for
perennials, so export zones are admin-1 by design), and emit:

  ingest/zones_export.py   EXPORT_ZONES dict (merged into ZONES) +
                           EXPORT_CROP {zone_key: crop} — config also
                           routes these to ADMIN1_VECTOR and
                           run_ingest stamps sector='export'
  appends export rows to data/econ/production_weights.csv

GAEZ crop proxies: coffee/tea/cocoa share the Stimulants raster
(separable per country: each belt is one-crop-dominated); vanilla and
cashew sit in CropsNES — both flagged approximations (SCOPING-V2.1).
Perennials get season 'year_round:1-12' and NO Ky yield function —
stress indicator only, feeding the export column, never food-CPI.

Usage: python compute/export_zones.py [--dry-run]
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
import rasterio.transform
import requests
from shapely.geometry import shape, Point

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAEZ = ROOT / "data" / "raw" / "gaez2015"
NE = ROOT / "data" / "raw" / "ne_countries.geojson"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}
WFS = "https://edcintl.cr.usgs.gov/geoserver/wfs"
ADMIN1 = "fews_shapefile_g2008_af_1:shapefile_g2008_af_1"

# (iso3, crop, GAEZ production raster, curated anchor or None).
# Peak-finding is used ONLY where the proxy raster is dominated by the
# target crop (ETH/RWA coffee, ZWE tobacco). Everywhere else the
# 24 Sep dry-run showed the proxy peak lands on the wrong belt (KEN
# "coffee" grabbed Kericho tea; "vanilla" grabbed Antananarivo
# vegetables; cashew peaks leaked across borders) — those get curated
# anchors from the known belts; the raster then only supplies the
# belt-share estimate around the anchor.
TARGETS = [
    ("ETH", "coffee", "Stimulants", None),            # Jimma/Wellega peak
    ("UGA", "coffee", "Stimulants", (-0.45, 31.75)),  # Masaka robusta
    ("RWA", "coffee", "Stimulants", None),            # Kivu shore peak
    ("KEN", "coffee", "Stimulants", (-0.42, 36.95)),  # Nyeri/central
    ("KEN", "tea", "Stimulants", (-0.37, 35.28)),     # Kericho
    ("MWI", "tea", "Stimulants", (-16.07, 35.14)),    # Thyolo
    ("ZWE", "tobacco", "Tobacco", None),              # Mashonaland peak
    ("MWI", "tobacco", "Tobacco", (-13.03, 33.48)),   # Kasungu
    ("TZA", "tobacco", "Tobacco", (-5.07, 32.06)),    # Urambo/Tabora
    ("MOZ", "tobacco", "Tobacco", (-14.75, 34.35)),   # Angonia/Tete
    ("MDG", "vanilla", "CropsNES", (-14.27, 50.16)),  # Sambava/SAVA
    ("CIV", "cocoa", "Stimulants", None),             # SW belt peak
    ("GHA", "cocoa", "Stimulants", None),             # Ashanti/Western peak
    ("NGA", "cocoa", "Stimulants", (7.10, 5.05)),     # Ondo State
    ("TZA", "cashew", "CropsNES", (-10.55, 39.90)),   # Mtwara
    ("MOZ", "cashew", "CropsNES", (-15.10, 39.30)),   # Nampula
    ("BEN", "cotton", "Cotton", None),                # Alibori/Banikoara peak
]
EXCLUSION_RADIUS_CELLS = 12   # ~1 degree at 5 arcmin


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:20]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ne = json.loads(NE.read_text())
    outlines = {}
    for f in ne["features"]:
        iso = f["properties"].get("ADM0_A3") or f["properties"].get("ISO_A3")
        outlines[iso] = shape(f["geometry"])

    rasters = {}
    for name in {r for _, _, r, _ in TARGETS}:
        with rasterio.open(GAEZ / f"Production_{name}_Total.tif") as src:
            rasters[name] = (src.read(1).copy(), src.transform)

    zones, weights = {}, []
    for iso, crop, rname, anchor in TARGETS:
        arr, tr = rasters[rname]
        # country mask WITHOUT all_touched: edge cells whose centers sit
        # across the border leaked Burundi/Zimbabwe peaks in the dry-run
        cmask = rasterio.features.geometry_mask(
            [outlines[iso].__geo_interface__], out_shape=arr.shape,
            transform=tr, invert=True, all_touched=False)
        vals = np.where(cmask & np.isfinite(arr) & (arr > 0), arr, 0)
        if vals.max() <= 0:
            print(f"{iso} {crop}: no {rname} production found — skipped")
            continue
        if anchor is not None:
            lat, lon = anchor
            i, j = rasterio.transform.rowcol(tr, lon, lat)
        else:
            i, j = np.unravel_index(np.argmax(vals), vals.shape)
            lon, lat = rasterio.transform.xy(tr, i, j)
        nat = float(vals.sum())
        r0 = EXCLUSION_RADIUS_CELLS
        belt = float(vals[max(0, i - r0):i + r0,
                          max(0, j - r0):j + r0].sum())
        share = belt / nat if nat else 0
        # resolve admin-1 name for the label
        try:
            r = requests.get(WFS, params={
                "service": "WFS", "version": "2.0.0",
                "request": "GetFeature", "typeNames": ADMIN1,
                "outputFormat": "application/json", "count": "1",
                "cql_filter": f"INTERSECTS(geom,POINT({lat} {lon}))",
                "propertyName": "ADM1_NAME,ADM0_NAME"},
                headers=UA, timeout=120)
            p = (r.json().get("features") or [{}])[0].get("properties", {})
            adm1 = p.get("ADM1_NAME") or "unknown"
        except Exception:  # noqa: BLE001
            adm1 = "unknown"
        zk = f"{iso.lower()}_{slug(crop)}"
        zones[zk] = (iso, f"{adm1} {crop} belt (export)",
                     float(round(lat, 4)), float(round(lon, 4)),
                     [("year_round", 1, 12)])
        weights.append((zk, iso, crop.capitalize(), round(share, 3),
                        f"GAEZ {rname} belt share; admin-1 {adm1}; "
                        "proxy raster — see SCOPING-V2.3"))
        print(f"{zk:16s} {adm1:18s} peak ({lat:.2f},{lon:.2f}) "
              f"belt {share:5.1%} of national {rname}")

    if args.dry_run:
        return 0
    gen = ["# GENERATED by compute/export_zones.py — do not hand-edit.",
           "EXPORT_ZONES = {"]
    for zk, v in zones.items():
        gen.append(f'    "{zk}": {v!r},')
    gen.append("}")
    gen.append("EXPORT_CROP = {")
    for zk, iso, crop, *_ in weights:
        gen.append(f'    "{zk}": "{crop.lower()}",')
    gen.append("}")
    (ROOT / "ingest" / "zones_export.py").write_text("\n".join(gen) + "\n")
    wcsv = ROOT / "data/econ/production_weights.csv"
    keep = [l for l in wcsv.read_text().splitlines()
            if not any(l.startswith(zk + ",") for zk in zones)]
    with open(wcsv, "w", newline="") as f:
        f.write("\n".join(keep) + "\n")
        w = csv.writer(f)
        for row in weights:
            w.writerow(row)
    print(f"\nwrote ingest/zones_export.py ({len(zones)} zones); "
          "weights appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
