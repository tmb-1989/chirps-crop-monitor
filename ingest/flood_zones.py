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
    "bas_wami":      (-6.83, 36.98, "Wami/Mkondoa at Kilosa", "TZA"),
    "bas_lindi":     (-9.90, 39.40, "Lindi/Mtwara coast", "TZA"),
    # --- Rwanda ---
    "bas_nyabarongo": (-1.95, 30.00, "Nyabarongo (Kigali)", "RWA"),
    "bas_sebeya":    (-1.65, 29.30, "Sebeya / NW highlands (Rubavu)", "RWA"),
    "bas_akanyaru":  (-2.45, 29.90, "Akanyaru / Bugesera south", "RWA"),
    # NB: the Musanze/Gakenke landslide belt (Mukungwa) falls inside the
    # bas_nyabarongo level-7 polygon — already covered, no separate basin.
    "bas_rusizi":    (-2.65, 29.02, "Rusizi / Bugarama plain", "RWA"),
    # --- Uganda ---
    "bas_elgon":     (0.95, 34.30, "Mt Elgon west (Manafwa/Bududa)", "UGA"),
    "bas_kampala":   (0.35, 32.60, "Kampala / Lake Victoria north", "UGA"),
    "bas_nyamwamba": (0.18, 30.10, "Nyamwamba / Rwenzori east (Kasese)",
                      "UGA"),
    "bas_kyoga":     (1.65, 33.55, "Lake Kyoga lowlands (Teso)", "UGA"),
    # NB: Mbale (Nabuyonga/Namatala) shares the bas_elgon level-7 polygon —
    # already covered; the 2022 miss was signature, not coverage.
    "bas_semliki":   (1.00, 30.45, "Semliki flats (Ntoroko)", "UGA"),
    # --- P6 extension: southern Africa (cyclone-season floods) ---
    "bas_limpopo_low": (-24.53, 32.98, "Lower Limpopo (Chokwe/Xai-Xai)",
                        "MOZ"),
    "bas_zambezi_delta": (-17.83, 35.33, "Lower Zambezi (Caia/Marromeu)",
                          "MOZ"),
    "bas_pungwe":    (-19.30, 34.20, "Pungwe (Nhamatanda/Beira hinterland)",
                      "MOZ"),
    "bas_buzi":      (-20.00, 33.90, "Buzi valley", "MOZ"),
    "bas_incomati":  (-25.05, 32.80, "Incomati (Magude/Xinavane)", "MOZ"),
    "bas_licungo":   (-16.84, 36.99, "Licungo at Mocuba", "MOZ"),
    "bas_shire_low": (-16.40, 34.95, "Lower Shire (Chikwawa/Nsanje)",
                      "MWI"),
    "bas_karonga":   (-9.93, 33.93, "Karonga lakeshore", "MWI"),
    "bas_phalombe":  (-15.80, 35.65, "Phalombe plain / Mulanje foothills",
                      "MWI"),
    "bas_ikopa":     (-18.90, 47.52, "Ikopa / Antananarivo plain", "MDG"),
    "bas_betsiboka_low": (-16.10, 46.63, "Lower Betsiboka (Marovoay)",
                          "MDG"),
    "bas_sava":      (-14.88, 50.00, "SAVA coast (Sambava/Antalaha)",
                      "MDG"),
    "bas_mananjary": (-21.22, 48.33, "Mananjary coast (cyclone belt)",
                      "MDG"),
    "bas_barotse":   (-15.25, 23.00, "Barotse floodplain (Mongu)", "ZMB"),
    "bas_lusaka":    (-15.42, 28.28, "Lusaka urban", "ZMB"),
    "bas_kafue_flats": (-15.75, 27.40, "Kafue flats (Mazabuka)", "ZMB"),
    "bas_muzarabani": (-16.40, 31.00, "Muzarabani (lower Musengezi)",
                       "ZWE"),
    "bas_tsholotsho": (-19.77, 27.76, "Gwayi/Tsholotsho", "ZWE"),
    "bas_chimanimani": (-19.80, 32.87, "Chimanimani (eastern highlands)",
                        "ZWE"),
    "bas_kzn_coast": (-29.80, 30.90, "KwaZulu-Natal coast (Mgeni/Durban)",
                      "ZAF"),
    "bas_luvuvhu":   (-22.75, 30.90, "Luvuvhu/Limpopo lowveld", "ZAF"),
    "bas_jukskei":   (-26.05, 28.05, "Jukskei (Johannesburg/Alexandra)",
                      "ZAF"),
    # --- P6 extension: West Africa (monsoon floods) ---
    "bas_lokoja":    (9.08, 6.73, "Niger-Benue confluence (Lokoja)",
                      "NGA"),
    "bas_benue_low": (7.73, 8.52, "Lower Benue (Makurdi)", "NGA"),
    "bas_niger_delta": (4.92, 6.26, "Lower Niger (Bayelsa/Yenagoa)",
                        "NGA"),
    "bas_hadejia":   (12.45, 10.04, "Hadejia-Jama'are (Kano NE)", "NGA"),
    "bas_maiduguri": (11.83, 13.15, "Ngadda at Maiduguri (Alau dam)",
                      "NGA"),
    "bas_white_volta": (10.90, -0.35, "White Volta (Bagre spill reach)",
                        "GHA"),
    "bas_accra":     (5.60, -0.22, "Odaw / Accra urban", "GHA"),
    "bas_lower_volta": (6.00, 0.40, "Lower Volta (Akosombo spill reach)",
                        "GHA"),
    "bas_abidjan":   (5.37, -4.01, "Abidjan lagoon catchments", "CIV"),
    "bas_bandama_low": (5.90, -4.82, "Lower Bandama (Tiassalé)", "CIV"),
    "bas_oueme_low": (6.60, 2.47, "Lower Ouémé (Cotonou plain)", "BEN"),
    "bas_niger_malanville": (11.85, 3.38, "Niger at Malanville", "BEN"),
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
                rp = g.representative_point()
                feats.append({
                    "type": "Feature", "geometry": mapping(g),
                    "properties": {
                        "zone_key": key, "name": name, "iso3": iso3,
                        "hybas_id": int(d["HYBAS_ID"]),
                        "next_down": int(d["NEXT_DOWN"]),
                        "area_km2": round(d["SUB_AREA"]),
                        "downstream": ds[0] if ds else None,
                        "routing_days": ds[1] if ds else None,
                        # app label/click anchor — the deployed app has
                        # no shapely (added post-hoc before; now built in)
                        "label_lon": round(rp.x, 4),
                        "label_lat": round(rp.y, 4),
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
