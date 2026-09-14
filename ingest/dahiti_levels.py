"""DAHITI (DGFI-TUM) satellite-altimetry water levels — DRC hydro layer.

SCOPING-DRC-HYDRO.md D1: three curated stations for the Katanga cascade +
Inga etiage monitor. Ported from elnino-hydro-dashboard/ingest/dahiti.py
(v2 API: POST https://dahiti.dgfi.tum.de/api/v2/<action>/ with api_key
as a form field; response fields are wse/wse_u, not water_level).

Requires a free API key: register at https://dahiti.dgfi.tum.de/en/, then
set DAHITI_API_KEY in the environment or in a `.env` file at the repo root
(gitignored — never commit it).

Weekly cadence: gated with --if-due by run_update.sh (see there for the
marker-file age check), not by this module.

Usage:
    python ingest/dahiti_levels.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://dahiti.dgfi.tum.de/api/v2"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}

# Curated stations (SCOPING-DRC-HYDRO.md feasibility table, verified
# live Sep 2026): station_key -> dahiti_id.
STATIONS = {
    "inga_congo": 13226,           # Congo at Inga, 6km from dam — etiage
    "nzilo_reservoir": 13235,      # Lac Delcommune / Nzilo reservoir
    "lufira_tshangalele": 21410,   # Lufira below Tshangalele (outflow proxy;
                                    # no DAHITI target on Lake Tshangalele
                                    # itself — verified against all 1,059
                                    # DRC entries, see scoping doc)
}


class MissingKey(RuntimeError):
    pass


def dahiti_api_key() -> str | None:
    """DAHITI key from env var or a `.env` line `DAHITI_API_KEY=...` at
    the repo root (elnino-hydro-dashboard convention, ported verbatim)."""
    key = os.environ.get("DAHITI_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DAHITI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _post(endpoint: str, **params):
    key = dahiti_api_key()
    if not key:
        raise MissingKey(
            "No DAHITI_API_KEY set. Register (free) at "
            "https://dahiti.dgfi.tum.de/en/ and add DAHITI_API_KEY=<key> "
            "to .env")
    resp = requests.post(
        f"{BASE}/{endpoint}/",
        data={"api_key": key, **params},
        headers=UA, timeout=120,
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(
            f"DAHITI {endpoint}: non-JSON response (bad API key?): "
            f"{resp.text[:200]!r}")
    if isinstance(payload, dict) and payload.get("code") not in (None, 200):
        raise RuntimeError(f"DAHITI {endpoint}: {payload.get('message')}")
    return payload


def water_level(dahiti_id: int) -> pd.DataFrame:
    data = _post("download-water-level", dahiti_id=dahiti_id, format="json")
    payload = data.get("data", data) if isinstance(data, dict) else data
    df = pd.DataFrame(payload)
    df = df.rename(columns={
        "datetime": "date",
        "wse": "level_m",       # water surface elevation (v2)
        "wse_u": "error_m",
        "water_level": "level_m",  # older field name, just in case
    })
    df["date"] = pd.to_datetime(df["date"])
    keep = ["date", "level_m"] + (["error_m"] if "error_m" in df.columns else [])
    return df[keep]


def run() -> dict:
    con = db.connect()
    out = {}
    for station_key, dahiti_id in STATIONS.items():
        try:
            df = water_level(dahiti_id)
        except Exception as exc:  # noqa: BLE001
            out[station_key] = f"FAILED: {exc}"
            print(f"[{station_key}] FAILED: {exc}", file=sys.stderr)
            continue
        if df.empty:
            out[station_key] = "no data returned"
            continue
        rows = [
            (d.date().isoformat(), float(lvl),
             None if err is None or pd.isna(err) else float(err))
            for d, lvl, err in zip(
                df["date"], df["level_m"],
                df["error_m"] if "error_m" in df.columns else [None] * len(df))
        ]
        n_new = db.upsert_hydro_levels(con, station_key, rows)
        con.commit()
        last = df.iloc[-1]
        out[station_key] = (
            f"{len(df)} rows ({n_new} new), last {last['date'].date()} = "
            f"{last['level_m']:.2f}m")
        print(f"[{station_key}] {out[station_key]}")
    con.close()
    return out


if __name__ == "__main__":
    result = run()
    failed = [k for k, v in result.items() if v.startswith("FAILED")]
    raise SystemExit(1 if failed else 0)
