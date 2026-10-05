"""CHIRPS crop-zone monitor — Phase 1 dashboard.

Run:  cd ~/claude/chirps-crop-monitor && ./venv/bin/streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# full archive locally; deployed clones carry the trimmed app.sqlite
# (observations >= 2010 — see ingest/app_db.py; GitHub's 100MB limit)
_dbdir = pathlib.Path(__file__).resolve().parent.parent / "db"
DB = _dbdir / "monitor.sqlite"
if not DB.exists():
    DB = _dbdir / "app.sqlite"
# fast-moving tables (risk board, ENSO/IOD, Kariba, GEFS, alerts) live in a
# small separate file committed daily; see ingest/split_db.py
LIVE = DB.parent / "live.sqlite"
CLIM_START, CLIM_END = 1991, 2020

st.set_page_config(page_title="El Niño Sovereign Risk Monitor",
                   layout="wide")


@st.cache_data(ttl=600)
def load(query: str, params=()) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    if LIVE.exists():
        con.execute("ATTACH DATABASE ? AS live", (str(LIVE),))
    df = pd.read_sql_query(query, con, params=params)
    con.close()
    return df


zones = load("SELECT * FROM zones ORDER BY iso3, name")
if zones.empty:
    st.error("No zones in DB — run ingest first.")
    st.stop()

st.title("El Niño Sovereign Risk Monitor")
st.caption(
    "CHIRPS pentad rainfall, WRSI (Water Requirement Satisfaction Index) and "
    "FLDAS soil moisture, zonal means over "
    "FEWS NET crop zones (USGS GeoEngine API). Climatology 1991–2020.")

zone_label = {
    r.zone_key: f"{r['name']} — {r.crop or '?'} ({r.adm1 or '?'})"
    for _, r in zones.iterrows()
}

with st.expander("What the indicators mean and how to read them"):
    st.markdown("""
**CHIRPS rainfall** — satellite + rain-gauge blended precipitation
(Climate Hazards Center, UCSB), updated every 5 days with ~2-day lag.
Charts show the season's running total against the 1991–2020 mean and the
20–80th percentile band: inside the band ≈ normal, below the band = a
developing deficit. The dotted extension is the CHIRPS-GEFS forecast
(next ~2 weeks, indicative only). *Monthly z-score* expresses a month's
rainfall in standard deviations from normal — comparable across zones;
|z| ≥ 2 is a roughly 1-in-40 extreme. In-season prints matter; dry-season
z-scores are noise.

**WRSI — Water Requirement Satisfaction Index** — an FAO crop-water model
tracking how much of the maize crop's water *requirement* was met, dekad
by dekad, weighted by growth stage. Shown as % of the 1982–2021 median
for the same point in the season: ~100 = normal year. **80–94 = mild
stress · 60–80 = moderate stress (yield loss) · below 60 = severe
stress/failure.** Deficits are permanent within a season — the index
never recovers even if late rains arrive — making it the best single
harvest proxy. Only meaningful in-season (reads ~100 off-season).

**FLDAS root-zone soil moisture** — modelled water content of the top
100cm of soil (NASA land-surface model), monthly, ~6-week lag. Shown as
% of the long-run mean: it moves slowly and integrates rainfall, so
deviations are smaller but more persistent than rainfall's.
**85–95 = watch territory · below 85 = issue level.** Read it as the
season's buffer: at planting it sets how much insurance the crop starts
with; after a failed season it shows whether the drought carries into
the next one. Low WRSI + normal soil moisture = this harvest damaged but
reserves intact; both low = the compounding case that precedes major
food-security and fiscal events.

**SPI — Standardized Precipitation Index** — recent rainfall expressed in
standard deviations from its own 1991–2020 distribution (gamma-fitted per
dekad-of-year, so values are comparable across seasons and zones). SPI-1
sums the trailing ~1 month (fast, noisy — flags acute dry spells); SPI-3
the trailing ~3 months (the agricultural signal — moves with WRSI).
**−1 = moderate · −1.5 = severe · −2 = extreme drought** (≈1-in-40);
positive values mirror on the wet side. Computed from CHIRPS dekads, so
readings run ~4–14 days behind real time (a dekad closes, its prelim
lands ~3 days later, the next scheduled update ingests it).

**El Niño benchmarks** — each indicator's worst print during the 2015-16
and 2023-24 El Niño droughts (Jul–Jun windows), the two modern reference
crises. A current value at or below an episode low means conditions are
tracking as bad as the worst of those events.
""")

today = dt.date.today()
view = st.sidebar.radio("View", ["Overview", "Country"])
# ======================== COUNTRY RISK ====================================
NAMES_CR = {"KEN": "Kenya", "ETH": "Ethiopia", "TZA": "Tanzania",
            "RWA": "Rwanda", "UGA": "Uganda", "ZMB": "Zambia",
            "MWI": "Malawi", "ZWE": "Zimbabwe", "MOZ": "Mozambique",
            "MDG": "Madagascar", "ZAF": "South Africa",
            "NGA": "Nigeria", "GHA": "Ghana", "CIV": "Côte d'Ivoire",
            "BEN": "Benin"}
DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⚪"}
FILL = {"green": "#e6f4e6", "yellow": "#fdf3d7", "red": "#fbe3e3",
        "gray": "#f0f0f0"}


def drought_region_block(expander: bool = True) -> None:
    """Per-zone drought lights table (zone_risk), worst-first per country.
    Rendered inside an expander (Country risk view) or inline (Overview)."""
    zrisk = load("SELECT * FROM zone_risk WHERE factor IN "
                 "('drought','export')")
    if zrisk.empty:
        return
    try:
        shock = load("SELECT zone_key, shock, shock_p10, shock_p90, "
                     "provisional FROM zone_output_shock WHERE in_season=1"
                     ).set_index("zone_key")
    except Exception:  # table absent until compute/cpi_impulse.py runs
        shock = pd.DataFrame()
    rank = {"red": 3, "yellow": 2, "green": 1, "gray": 0}
    zrisk["_r"] = zrisk.status.map(rank)
    zrisk["_c"] = zrisk.country.map(
        {c: i for i, c in enumerate(NAMES_CR)}).fillna(99)
    zrisk = zrisk.sort_values(["_c", "_r", "wrsi"],
                              ascending=[True, False, True])
    n_hot = int((zrisk._r >= 2).sum())
    label = (f"Drought by growing region ({n_hot} of {len(zrisk)} zones "
             "at yellow/red)")
    ctx = st.expander(label, expanded=n_hot > 0) if expander \
        else st.container()
    with ctx:
        if not expander:
            st.markdown(f"**{label}**")
        drows, dfills = [], []
        for _, z in zrisk.iterrows():
            wr_txt, wr_fill = "—", ""
            if pd.notna(z.wrsi):
                if z.wrsi_in_season:
                    wr_txt = f"{z.wrsi:.0f}"
                    wr_fill = FILL["red"] if z.wrsi < 80 else \
                        FILL["yellow"] if z.wrsi < 95 else FILL["green"]
                else:
                    wr_txt = f"{z.wrsi:.0f} · off-season"
            sp_txt, sp_fill = "—", ""
            if pd.notna(z.spi3):
                sp_txt = f"{z.spi3:.1f}"
                sp_fill = FILL["red"] if z.spi3 <= -1.5 else \
                    FILL["yellow"] if z.spi3 <= -1 else FILL["green"]
            sm_txt, sm_fill = "—", ""
            if pd.notna(z.sm):
                sm_txt = f"{z.sm:.0f}%"
                sm_fill = FILL["red"] if z.sm < 82 else \
                    FILL["yellow"] if z.sm < 85 else FILL["green"]
            os_txt, os_fill = "—", ""
            if not shock.empty and z.zone_key in shock.index \
                    and pd.notna(shock.loc[z.zone_key, "shock"]):
                s = shock.loc[z.zone_key]
                os_txt = (f"{s.shock:.0%} "
                          f"[{s.shock_p10:.0%}–{s.shock_p90:.0%}]"
                          + (" ·prov" if s.provisional else ""))
                os_fill = FILL["red"] if s.shock >= 0.4 else \
                    FILL["yellow"] if s.shock >= 0.2 else FILL["green"]
            season = getattr(z, "season", None)
            drows.append({
                "Country": NAMES_CR.get(z.country, z.country),
                "Region": f"{DOT[z.status]} {z['name']}"
                          + (f" · {season}" if season else "")
                          + (" 🌱" if z.factor == "export" else ""),
                "WRSI %med": wr_txt, "SPI-3": sp_txt,
                "Soil moisture": sm_txt, "Output shock": os_txt,
                "Driver": z.reason})
            dfills.append({
                "Country": "", "Region": FILL[z.status],
                "WRSI %med": wr_fill, "SPI-3": sp_fill,
                "Soil moisture": sm_fill, "Output shock": os_fill,
                "Driver": ""})
        dtbl, dfill = pd.DataFrame(drows), pd.DataFrame(dfills)
        dstyled = dtbl.style.apply(
            lambda col: [f"background-color: {dfill.loc[i, col.name]}"
                         if dfill.loc[i, col.name] else ""
                         for i in col.index], axis=0)
        st.dataframe(dstyled, hide_index=True, use_container_width=True,
                     height=min(70 + 35 * len(dtbl), 600))
        st.caption(
            "Each region carries its own light; the country-level drought "
            "cell is the worst of these. WRSI is dimmed off-season (it "
            "reads ~100 and shouldn't drive a light). Readings are colored "
            "by their own thresholds — WRSI <80 / <95, SPI-3 ≤−1.5 / ≤−1, "
            "soil moisture <82% / <85% — so you can see which indicator "
            "trips a light, not just that one did. Output shock = implied "
            "share of the zone's water-limited yield lost (FAO Ky × the "
            "WRSI deficit vs the zone's own median, P10–P90 band; ·prov "
            "= season still running, can only worsen).")


if view == "Overview":
    st.subheader("Country risk board — ENSO / IOD / drought / flood")
    cr = load("SELECT * FROM country_risk")
    if cr.empty:
        st.warning("No country_risk data — run compute/country_risk.py.")
        st.stop()

    FACTORS = [("enso", "El Niño / ENSO"), ("iod", "Indian Ocean Dipole"),
               ("drought", "Drought"), ("export", "Export crops"),
               ("flood", "Flood"), ("hydro", "Hydropower")]

    # compact HTML board: lights only, the reason appears on hover
    # (title attribute) — the full-text table outgrew the screen
    import html as _html
    cell = {(r.country, r.factor): r for _, r in cr.iterrows()}
    head = "".join(f"<th style='padding:6px 10px;text-align:center;"
                   f"font-weight:600'>{label}</th>"
                   for _, label in FACTORS)
    body = []
    for c in NAMES_CR:
        tds = [f"<td style='padding:6px 10px;white-space:nowrap'>"
               f"{NAMES_CR[c]}</td>"]
        for fac, label in FACTORS:
            r = cell.get((c, fac))
            if r is None:
                dot, fill, tip = "⚪", FILL["gray"], "no data"
            else:
                dot, fill = DOT[r.status], FILL[r.status]
                tip = f"{r.reason or ''}" + \
                    (f"  (as of {r.as_of})" if r.as_of else "")
            tds.append(
                f"<td title=\"{_html.escape(tip, quote=True)}\" "
                f"style='background:{fill};text-align:center;"
                f"padding:6px 10px;font-size:15px;cursor:default'>"
                f"{dot}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    st.markdown(
        "<table style='border-collapse:collapse;width:100%'>"
        f"<tr><th style='text-align:left;padding:6px 10px'>Country</th>"
        f"{head}</tr>" + "".join(body) + "</table>",
        unsafe_allow_html=True)
    st.caption("Hover a light for the reason behind it.")

    ts = cr.computed_at.max()
    age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(ts)).total_seconds() \
        / 3600
    msg = f"Board computed {ts} UTC ({age_h:.0f}h ago)."
    if age_h > 24 * 7:
        st.warning(msg + " — STALE: the update pipeline may not be running.")
    else:
        st.caption(msg)
    st.caption(
        "Worst case aggregation: the most stressed zone or basin colors "
        "its whole country, and each cell names the culprit. ⚪ = inputs "
        "missing or stale — never read gray as safe. ENSO cells pass the "
        "global ONI phase through each country's impact season "
        "(El Niño ≈ OND floods in East Africa but main-season drought in "
        "southern Africa); 'developing' is detection, not a forecast. "
        "Drought = in-season WRSI, SPI-3 and soil moisture over crop "
        "zones; flood = the backtested basin flood-watch layer. "
        "Hydropower = Lake Kariba level, usable storage and 4-week "
        "drawdown rate (Zambia only so far). The IOD column is the "
        "dipole in its own right for the OND short-rains countries "
        "(positive = wet East Africa; |DMI| ≥ 0.4 event, ≥ 0.75 "
        "flood-year class); East African ENSO cells are IOD-led too — "
        "El Niño without a positive dipole reads as an enhanced-rains "
        "watch, not an alert.")

    # ---- climate drivers: ENSO left, IOD right ---------------------------
    with st.expander("Climate drivers — weekly Niño 3.4, IOD"):
        cA, cB = st.columns(2)
        wk = load("SELECT date, nino34_anom FROM enso_weekly "
                  "WHERE date >= '2014-01-01' ORDER BY date")
        dmi = load("SELECT date, dmi FROM iod_dmi_oisst "
                   "WHERE date >= '2014-01-01' ORDER BY date")
        with cA:
            if not wk.empty:
                fe = go.Figure()
                fe.add_scatter(x=pd.to_datetime(wk.date), y=wk.nino34_anom,
                               name="Niño 3.4 weekly", line=dict(
                                   color="crimson", width=1.5))
                fe.add_hline(y=0, line_color="gray", line_width=1)
                for y in (0.5, -0.5):
                    fe.add_hline(y=y, line_dash="dot", line_color="gray")
                fe.update_layout(title="ENSO — weekly Niño 3.4 (°C "
                                       "anomaly)", height=320,
                                 margin=dict(t=40, b=0),
                                 showlegend=False)
                st.plotly_chart(fe, use_container_width=True)
                st.caption("El Niño/La Niña beyond ±0.5. Drives the "
                           "southern Africa main-season drought signal "
                           "and Ethiopia's kiremt; East Africa's short "
                           "rains respond mostly via the dipole (right).")
        with cB:
            if not dmi.empty:
                fi = go.Figure()
                fi.add_scatter(x=pd.to_datetime(dmi.date), y=dmi.dmi,
                               name="DMI (OISST)",
                               line=dict(color="steelblue", width=1.5))
                fi.add_hline(y=0, line_color="gray", line_width=1)
                for y in (0.4, -0.4):
                    fi.add_hline(y=y, line_dash="dot", line_color="gray")
                fi.add_hline(y=0.75, line_dash="dot", line_color="crimson",
                             annotation_text="+0.75 flood-year class",
                             annotation_font=dict(size=11, color="crimson"))
                fi.add_hline(y=-0.75, line_dash="dot", line_color="darkred",
                             annotation_text="−0.75 failed-rains class",
                             annotation_position="bottom right",
                             annotation_font=dict(size=11, color="darkred"))
                fi.update_layout(title="Indian Ocean Dipole — DMI (°C "
                                       "anomaly, OISST)", height=320,
                                 margin=dict(t=40, b=0),
                                 showlegend=False)
                st.plotly_chart(fi, use_container_width=True)
                st.caption("Dipole event beyond ±0.4; positive = wet East "
                           "Africa OND short rains (1997/2019 floods), "
                           "negative = failed short rains (2020-22). "
                           "DMI lags ~1-2 months (OISST monthly).")

    # ---- food-CPI impulse (SCOPING-CPI) ----------------------------------
    try:
        imp = load("SELECT * FROM cpi_impulse ORDER BY cpi_pp DESC")
    except Exception:
        imp = pd.DataFrame()
    if not imp.empty:
        with st.expander(
                f"Food-CPI impulse — {len(imp)} country(ies) in season",
                expanded=bool((imp.cpi_pp >= 1).any())):
            fc = go.Figure()
            names = [NAMES_CR.get(c, c) for c in imp.country]
            fc.add_bar(
                x=names, y=imp.cpi_pp,
                error_y=dict(type="data", symmetric=False,
                             array=imp.cpi_p90 - imp.cpi_pp,
                             arrayminus=imp.cpi_pp - imp.cpi_p10),
                marker_color=["crimson" if v >= 2 else "orange"
                              if v >= 1 else "seagreen"
                              for v in imp.cpi_pp])
            fc.update_layout(
                title="Estimated food-CPI impulse from in-season crop "
                      "water stress (pp, P10–P90)",
                height=300, margin=dict(t=40, b=0), showlegend=False)
            st.plotly_chart(fc, use_container_width=True)
            dt_ = imp.assign(
                Country=names,
                Shock=[f"{r.out_shock:.0%} [{r.out_p10:.0%}–{r.out_p90:.0%}]"
                       for _, r in imp.iterrows()],
                Impulse=[f"+{r.cpi_pp:.1f}pp [{r.cpi_p10:.1f}–"
                         f"{r.cpi_p90:.1f}]" for _, r in imp.iterrows()],
                Coverage=[f"{r.coverage:.0%}" for _, r in imp.iterrows()],
                Elasticity=imp.elasticity,
                Status=["provisional (in season)" if r.provisional
                        else "season complete" for _, r in imp.iterrows()],
            )[["Country", "Shock", "Impulse", "Coverage", "Elasticity",
               "Status"]]
            st.dataframe(dt_, hide_index=True, use_container_width=True)
            st.caption(
                "Chain: zone WRSI → FAO Ky yield loss → production-"
                "weighted national shock (Coverage = share of national "
                "production our zones represent — the rest is assumed "
                "0–50% as affected) → staple price via elasticity "
                "(fitted on our own WRSI/price history where usable, "
                "IMF-informed prior 1.5 otherwise; capped at +50% import "
                "parity) → CPI via food weight × staple share. Ceteris "
                "paribus on FX and policy (export bans, subsidies, duty "
                "waivers all break the elasticity — Kenya 2026 did all "
                "three). The band is the estimate; the midpoint is not. "
                "See SCOPING-CPI.md.")

    # ---- drought drill-down: one light per growing region ----------------
    drought_region_block(expander=True)

    log = load("SELECT changed_at, country, factor, prev, status, reason "
               "FROM country_risk_log ORDER BY changed_at DESC LIMIT 20")
    with st.expander(f"Recent status changes ({len(log)})"):
        if log.empty:
            st.caption("No changes recorded yet — the log starts with the "
                       "board's second computation.")
        else:
            st.dataframe(log, hide_index=True, use_container_width=True)
    _map_png = DB.parent.parent / "data" / "coverage_map.png"
    if _map_png.exists():
        with st.expander("Geographic coverage — zones, basins, cities",
                         expanded=False):
            st.image(str(_map_png), use_container_width=True)
            st.caption(
                "Crop zones colored by crop (purple = export belts), "
                "flood basins in blue, over Natural Earth outlines. "
                "Regenerated on dekad days by compute/coverage_map.py.")
    st.stop()


# ======================== FLOOD SECTION (Country tab) =====
def _flood_section(_ciso):
    st.divider()
    st.header("Flood risk")
    import json as _json
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                            / "compute"))
    import flood_signals as _fsig  # noqa: E402
    COUNTRY, EVENTS, FLOOD_MONTHS = (_fsig.COUNTRY, _fsig.EVENTS,
                                     _fsig.FLOOD_MONTHS)
    PARAMS, TELEMETRY_ONLY = _fsig.PARAMS, _fsig.TELEMETRY_ONLY
    # getattr fallback: a stale cached flood_signals module (seen on
    # Streamlit Cloud after rapid successive pushes) must degrade to the
    # local helper, not take the whole view down with an ImportError
    if hasattr(_fsig, "pentad_end"):
        pentad_end = _fsig.pentad_end
    else:
        def pentad_end(granule_start: str) -> str:
            import calendar as _cal
            d = dt.date.fromisoformat(granule_start)
            end = d.replace(day=_cal.monthrange(d.year, d.month)[1]) \
                if d.day >= 26 else d + dt.timedelta(days=4)
            return end.strftime("%b %-d")

    gj = _json.loads(open(pathlib.Path(__file__).resolve().parent.parent /
                          "data" / "zones" / "basins.geojson").read())
    props = {f["properties"]["zone_key"]: f["properties"]
             for f in gj["features"]}
    iso3 = _ciso
    if iso3 not in {p["iso3"] for p in props.values()}:
        st.info("No flood layer for this country yet — basin selection "
                "and event-catalog calibration are scoped (SCOPING-V2 "
                "P6 covers MOZ/MWI/MDG next).")
        return
    czones = [zk for zk, p in sorted(props.items()) if p["iso3"] == iso3]

    st.subheader(f"Flood watch — {COUNTRY[iso3]} basins")
    fs = load("SELECT * FROM flood_state WHERE zone_key IN (%s) "
              "ORDER BY granule_start" % ",".join("?" * len(czones)),
              tuple(czones))
    if fs.empty:
        st.warning(f"No flood_state data for {COUNTRY[iso3]} — the pentad "
                   "backfill may still be running; then run "
                   "compute/flood_signals.py.")
        return
    fs["date"] = pd.to_datetime(fs.granule_start)
    latest_p = fs.granule_start.max()
    cur = fs[fs.granule_start == latest_p].set_index("zone_key")

    # regional state: calibrated per-country rule (PARAMS), in season;
    # telemetry-only basins are shown but excluded from the rule
    r1, r2 = PARAMS[iso3]["region"]
    lat_month = pd.Timestamp(latest_p).month
    cur_rule = cur[~cur.index.isin(TELEMETRY_ONLY)]
    n1 = int((cur_rule.tier >= 1).sum())
    n2 = int((cur_rule.tier >= 2).sum())
    regional = (n1 >= r1 and n2 >= r2 and lat_month in FLOOD_MONTHS[iso3])
    if regional:
        st.error(f"{COUNTRY[iso3].upper()} REGIONAL FLOOD ALERT — "
                 f"{n2} basin(s) alerting, {n1} armed (rain observed "
                 f"through {pentad_end(latest_p)})")
    else:
        st.success(f"Regional state: clear — rain observed through "
                   f"{pentad_end(latest_p)} ({n1} basin(s) armed, "
                   f"{n2} alerting)")
    runs = load("SELECT last_run, latest_pentad, regional FROM flood_runs "
                "WHERE id=1")
    if not runs.empty:
        r = runs.iloc[0]
        age_h = (pd.Timestamp.now(tz="UTC")
                 - pd.Timestamp(r.last_run)).total_seconds() / 3600
        msg = (f"Signals last computed {r.last_run} UTC "
               f"({age_h:.0f}h ago); rainfall observed through "
               f"{pentad_end(r.latest_pentad)} (latest CHIRPS pentad — "
               "new pentads publish ~2-5 days after each 5-day period "
               "closes).")
        if age_h > 24 * 7:
            st.warning(msg + " — STALE: the update pipeline may not be "
                             "running; a 'clear' state this old is not "
                             "evidence of anything.")
        else:
            st.caption(msg)
    hist_al = load(
        "SELECT granule_start, state, detail, recorded_at FROM flood_alerts "
        "WHERE country=? ORDER BY granule_start DESC LIMIT 12", (iso3,))
    with st.expander(f"Alert history ({len(hist_al)} recorded)"):
        if hist_al.empty:
            st.caption("No regional alerts recorded since the layer went "
                       "live (Aug 2026). Historical episodes before that "
                       "appear in the backtest, not here.")
        else:
            st.dataframe(hist_al, hide_index=True, use_container_width=True)

    # basin table with GEFS outlook + downstream pairing
    gefs = load("SELECT zone_key, horizon, fcst_mm, pct_clim FROM flood_gefs "
                "WHERE issue_date = (SELECT max(issue_date) FROM flood_gefs)")
    g10 = gefs[gefs.horizon == "10_day"].set_index("zone_key")
    rows = []
    for zk, r in cur.iterrows():
        p = props.get(zk, {})
        rows.append({
            "basin": p.get("name", zk) +
            (" ‡" if zk in TELEMETRY_ONLY else ""),
            "tier": {0: "—", 1: "WATCH", 2: "ALERT"}.get(int(r.tier), "—"),
            "signature": r.signature or "—",
            "pentad mm": round(r.rain_mm, 1),
            "% of normal": None if pd.isna(r.pct_normal) else round(r.pct_normal),
            "antecedent pctile": None if pd.isna(r.ante_pct) else round(r.ante_pct),
            "GEFS 10-day mm": None if zk not in g10.index else round(g10.loc[zk, "fcst_mm"], 1),
            "drains to": props.get(p.get("downstream") or "", {}).get("name", "—"),
            "routing (days)": p.get("routing_days") or "—",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if any(zk in TELEMETRY_ONLY for zk in cur.index):
        st.caption("‡ telemetry-only basin: monitored and shown, but "
                   "excluded from the calibrated regional-alert rule "
                   "(its inclusion degraded the country's backtest "
                   "precision without adding recall).")

    # ---- mechanism charts ------------------------------------------------
    # documented flood events for the selected country (chart markers)
    FLOOD_EVENTS = [(a, b, f"'{a[2:4]}") for a, b, _sev in EVENTS[iso3]]
    ORDER = {
        "KEN": ["bas_nzoia_hw", "bas_nzoia_low", "bas_lakeshore",
                "bas_nairobi", "bas_tana_hw", "bas_tana_mid", "bas_ewaso"],
        "ETH": ["bas_laketana", "bas_baro", "bas_omo_low", "bas_awash_hw",
                "bas_awash_mid", "bas_diredawa", "bas_shabelle_hw",
                "bas_shabelle_mid", "bas_genale"],
        "TZA": ["bas_mwanza", "bas_pangani_hw", "bas_wami", "bas_dar",
                "bas_kilombero", "bas_rufiji_low", "bas_lindi"],
        "RWA": ["bas_sebeya", "bas_nyabarongo", "bas_akanyaru",
                "bas_rusizi"],
        "UGA": ["bas_semliki", "bas_nyamwamba", "bas_kampala", "bas_kyoga",
                "bas_elgon"],
    }
    BORDER = [zk for zk in ORDER.get(iso3, czones) if zk in czones] + \
        [zk for zk in czones if zk not in ORDER.get(iso3, [])]

    def regional_diamonds(df):
        """Regional-alert pentads with dominant signature."""
        rows = []
        for d, grp in df.groupby("date"):
            grp = grp[~grp.zone_key.isin(TELEMETRY_ONLY)]
            n1 = int((grp.tier >= 1).sum())
            n2 = int((grp.tier >= 2).sum())
            if n1 >= r1 and n2 >= r2 and d.month in FLOOD_MONTHS[iso3]:
                sigs = set(grp[grp.tier >= 2].signature.dropna())
                rows.append((d, "whiplash" if sigs == {"whiplash"}
                             else "saturation"))
        return pd.DataFrame(rows, columns=["date", "sig"])

    def mechanism_chart(hist, title, show_bursts, gefs=None):
        blabel = {zk: props.get(zk, {}).get("name", zk) for zk in BORDER}
        f = go.Figure()
        piv = hist.pivot_table(index="zone_key", columns="date",
                               values="ante_pct").reindex(BORDER)
        f.add_heatmap(
            x=piv.columns, y=[blabel[z] for z in piv.index], z=piv.values,
            colorscale=[[0, "#d9c8a0"], [0.5, "#f5f2ea"],
                        [0.8, "#a8c8e8"], [1, "#2f6db3"]],
            zmin=0, zmax=100,
            colorbar=dict(title="catchment<br>wetness<br>pctile",
                          thickness=12))
        if show_bursts:
            bursts = hist[(hist.pct_normal >= 150) & (hist.rain_mm >= 15)]
            if not bursts.empty:
                f.add_scatter(
                    x=bursts.date,
                    y=[blabel.get(z, z) for z in bursts.zone_key],
                    mode="markers",
                    marker=dict(size=list(bursts.pct_normal.clip(150, 500)
                                          / 45),
                                color="black", symbol="circle-open",
                                line=dict(width=1.8)),
                    name="rainfall burst ≥150% of normal")
        al = hist[hist.tier == 2]
        if not al.empty:
            f.add_scatter(x=al.date,
                          y=[blabel.get(z, z) for z in al.zone_key],
                          mode="markers",
                          marker=dict(size=10, color="crimson",
                                      symbol="x-thin",
                                      line=dict(width=2.4, color="crimson")),
                          name="basin flood alert")
        reg = regional_diamonds(hist)
        REG_NAME = {
            "saturation": "Regional flood warning triggered "
                          "(ground saturation)",
            "whiplash": "Regional flood warning (whiplash effect)",
        }
        for sig, color in (("saturation", "#1a2f6b"), ("whiplash", "#e07b00")):
            sub = reg[reg.sig == sig]
            if not sub.empty:
                f.add_scatter(x=sub.date,
                              y=["REGIONAL ALERT"] * len(sub),
                              mode="markers",
                              marker=dict(size=11, color=color,
                                          symbol="diamond"),
                              name=REG_NAME[sig])
        w0 = hist.date.min()
        for x0, x1, lbl in FLOOD_EVENTS:
            if pd.Timestamp(x1) >= w0:
                for edge in (max(pd.Timestamp(x0), w0).isoformat(), x1):
                    f.add_vline(x=edge, line_width=1.5, line_color="crimson",
                                line_dash="dot", layer="above")
                f.add_annotation(x=max(pd.Timestamp(x0), w0).isoformat(),
                                 y=1.05, yref="paper", text=lbl,
                                 showarrow=False, xanchor="left",
                                 font=dict(size=11, color="crimson"))
        if gefs is not None and not gefs.empty:
            x0 = hist.date.max() + pd.Timedelta(days=5)
            f.add_vrect(x0=x0, x1=x0 + pd.Timedelta(days=10),
                        fillcolor="gray", opacity=0.10, line_width=0)
            wet_fc = gefs[gefs.pct_clim >= 150].index.tolist()
            f.add_annotation(
                x=(x0 + pd.Timedelta(days=5)).isoformat(), y=1.05,
                yref="paper", showarrow=False,
                font=dict(size=11, color="gray"),
                text=("GEFS 10-day: " + (", ".join(
                    blabel.get(z, z) for z in wet_fc) + " wet"
                    if wet_fc else "no heavy rain signalled")))
        f.update_layout(title=title, height=420,
                        margin=dict(t=40, b=10),
                        legend=dict(orientation="h", y=-0.15))
        return f

    st.plotly_chart(
        mechanism_chart(fs[fs.date >= fs.date.max() - pd.Timedelta(days=365)],
                        "Flood risk timeline — last 12 months",
                        show_bursts=True, gefs=g10),
        use_container_width=True)

    # ---- current basin-state map (mirrors the WRSI country map) ---------
    def _basin_color(row):
        if row is None:
            return "#d8d8d8", "no data"
        if row.tier >= 2:
            return "#d95f4b", "basin flood ALERT"
        if row.tier >= 1:
            return "#ec9b3b", "armed / watch"
        if (row.pct_normal or 0) >= 150:
            return "#e8c84d", "rainfall burst ≥150% of normal"
        return "#a8c6e4", "quiet"

    fb = go.Figure()
    _fl_bounds = None
    _oc = _dbdir.parent / "data/zones/country_outlines.geojson"
    if _oc.exists():
        for f in _json.loads(_oc.read_text())["features"]:
            if f["properties"]["iso3"] != iso3:
                continue
            pls = f["geometry"]["coordinates"]
            if f["geometry"]["type"] == "Polygon":
                pls = [pls]
            for rings in pls:
                _ox = [q[0] for q in rings[0]]
                _oy = [q[1] for q in rings[0]]
                if _fl_bounds is None:
                    _fl_bounds = [min(_ox), min(_oy), max(_ox), max(_oy)]
                else:
                    _fl_bounds = [min(_fl_bounds[0], min(_ox)),
                                  min(_fl_bounds[1], min(_oy)),
                                  max(_fl_bounds[2], max(_ox)),
                                  max(_fl_bounds[3], max(_oy))]
                fb.add_scatter(x=_ox, y=_oy,
                               mode="lines", name="",
                               line=dict(color="#9a9a9a", width=1),
                               fill="toself", fillcolor="#f4f1ea",
                               hoverinfo="skip", showlegend=False)
    _bx, _by, _bt = [], [], []
    for f in gj["features"]:
        p = f["properties"]
        if p["iso3"] != iso3:
            continue
        zk = p["zone_key"]
        row = cur.loc[zk] if zk in cur.index else None
        fc, state = _basin_color(row)
        tip = (f"<b>{p.get('name', zk)}</b><br>{state}"
               + ("" if row is None else
                  f"<br>catchment wetness: {row.ante_pct:.0f}th pctile"
                  f"<br>pentad rain: {row.pct_normal:.0f}% of normal"
                  + (f"<br>signature: {row.signature}"
                     if row.signature else ""))
               + "<extra></extra>")
        pls = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            pls = [pls]
        for rings in pls:
            fb.add_scatter(x=[q[0] for q in rings[0]],
                           y=[q[1] for q in rings[0]],
                           mode="lines", fill="toself", fillcolor=fc,
                           opacity=0.8, name="",
                           line=dict(color="white", width=0.7),
                           hovertemplate=tip, showlegend=False)
        _bx.append(p.get("label_lon"))
        _by.append(p.get("label_lat"))
        _bt.append("" if row is None or pd.isna(row.ante_pct)
                   else f"{row.ante_pct:.0f}")
    fb.add_scatter(x=_bx, y=_by, mode="text", text=_bt, name="",
                   textfont=dict(size=10, color="#1f2733"),
                   hoverinfo="skip", showlegend=False)
    _reg_sfx = "  —  🔴 REGIONAL ALERT ACTIVE" if regional else ""
    fb.update_layout(
        height=480, margin=dict(l=0, r=0, t=30, b=0),
        title=dict(text="Current basin state (numbers = catchment "
                        f"wetness percentile){_reg_sfx}",
                   font=dict(size=13)),
        # frame the COUNTRY, not the basin extents: level-7 basins
        # legitimately cross borders (Akanyaru is the Burundi border
        # river) and autoscaling let the spill dominate tiny countries
        # like Rwanda
        xaxis=dict(visible=False,
                   range=None if _fl_bounds is None else
                   [_fl_bounds[0] - 0.3, _fl_bounds[2] + 0.3]),
        yaxis=dict(visible=False, scaleanchor="x",
                   range=None if _fl_bounds is None else
                   [_fl_bounds[1] - 0.3, _fl_bounds[3] + 0.3]),
        plot_bgcolor="white", dragmode="pan")
    st.plotly_chart(fb, use_container_width=True)
    st.caption("🔴 red = basin flood alert · 🟠 orange = armed/watch "
               "(saturated catchment) · 🟡 gold = rainfall burst ≥150% "
               "of normal this pentad · 🔵 blue = quiet · gray = no "
               "data. Hover a basin for detail; the regional alert "
               "requires multiple basins armed/alerting in season.")

    with st.expander("Full history 1998–2026 — every documented flood vs "
                     "the signal record"):
        st.plotly_chart(
            mechanism_chart(fs[fs.date >= "1998-01-01"],
                            "Flood risk timeline, 1998–2026",
                            show_bursts=False),
            use_container_width=True)
    st.caption(
        "How to read this: a basin alert (red ×) fires when very heavy "
        "rain lands on ground that is already unusually wet — or, for the "
        "orange 'whiplash' case, on ground baked dry by drought. A REGIONAL "
        "ALERT (diamond) needs at least two basins in that state at once "
        "during a rainy season, which filters out most one-off local "
        "storms. In testing against 1999–2026, the Kenya regional alert "
        "flagged every major flood since 2006, usually days to weeks ahead; "
        "roughly half its firings were followed by a major flood, with a "
        "false alarm about every one to two years. Ethiopia, Tanzania, "
        "Rwanda and Uganda use the same rule — see each country's backtest "
        "in compute/flood_signals.py output for its own hit/false-alarm "
        "record before leaning on it. Individual basin alerts "
        "fire much more often and are best read alongside the rest of the "
        "chart. The gray band shows the rainfall forecast for the next "
        "10 days.")
    return


# ======================== HYDRO SECTION (Country tab) =====
def _hydro_section(_ciso):
    st.divider()
    st.header("Hydropower")
    if _ciso != "ZMB":
        st.info("No hydropower monitor for this country — Kariba (Zambia) only; the seven-dam catchment expansion is scoped as P5.")
        return
    st.subheader("Hydropower — Lake Kariba (Zambia)")
    hy = load("SELECT status, reason, as_of FROM country_risk WHERE "
              "country='ZMB' AND factor='hydro'")
    if not hy.empty:
        h = hy.iloc[0]
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴",
                "gray": "⚪"}.get(h.status, "⚪")
        (st.success if h.status == "green" else
         st.warning if h.status == "yellow" else
         st.error if h.status == "red" else st.info)(
            f"{icon} {h.reason} (as of {h.as_of})")

    kb = load("SELECT date, level_m, pct_full FROM ("
              "SELECT date, level_m, pct_full FROM kariba_reservoir WHERE "
              "level_m IS NOT NULL UNION SELECT date, level_m, pct_full "
              "FROM kariba_level WHERE level_m IS NOT NULL) ORDER BY date")
    if kb.empty:
        st.warning("No Kariba data — run ingest/kariba.py.")
        return
    kb["date"] = pd.to_datetime(kb.date)
    kb = kb.drop_duplicates("date", keep="last").set_index("date")

    # ---- water level, full history --------------------------------------
    fk = go.Figure()
    fk.add_scatter(x=kb.index, y=kb.level_m, name="level (m)",
                   line=dict(color="seagreen"), connectgaps=False)
    pct = kb.pct_full.dropna()
    if not pct.empty:
        fk.add_scatter(x=pct.index, y=pct, name="usable storage (%)",
                       yaxis="y2", line=dict(color="steelblue", width=1,
                                             dash="dot"),
                       connectgaps=False)
    fk.add_hline(y=478, line_dash="dot", line_color="crimson",
                 annotation_text="478m severe rationing",
                 annotation_font=dict(size=11, color="crimson"))
    fk.add_hline(y=475.5, line_dash="dot", line_color="darkred",
                 annotation_text="475.5m minimum operating level",
                 annotation_position="bottom right",
                 annotation_font=dict(size=11, color="darkred"))
    fk.update_layout(
        title="Lake Kariba water level (Zambezi River Authority)",
        height=420, margin=dict(t=40, b=0),
        yaxis=dict(title="m", range=[474.5,
                                     max(490.0, kb.level_m.max() + 1)]),
        yaxis2=dict(title="% usable", overlaying="y", side="right",
                    range=[0, 100], showgrid=False),
        legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fk, use_container_width=True)
    st.caption("Daily gauge scraped from the ZRA (history seeded from the "
               "elnino-hydro-dashboard archive, 2017–; gaps are unscraped "
               "periods, not outages). Usable storage is % of live "
               "storage — the 2022 and 2024 crises bottomed near 5–10%.")

    # ---- drawdown monitor: trailing gap-free window ----------------------
    rs = load("SELECT date, level_m, turbine_discharge_m3s FROM "
              "kariba_reservoir ORDER BY date")
    rs["date"] = pd.to_datetime(rs.date)
    idx = rs.set_index("date").sort_index()
    dates = idx.index.to_series()
    gaps = dates.diff() > pd.Timedelta(days=7)
    run_start = dates[gaps].iloc[-1] if gaps.any() else dates.iloc[0]
    cutoff = max(run_start, idx.index.max() - pd.Timedelta(days=120))

    lvl_all = kb.level_m.resample("D").mean().interpolate(limit=10)
    rate = ((lvl_all.shift(28) - lvl_all) / 4.0).dropna()
    rate = rate[rate.index >= cutoff]
    td = idx.turbine_discharge_m3s.dropna()
    td30 = td.rolling("30D").mean()

    fd = go.Figure()
    if not rate.empty:
        fd.add_scatter(x=rate.index, y=rate, name="4-week drawdown rate",
                       line=dict(color="crimson", width=2))
    for y, lbl, color in ((0.15, "0.15 m/wk — 478m in play pre-refill",
                           "orange"),
                          (0.20, "0.20 m/wk — 478m by mid-Jan", "crimson")):
        fd.add_hline(y=y, line_dash="dot", line_color=color,
                     annotation_text=lbl,
                     annotation_font=dict(size=11, color=color))
    fd.add_hline(y=0, line_color="gray", line_width=1)
    if not td.empty:
        sel = td[td.index >= cutoff]
        fd.add_scatter(x=sel.index, y=sel, name="turbine discharge (m³/s)",
                       yaxis="y2", mode="markers",
                       marker=dict(size=4, color="steelblue", opacity=0.5))
        sel30 = td30[td30.index >= cutoff]
        fd.add_scatter(x=sel30.index, y=sel30, name="discharge 30-day mean",
                       yaxis="y2", line=dict(color="steelblue", width=2))
    fd.update_layout(
        title="Kariba drawdown monitor — recent weeks",
        height=420, margin=dict(t=40, b=0),
        yaxis=dict(title="level fall, m/week"),
        yaxis2=dict(title="m³/s", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero"),
        legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fd, use_container_width=True)
    st.caption(
        "The drawdown rate (level fall per week, positive = falling) is "
        "the red-flag variable: it responds to generation decisions weeks "
        "before load shedding is announced. Sustained readings above "
        "0.15 m/wk put the 478m severe-rationing boundary in play before "
        "the mid-February refill; above 0.20 m/wk it is reached by "
        "mid-January regardless of the rains. Discharge dots show the "
        "turbine flow driving the drawdown (window limited to the "
        "gap-free scrape era). Thresholds calibrated in the "
        "elnino-hydro-dashboard project on 2017–2026 seasons.")
    return


# ======================== COUNTRY TAB =====================
import json as _json  # noqa: E402

_iso_opts = [c for c in NAMES_CR if c in set(zones.iso3)]
_ciso = st.sidebar.selectbox("Country", _iso_opts,
                             format_func=lambda c: NAMES_CR.get(c, c))
_czones = zones[zones.iso3 == _ciso]


def _wrsi_band(w, ins):
    """Mirror the WRSI chart bands (gold 80-94, orange 60-79, red <60)."""
    if w is None or pd.isna(w) or not ins:
        return "#d8d8d8", "off-season / no data"
    if w >= 95:
        return "#79c47e", "normal (≥95)"
    if w >= 80:
        return "#e8c84d", "mild stress (80–94)"
    if w >= 60:
        return "#ec9b3b", "stressed (60–79)"
    return "#d95f4b", "crop-failure risk (<60)"


_zrisk_map = load("SELECT zone_key, wrsi, wrsi_in_season, spi3, sm, "
                  "season FROM zone_risk WHERE factor IN "
                  "('drought','export')").drop_duplicates("zone_key") \
    .set_index("zone_key")
_zgj = _json.loads((_dbdir.parent / "data/zones/zones.geojson").read_text())
_out_path = _dbdir.parent / "data/zones/country_outlines.geojson"

fmap = go.Figure()
_cn_bounds = None
if _out_path.exists():
    for f in _json.loads(_out_path.read_text())["features"]:
        if f["properties"]["iso3"] != _ciso:
            continue
        polys = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            polys = [polys]
        for rings in polys:
            xs = [p[0] for p in rings[0]]
            ys = [p[1] for p in rings[0]]
            if _cn_bounds is None:
                _cn_bounds = [min(xs), min(ys), max(xs), max(ys)]
            else:
                _cn_bounds = [min(_cn_bounds[0], min(xs)),
                              min(_cn_bounds[1], min(ys)),
                              max(_cn_bounds[2], max(xs)),
                              max(_cn_bounds[3], max(ys))]
            fmap.add_scatter(x=xs, y=ys, mode="lines",
                             line=dict(color="#9a9a9a", width=1),
                             fill="toself", fillcolor="#f4f1ea",
                             hoverinfo="skip", showlegend=False)
_map_zones = []
for f in _zgj["features"]:
    p = f["properties"]
    zk = p["zone_key"]
    if p["iso3"] != _ciso or zk.endswith(("_basket", "_belt_x")) or \
            zk in ("ken_grain_basket", "zmb_maize_belt",
                   "zaf_maize_triangle"):
        continue
    r = _zrisk_map.loc[zk] if zk in _zrisk_map.index else None
    w = None if r is None else r.wrsi
    ins = 0 if r is None else r.wrsi_in_season
    fc, blab = _wrsi_band(w, ins)
    meta = _czones.set_index("zone_key").loc[zk] \
        if zk in set(_czones.zone_key) else None
    sector = getattr(meta, "sector", None) or "staple"
    hover = (f"<b>{zone_label.get(zk, zk)}</b><br>"
             f"{(getattr(meta, 'crop', None) or '?')}"
             f"{' · export belt' if sector == 'export' else ''}<br>"
             f"WRSI: {'—' if w is None or pd.isna(w) else f'{w:.0f}'} "
             f"({blab})<br>"
             + ("" if r is None or pd.isna(r.spi3)
                else f"SPI-3: {r.spi3:.1f}<br>")
             + ("" if r is None or pd.isna(r.sm)
                else f"Soil moisture: {r.sm:.0f}%<br>")
             + ("" if r is None or not r.season
                else f"Season: {r.season}")
             + "<extra></extra>")
    polys = f["geometry"]["coordinates"]
    if f["geometry"]["type"] == "Polygon":
        polys = [polys]
    first = True
    for rings in polys:
        xs = [pt[0] for pt in rings[0]]
        ys = [pt[1] for pt in rings[0]]
        fmap.add_scatter(
            x=xs, y=ys, mode="lines", fill="toself", fillcolor=fc,
            opacity=0.85, name="",
            line=dict(color="#5f4379" if sector == "export" else "white",
                      width=1.6 if sector == "export" else 0.7),
            customdata=[zk] * len(xs), hovertemplate=hover,
            showlegend=False)
        first = False
    _map_zones.append((zk, f, w, hover))
# centroid markers: the WRSI value labels AND fat click targets —
# clicking a polygon interior does not emit a plotly point selection,
# clicking the (invisible) centroid marker does
if _map_zones:
    # label points precomputed by ingest/zones_geo.py — the deployed
    # app must not import shapely (not in requirements.txt)
    _cx, _cy, _ct, _ck, _ch = [], [], [], [], []
    for zk, f, w, hover in _map_zones:
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0]
        if f["geometry"]["type"] == "MultiPolygon":
            ring = ring[0]
        _cx.append(p.get("label_lon",
                         sum(q[0] for q in ring) / len(ring)))
        _cy.append(p.get("label_lat",
                         sum(q[1] for q in ring) / len(ring)))
        _ct.append("" if w is None or pd.isna(w) else f"{w:.0f}")
        _ck.append(zk)
        _ch.append(hover)
    fmap.add_scatter(
        x=_cx, y=_cy, mode="markers+text", text=_ct, name="",
        textfont=dict(size=11, color="#1f2733", family="sans-serif"),
        marker=dict(size=30, color="rgba(0,0,0,0)"),
        customdata=_ck, hovertemplate=_ch, showlegend=False)
fmap.update_layout(
    height=540, margin=dict(l=0, r=0, t=30, b=0),
    title=dict(text=f"Current WRSI by growing region — "
                    f"{NAMES_CR.get(_ciso, _ciso)} (click a zone to open "
                    "its charts below)", font=dict(size=13)),
    xaxis=dict(visible=False,
               range=None if _cn_bounds is None else
               [_cn_bounds[0] - 0.3, _cn_bounds[2] + 0.3]),
    yaxis=dict(visible=False, scaleanchor="x",
               range=None if _cn_bounds is None else
               [_cn_bounds[1] - 0.3, _cn_bounds[3] + 0.3]),
    plot_bgcolor="white", dragmode="pan")
_ev = st.plotly_chart(fmap, use_container_width=True,
                      on_select="rerun", key=f"hydmap_{_ciso}")
try:
    _pts = _ev.selection.points if _ev else []
    if _pts:
        st.session_state["hyd_zone"] = _pts[0]["customdata"]
except Exception:
    pass
st.caption("Shading mirrors the WRSI chart bands: green ≥95, gold 80–94, "
           "orange 60–79, red <60; gray = off-season or no data. Purple "
           "edges mark export belts (clipped to their GAEZ crop "
           "footprint). Hover for SPI-3, soil moisture and season.")

_opts = list(_czones.zone_key)
_default = st.session_state.get("hyd_zone")
zone_key = st.sidebar.selectbox(
    "Crop zone", _opts,
    index=_opts.index(_default) if _default in _opts else 0,
    format_func=lambda k: zone_label[k])
zrow = zones.set_index("zone_key").loc[zone_key]

# ---- season handling ----------------------------------------------------
seasons = []
for tok in (zrow.seasons or "").split(","):
    if tok:
        name, rng = tok.split(":")
        a, b = rng.split("-")
        seasons.append((name, int(a), int(b)))
season_name, s_start, s_end = seasons[0] if len(seasons) == 1 else \
    seasons[st.sidebar.radio("Season", range(len(seasons)),
                             format_func=lambda i: seasons[i][0])]
cross_year = s_end < s_start

today = dt.date.today()


def season_year_of(d: dt.date) -> int:
    """Label a date with its season start-year."""
    if not cross_year:
        return d.year
    return d.year if d.month >= s_start else d.year - 1


def in_season(m: int) -> bool:
    return (s_start <= m <= s_end) if not cross_year else \
        (m >= s_start or m <= s_end)


# ---- rainfall: cumulative season-to-date vs climatology -----------------
# EWX pentads where available; local CHIRPS v3 dekads otherwise (composites)
pent = load(
    "SELECT granule_start, granule_end, value, dataset FROM observations "
    "WHERE zone_key=? AND dataset IN "
    "('chirps_global_pentad_data','chirps-prelim_global_pentad_data') "
    "ORDER BY granule_start", (zone_key,))
granularity = "pentads"
if pent.empty:
    pent = load(
        "SELECT granule_start, granule_end, value, dataset FROM observations "
        "WHERE zone_key=? AND dataset IN "
        "('chirps3local_dekad_data','chirps3local-prelim_dekad_data') "
        "ORDER BY granule_start", (zone_key,))
    granularity = "dekads"
if pent.empty:
    st.warning("No rainfall data ingested yet for this zone — backfill may "
               "still be running.")
    st.stop()

# the API pads the final series with prelim and GEFS *forecast* granules;
# classify by the catalog end dates stored at ingest time
meta = load("SELECT dataset, granule_end FROM dataset_meta")
ends = dict(zip(meta.dataset, pd.to_datetime(meta.granule_end).dt.date)) \
    if not meta.empty else {}
final_end = ends.get("chirps_global_pentad_data", dt.date(1900, 1, 1))
prelim_end = ends.get("chirps-prelim_global_pentad_data", today)

starts = pd.to_datetime(pent["granule_start"])
pent["date"] = starts.dt.date
pent["month"] = starts.dt.month
pent["day"] = starts.dt.day
if granularity == "dekads":
    # local pipeline: provenance is explicit in the dataset name, and the
    # series contains no forecast padding
    pent["source"] = np.where(pent["dataset"].str.contains("prelim"),
                              "prelim", "final")
    pent = (pent.sort_values("source")  # 'final' < 'prelim': final wins
            .drop_duplicates("granule_start", keep="first"))
    forecast = pent.iloc[0:0].copy()
else:
    pent = (pent.sort_values("dataset")  # prelim sorts first; final wins
            .drop_duplicates("granule_start", keep="last"))
    pent["source"] = "final"
    pent.loc[pent["date"] > final_end, "source"] = "prelim"
    pent.loc[pent["date"] > prelim_end, "source"] = "forecast"
    forecast = pent[pent["source"] == "forecast"].copy()
    pent = pent[pent["source"] != "forecast"].copy()
series_end = pent["date"].max()  # before season filtering
pent = pent[pent["month"].map(in_season)].copy()
pent["syear"] = pent["date"].map(season_year_of)
# day-of-season index for alignment across years
pent["doy_key"] = [
    f"{(m - s_start) % 12:02d}-{d:02d}"
    for m, d in zip(pent["month"], pent["day"])
]
pent = pent.sort_values(["syear", "doy_key"])
pent["cum"] = pent.groupby("syear")["value"].cumsum()

clim_years = range(CLIM_START, CLIM_END + 1)
clim = (pent[pent["syear"].isin(clim_years)]
        .groupby("doy_key")["cum"]
        .agg(clim_mean="mean",
             clim_p20=lambda s: s.quantile(0.2),
             clim_p80=lambda s: s.quantile(0.8))
        .reset_index())

cur_sy = season_year_of(today)
cur = pent[pent.syear == cur_sy]
prev = pent[pent.syear == cur_sy - 1]

if (today - series_end).days > 30:
    st.info(f"Series for this zone currently ends {series_end} — the local "
            "CHIRPS v3 backfill is still in progress (composite zones exist "
            "only in the local pipeline). Charts show what has been "
            "ingested so far.")

c1, c2, c3, c4 = st.columns(4)
if not cur.empty:
    merged = clim[clim.doy_key <= cur.doy_key.max()]
    pct = 100 * cur.cum.iloc[-1] / merged.clim_mean.iloc[-1] \
        if not merged.empty and merged.clim_mean.iloc[-1] > 0 else float("nan")
    c1.metric(f"{season_name} {cur_sy} season-to-date",
              f"{cur.cum.iloc[-1]:.0f} mm", f"{pct:.0f}% of normal",
              delta_color="off")
    c2.metric("Latest pentad",
              f"{cur.value.iloc[-1]:.0f} mm",
              cur["source"].iloc[-1], delta_color="off")

# WRSI + soil moisture latest
lw = load("SELECT granule_start, value FROM observations WHERE zone_key=? "
          "AND dataset='lwrsi_africa_dekad_pctm' ORDER BY granule_start",
          (zone_key,))
if not lw.empty:
    c3.metric("Water Req. Satisfaction Index (% of median)", f"{lw.value.iloc[-1]:.0f}%",
              lw.granule_start.iloc[-1], delta_color="off")
sm = load("SELECT granule_start, value FROM observations WHERE zone_key=? "
          "AND dataset='soilmoisture-0-100cm_global_month_pctm' "
          "ORDER BY granule_start", (zone_key,))
if not sm.empty:
    c4.metric("Root-zone soil moisture (% of mean)",
              f"{sm.value.iloc[-1]:.0f}%", sm.granule_start.iloc[-1],
              delta_color="off")

# cumulative chart
fig = go.Figure()
fig.add_scatter(x=clim.doy_key, y=clim.clim_p80, line=dict(width=0),
                showlegend=False, hoverinfo="skip")
fig.add_scatter(x=clim.doy_key, y=clim.clim_p20, fill="tonexty",
                fillcolor="rgba(120,120,120,0.2)", line=dict(width=0),
                name="20–80th pct 1991–2020")
fig.add_scatter(x=clim.doy_key, y=clim.clim_mean,
                line=dict(color="gray", dash="dash"), name="1991–2020 mean")
# El Niño comparator seasons
for nino_sy, color in ((2015, "darkorange"), (2023, "mediumpurple")):
    nino = pent[pent.syear == nino_sy]
    if not nino.empty:
        label = (f"{nino_sy}-{str(nino_sy + 1)[2:]} El Niño" if cross_year
                 else f"{nino_sy} (El Niño)")
        fig.add_scatter(x=nino.doy_key, y=nino.cum,
                        line=dict(color=color, dash="dot", width=2),
                        name=label)
if not prev.empty:
    fig.add_scatter(x=prev.doy_key, y=prev.cum,
                    line=dict(color="steelblue"), name=f"{cur_sy - 1}")
if not cur.empty:
    fig.add_scatter(x=cur.doy_key, y=cur.cum,
                    line=dict(color="crimson", width=3), name=f"{cur_sy}")
    # dotted CHIRPS-GEFS forecast extension off the last observed point
    fc = forecast[forecast["date"].map(season_year_of) == cur_sy]
    if not fc.empty:
        fc = fc.sort_values("date")
        fc_keys = [f"{(m - s_start) % 12:02d}-{d:02d}"
                   for m, d in zip(fc["month"], fc["day"])]
        fig.add_scatter(
            x=[cur.doy_key.iloc[-1]] + fc_keys,
            y=cur.cum.iloc[-1] + pd.concat(
                [pd.Series([0.0]), fc["value"]]).cumsum().values,
            line=dict(color="crimson", width=2, dash="dot"),
            name="GEFS forecast")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
n_months = (s_end - s_start) % 12 + 1
fig.update_xaxes(
    type="category",
    tickvals=[f"{o:02d}-01" for o in range(n_months)],
    ticktext=[MONTHS[(s_start - 1 + o) % 12] for o in range(n_months)])
fig.update_layout(
    title=f"Cumulative rainfall, {season_name} season",
    xaxis_title=None,
    yaxis_title="mm", height=420, margin=dict(t=40, b=0))
st.plotly_chart(fig, use_container_width=True)

# rate view: each dekad as % of its own 1991-2020 normal — makes a
# one-dekad recovery or collapse visible where the cumulative line hides it
rate = load("SELECT granule_start, pct_normal FROM dekad_metrics WHERE "
            "zone_key=? AND pct_normal IS NOT NULL "
            "ORDER BY granule_start DESC LIMIT 18", (zone_key,))
if not rate.empty:
    rate = rate.sort_values("granule_start")
    fr = go.Figure()
    fr.add_bar(x=rate.granule_start, y=rate.pct_normal,
               marker_color=["indianred" if v < 80 else
                             "goldenrod" if v < 95 else "seagreen"
                             for v in rate.pct_normal])
    fr.add_hline(y=100, line_dash="dash", line_color="gray")
    fr.update_xaxes(type="category", tickangle=-45, tickfont=dict(size=10))
    fr.update_layout(title="Rainfall vs normal, 10-day periods "
                           "(last 6 months)",
                     yaxis_title="% of normal", height=280,
                     margin=dict(t=40, b=0), showlegend=False)
    st.plotly_chart(fr, use_container_width=True)
    st.caption("Each bar compares a 10-day total to the 1991–2020 average "
               "for that same period of the year, so seasonality is already "
               "accounted for: 100% = typical for the time of year.")

# ---- WRSI and soil moisture panels --------------------------------------
col_a, col_b = st.columns(2)
with col_a:
    if not lw.empty:
        lw["date"] = pd.to_datetime(lw.granule_start)
        f2 = go.Figure()
        f2.add_scatter(x=lw.date, y=lw.value,
                       name="WRSI % of median")
        f2.add_hline(y=100, line_dash="dash", line_color="gray")
        f2.add_hrect(y0=80, y1=94, fillcolor="gold", opacity=0.13,
                     line_width=0, annotation_text="mild stress",
                     annotation_position="top left",
                     annotation_font_size=11)
        f2.add_hrect(y0=60, y1=80, fillcolor="orange", opacity=0.15,
                     line_width=0, annotation_text="moderate stress",
                     annotation_position="top left",
                     annotation_font_size=11)
        f2.add_hrect(y0=0, y1=60, fillcolor="orangered", opacity=0.15,
                     line_width=0, annotation_text="severe stress",
                     annotation_position="top left",
                     annotation_font_size=11)
        recent_lw = lw[lw.date >= lw.date.max() - pd.Timedelta(days=1460)]
        f2.update_yaxes(range=[min(55.0, recent_lw.value.min() - 5),
                               max(140.0, recent_lw.value.max() + 5)])
        f2.update_layout(title="Water Requirement Satisfaction Index, % of "
                               "1982–2021 median (dekadal)",
                         height=340, margin=dict(t=40, b=0),
                         xaxis_range=[lw.date.max() - pd.Timedelta(days=1460),
                                      lw.date.max()])
        st.plotly_chart(f2, use_container_width=True)
with col_b:
    if not sm.empty:
        sm["date"] = pd.to_datetime(sm.granule_start)
        f3 = go.Figure()
        f3.add_scatter(x=sm.date, y=sm.value, name="0–100cm % of mean",
                       line=dict(color="saddlebrown"))
        f3.add_hline(y=100, line_dash="dash", line_color="gray")
        f3.add_hrect(y0=85, y1=95, fillcolor="gold", opacity=0.15,
                     line_width=0, annotation_text="watch territory",
                     annotation_position="top left",
                     annotation_font_size=11)
        f3.add_hrect(y0=75, y1=85, fillcolor="orangered", opacity=0.13,
                     line_width=0, annotation_text="issue level",
                     annotation_position="top left",
                     annotation_font_size=11)
        f3.update_layout(title="FLDAS root-zone soil moisture, % of mean "
                               "(monthly)", height=340,
                         margin=dict(t=40, b=0),
                         xaxis_range=[sm.date.max() - pd.Timedelta(days=1460),
                                      sm.date.max()])
        st.plotly_chart(f3, use_container_width=True)

# ---- Phase 2: SPI, onset/dry-spell, EWX cross-check ---------------------
have_metrics = not load(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name='dekad_metrics'").empty
if have_metrics:
    dm = load("SELECT granule_start, rain_mm, pct_normal, spi1, spi3, source "
              "FROM dekad_metrics WHERE zone_key=? ORDER BY granule_start",
              (zone_key,))
    if not dm.empty:
        dm["date"] = pd.to_datetime(dm.granule_start)
        recent = dm[dm.date >= dm.date.max() - pd.Timedelta(days=1825)]
        f4 = go.Figure()
        f4.add_scatter(x=recent.date, y=recent.spi1, name="SPI-1 (1 month)",
                       line=dict(color="steelblue", width=1))
        f4.add_scatter(x=recent.date, y=recent.spi3, name="SPI-3 (3 months)",
                       line=dict(color="crimson", width=2))
        f4.add_hline(y=0, line_dash="dash", line_color="gray")
        f4.add_hline(y=-1, line_dash="dot", line_color="orange",
                     annotation_text="−1 moderate (drier than 84% of history)",
                     annotation_position="bottom left",
                     annotation_font=dict(size=11, color="orange"))
        f4.add_hline(y=-1.5, line_dash="dot", line_color="red",
                     annotation_text="−1.5 severe (drier than 93% of history)",
                     annotation_position="bottom left",
                     annotation_font=dict(size=11, color="red"))
        f4.add_hline(y=-2, line_dash="dot", line_color="darkred",
                     annotation_text="−2 extreme drought (drier than 98% of "
                                     "history)",
                     annotation_position="bottom left",
                     annotation_font=dict(size=11, color="darkred"))
        f4.update_yaxes(range=[min(-2.4, float(min(recent.spi1.min(),
                                                   recent.spi3.min())) - 0.2),
                               max(2.4, float(max(recent.spi1.max(),
                                                  recent.spi3.max())) + 0.2)])
        f4.update_layout(title="Standardized Precipitation Index (SPI)",
                         height=340,
                         margin=dict(t=40, b=0))
        st.plotly_chart(f4, use_container_width=True)
        st.caption("Computed from local CHIRPS v3 dekads; gamma-fitted "
                   "per dekad-of-year on 1991–2020.")

    sm2 = load("SELECT season_name, season_year, onset_start, "
               "onset_delay_dekads, max_dry_spell FROM season_metrics "
               "WHERE zone_key=? ORDER BY season_year DESC LIMIT 6",
               (zone_key,))
    if not sm2.empty:
        c5, c6, c7 = st.columns(3)
        latest = sm2.iloc[0]
        c5.metric(f"Onset of rains ({latest.season_name} "
                  f"{int(latest.season_year)})",
                  latest.onset_start or "not yet",
                  None if pd.isna(latest.onset_delay_dekads) else
                  f"{latest.onset_delay_dekads:+.0f} dekads vs median",
                  delta_color="inverse")
        c6.metric("Max dry spell this season",
                  f"{int(latest.max_dry_spell)} dekads",
                  "≥2 is a planting-window red flag", delta_color="off")
        with c7.expander("Past seasons"):
            st.dataframe(sm2, hide_index=True)

    with st.expander("Cross-check: local CHIRPS v3 vs USGS EWX (dekads vs "
                     "pentad-pairs may differ at month edges)"):
        loc = load("SELECT granule_start, value FROM observations WHERE "
                   "zone_key=? AND dataset='chirps3local_dekad_data' "
                   "AND granule_start >= '2024-01-01'", (zone_key,))
        ewx = load("SELECT granule_start, value FROM observations WHERE "
                   "zone_key=? AND dataset='chirps_global_pentad_data' "
                   "AND granule_start >= '2024-01-01'", (zone_key,))
        if not loc.empty and not ewx.empty:
            f5 = go.Figure()
            f5.add_scatter(x=pd.to_datetime(loc.granule_start), y=loc.value,
                           name="local v3 dekads", line=dict(color="crimson"))
            f5.add_bar(x=pd.to_datetime(ewx.granule_start), y=ewx.value,
                       name="EWX pentads", marker_color="steelblue",
                       opacity=0.5)
            f5.update_layout(height=300, margin=dict(t=20, b=0))
            st.plotly_chart(f5, use_container_width=True)
        else:
            st.caption("Need both local and EWX series for this zone.")

# ---- El Niño episode benchmarks -----------------------------------------
EPISODES = {"2015-16": ("2015-07-01", "2016-06-30"),
            "2023-24": ("2023-07-01", "2024-06-30")}
BENCH_DS = {"Water Req. Satisfaction Index (% of median)": "lwrsi_africa_dekad_pctm",
            "Root-zone SM (% of mean)": "soilmoisture-0-100cm_global_month_pctm",
            "CHIRPS monthly z-score": "chirps_global_month_zscore"}
with st.expander("El Niño benchmarks — current vs worst prints of 2015-16 "
                 "and 2023-24", expanded=False):
    brows = []
    for label, ds in BENCH_DS.items():
        cur = load("SELECT value, granule_start FROM observations WHERE "
                   "zone_key=? AND dataset=? ORDER BY granule_start DESC "
                   "LIMIT 1", (zone_key, ds))
        row = {"indicator": label,
               "current": round(cur.value.iloc[0], 1) if not cur.empty else None,
               "as of": cur.granule_start.iloc[0] if not cur.empty else None}
        for ep, (a, b) in EPISODES.items():
            m = load("SELECT value, granule_start FROM observations WHERE "
                     "zone_key=? AND dataset=? AND granule_start BETWEEN ? "
                     "AND ? ORDER BY value ASC LIMIT 1", (zone_key, ds, a, b))
            row[f"{ep} low"] = round(m.value.iloc[0], 1) if not m.empty else None
            row[f"{ep} low date"] = m.granule_start.iloc[0] if not m.empty else None
        brows.append(row)
    bdf = pd.DataFrame(brows)
    st.dataframe(bdf, hide_index=True, use_container_width=True)
    flags = [r["indicator"] for r in brows
             if r["current"] is not None and r.get("2015-16 low") is not None
             and r["current"] < min(x for x in (r.get("2015-16 low"),
                                                r.get("2023-24 low"))
                                    if x is not None)]
    if flags:
        st.error("Current print is below BOTH El Niño episode lows for: "
                 + ", ".join(flags))
    st.caption("Episode windows Jul–Jun. Episode lows are the single worst "
               "granule in the window; off-season WRSI reads ~100, so for "
               "southern-Africa zones compare during Oct–Apr only. "
               "Composite zones have no EWX series — benchmark their "
               "member zones.")

# ---- all-zones anomaly table --------------------------------------------
st.subheader("All zones — latest readings")
rows = []
for zk in zones.zone_key:
    r = {"zone": zone_label[zk]}
    d = load("SELECT dataset, granule_start, value FROM observations "
             "WHERE zone_key=? AND dataset IN ('lwrsi_africa_dekad_pctm',"
             "'soilmoisture-0-100cm_global_month_pctm',"
             "'chirps_global_month_zscore') ORDER BY granule_start", (zk,))
    for ds, label in [("lwrsi_africa_dekad_pctm", "WRSI %median (water req. satisfaction)"),
                      ("soilmoisture-0-100cm_global_month_pctm", "SM %mean"),
                      ("chirps_global_month_zscore", "CHIRPS mth z-score")]:
        sub = d[d.dataset == ds]
        r[label] = round(sub.value.iloc[-1], 1) if not sub.empty else None
    rows.append(r)
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.caption("Sources: CHC UCSB CHIRPS via USGS FEWS NET GeoEngine; "
           "FEWS NET crop zones (fews_shapefile_cropzones); FLDAS Noah; "
           "LWRSI. Prelim pentads revise when late gauge data arrives.")

# flood + hydro components for the selected country
_flood_section(_ciso)
_hydro_section(_ciso)

