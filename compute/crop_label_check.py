"""Crop-label mismatch check: FEWS NET polygon crop vs modelled commodity.

The Landscape WRSI models one generic crop per pixel, so the WRSI value
does not depend on the crop; only Ky in cpi_impulse.leg_a does. The
commodity in data/econ/production_weights.csv follows GAEZ production
(what is actually grown and priced), which can differ from the FEWS NET
polygon label — e.g. a 'sorghum' polygon where GAEZ says maize dominates.
That is not necessarily wrong, but the zone's shock then carries the
modelled commodity's Ky, so the mismatches are listed here with the Ky
ratio they imply.

Read-only. Usage: python compute/crop_label_check.py [--app-db]
"""
from __future__ import annotations

import csv
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "compute"))
import db  # noqa: E402
from cpi_impulse import KY  # noqa: E402

# FEWS NET polygon label -> the commodity it implies (as production_audit)
LABEL_COMMODITY = {"maize": "Maize", "small grains": "Wheat",
                   "sorghum": "Sorghum", "rice": "Rice"}


def mismatches() -> list[dict]:
    # the full archive on the mini; dev checkouts read the committed
    # app.sqlite (same zones table) — as the app does
    path = db.DB_PATH if db.DB_PATH.exists() and len(sys.argv) < 2 \
        else db.DB_PATH.parent / "app.sqlite"
    con = sqlite3.connect(path)
    crop = dict(con.execute("SELECT zone_key, crop FROM zones"))
    con.close()
    out = []
    with open(ROOT / "data" / "econ" / "production_weights.csv") as fh:
        for r in csv.DictReader(fh):
            if r["commodity"] not in KY:
                continue  # export belts: no WRSI, no Ky
            label = crop.get(r["zone_key"])
            implied = LABEL_COMMODITY.get((label or "").strip().lower())
            if implied == r["commodity"]:
                continue
            ky_m = KY.get(r["commodity"], 1.25)
            ky_l = KY.get(implied) if implied else None
            out.append({**r, "polygon_label": label or "(none)",
                        "ky_model": ky_m, "ky_label": ky_l,
                        "ky_ratio": None if not ky_l else ky_m / ky_l})
    return out


def main() -> int:
    rows = mismatches()
    print(f"{len(rows)} zones where the FEWS NET polygon label differs "
          "from the modelled commodity:\n")
    print(f"{'zone':22} {'label':13} {'commodity':10} {'share':>6} "
          f"{'Ky':>5} {'Ky(label)':>9} {'shock x':>8}")
    for r in sorted(rows, key=lambda r: (r["iso3"], r["zone_key"])):
        kl = "—" if r["ky_label"] is None else f"{r['ky_label']:.2f}"
        kr = "—" if r["ky_ratio"] is None else f"{r['ky_ratio']:.2f}"
        print(f"{r['zone_key']:22} {r['polygon_label']:13} "
              f"{r['commodity']:10} {float(r['share_national']):6.3f} "
              f"{r['ky_model']:5.2f} {kl:>9} {kr:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
