"""Client for the USGS FEWS NET GeoEngine 5 timeseries API and GeoServer WFS.

Request shape reverse-engineered from the EWX Next Generation Viewer
(earlywarning.usgs.gov/fews/ewx). Key quirks:
- vector_dataset is a namespaced "workspace:layer" pair
- raster_dataset accepts multiple datasets colon-joined; the response is
  keyed by dataset name
- the statistic path segment must match the dataset suffix (data/anom/...)
- seasons is a comma-separated year list; the polygon is selected by
  point-in-polygon on lat/lon
- WFS CQL INTERSECTS uses (lat lon) axis order on this server, and the
  geometry column is `geom`
"""
from __future__ import annotations

import datetime as dt
import time
from urllib.parse import quote

import requests

from config import EWX_TS_BASE, WFS_BASE, CROPZONE_VECTOR, FIRST_SEASON

UA = {"User-Agent": "chirps-crop-monitor/0.1 (research; thaddeus.best@gmail.com)"}
TIMEOUT = 180


def fetch_timeseries(vector: str, rasters: list[str], periodicity: str,
                     statistic: str, lat: float, lon: float,
                     first_season: int = FIRST_SEASON,
                     last_season: int | None = None,
                     retries: int = 3) -> dict:
    """Return {dataset: [(granule_start, granule_end, value), ...]}.

    For large/complex polygons the API silently returns an empty body when
    too many seasons are requested at once; fall back to 15-season chunks
    and merge.
    """
    if last_season is None:
        last_season = dt.date.today().year
    result = _fetch_range(vector, rasters, periodicity, statistic, lat, lon,
                          first_season, last_season, retries)
    if any(result.values()) or last_season - first_season < 15:
        return result
    merged: dict = {}
    for a in range(first_season, last_season + 1, 15):
        b = min(a + 14, last_season)
        chunk = _fetch_range(vector, rasters, periodicity, statistic,
                             lat, lon, a, b, retries)
        for ds, rows in chunk.items():
            merged.setdefault(ds, []).extend(rows)
        time.sleep(1)
    return {ds: sorted(rows) for ds, rows in merged.items()}


def _fetch_range(vector: str, rasters: list[str], periodicity: str,
                 statistic: str, lat: float, lon: float,
                 first_season: int, last_season: int, retries: int) -> dict:
    seasons = ",".join(str(y) for y in range(first_season, last_season + 1))
    url = (
        f"{EWX_TS_BASE}/vector_dataset/{vector}"
        f"/raster_dataset/{':'.join(rasters)}"
        f"/periodicity/{periodicity}/statistic/{statistic}"
        f"/lat/{lat}/lon/{lon}/seasons/{quote(seasons, safe='')}"
        f"/zonal_stat_type/mean/mean-median/false"
    )
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            if "error" in payload:
                raise RuntimeError(f"API error: {payload['error']}")
            return _parse(payload)
        except Exception as e:  # noqa: BLE001 - retry then re-raise
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"timeseries fetch failed for {url}") from last_err


def _parse(payload: dict) -> dict:
    out = {}
    for dataset, body in payload.items():
        rows = []
        for _year, granules in (body.get("data") or {}).items():
            for g in granules:
                if g.get("value") is None:
                    continue
                rows.append((g["granule_start"], g["granule_end"],
                             float(g["value"])))
        out[dataset] = sorted(rows)
    return out


def dataset_end_dates(datasets: list[str]) -> dict:
    """granule_end per dataset from the GeoEngine catalog.

    The timeseries API silently extends a 'final' series with prelim and
    CHIRPS-GEFS forecast granules beyond the dataset's real end date; the
    catalog end date is the only way to tell them apart.
    """
    url = EWX_TS_BASE.replace("/rest/timeseries/version/5.0",
                              "/rest/version/5.0/config")
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    wanted = set(datasets)
    out = {}
    for group in r.json().values():
        if not isinstance(group, dict):
            continue
        for name, meta in group.items():
            if name in wanted and isinstance(meta, dict):
                out[name] = ((meta.get("end") or {}).get("granule_end"))
    return out


def zone_info(lat: float, lon: float) -> dict | None:
    """Resolve crop-zone attributes at a point via WFS (axis order: lat lon)."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": CROPZONE_VECTOR, "outputFormat": "application/json",
        "count": "1",
        "cql_filter": f"INTERSECTS(geom,POINT({lat} {lon}))",
        "propertyName": "FID_cropzo,CROP,ADM0_NAME,ADM1_NAME",
    }
    r = requests.get(WFS_BASE, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    feats = r.json().get("features") or []
    if not feats:
        return None
    p = feats[0]["properties"]
    return {"fid": p.get("FID_cropzo"), "crop": p.get("CROP"),
            "adm0": p.get("ADM0_NAME"), "adm1": p.get("ADM1_NAME")}
