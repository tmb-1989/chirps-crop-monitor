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

DB = pathlib.Path(__file__).resolve().parent.parent / "db" / "monitor.sqlite"
CLIM_START, CLIM_END = 1991, 2020

st.set_page_config(page_title="Crop-zone rainfall monitor", layout="wide")


@st.cache_data(ttl=600)
def load(query: str, params=()) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(query, con, params=params)
    con.close()
    return df


zones = load("SELECT * FROM zones ORDER BY iso3, name")
if zones.empty:
    st.error("No zones in DB — run ingest first.")
    st.stop()

st.title("Crop-zone rainfall & soil moisture monitor")
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
view = st.sidebar.radio("View", ["Overview", "Zone detail", "Flood watch"])

# ======================== OVERVIEW ========================================
# one representative zone per country, its main season
REP_ZONES = ["ken_uasin_gishu", "eth_oromia_maize", "tza_mbeya",
             "uga_eastern", "zmb_central", "mwi_lilongwe", "zwe_mash_west",
             "moz_zambezia", "mdg_vakinankaratra", "zaf_free_state"]
BENCH = [("WRSI %med", "lwrsi_africa_dekad_pctm"),
         ("SM %mean", "soilmoisture-0-100cm_global_month_pctm"),
         ("CHIRPS z-score", "chirps_global_month_zscore")]
EPISODES_OV = {"15-16": ("2015-07-01", "2016-06-30"),
               "23-24": ("2023-07-01", "2024-06-30")}


def season_cum_frame(zk: str, a: int, b: int):
    """Per-season-year cumulative rainfall (EWX pentads) for one zone."""
    cross = b < a
    df = load(
        "SELECT granule_start, value FROM observations WHERE zone_key=? AND "
        "dataset IN ('chirps_global_pentad_data',"
        "'chirps-prelim_global_pentad_data') ORDER BY granule_start", (zk,))
    if df.empty:
        return None, None, None
    df = df.drop_duplicates("granule_start", keep="first")
    ts = pd.to_datetime(df.granule_start)
    df["month"], df["day"] = ts.dt.month, ts.dt.day
    df = df[[(m >= a or m <= b) if cross else (a <= m <= b)
             for m in df["month"]]].copy()
    df["syear"] = [t.year if (not cross or m >= a) else t.year - 1
                   for t, m in zip(ts[df.index], df["month"])]
    df["doy_key"] = [f"{(m - a) % 12:02d}-{d:02d}"
                     for m, d in zip(df["month"], df["day"])]
    df = df.sort_values(["syear", "doy_key"])
    df["cum"] = df.groupby("syear")["value"].cumsum()
    clim = (df[(df.syear >= CLIM_START) & (df.syear <= CLIM_END)]
            .groupby("doy_key")["cum"]
            .agg(mean="mean", p20=lambda s: s.quantile(0.2),
                 p80=lambda s: s.quantile(0.8)).reset_index())
    cur_sy = today.year if (not cross or today.month >= a) else today.year - 1
    return df, clim, cur_sy


if view == "Overview":
    st.subheader("Main-season cumulative rainfall by country")
    cols = st.columns(3)
    for i, zk in enumerate(REP_ZONES):
        zr = zones.set_index("zone_key").loc[zk]
        sname, rng = (zr.seasons or "x:1-12").split(",")[0].split(":")
        a, b = (int(v) for v in rng.split("-"))
        df, clim, cur_sy = season_cum_frame(zk, a, b)
        with cols[i % 3]:
            if df is None:
                st.caption(f"{zr['name']}: no data")
                continue
            fo = go.Figure()
            fo.add_scatter(x=clim.doy_key, y=clim.p80, line=dict(width=0),
                           showlegend=False, hoverinfo="skip")
            fo.add_scatter(x=clim.doy_key, y=clim.p20, fill="tonexty",
                           fillcolor="rgba(120,120,120,0.2)",
                           line=dict(width=0), showlegend=False)
            fo.add_scatter(x=clim.doy_key, y=clim["mean"],
                           line=dict(color="gray", dash="dash"),
                           showlegend=False)
            for sy, color in ((2015, "darkorange"), (2023, "mediumpurple")):
                n = df[df.syear == sy]
                if not n.empty:
                    fo.add_scatter(x=n.doy_key, y=n.cum,
                                   line=dict(color=color, dash="dot",
                                             width=1.5), showlegend=False)
            c = df[df.syear == cur_sy]
            if not c.empty:
                fo.add_scatter(x=c.doy_key, y=c.cum,
                               line=dict(color="crimson", width=2.5),
                               showlegend=False)
            MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                   "Sep", "Oct", "Nov", "Dec"]
            in_now = (today.month >= a or today.month <= b) if b < a else \
                (a <= today.month <= b)
            if not in_now:
                fo.add_annotation(
                    text=f"season complete — next starts {MON[a - 1]}",
                    xref="paper", yref="paper", x=0.98, y=0.03,
                    showarrow=False, font=dict(size=11, color="gray"),
                    xanchor="right")
            n_m = (b - a) % 12 + 1
            fo.update_xaxes(
                type="category",
                tickvals=[f"{o:02d}-01" for o in range(0, n_m, 2)],
                ticktext=[["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                           "Aug", "Sep", "Oct", "Nov", "Dec"][(a - 1 + o) % 12]
                          for o in range(0, n_m, 2)])
            fo.update_layout(
                title=dict(text=f"{zr['name']} — {sname} {cur_sy}"
                                if c is not None and not c.empty else
                                f"{zr['name']} — {sname}", font=dict(size=13)),
                height=240, margin=dict(t=30, b=0, l=0, r=0),
                showlegend=False)
            st.plotly_chart(fo, use_container_width=True)
    st.caption("Red = current season · gray dash = 1991–2020 mean · band = "
               "20–80th pct · orange dots = 2015-16 El Niño · purple dots = "
               "2023-24 El Niño")

    def mini_indicator_chart(zk: str, ds: str, bands, y_floor, y_cap,
                             color, title):
        s = load("SELECT granule_start, value FROM observations WHERE "
                 "zone_key=? AND dataset=? ORDER BY granule_start", (zk, ds))
        if s.empty:
            return None
        s["date"] = pd.to_datetime(s.granule_start)
        s = s[s.date >= s.date.max() - pd.Timedelta(days=1460)]
        fm = go.Figure()
        fm.add_scatter(x=s.date, y=s.value, line=dict(color=color, width=1.5),
                       showlegend=False)
        fm.add_hline(y=100, line_dash="dash", line_color="gray")
        for y0, y1, fill, opac in bands:
            fm.add_hrect(y0=y0, y1=y1, fillcolor=fill, opacity=opac,
                         line_width=0)
        fm.update_yaxes(range=[min(y_floor, s.value.min() - 5),
                               max(y_cap, s.value.max() + 5)])
        fm.update_layout(title=dict(text=title, font=dict(size=13)),
                         height=220, margin=dict(t=30, b=0, l=0, r=0),
                         showlegend=False)
        return fm

    WRSI_BANDS = [(80, 94, "gold", 0.13), (60, 80, "orange", 0.15),
                  (0, 60, "orangered", 0.15)]
    SM_BANDS = [(85, 95, "gold", 0.15), (75, 85, "orangered", 0.13)]

    st.subheader("Water Requirement Satisfaction Index (% of median)")
    cols = st.columns(3)
    for i, zk in enumerate(REP_ZONES):
        zr = zones.set_index("zone_key").loc[zk]
        with cols[i % 3]:
            fm = mini_indicator_chart(
                zk, "lwrsi_africa_dekad_pctm", WRSI_BANDS, 55.0, 140.0,
                "steelblue", zr["name"])
            if fm is not None:
                st.plotly_chart(fm, use_container_width=True)
            else:
                st.caption(f"{zr['name']}: no data")
    st.caption("Bands: gold = mild stress (80–94) · orange = moderate "
               "stress (60–80) · red = severe stress (<60)")

    st.subheader("FLDAS root-zone soil moisture (% of mean)")
    cols = st.columns(3)
    for i, zk in enumerate(REP_ZONES):
        zr = zones.set_index("zone_key").loc[zk]
        with cols[i % 3]:
            fm = mini_indicator_chart(
                zk, "soilmoisture-0-100cm_global_month_pctm", SM_BANDS,
                70.0, 125.0, "saddlebrown", zr["name"])
            if fm is not None:
                st.plotly_chart(fm, use_container_width=True)
            else:
                st.caption(f"{zr['name']}: no data")
    st.caption("Bands: gold = watch territory (85–95) · red = issue level "
               "(75–85)")

    st.subheader("Indicators vs El Niño episode lows")
    orows = []
    for zk in zones.zone_key:
        if zones.set_index("zone_key").loc[zk, "iso3"] is None:
            continue
        r = {"zone": zone_label[zk]}
        any_data = False
        for label, ds in BENCH:
            curv = load("SELECT value FROM observations WHERE zone_key=? AND "
                        "dataset=? ORDER BY granule_start DESC LIMIT 1",
                        (zk, ds))
            r[f"{label} now"] = round(curv.value.iloc[0], 1) \
                if not curv.empty else None
            any_data = any_data or not curv.empty
            for ep, (x, y) in EPISODES_OV.items():
                m = load("SELECT min(value) v FROM observations WHERE "
                         "zone_key=? AND dataset=? AND granule_start "
                         "BETWEEN ? AND ?", (zk, ds, x, y))
                r[f"{label} {ep} low"] = round(m.v.iloc[0], 1) \
                    if not m.empty and m.v.iloc[0] is not None else None
        if any_data:
            orows.append(r)
    odf = pd.DataFrame(orows)

    def _wrsi_shade(v):
        # mirrors the zone-detail WRSI chart bands
        if pd.isna(v):
            return ""
        if v < 60:
            return "background-color: rgba(255,69,0,0.40)"    # severe
        if v < 80:
            return "background-color: rgba(255,165,0,0.35)"   # moderate
        if v < 95:
            return "background-color: rgba(255,215,0,0.30)"   # mild
        return ""

    def _sm_shade(v):
        # mirrors the soil-moisture chart bands
        if pd.isna(v):
            return ""
        if v < 75:
            return "background-color: rgba(255,69,0,0.40)"    # beyond issue
        if v < 85:
            return "background-color: rgba(255,69,0,0.22)"    # issue level
        if v < 95:
            return "background-color: rgba(255,215,0,0.30)"   # watch
        return ""

    def _anom_shade(v):
        # continuous diverging gradient: deeper red = drier, light blue =
        # wetter; z-score scale saturates at |z| = 2.5
        if pd.isna(v):
            return ""
        if v < 0:
            alpha = min(0.45, abs(v) / 2.5 * 0.45)
            return f"background-color: rgba(255,69,0,{alpha:.2f})"
        alpha = min(0.30, v / 2.5 * 0.30)
        return f"background-color: rgba(70,130,180,{alpha:.2f})"

    wrsi_cols = [c for c in odf.columns if c.startswith("WRSI")]
    sm_cols = [c for c in odf.columns if c.startswith("SM")]
    anom_cols = [c for c in odf.columns if c.startswith("CHIRPS")]
    styled = (odf.style
              .map(_wrsi_shade, subset=wrsi_cols)
              .map(_sm_shade, subset=sm_cols)
              .map(_anom_shade, subset=anom_cols)
              .format(precision=1))
    st.dataframe(styled, hide_index=True,
                 use_container_width=True, height=520)
    st.caption("Lows are the worst single granule in each Jul–Jun episode "
               "window. Off-season WRSI reads ~100 — compare southern-Africa "
               "zones during Oct–Apr only. Composite zones (local pipeline "
               "only) are excluded until their backfill completes.")
    st.stop()

# ======================== FLOOD WATCH =====================================
if view == "Flood watch":
    import json as _json
    st.subheader("Flood watch — Kenya pilot basins")
    fs = load("SELECT * FROM flood_state ORDER BY granule_start")
    if fs.empty:
        st.warning("No flood_state data — run compute/flood_signals.py.")
        st.stop()
    fs["date"] = pd.to_datetime(fs.granule_start)
    latest_p = fs.granule_start.max()
    cur = fs[fs.granule_start == latest_p].set_index("zone_key")

    # regional state: >=2 basins armed, >=1 alerting, in flood-season months
    lat_month = pd.Timestamp(latest_p).month
    n1 = int((cur.tier >= 1).sum())
    n2 = int((cur.tier >= 2).sum())
    regional = (n1 >= 2 and n2 >= 1 and
                lat_month in {2, 3, 4, 5, 10, 11, 12})
    if regional:
        st.error(f"REGIONAL FLOOD ALERT — {n2} basin(s) alerting, "
                 f"{n1} armed (as of {latest_p})")
    else:
        st.success(f"Regional state: clear (as of pentad starting {latest_p} "
                   f"— {n1} basin(s) armed, {n2} alerting)")
    runs = load("SELECT last_run, latest_pentad, regional FROM flood_runs "
                "WHERE id=1")
    if not runs.empty:
        r = runs.iloc[0]
        age_h = (pd.Timestamp.now(tz="UTC")
                 - pd.Timestamp(r.last_run)).total_seconds() / 3600
        msg = (f"Signals last computed {r.last_run} UTC "
               f"({age_h:.0f}h ago), data through pentad {r.latest_pentad}.")
        if age_h > 24 * 7:
            st.warning(msg + " — STALE: the update pipeline may not be "
                             "running; a 'clear' state this old is not "
                             "evidence of anything.")
        else:
            st.caption(msg)
    hist_al = load("SELECT granule_start, state, detail, recorded_at "
                   "FROM flood_alerts ORDER BY granule_start DESC LIMIT 12")
    with st.expander(f"Alert history ({len(hist_al)} recorded)"):
        if hist_al.empty:
            st.caption("No regional alerts recorded since the layer went "
                       "live (Aug 2026). Historical episodes before that "
                       "appear in the backtest, not here.")
        else:
            st.dataframe(hist_al, hide_index=True, use_container_width=True)

    # basin table with GEFS outlook + downstream pairing
    gj = _json.loads(open(pathlib.Path(__file__).resolve().parent.parent /
                          "data" / "zones" / "basins.geojson").read())
    props = {f["properties"]["zone_key"]: f["properties"]
             for f in gj["features"]}
    gefs = load("SELECT zone_key, horizon, fcst_mm, pct_clim FROM flood_gefs "
                "WHERE issue_date = (SELECT max(issue_date) FROM flood_gefs)")
    g10 = gefs[gefs.horizon == "10_day"].set_index("zone_key")
    rows = []
    for zk, r in cur.iterrows():
        p = props.get(zk, {})
        rows.append({
            "basin": p.get("name", zk),
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

    # ---- mechanism charts ------------------------------------------------
    FLOOD_EVENTS = [
        ("2001-11-01", "2001-12-15", "'01"), ("2002-04-15", "2002-05-31", "'02"),
        ("2006-10-15", "2006-12-15", "'06"), ("2007-09-01", "2007-10-31", "'07"),
        ("2008-10-15", "2008-11-30", "'08"), ("2010-03-01", "2010-05-15", "'10"),
        ("2012-04-01", "2012-05-31", "'12"), ("2013-03-15", "2013-05-15", "'13"),
        ("2015-10-15", "2015-12-31", "'15"), ("2018-03-01", "2018-05-31", "'18"),
        ("2019-10-01", "2019-12-31", "'19"), ("2020-03-15", "2020-05-31", "'20"),
        ("2023-10-15", "2023-12-15", "'23"), ("2024-03-15", "2024-05-15", "'24"),
        ("2026-03-05", "2026-05-31", "MAM 2026 floods"),
    ]
    BORDER = ["bas_nzoia_hw", "bas_nzoia_low", "bas_lakeshore",
              "bas_nairobi", "bas_tana_hw", "bas_tana_mid", "bas_ewaso"]

    def regional_diamonds(df):
        """Regional-alert pentads with dominant signature."""
        rows = []
        for d, grp in df.groupby("date"):
            n1 = int((grp.tier >= 1).sum())
            n2 = int((grp.tier >= 2).sum())
            if n1 >= 2 and n2 >= 1 and d.month in {2, 3, 4, 5, 10, 11, 12}:
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
                          name="basin alert (context tier)")
        reg = regional_diamonds(hist)
        for sig, color in (("saturation", "#1a2f6b"), ("whiplash", "#e07b00")):
            sub = reg[reg.sig == sig]
            if not sub.empty:
                f.add_scatter(x=sub.date,
                              y=["REGIONAL ALERT"] * len(sub),
                              mode="markers",
                              marker=dict(size=11, color=color,
                                          symbol="diamond"),
                              name=f"REGIONAL alert — {sig}")
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
                        "Mechanism view, last 12 months — alerts fire where "
                        "a burst (circle) lands on a saturated catchment; "
                        "orange diamonds = dry-whiplash firings",
                        show_bursts=True, gefs=g10),
        use_container_width=True)
    with st.expander("Full history 1998–2026 — every documented flood vs "
                     "the signal record"):
        st.plotly_chart(
            mechanism_chart(fs[fs.date >= "1998-01-01"],
                            "Mechanism view, 1998–2026 (bursts omitted at "
                            "this scale)", show_bursts=False),
            use_container_width=True)
    st.caption(
        "How to read this: a basin alert (red ×) fires when very heavy "
        "rain lands on ground that is already unusually wet — or, for the "
        "orange 'whiplash' case, on ground baked dry by drought. A REGIONAL "
        "ALERT (diamond) needs at least two basins in that state at once "
        "during a rainy season, which filters out most one-off local "
        "storms. In testing against 1999–2026, the regional alert flagged "
        "every major Kenya flood since 2006, usually days to weeks ahead; "
        "roughly half its firings were followed by a major flood, with a "
        "false alarm about every one to two years. Individual basin alerts "
        "fire much more often and are best read alongside the rest of the "
        "chart. The gray band shows the rainfall forecast for the next "
        "10 days.")
    st.stop()

# ======================== ZONE DETAIL =====================================
zone_key = st.sidebar.selectbox(
    "Crop zone", zones.zone_key, format_func=lambda k: zone_label[k])
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
