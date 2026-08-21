"""Local CHIRPS v3 dekadal raster pipeline (Phase 2).

Streams dekad GeoTIFFs from data.chc.ucsb.edu, computes zonal means over
the zones in data/zones/zones.geojson, and stores them in the same
observations table as the EWX ingest under datasets:
    chirps3local_dekad_data          (final, Africa subdomain)
    chirps3local-prelim_dekad_data   (prelim, global file windowed to Africa)

Zone pixel masks are rasterized once against the Africa grid and cached in
data/zones/masks.npz. Raster files are deleted after processing except a
rolling cache of the most recent KEEP_DEKADS dekads.

Usage:
    python chirps_raster.py             # incremental: last 2 years + prelim
    python chirps_raster.py --full      # backfill 1981-present
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import pathlib
import re
import sys
import time

import numpy as np
import rasterio
import rasterio.features
import rasterio.windows
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0"
FINAL_DIR = f"{BASE}/dekads/africa/tifs"
PRELIM_DIR = f"{BASE}/prelim/dekads/global/tifs"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}

ROOT = pathlib.Path(__file__).resolve().parent.parent
ZONES_GJ = ROOT / "data" / "zones" / "zones.geojson"
MASKS_NPZ = ROOT / "data" / "zones" / "masks.npz"
CACHE = ROOT / "data" / "rasters"
KEEP_DEKADS = 72  # rolling on-disk cache (~2 years)

DS_FINAL = "chirps3local_dekad_data"
DS_PRELIM = "chirps3local-prelim_dekad_data"


def list_remote(dir_url: str) -> list[str]:
    r = requests.get(dir_url + "/", headers=UA, timeout=120)
    r.raise_for_status()
    names = sorted(set(re.findall(r'href="(chirps-v3\.0\.\d{4}\.\d{2}\.\d\.tif)"',
                                  r.text)))
    return names


def dekad_dates(name: str) -> tuple[str, str]:
    y, m, d = map(int, re.match(r"chirps-v3\.0\.(\d{4})\.(\d{2})\.(\d)", name).groups())
    start = dt.date(y, m, {1: 1, 2: 11, 3: 21}[d])
    end = dt.date(y, m, {1: 10, 2: 20, 3: calendar.monthrange(y, m)[1]}[d])
    return start.isoformat(), end.isoformat()


def build_masks(ref_tif_bytes: bytes):
    """Rasterize every zone onto the Africa grid; cache as boolean masks."""
    import json
    gj = json.loads(ZONES_GJ.read_text())
    with rasterio.open(io.BytesIO(ref_tif_bytes)) as src:
        transform, shape_, bounds = src.transform, (src.height, src.width), src.bounds
    masks, empty = {}, []
    for f in gj["features"]:
        key = f["properties"]["zone_key"]
        m = rasterio.features.rasterize(
            [(f["geometry"], 1)], out_shape=shape_, transform=transform,
            fill=0, dtype="uint8").astype(bool)
        if not m.any():
            empty.append(key)
        masks[key] = m
    if empty:
        raise RuntimeError(f"zones rasterized to zero pixels: {empty}")
    np.savez_compressed(MASKS_NPZ, **{k: v for k, v in masks.items()},
                        __bounds__=np.array(bounds))
    print(f"masks built: {len(masks)} zones on grid {shape_}")
    return masks, bounds


def load_masks():
    z = np.load(MASKS_NPZ)
    bounds = tuple(z["__bounds__"])
    return {k: z[k] for k in z.files if k != "__bounds__"}, bounds


def fetch(url: str, retries: int = 3) -> bytes:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=300)
            if r.status_code == 404:
                return b""
            if r.status_code == 403:
                # CrowdSec ban — retrying only prolongs it; bail out and
                # resume after the ban expires
                raise SystemExit(f"403 CrowdSec ban from CHC at {url}; "
                                 "wait for the ban to lift and re-run")
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"download failed: {url}") from last


def zonal_means(tif_bytes: bytes, masks: dict, window_bounds=None) -> dict:
    with rasterio.open(io.BytesIO(tif_bytes)) as src:
        if window_bounds is not None:  # global prelim -> window to Africa grid
            win = rasterio.windows.from_bounds(*window_bounds,
                                               transform=src.transform)
            win = win.round_offsets().round_lengths()
            arr = src.read(1, window=win)
        else:
            arr = src.read(1)
        nodata = src.nodata if src.nodata is not None else -9999.0
    out = {}
    for key, m in masks.items():
        if m.shape != arr.shape:
            raise RuntimeError(f"grid mismatch for {key}: {m.shape} vs {arr.shape}")
        vals = arr[m]
        vals = vals[(vals != nodata) & np.isfinite(vals)]
        out[key] = float(vals.mean()) if vals.size else None
    return out


def process(names: list[str], dir_url: str, dataset: str, masks: dict,
            con, window_bounds=None, skip_existing=True) -> int:
    from concurrent.futures import ThreadPoolExecutor

    done = 0
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT granule_start FROM observations WHERE dataset=?",
        (dataset,))} if skip_existing else set()
    todo = [n for n in names if dekad_dates(n)[0] not in have]

    def get(name: str) -> bytes:
        cached = CACHE / dataset / name
        if cached.exists():
            return cached.read_bytes()
        blob = fetch(f"{dir_url}/{name}")
        if blob:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(blob)
        # data.chc.ucsb.edu runs CrowdSec: parallel/burst downloads get the
        # IP banned (403 "CrowdSec Ban"). Stay slow and sequential.
        time.sleep(1.0)
        return blob

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i in range(0, len(todo), 60):
            batch = todo[i:i + 60]
            for name, blob in zip(batch, pool.map(get, batch)):
                if not blob:
                    print(f"  missing remote: {name}", file=sys.stderr)
                    continue
                start, end = dekad_dates(name)
                means = zonal_means(blob, masks, window_bounds)
                for zone_key, v in means.items():
                    if v is not None:
                        db.upsert_observations(con, zone_key, dataset,
                                               [(start, end, v)])
                con.commit()
                done += 1
                if done % 100 == 0:
                    print(f"  {dataset}: {done} dekads processed "
                          f"(through {start})")
            # keep the temp cache bounded during backfill
            files = sorted((CACHE / dataset).glob("*.tif"))
            for f in files[:-KEEP_DEKADS]:
                f.unlink()
    return done


def prune_cache():
    for sub in CACHE.iterdir() if CACHE.exists() else []:
        if not sub.is_dir():
            continue
        files = sorted(sub.glob("*.tif"))
        for f in files[:-KEEP_DEKADS]:
            f.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    finals = list_remote(FINAL_DIR)
    prelims = list_remote(PRELIM_DIR)
    if not args.full:
        cutoff = f"chirps-v3.0.{dt.date.today().year - 2}"
        finals = [n for n in finals if n >= cutoff]
        prelims = [n for n in prelims if n >= cutoff]
    # prelim: only dekads not yet in final
    prelims = [n for n in prelims if n not in set(finals)]

    con = db.connect()
    if MASKS_NPZ.exists():
        masks, bounds = load_masks()
    else:
        ref = fetch(f"{FINAL_DIR}/{finals[-1]}")
        masks, bounds = build_masks(ref)

    n1 = process(finals, FINAL_DIR, DS_FINAL, masks, con)
    n2 = process(prelims, PRELIM_DIR, DS_PRELIM, masks, con,
                 window_bounds=bounds, skip_existing=False)
    prune_cache()
    con.close()
    print(f"processed {n1} final + {n2} prelim dekads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
