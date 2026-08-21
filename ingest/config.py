"""Monitoring zone anchors and EWX dataset definitions.

Each zone is an anchor point (lat, lon) inside a FEWS NET crop-zone polygon
(fews_shapefile_cropzones). The GeoEngine timeseries API does point-in-polygon
selection, so the anchor pins the zone; actual zone attributes (crop, admin
names) are resolved via WFS at ingest time and stored in the zones table.

Season windows are (start_month, end_month) with end < start meaning the
season crosses the calendar year (e.g. Oct-Apr southern Africa).
"""

EWX_TS_BASE = (
    "https://edcintl.cr.usgs.gov/geoengine5/rest/timeseries/version/5.0"
)
WFS_BASE = "https://edcintl.cr.usgs.gov/geoserver/wfs"
CROPZONE_VECTOR = "fews_shapefile_cropzones:shapefile_cropzones"
ADMIN1_VECTOR = "fews_shapefile_g2008_af_1:shapefile_g2008_af_1"

FIRST_SEASON = 1981

# zone_key: (country_iso3, name, lat, lon, [(season_name, start_m, end_m), ...])
ZONES = {
    "ken_uasin_gishu": ("KEN", "Kenya grain basket (Uasin Gishu)", 0.52, 35.27,
                        [("long_rains", 3, 9)]),
    "ken_trans_nzoia": ("KEN", "Kenya grain basket (Trans Nzoia)", 1.02, 35.00,
                        [("long_rains", 3, 9)]),
    # anchor must fall inside a crop-zone polygon or the API 500s;
    # this is the Oromia small-grains (wheat/barley) highland belt
    "eth_arsi":       ("ETH", "Ethiopia Oromia highlands (wheat/barley)",
                       9.6626, 39.1478,
                       [("belg", 2, 5), ("kiremt", 6, 9)]),
    "eth_gojjam":     ("ETH", "Ethiopia West Gojjam", 10.30, 37.50,
                       [("kiremt", 6, 9)]),
    # the big western Oromia maize belt (Wellega/Jimma) — Ethiopia's
    # largest crop zone; Mar-Nov matches the EWX maize WRSI window
    "eth_oromia_maize": ("ETH", "Ethiopia Oromia maize belt", 8.798, 35.223,
                         [("meher", 3, 11)]),
    "tza_mbeya":      ("TZA", "Tanzania Southern Highlands (Mbeya)", -8.90, 33.40,
                       [("msimu", 11, 5)]),
    "uga_masindi":    ("UGA", "Uganda Masindi", 1.70, 31.70,
                       [("first", 3, 6), ("second", 8, 11)]),
    # Eastern region is Uganda's main maize producer (Iganga-Mbale corridor)
    "uga_eastern":    ("UGA", "Uganda Eastern maize belt", 1.2051, 33.6634,
                       [("first", 3, 6), ("second", 8, 11)]),
    "zmb_central":    ("ZMB", "Zambia Central maize belt", -14.40, 28.40,
                       [("main", 10, 4)]),
    "zmb_southern":   ("ZMB", "Zambia Southern province", -16.80, 27.00,
                       [("main", 10, 4)]),
    "zmb_eastern":    ("ZMB", "Zambia Eastern province", -13.60, 32.60,
                       [("main", 10, 4)]),
    "mwi_lilongwe":   ("MWI", "Malawi Lilongwe plain", -14.00, 33.70,
                       [("main", 10, 4)]),
    "zwe_mash_west":  ("ZWE", "Zimbabwe Mashonaland West", -17.50, 30.20,
                       [("main", 10, 4)]),
    "moz_manica":     ("MOZ", "Mozambique Manica", -19.10, 33.50,
                       [("main", 10, 4)]),
    # Zambezia is Mozambique's largest maize producer (northern heartland)
    "moz_zambezia":   ("MOZ", "Mozambique Zambezia", -16.5095, 36.8684,
                       [("main", 10, 4)]),
    "mdg_vakinankaratra": ("MDG", "Madagascar Vakinankaratra", -19.80, 47.00,
                           [("main", 10, 4)]),
}

# zones whose crop-zone polygon has no pre-computed zonal stats on the
# server (all-null values — e.g. every Malawi crop zone) fall back to
# admin-1 boundaries
VECTOR_OVERRIDES = {
    "mwi_lilongwe": ADMIN1_VECTOR,
    "moz_zambezia": ADMIN1_VECTOR,
}

# one API request per (zone, dataset) — the API silently drops extra
# datasets when several are colon-joined in raster_dataset.
# statistic must match the dataset suffix (data/anom/zscore/pctm).
DATASETS = [
    # (raster_dataset, periodicity, statistic)
    ("chirps_global_pentad_data", "pentad", "data"),
    ("chirps-prelim_global_pentad_data", "pentad", "data"),
    ("chirps_global_month_data", "month", "data"),
    ("chirps-prelim_global_month_data", "month", "data"),
    ("chirps_global_month_anom", "month", "anom"),
    ("chirps_global_month_zscore", "month", "zscore"),
    ("lwrsi_africa_dekad_data", "dekad", "data"),
    ("lwrsi_africa_dekad_pctm", "dekad", "pctm"),
    ("soilmoisture-0-10cm_global_month_data", "month", "data"),
    ("soilmoisture-0-100cm_global_month_data", "month", "data"),
    ("soilmoisture-0-10cm_global_month_pctm", "month", "pctm"),
    ("soilmoisture-0-100cm_global_month_pctm", "month", "pctm"),
]
