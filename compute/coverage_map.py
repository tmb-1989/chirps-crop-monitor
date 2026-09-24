"""Coverage map: crop zones (colored by crop), flood basins, cities.

One panel per monitored country. Season calendars are printed as a
caption line under each panel title (not on the polygons — V2.9).
Country outlines: Natural Earth 50m (data/raw/ne_countries.geojson).

Usage: python compute/coverage_map.py [out.png]
"""
from __future__ import annotations

import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from shapely.geometry import shape  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
import db  # noqa: E402

NAMES = {"KEN": "Kenya", "ETH": "Ethiopia", "TZA": "Tanzania",
         "RWA": "Rwanda", "UGA": "Uganda", "ZMB": "Zambia",
         "MWI": "Malawi", "ZWE": "Zimbabwe", "MOZ": "Mozambique",
         "MDG": "Madagascar", "ZAF": "South Africa"}
ORDER = list(NAMES)
COMPOSITES = {"ken_grain_basket", "zmb_maize_belt", "zaf_maize_triangle"}
CROP_STYLE = {"maize": ("#2e8b57", "#1d5c39"),
              "small grains": ("#c9a227", "#8a6d14"),
              "sorghum": ("#b0653a", "#7d431f")}
MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
       "Sep", "Oct", "Nov", "Dec"]

# (name, lat, lon, capital, label_dx, label_dy) — offsets in points,
# hand-set where labels collided with zone/city text (V2.9)
CITIES = {
 "KEN": [("Nairobi", -1.29, 36.82, 1, 4, 3), ("Mombasa", -4.05, 39.67, 0, 4, 3),
         ("Kisumu", -0.10, 34.76, 0, -2, -9), ("Eldoret", 0.52, 35.27, 0, 5, 5),
         ("Garissa", -0.45, 39.64, 0, 4, 3)],
 "ETH": [("Addis Ababa", 9.03, 38.74, 1, 5, -9), ("Dire Dawa", 9.59, 41.87, 0, 4, 5),
         ("Bahir Dar", 11.59, 37.39, 0, 4, 3), ("Hawassa", 7.06, 38.48, 0, 4, 3),
         ("Gode", 5.95, 43.55, 0, 4, 3)],
 "TZA": [("Dodoma", -6.16, 35.75, 1, 4, 5), ("Dar es Salaam", -6.82, 39.28, 0, 4, -9),
         ("Mwanza", -2.52, 32.90, 0, 4, 3), ("Mbeya", -8.90, 33.45, 0, 4, 3),
         ("Morogoro", -6.82, 37.66, 0, -6, 6)],
 "RWA": [("Kigali", -1.94, 30.06, 1, 4, 3), ("Rubavu", -1.68, 29.26, 0, 4, 3),
         ("Nyagatare", -1.29, 30.33, 0, 4, 3), ("Huye", -2.60, 29.74, 0, 4, 3)],
 "UGA": [("Kampala", 0.35, 32.58, 1, 4, 3), ("Mbale", 1.08, 34.18, 0, 4, -9),
         ("Kasese", 0.18, 30.09, 0, 4, 3), ("Gulu", 2.77, 32.30, 0, 4, 3),
         ("Soroti", 1.71, 33.61, 0, 4, 3)],
 "ZMB": [("Lusaka", -15.39, 28.32, 1, 5, 5), ("Ndola", -12.97, 28.63, 0, 4, 3),
         ("Livingstone", -17.85, 25.86, 0, 4, 3), ("Chipata", -13.63, 32.65, 0, -46, 3)],
 "MWI": [("Lilongwe", -13.98, 33.79, 1, 5, 5), ("Blantyre", -15.79, 35.00, 0, 4, 3),
         ("Mzuzu", -11.46, 34.02, 0, 4, 3)],
 "ZWE": [("Harare", -17.83, 31.05, 1, 4, -9), ("Bulawayo", -20.16, 28.58, 0, 4, 3),
         ("Chinhoyi", -17.37, 30.19, 0, 4, 5)],
 "MOZ": [("Maputo", -25.97, 32.57, 1, 4, 3), ("Beira", -19.83, 34.85, 0, 4, 3),
         ("Quelimane", -17.88, 36.89, 0, 4, 3), ("Chimoio", -19.12, 33.48, 0, 4, -9)],
 "MDG": [("Antananarivo", -18.88, 47.51, 1, 5, 4), ("Antsirabe", -19.87, 47.03, 0, 4, -9),
         ("Toamasina", -18.15, 49.40, 0, 4, 3)],
 "ZAF": [("Pretoria", -25.75, 28.19, 1, 4, 5), ("Johannesburg", -26.20, 28.05, 0, 4, -10),
         ("Bloemfontein", -29.12, 26.21, 0, 4, -9), ("Durban", -29.86, 31.02, 0, 4, 3),
         ("Cape Town", -33.93, 18.42, 0, 4, 3)],
}


def season_txt(s: str) -> str:
    parts = []
    for p in s.split(","):
        name, ab = p.split(":")
        a, b = ab.split("-")
        parts.append(f"{name.replace('_', ' ')} {MON[int(a)]}–{MON[int(b)]}")
    return ", ".join(parts)


def draw_geom(ax, geom, **kw):
    for g in (geom.geoms if hasattr(geom, "geoms") else [geom]):
        xs, ys = g.exterior.xy
        ax.fill(xs, ys, **kw)


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "data" / "coverage_map.png"
    con = db.connect()
    zmeta = {r[0]: {"crop": (r[1] or "maize").lower(), "seasons": r[2]}
             for r in con.execute("SELECT zone_key, crop, seasons FROM zones")}
    zones = json.loads((ROOT / "data/zones/zones.geojson").read_text())
    basins = json.loads((ROOT / "data/zones/basins.geojson").read_text())
    ne = json.loads((ROOT / "data/raw/ne_countries.geojson").read_text())

    outlines = {}
    for f in ne["features"]:
        iso = f["properties"].get("ADM0_A3") or f["properties"].get("ISO_A3")
        if iso in NAMES:
            outlines[iso] = shape(f["geometry"])
    crop, flood = {}, {}
    for f in zones["features"]:
        p = f["properties"]
        if p["zone_key"] in COMPOSITES:
            continue
        crop.setdefault(p["iso3"], []).append((p["zone_key"],
                                               shape(f["geometry"])))
    for f in basins["features"]:
        flood.setdefault(f["properties"]["iso3"], []).append(
            shape(f["geometry"]))

    halo = [pe.withStroke(linewidth=2, foreground="white")]
    fig, axes = plt.subplots(3, 4, figsize=(18, 14))
    axes = axes.ravel()
    for ax, iso in zip(axes, ORDER):
        o = outlines[iso]
        geoms = o.geoms if hasattr(o, "geoms") else [o]
        main_g = max(geoms, key=lambda g: g.area)
        for g in geoms:
            if g.area < main_g.area * 0.001:
                continue
            xs, ys = g.exterior.xy
            ax.fill(xs, ys, color="#f2efe9", zorder=0)
            ax.plot(xs, ys, color="#8a8a8a", lw=0.8, zorder=3)
        minx, miny, maxx, maxy = main_g.bounds
        for g in flood.get(iso, []):
            draw_geom(ax, g, color="#3b7bbf", alpha=0.5, ec="#24568a",
                      lw=0.5, zorder=2)
        cal = []
        for zk, g in crop.get(iso, []):
            m = zmeta.get(zk, {})
            fc, ec = CROP_STYLE.get(m.get("crop"), CROP_STYLE["maize"])
            draw_geom(ax, g, color=fc, alpha=0.55, ec=ec, lw=0.5, zorder=1)
            entry = f"{m.get('crop')}: {season_txt(m.get('seasons', ''))}"
            if entry not in cal:
                cal.append(entry)
        for name, lat, lon, cap, dx, dy in CITIES[iso]:
            ax.plot(lon, lat, marker="*" if cap else "o",
                    ms=11 if cap else 5, color="#222222", mec="white",
                    mew=0.6, zorder=5)
            ax.annotate(name, (lon, lat), xytext=(dx, dy),
                        textcoords="offset points", fontsize=7.5,
                        color="#222222", zorder=5, path_effects=halo)
        nz, nb = len(crop.get(iso, [])), len(flood.get(iso, []))
        ax.set_title(f"{NAMES[iso]}  ({nz} crop zone{'s' * (nz != 1)}, "
                     f"{nb} flood basin{'s' * (nb != 1)})\n"
                     + "  ·  ".join(cal), fontsize=10)
        ax.set_xlim(minx - 0.4, maxx + 0.4)
        ax.set_ylim(miny - 0.4, maxy + 0.4)
        ax.set_aspect("equal")
        ax.axis("off")
    axes[-1].axis("off")
    axes[-1].legend(handles=[
        Patch(fc="#2e8b57", alpha=0.55, label="Crop zone — maize"),
        Patch(fc="#c9a227", alpha=0.55,
              label="Crop zone — small grains (wheat/barley)"),
        Patch(fc="#b0653a", alpha=0.55,
              label="Crop zone — sorghum (FEWS mask)"),
        Patch(fc="#3b7bbf", alpha=0.5, label="Flood basins"),
        Patch(fc="#f2efe9", ec="#8a8a8a", label="Country outline"),
        Line2D([], [], marker="*", ls="", ms=13, color="#222222",
               mec="white", label="Capital"),
        Line2D([], [], marker="o", ls="", ms=6, color="#222222",
               mec="white", label="Major city")],
        loc="center", fontsize=11, frameon=False)
    fig.suptitle("CHIRPS crop monitor — crop zones, flood basins, "
                 "major cities", fontsize=15, y=0.995)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
