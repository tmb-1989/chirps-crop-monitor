"""Staple market prices from the WFP price database (via HDX).

Per country: download the wfp-food-prices CSV, filter to the staple
commodities the CPI-impulse layer tracks (data/econ/cpi_weights.csv),
normalize to price per KG, and store the monthly cross-market median in
staple_prices (monitor.sqlite — it is history, not fast-moving state).

South Africa has no WFP feed; its leg-B elasticity falls back to the
literature prior (see compute/cpi_impulse.py).

Usage: python ingest/prices.py [--full]   (default: last 3 years)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ECON = ROOT / "data" / "econ" / "cpi_weights.csv"
UA = {"User-Agent": "chirps-crop-monitor/0.1 (research)"}

HDX = "https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices-for-{}"
SLUGS = {"KEN": "kenya", "ETH": "ethiopia", "TZA": "united-republic-of-tanzania",
         "RWA": "rwanda", "UGA": "uganda", "ZMB": "zambia", "MWI": "malawi",
         "ZWE": "zimbabwe", "MOZ": "mozambique", "MDG": "madagascar"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS staple_prices (
    iso3       TEXT NOT NULL,
    month      TEXT NOT NULL,   -- YYYY-MM-01
    commodity  TEXT NOT NULL,   -- normalized: Maize / Rice / Wheat
    pricetype  TEXT NOT NULL,   -- Retail / Wholesale
    price_kg   REAL,            -- median across markets, local currency
    usd_kg     REAL,
    n_markets  INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (iso3, month, commodity, pricetype)
);
"""

# WFP commodity labels vary ("Maize", "Maize (white)", "Maize meal",
# "Rice (local)", ...). Map to the normalized staple; grain preferred
# over meal — meal adds milling margin noise (keep both, distinguished
# by pricetype median; meal excluded for v1).
GRAIN_PAT = {
    "Maize": re.compile(r"^Maize(\s*\([^)]*\))?$", re.I),
    "Rice": re.compile(r"^Rice(\s*\([^)]*\))?$", re.I),
    "Wheat": re.compile(r"^Wheat(\s*\([^)]*\))?$", re.I),
}
# units convertible to KG
UNIT_KG = {"KG": 1.0, "90 KG": 90.0, "50 KG": 50.0, "100 KG": 100.0,
           "25 KG": 25.0, "G": 0.001, "400 G": 0.4}


def staples_for() -> dict:
    out = {}
    with open(ECON) as f:
        for r in csv.DictReader(f):
            # track the CPI staple plus wheat for Ethiopia (Arsi zone)
            cs = {r["staple_commodity"]}
            if r["iso3"] == "ETH":
                cs.add("Wheat")
            out[r["iso3"]] = cs
    return out


def fetch_csv(iso3: str) -> list[dict]:
    meta = json.load(urllib.request.urlopen(
        urllib.request.Request(HDX.format(SLUGS[iso3]), headers=UA),
        timeout=120))
    url = next(r["url"] for r in meta["result"]["resources"]
               if r["format"] == "CSV" and "price" in r["name"].lower())
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=300).read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="keep full history (default: last 3 years)")
    args = ap.parse_args()
    cutoff = "0000" if args.full else \
        f"{dt.date.today().year - 3}-01-01"

    con = db.connect()
    con.executescript(SCHEMA)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    staples = staples_for()
    failures = []
    for iso3, wanted in staples.items():
        if iso3 not in SLUGS:
            print(f"[{iso3}] no WFP feed — skipped (elasticity prior only)")
            continue
        try:
            rows = fetch_csv(iso3)
        except Exception as e:  # noqa: BLE001
            failures.append(iso3)
            print(f"[{iso3}] fetch FAILED: {e}", file=sys.stderr)
            continue
        # (month, commodity, pricetype) -> list of per-kg prices
        acc: dict = {}
        for r in rows:
            if r["date"] < cutoff or r.get("priceflag") not in (
                    "actual", "aggregate", "actual,aggregate"):
                continue
            norm = next((k for k, pat in GRAIN_PAT.items()
                         if pat.match(r["commodity"].strip())), None)
            if norm is None or norm not in wanted:
                continue
            kg = UNIT_KG.get(r["unit"])
            if not kg:
                continue
            try:
                price, usd = float(r["price"]) / kg, \
                    float(r["usdprice"]) / kg
            except (TypeError, ValueError):
                continue
            month = r["date"][:8] + "01"
            acc.setdefault((month, norm, r["pricetype"]), []).append(
                (price, usd))
        n = 0
        for (month, comm, ptype), vals in acc.items():
            vals.sort()
            med = vals[len(vals) // 2]
            con.execute(
                "INSERT OR REPLACE INTO staple_prices VALUES "
                "(?,?,?,?,?,?,?,?)",
                (iso3, month, comm, ptype, med[0], med[1], len(vals), now))
            n += 1
        con.commit()
        latest = max((k[0] for k in acc), default="-")
        print(f"[{iso3}] {n} month-rows upserted (through {latest})")
    con.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
