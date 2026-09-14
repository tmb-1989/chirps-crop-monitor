"""SEAS5 hindcast skill backtest — Phase S1 of the SEAS5 scoping.

Fetches ECMWF SEAS5 hindcasts (1993-2016, 25 members, leads 1-6) for a set
of init months over one continental Africa box, scores ensemble-median
skill against the monitor's own CHIRPS zonal series for every
(zone, season, init-month) combination in ingest/config.py ZONES, and
writes output/seas5_skill.csv + SEAS5-SKILL.md.

Ported from elnino-hydro-dashboard/ingest/seas5.py and seas5_skill.py --
same metric definitions (Pearson r, tercile hit rate via pd.qcut,
driest-8 overlap) so results are comparable to that dashboard's numbers.

Usage:
    python3 seas5_backtest.py fetch     # sequential CDS hindcast fetch (slow)
    python3 seas5_backtest.py verify    # build output/seas5_verification.csv from db
    python3 seas5_backtest.py score     # score + write outputs (needs fetch+verify done)
    python3 seas5_backtest.py all       # all three, in order
"""
from __future__ import annotations

import calendar
import json
import pathlib
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ingest"))
from config import ZONES  # noqa: E402

DB = ROOT / "db" / "monitor.sqlite"
CACHE = ROOT / "data" / "cache" / "seas5"
OUT = ROOT / "output"
MASKS = ROOT / "data" / "zones" / "masks.npz"

DATASET = "seasonal-monthly-single-levels"
HINDCAST_YEARS = [str(y) for y in range(1993, 2017)]
INIT_MONTHS = [2, 5, 6, 7, 8, 9, 10, 11]
AREA = [12, 10, -35, 52]  # N, W, S, E
VERIFY_YEARS = range(1993, 2018)  # covers season-year 2016 wrap into 2017

GATE_TERCILE = 0.45
GATE_R = 0.35


# --------------------------------------------------------------------------
# zone geometry: bounding boxes from data/zones/masks.npz
# --------------------------------------------------------------------------

def zone_bboxes() -> dict:
    """zone_key -> (south, north, west, east) lat/lon bbox from the mask."""
    d = np.load(MASKS)
    left, bottom, right, top = d["__bounds__"]
    H, W = d[list(ZONES.keys())[0]].shape
    res_lat = (top - bottom) / H
    res_lon = (right - left) / W
    out = {}
    for zk in ZONES:
        mask = d[zk]
        rows, cols = np.where(mask)
        if len(rows) == 0:
            raise ValueError(f"{zk}: empty mask")
        south = (top - (rows + 1) * res_lat).min()
        north = (top - rows * res_lat).max()
        west = (left + cols * res_lon).min()
        east = (left + (cols + 1) * res_lon).max()
        out[zk] = (float(south), float(north), float(west), float(east))
    return out


# --------------------------------------------------------------------------
# Step 3: hindcast fetch
# --------------------------------------------------------------------------

def hindcast_path(init_month: int) -> pathlib.Path:
    return CACHE / f"hindcast_af_m{init_month:02d}.nc"


def _retrieve(path: pathlib.Path, init_month: int):
    import cdsapi
    req = {
        "originating_centre": "ecmwf",
        "system": "51",
        "variable": "total_precipitation",
        "product_type": "monthly_mean",
        "year": HINDCAST_YEARS,
        "month": f"{init_month:02d}",
        "leadtime_month": ["1", "2", "3", "4", "5", "6"],
        "area": AREA,
        "data_format": "netcdf",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.nc")
    cdsapi.Client(quiet=True).retrieve(DATASET, req, str(tmp))
    tmp.rename(path)


def fetch_all():
    results = {}
    for m in INIT_MONTHS:
        path = hindcast_path(m)
        if path.exists() and path.stat().st_size > 0:
            print(f"[{time.strftime('%H:%M:%S')}] init {m:02d}: cached ({path.stat().st_size} bytes), skipping")
            results[m] = "cached"
            continue
        print(f"[{time.strftime('%H:%M:%S')}] init {m:02d}: requesting from CDS (may queue for a while)...")
        ok = False
        last_exc = None
        for attempt in (1, 2):
            try:
                _retrieve(path, m)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                print(f"[{time.strftime('%H:%M:%S')}] init {m:02d}: attempt {attempt} failed: {exc}")
                if attempt == 1:
                    time.sleep(30)
        if ok:
            print(f"[{time.strftime('%H:%M:%S')}] init {m:02d}: fetched -> {path}")
            results[m] = "fetched"
        else:
            print(f"[{time.strftime('%H:%M:%S')}] init {m:02d}: FAILED twice: {last_exc}")
            results[m] = f"failed: {last_exc}"
    print("fetch summary:", results)
    return results


# --------------------------------------------------------------------------
# Step 2: verification series from the monitor's own db
# --------------------------------------------------------------------------

def build_verification():
    con = sqlite3.connect(DB)
    rows = []
    for zk in ZONES:
        df = pd.read_sql_query(
            "select granule_start, value from observations "
            "where zone_key=? and dataset='chirps_global_month_data' "
            "order by granule_start",
            con, params=(zk,),
        )
        df["date"] = pd.to_datetime(df["granule_start"])
        df = df[(df["date"].dt.year >= min(VERIFY_YEARS)) & (df["date"].dt.year <= max(VERIFY_YEARS))]
        if df.empty:
            print(f"WARNING: {zk}: no verification data in db")
            continue
        for _, r in df.iterrows():
            rows.append({"zone_key": zk, "year": r["date"].year,
                         "month": r["date"].month, "rain_mm": round(float(r["value"]), 2)})
    con.close()
    out = pd.DataFrame(rows).sort_values(["zone_key", "year", "month"])
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "seas5_verification.csv", index=False)
    print(f"wrote {OUT / 'seas5_verification.csv'} ({len(out)} rows, "
          f"{out['zone_key'].nunique()} zones, years {out['year'].min()}-{out['year'].max()})")
    return out


# --------------------------------------------------------------------------
# Step 4: scoring
# --------------------------------------------------------------------------

def lead_to_month(init_month: int, lead: int) -> int:
    return (init_month + lead - 1) % 12 + 1


def season_months(start_m: int, end_m: int) -> list:
    """List of calendar months in the season, in order, handling wrap."""
    if end_m >= start_m:
        return list(range(start_m, end_m + 1))
    return list(range(start_m, 13)) + list(range(1, end_m + 1))


def covered_leads(init_month: int, months: list) -> list:
    """[(lead, calendar_month), ...] for leads 1-6 whose month is in `months`."""
    out = []
    for lead in range(1, 7):
        m = lead_to_month(init_month, lead)
        if m in months:
            out.append((lead, m))
    return out


def open_hindcast(init_month: int):
    """Return tp reshaped to canonical axes (member, year, lead, lat, lon)."""
    import netCDF4
    ds = netCDF4.Dataset(hindcast_path(init_month))
    lats = np.array(ds.variables["latitude"][:])
    lons = np.array(ds.variables["longitude"][:])
    frt = ds.variables["forecast_reference_time"]
    years = [d.year for d in netCDF4.num2date(frt[:], frt.units)]
    tp = np.array(ds.variables["tprate"][:])
    dims = list(ds.variables["tprate"].dimensions)
    ds.close()

    lead_name = next(c for c in ("forecastMonth", "leadtime_month", "forecast_month") if c in dims)
    canonical = ["number", "forecast_reference_time", lead_name, "latitude", "longitude"]
    tp = np.moveaxis(tp, [dims.index(d) for d in canonical], [0, 1, 2, 3, 4])
    return tp, lats, lons, years


def box_mean_index(lats, lons, south, north, west, east):
    lat_idx = np.where((lats >= south) & (lats <= north))[0]
    lon_idx = np.where((lons >= west) & (lons <= east))[0]
    if len(lat_idx) == 0:
        lat_idx = np.array([int(np.argmin(np.abs(lats - (south + north) / 2)))])
    if len(lon_idx) == 0:
        lon_idx = np.array([int(np.argmin(np.abs(lons - (west + east) / 2)))])
    return lat_idx, lon_idx


def score_all():
    verif = pd.read_csv(OUT / "seas5_verification.csv")
    bboxes = zone_bboxes()

    available_inits = [m for m in INIT_MONTHS if hindcast_path(m).exists() and hindcast_path(m).stat().st_size > 0]
    missing_inits = [m for m in INIT_MONTHS if m not in available_inits]
    print(f"available init months: {available_inits}")
    if missing_inits:
        print(f"MISSING init months (fetch failed or not run): {missing_inits}")

    # pre-load each available init's hindcast cube once, reshaped to
    # canonical axes (member, year, lead, lat, lon)
    cubes = {}
    for m in available_inits:
        tp, lats, lons, years = open_hindcast(m)
        cubes[m] = dict(tp=tp, lats=lats, lons=lons, years=years)
        print(f"init {m:02d}: tp shape (member,year,lead,lat,lon) = {tp.shape}, years {years[0]}-{years[-1]}")

    rows = []
    zone_season_has_pass = {}
    for zk, (iso3, name, zlat, zlon, seasons) in ZONES.items():
        south, north, west, east = bboxes[zk]
        for season_name, start_m, end_m in seasons:
            months = season_months(start_m, end_m)
            key = (zk, season_name)
            zone_season_has_pass.setdefault(key, False)
            for m in INIT_MONTHS:
                covered = covered_leads(m, months)
                if len(covered) < 3:
                    continue
                if m not in cubes:
                    rows.append({
                        "zone_key": zk, "iso3": iso3, "zone_name": name,
                        "season": season_name, "init_month": m,
                        "init_month_name": calendar.month_abbr[m],
                        "months_covered": "".join(calendar.month_abbr[mm][0] for _, mm in covered),
                        "lead_range": f"{covered[0][0]}-{covered[-1][0]}",
                        "n_years": None, "corr_r": None, "tercile_hit": None,
                        "dry8_overlap": None, "fc_range_mm": None, "obs_range_mm": None,
                        "gate_pass": None, "note": "hindcast fetch missing/failed",
                    })
                    continue

                c = cubes[m]
                tp, lats, lons, hind_years = c["tp"], c["lats"], c["lons"], c["years"]
                lat_idx, lon_idx = box_mean_index(lats, lons, south, north, west, east)

                # tp is (member, year, lead, lat, lon); box-mean over lat/lon,
                # then accumulate mm over the covered lead months.
                box = tp[:, :, :, lat_idx, :][:, :, :, :, lon_idx]  # (member,year,lead,latN,lonN)
                box = box.mean(axis=(3, 4))  # (member, year, lead)
                total = np.zeros(box.shape[:2])  # (member, year)
                for lead, cal_month in covered:
                    nd = calendar.monthrange(2001, cal_month)[1]
                    total += box[:, :, lead - 1] * nd * 86400.0 * 1000.0  # m/s -> mm/month
                fc_med = np.median(total, axis=0)  # -> per year

                # observed: sum months over the right season-year per hindcast year.
                # season year y = the year the season STARTS in; a wrapping season
                # (end_m < start_m) puts months after Dec into year y+1.
                wraps = end_m < start_m
                obs_vals = []
                for y in hind_years:
                    tot = 0.0
                    ok = True
                    for _, cal_month in covered:
                        yy = y + 1 if (wraps and cal_month <= end_m) else y
                        sub = verif[(verif.zone_key == zk) & (verif.year == yy) & (verif.month == cal_month)]
                        if sub.empty:
                            ok = False
                            break
                        tot += float(sub["rain_mm"].iloc[0])
                    obs_vals.append(tot if ok else np.nan)
                obs_vals = np.array(obs_vals)
                valid = ~np.isnan(obs_vals)
                fc_valid = fc_med[valid]
                obs_valid = obs_vals[valid]
                n = len(obs_valid)
                if n < 5:
                    rows.append({
                        "zone_key": zk, "iso3": iso3, "zone_name": name,
                        "season": season_name, "init_month": m,
                        "init_month_name": calendar.month_abbr[m],
                        "months_covered": "".join(calendar.month_abbr[mm][0] for _, mm in covered),
                        "lead_range": f"{covered[0][0]}-{covered[-1][0]}",
                        "n_years": n, "corr_r": None, "tercile_hit": None,
                        "dry8_overlap": None, "fc_range_mm": None, "obs_range_mm": None,
                        "gate_pass": None, "note": "insufficient verification years",
                    })
                    continue

                r = float(np.corrcoef(fc_valid, obs_valid)[0, 1])
                ft = pd.qcut(fc_valid, 3, labels=False, duplicates="drop")
                ot = pd.qcut(obs_valid, 3, labels=False, duplicates="drop")
                tercile_hit = float((ft == ot).mean())
                n_dry = min(8, n)
                dry_obs = set(np.argsort(obs_valid)[:n_dry])
                dry_fc = set(np.argsort(fc_valid)[:n_dry])
                dry8 = len(dry_obs & dry_fc)
                gate = (tercile_hit >= GATE_TERCILE) and (r >= GATE_R)
                if gate:
                    zone_season_has_pass[key] = True

                rows.append({
                    "zone_key": zk, "iso3": iso3, "zone_name": name,
                    "season": season_name, "init_month": m,
                    "init_month_name": calendar.month_abbr[m],
                    "months_covered": "".join(calendar.month_abbr[mm][0] for _, mm in covered),
                    "lead_range": f"{covered[0][0]}-{covered[-1][0]}",
                    "n_years": n, "corr_r": round(r, 3),
                    "tercile_hit": round(tercile_hit, 3),
                    "dry8_overlap": f"{dry8}/{n_dry}",
                    "fc_range_mm": round(float(fc_valid.max() - fc_valid.min()), 1),
                    "obs_range_mm": round(float(obs_valid.max() - obs_valid.min()), 1),
                    "gate_pass": gate, "note": "",
                })

    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "seas5_skill.csv", index=False)
    print(f"wrote {OUT / 'seas5_skill.csv'} ({len(df)} rows)")
    write_markdown(df, zone_season_has_pass, missing_inits)
    return df


def write_markdown(df: pd.DataFrame, zone_season_has_pass: dict, missing_inits: list):
    scored = df[df["corr_r"].notna()]
    n_scored = len(scored)
    n_pass = int(scored["gate_pass"].sum()) if n_scored else 0

    lines = []
    lines.append("# SEAS5 hindcast skill — Phase S1 gate table\n")
    lines.append(f"Generated by `seas5_backtest.py`. Gate: tercile hit >= {GATE_TERCILE:.0%} "
                 f"AND Pearson r >= {GATE_R}. n=24 hindcast seasons (1993-2016) at most, so "
                 f"tercile standard error is ~10pp -- numbers only, no significance claims.\n")
    lines.append(f"**{n_scored} combinations scored, {n_pass} passed the gate.**\n")
    if missing_inits:
        lines.append(f"Init months with no hindcast file (fetch failed/skipped): "
                     f"{', '.join(calendar.month_abbr[m] for m in missing_inits)}.\n")
    else:
        lines.append("All 8 planned hindcast fetches (init months "
                     f"{', '.join(calendar.month_abbr[m] for m in INIT_MONTHS)}) succeeded; "
                     "no gaps in coverage.\n")

    n_full = int((df["n_years"] == 24).sum())
    if n_full < len(df):
        lines.append(f"{len(df) - n_full} of {len(df)} combinations had fewer than the full "
                     "24 hindcast years of verification data (see `note` column in "
                     "output/seas5_skill.csv) -- scored on what was available, never interpolated.\n")
    else:
        lines.append("Verification data (from the monitor's own db, `chirps_global_month_data`, "
                     "1993-2017) was complete for every scored combination: all n=24.\n")

    lines.append("Method: continental SEAS5 hindcast (1993-2016, 25 members, leads 1-6) fetched "
                 "once per init month over box [12N,10W,-35S,52E] on ECMWF's native 1-degree grid; "
                 "verification is the monitor's own CHIRPS v3 zonal series (db/monitor.sqlite, "
                 "not EWX -- the db already reaches back to 1981). Zone box = bounding box of the "
                 "zone's CHIRPS mask (data/zones/masks.npz); zones smaller than one 1-degree cell "
                 "(eth_arsi, ken_trans_nzoia, uga_masindi) use the nearest single grid cell to the "
                 "zone centroid. Forecast = ensemble-median of member totals (mm) over the season "
                 "months within leads 1-6 of that init; only combinations covering >=3 season "
                 "months are scored. Gate mirrors elnino-hydro-dashboard's seas5_skill.py: same "
                 "metric definitions, so results are directly comparable to that dashboard's "
                 "Kariba/Kafue numbers.\n")

    no_pass = [k for k, v in zone_season_has_pass.items() if not v]
    if no_pass:
        lines.append("Zones/seasons with **no passing init at all**:\n")
        for zk, sn in sorted(no_pass):
            lines.append(f"- {zk} / {sn}")
        lines.append("")

    for zk, (iso3, name, lat, lon, seasons) in ZONES.items():
        sub = df[df.zone_key == zk].sort_values(["season", "init_month"])
        if sub.empty:
            continue
        lines.append(f"\n## {zk} -- {name} ({iso3})\n")
        lines.append("| season | init | months | lead | n | r | tercile | dry8 | fc range mm | obs range mm | gate |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, row in sub.iterrows():
            vals = [row.season, row.init_month_name, row.months_covered, row.lead_range]
            if pd.isna(row.corr_r):
                vals += ["-", "-", "-", "-", "-", "-", f"FAIL ({row.note})" if row.note else "n/a"]
            else:
                cells = [str(row.n_years), f"{row.corr_r:.2f}", f"{row.tercile_hit:.0%}",
                        row.dry8_overlap, f"{row.fc_range_mm:.0f}", f"{row.obs_range_mm:.0f}"]
                gate_str = "PASS" if row.gate_pass else "fail"
                vals += cells + [gate_str]
            cells_fmt = [str(v) for v in vals]
            if not pd.isna(row.corr_r) and row.gate_pass:
                cells_fmt = [f"**{c}**" for c in cells_fmt]
            lines.append("| " + " | ".join(cells_fmt) + " |")

    text = "\n".join(lines) + "\n"
    (ROOT / "SEAS5-SKILL.md").write_text(text)
    print(f"wrote {ROOT / 'SEAS5-SKILL.md'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("fetch", "all"):
        fetch_all()
    if cmd in ("verify", "all"):
        build_verification()
    if cmd in ("score", "all"):
        score_all()
