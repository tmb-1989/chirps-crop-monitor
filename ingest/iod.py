"""Indian Ocean Dipole (DMI) ingest — two sources, ported from the
elnino-hydro-dashboard project.

1. `iod_dmi` — PSL's HadISST1.1 DMI (plain text, monthly to 1870,
   ~2.5 months stale). Kept as the cross-validation reference only.
2. `iod_dmi_oisst` — near-real-time DMI computed here from NOAA OISST
   v2.1 monthlies over OPeNDAP (~40-day lag). This is the series the
   country risk board reads. Standard Saji et al. (1999) definition:
   DMI = SST anomaly (50-70E, 10S-10N) minus (90-110E, 10S-0), anomalies
   vs the 1991-2020 monthly climatology, cos(lat)-weighted, land masked.

Why the IOD is on the board: for the Oct-Dec short rains over the Horn
and East Africa the IOD is a STRONGER predictor than ENSO. Positive DMI
= wet East Africa (2019 floods); negative DMI + La Niña produced the
2020-22 five-season drought. The two series are NOT interchangeable
(different SST products; HadISST damps the dipole by roughly half) —
never splice them; the board uses OISST only.

OPeNDAP guards (hard-won upstream, do not remove): PSL truncates large
responses SILENTLY, returning short or zero-filled data without raising
— an unguarded read once produced a DMI of -27.9 degC. Every block read
is shape- and plausibility-checked and retried.

Run: python ingest/iod.py            (full OISST history, ~1-2 min)
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import time

import numpy as np
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

HAD_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
HAD_MISSING = -9999.0

BASE = "https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.oisst.v2.highres/"
MEAN_URL = BASE + "sst.mon.mean.nc"
LTM_URL = BASE + "sst.mon.ltm.1991-2020.nc"

# (lon_min, lon_max, lat_min, lat_max) in the files' 0-360 convention
WEST_BOX = (50.0, 70.0, -10.0, 10.0)
EAST_BOX = (90.0, 110.0, -10.0, 0.0)

RETRIES = 4
BACKOFF_S = 8            # PSL returns HTTP 429 under rapid repeats
PLAUSIBLE_SST_C = (15.0, 35.0)
TIME_BLOCK = 120
INTER_REQUEST_S = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.iod_dmi (
    date TEXT PRIMARY KEY,
    dmi  REAL
);
CREATE TABLE IF NOT EXISTS live.iod_dmi_oisst (
    date TEXT PRIMARY KEY,
    dmi  REAL,
    anom_west REAL,
    anom_east REAL
);
"""


# ------------------------------------------------- HadISST reference DMI
def fetch_dmi_hadisst() -> list[tuple]:
    r = requests.get(HAD_URL, timeout=60)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) != 13:
            continue
        try:
            year = int(parts[0])
            vals = [float(x) for x in parts[1:]]
        except ValueError:
            continue
        if not 1800 <= year <= 2100:
            continue
        rows.extend((dt.date(year, m, 1).isoformat(), v)
                    for m, v in enumerate(vals, start=1) if v != HAD_MISSING)
    if not rows:
        raise RuntimeError("no DMI rows parsed — PSL layout change?")
    return rows


# ------------------------------------------------- OISST live DMI
def _open(url):
    import netCDF4 as nc
    last = None
    for attempt in range(RETRIES):
        try:
            return nc.Dataset(url)
        except Exception as exc:  # noqa: BLE001 - transport-level, retry
            last = exc
            time.sleep(BACKOFF_S * (attempt + 1))
    raise RuntimeError(f"OPeNDAP open failed after {RETRIES} tries: {last}")


def _slice(arr, lo, hi):
    w = np.where((arr >= lo) & (arr <= hi))[0]
    if len(w) == 0:
        raise ValueError(f"no grid points between {lo} and {hi}")
    return int(w[0]), int(w[-1]) + 1


def _check(data, want_shape):
    """Reject a silently truncated or zero-filled DAP response."""
    if data.shape != want_shape:
        raise ValueError(f"short read: got {data.shape}, want {want_shape}")
    covered = np.ma.count(data, axis=(1, 2))
    if (covered == 0).any():
        raise ValueError(f"{int((covered == 0).sum())} timesteps fully masked")
    med = float(np.ma.median(data))
    if not PLAUSIBLE_SST_C[0] <= med <= PLAUSIBLE_SST_C[1]:
        raise ValueError(
            f"median SST {med:.2f} degC outside {PLAUSIBLE_SST_C}")


def _box_mean(var, lat, lon, box, n_time, label):
    """cos(lat)-weighted box mean per timestep, land excluded, block reads
    validated (the failure mode is a silent zero fill, not an exception)."""
    lon0, lon1, lat0, lat1 = box
    la0, la1 = _slice(lat, lat0, lat1)
    lo0, lo1 = _slice(lon, lon0, lon1)
    weights_1d = np.cos(np.deg2rad(lat[la0:la1]))
    means = []
    for start in range(0, n_time, TIME_BLOCK):
        stop = min(start + TIME_BLOCK, n_time)
        want = (stop - start, la1 - la0, lo1 - lo0)
        for attempt in range(RETRIES):
            try:
                block = var[start:stop, la0:la1, lo0:lo1]
                _check(block, want)
                break
            except Exception as exc:  # noqa: BLE001 - incl. silent truncation
                if attempt == RETRIES - 1:
                    raise RuntimeError(
                        f"{label} months {start}-{stop}: {exc}") from exc
                time.sleep(BACKOFF_S * (attempt + 1))
        w = np.broadcast_to(weights_1d[None, :, None], block.shape)
        means.append(np.ma.average(block, axis=(1, 2), weights=w))
        time.sleep(INTER_REQUEST_S)
    return np.ma.concatenate(means)


def fetch_dmi_oisst() -> list[tuple]:
    import netCDF4 as nc

    ltm = _open(LTM_URL)
    lat, lon = ltm.variables["lat"][:], ltm.variables["lon"][:]
    ltm_west = _box_mean(ltm.variables["sst"], lat, lon, WEST_BOX, 12,
                         "climatology west")
    ltm_east = _box_mean(ltm.variables["sst"], lat, lon, EAST_BOX, 12,
                         "climatology east")
    ltm.close()
    time.sleep(BACKOFF_S)      # do not trip the PSL rate limit

    ds = _open(MEAN_URL)
    tvar = ds.variables["time"]
    n = len(tvar)
    dates = [dt.date(d.year, d.month, 1) for d in
             nc.num2date(tvar[:], tvar.units,
                         only_use_cftime_datetimes=False)]
    lat, lon = ds.variables["lat"][:], ds.variables["lon"][:]
    west = _box_mean(ds.variables["sst"], lat, lon, WEST_BOX, n,
                     "monthly mean west")
    east = _box_mean(ds.variables["sst"], lat, lon, EAST_BOX, n,
                     "monthly mean east")
    ds.close()

    months = np.array([d.month for d in dates]) - 1
    anom_west = west - ltm_west[months]
    anom_east = east - ltm_east[months]
    dmi = np.ma.filled(anom_west - anom_east, np.nan)
    aw = np.ma.filled(anom_west, np.nan)
    ae = np.ma.filled(anom_east, np.nan)
    return [(d.isoformat(), float(v), float(w), float(e))
            for d, v, w, e in zip(dates, dmi, aw, ae) if np.isfinite(v)]


# Pass conditions, each targeting one real failure mode (see upstream
# module history: the sign test sits at 0.7 because HadISST damps the
# dipole by ~half; amplitudes differ, sign and ranking agree).
MIN_CORR_VS_HADISST = 0.70
MAX_SEASONAL_RANGE = 0.15
PLAUSIBLE_STD = (0.20, 0.80)


def validate(con) -> dict:
    """Three checks: flipped sign, wrong climatology, truncated read."""
    o = con.execute("SELECT date, dmi FROM iod_dmi_oisst "
                    "ORDER BY date").fetchall()
    if not o:
        return {"verdict": "no data"}
    dates = [r[0] for r in o]
    vals = np.array([r[1] for r in o])
    months = np.array([int(d[5:7]) for d in dates])
    monthly = [vals[months == m].mean() for m in range(1, 13)]
    seasonal_range = float(max(monthly) - min(monthly))
    std = float(vals.std())
    out = {"seasonal_residual": round(seasonal_range, 3),
           "std_degC": round(std, 3)}
    fail = []
    if seasonal_range > MAX_SEASONAL_RANGE:
        fail.append(f"residual seasonal cycle {seasonal_range:.3f}")
    if not PLAUSIBLE_STD[0] <= std <= PLAUSIBLE_STD[1]:
        fail.append(f"std {std:.3f} outside {PLAUSIBLE_STD}")
    had = dict(con.execute("SELECT date, dmi FROM iod_dmi").fetchall())
    both = [(v, had[d]) for d, v in zip(dates, vals) if d in had]
    if len(both) >= 24:
        a, b = np.array(both).T
        r = float(np.corrcoef(a, b)[0, 1])
        out["corr_vs_hadisst"] = round(r, 3)
        out["overlap_months"] = len(both)
        if r < MIN_CORR_VS_HADISST:
            fail.append(f"corr {r:.3f} below {MIN_CORR_VS_HADISST}")
    out["verdict"] = "ok" if not fail else "; ".join(fail)
    return out


def latest_dmi(con) -> tuple | None:
    """(date, dmi, lag_days) of the freshest OISST DMI, or None."""
    row = con.execute("SELECT date, dmi FROM iod_dmi_oisst "
                      "ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        return None
    lag = (dt.date.today() - dt.date.fromisoformat(row[0])).days
    return (row[0], row[1], lag)


def main() -> int:
    con = db.connect()
    con.executescript(SCHEMA)
    had = fetch_dmi_hadisst()
    con.executemany("INSERT OR REPLACE INTO iod_dmi VALUES (?,?)", had)
    oisst = fetch_dmi_oisst()
    con.executemany("INSERT OR REPLACE INTO iod_dmi_oisst VALUES (?,?,?,?)",
                    oisst)
    con.commit()
    v = validate(con)
    d, dmi, lag = latest_dmi(con)
    print(f"DMI (OISST): {len(oisst)} months through {d} "
          f"({dmi:+.2f}, lag {lag}d); HadISST ref {len(had)} months; "
          f"validate: {v['verdict']}")
    if v["verdict"] != "ok":
        print(f"VALIDATION DETAIL: {v}", file=sys.stderr)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
