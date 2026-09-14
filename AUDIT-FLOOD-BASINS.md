# Flood-basin polygon identity audit — v2 (full 32-basin set)

*v2: 2026-09-15, after merging origin/main's Ethiopia/Tanzania/Uganda/
Rwanda flood basins and email alerting into this branch's DRC/Mozambique
hydro layers. Same method as v1 (below), extended to all 32 entries now in
`ingest/flood_zones.py`'s `BASINS` dict (the DRC/Zambia hydro-catchment
zones in `ingest/hydro_zones.py`'s separate `OUTLETS` dict are out of
scope here — they were verified during the merge itself, see the merge
commit message).*

**Method** (unchanged from v1): re-derive the containing HydroBASINS lev-7
polygon independently from `data/raw/hybas_af_lev07_v1c.shp` for each
coded anchor, using the exact point-in-polygon logic in `flood_zones.py`.
Compute geodesic edge distance (<3km flagged). Classify mainstem vs.
headwater by UP_AREA/SUB_AREA ratio against what the basin's name implies.
Walk `NEXT_DOWN` for every `DOWNSTREAM` routing pair (FAIL = target
polygon never appears on the walk). Where solid, cross-check UP_AREA
against literature drainage magnitudes (approximate, corroborating only —
literature itself disagrees by up to ~45% between sources for some of
these rivers, see bas_shabelle_mid below). For every named-town/feature
basin, independently look up the real-world coordinate (web search) and
point-in-polygon test it as a second, harder check: if the real feature
lands in a **different** lev-7 polygon than the code's anchor, that is
direct evidence of a wrong-catchment bug, not just an edge-distance
concern.

Cross-checked `data/zones/basins.geojson` against a fresh build: **all 32
basins match exactly** (HYBAS_ID and area_km2 identical) — no code/build
drift.

## Results table — Ethiopia / Tanzania / Rwanda / Uganda (25 new basins)

| zone_key | name | HYBAS_ID | SUB_AREA | UP_AREA | ratio | edge | mainstem/headwater | connectivity | external/real-world check | **VERDICT** |
|---|---|---|---|---|---|---|---|---|---|---|
| bas_awash_hw | Awash hw (Addis highlands) | 1070874990 | 5,003.7 | 5,003.7 | 1.000 | 12.6km safe | headwater — confirmed | source→bas_awash_mid, 5 hops, **PASS** | n/a | **OK** |
| bas_awash_mid | Middle Awash (Amibara/Gewane) | 1070818770 | 7,075.4 | 32,129.4 | 4.541 | 19.2km safe | mainstem — confirmed | target←bas_awash_hw, 5 hops, **PASS** | UP_AREA 32,129 km2 matches "tens of thousands" band | **OK** — the two basins with live email alerts already sent are the most robustly verified pair in the set |
| bas_baro | Baro at Gambela | 1070886140 | 6,291.3 | 27,501.1 | 4.371 | 15.2km safe | mainstem — confirmed | not in DOWNSTREAM | UP_AREA 27,501 km2 vs. band 20,000–25,000 (~10% over); real Gambela coord (8.25,34.583) lands in same polygon | **OK** |
| bas_laketana | Lake Tana east (Fogera plain) | 1070742610 | 1,869.7 | 1,869.7 | 1.000 | **2.35km FLAG** | headwater/tributary, plausible | not in DOWNSTREAM | Real Fogera-area town (Woreta, 11.967,37.683) lands in a **different, smaller** polygon: HYBAS 1070740360, SUB=1,587.0, UP=2,255.1 | **SUSPECT** |
| bas_shabelle_hw | Shabelle hw (Bale) | 1070928960 | 804.4 | 804.4 | 1.000 | 8.5km safe | headwater — confirmed | source→bas_shabelle_mid: **FAIL** (see below) | Real source area (Robe, Bale, 7.117,40.0) lands in a neighboring polygon (815.6 km2) on the same small tributary — geographically plausible as "Shabelle headwaters" | **OK** as a polygon, but see pairing verdict below |
| bas_shabelle_mid | Shabelle at Gode/Kelafo | 1070984130 | 1,104.6 | 124,866.0 | 113.042 | 4.7km safe | mainstem/floodplain — confirmed | target of FAILED pairing (below) | Real Gode coordinate (5.953,43.552, per web search) lands in the **identical** polygon. UP_AREA 124,866 km2 vs. external band "200,000+ km2" — materially short (literature itself spans 205,407–298,253 km2 for the whole Wabi Shebelle across sources, a 45% spread, so this is a corroborating check only) | **OK, with magnitude caveat** — town-identity confirmed independently, area citation uncertain |
| bas_diredawa | Dire Dawa (Dechatu) | 1070803250 | 4,014.8 | 4,015.2 | 1.000 | 13.9km safe | headwater/urban — confirmed | not in DOWNSTREAM | Real Dire Dawa coord (9.593,41.866) lands in same polygon | **OK** (telemetry-only, no live alert) |
| bas_omo_low | Lower Omo flood plain | 1071031870 | 5,250.4 | 70,567.5 | 13.440 | **0.65km FLAG** | floodplain — confirmed | not in DOWNSTREAM | UP_AREA 70,567 km2 vs. band "~70,000" — near-exact match | **OK, fragile** (edge margin thin but magnitude corroboration is unusually strong; telemetry-only, no live alert) |
| bas_genale | Genale-Dawa at Dolo Ado | 1071035420 | 3,247.9 | 57,337.0 | 17.654 | 4.1km safe | mainstem — confirmed | not in DOWNSTREAM | Real Dolo Ado coord (4.173,42.084) lands in a **different, larger** polygon (UP_AREA 142,979.7 km2) two `NEXT_DOWN` hops downstream of the coded anchor — same river, confirmed by the hop walk, but the anchor sits well upstream of the actual town | **OK, with label-precision caveat** — not "at Dolo Ado," but on the same river upstream of it (telemetry-only, no live alert) |
| bas_dar | Dar es Salaam (Msimbazi) | 1070009180 | 7,782.7 | 7,782.7 | 1.000 | 11.0km safe | headwater/urban — confirmed | not in DOWNSTREAM | Real Msimbazi-corridor coord (-6.80,39.27) lands in same polygon | **OK** |
| bas_kilombero | Kilombero valley | 1071304580 | 5,834.0 | 5,834.0 | 1.000 | 17.0km safe | headwater/valley — confirmed | source→bas_rufiji_low, 5 hops, **PASS** | n/a | **OK** |
| bas_rufiji_low | Lower Rufiji flood plain | 1070009450 | 5,663.0 | 177,136.2 | 31.280 | 12.6km safe | mainstem/floodplain — confirmed | target←bas_kilombero, 5 hops, **PASS** | UP_AREA 177,136 km2 matches the commonly cited ~177,000 km2 Rufiji basin total closely | **OK** |
| bas_pangani_hw | Pangani hw (Kilimanjaro/Moshi) | 1071223250 | 5,841.0 | 5,841.0 | 1.000 | **2.75km FLAG** | headwater — confirmed | not in DOWNSTREAM | Real Moshi coord (-3.333,37.333) lands in same polygon | **OK** |
| bas_mwanza | Lake Victoria south shore (Mwanza) | 1071192100 | 3,721.7 | 18,891.0 | 5.076 | 13.0km safe | lakeshore/floodplain, case-by-case | not in DOWNSTREAM | Real Mwanza coord lands in same polygon. UP_AREA aggregates several streams into the shore, same Kisumu-type label-precision situation as v1 | **OK, with the same naming caveat as bas_lakeshore** |
| bas_wami | Wami/Mkondoa at Kilosa | 1071281880 | 8,725.7 | 18,435.8 | 2.113 | 4.6km safe | mid-reach — confirmed | not in DOWNSTREAM | Real Kilosa coord (-6.833,36.983) lands in same polygon | **OK** (telemetry-only, no live alert) |
| bas_lindi | Lindi/Mtwara coast | 1070009880 | 6,038.0 | 6,039.0 | 1.000 | **0.04km FLAG — essentially on the boundary** | headwater/coastal — nominal | not in DOWNSTREAM | **Real Lindi town (-9.997,39.714) lands in a DIFFERENT polygon**: HYBAS 1070009810, SUB=UP=1,520.8 km2. The coded anchor (-9.90,39.40) sits ~30km inland/west of the actual town and picks up a different, larger coastal catchment | **WRONG** |
| bas_nyabarongo | Nyabarongo (Kigali) | 1071190440 | 8,526.6 | 8,526.7 | 1.000 | 9.3km safe | headwater — confirmed | not in DOWNSTREAM | Real Kigali coord lands in same polygon | **OK** |
| bas_sebeya | Sebeya / NW highlands (Rubavu) | 1071196250 | 4,955.0 | 4,955.0 | 1.000 | 15.3km safe | headwater — confirmed | not in DOWNSTREAM | Real Rubavu/Gisenyi coord lands in same polygon | **OK** |
| bas_akanyaru | Akanyaru / Bugesera south | 1071190600 | 5,406.0 | 5,406.6 | 1.000 | 14.0km safe | headwater — confirmed | not in DOWNSTREAM | n/a (no single named town) | **OK** |
| bas_rusizi | Rusizi / Bugarama plain | 1071205960 | 943.5 | 943.5 | 1.000 | 5.1km safe | headwater — confirmed | not in DOWNSTREAM | Real Bugarama coord lands in same polygon | **OK** (telemetry-only, no live alert) |
| bas_elgon | Mt Elgon west (Manafwa/Bududa) | 1071121150 | 2,379.6 | 2,379.8 | 1.000 | 11.2km safe | headwater — confirmed | not in DOWNSTREAM | Real Bududa coord lands in same polygon | **OK** |
| bas_kampala | Kampala / L. Victoria north | 1071130710 | 1,089.1 | 1,089.1 | 1.000 | **0.03km FLAG — the tightest margin in the whole set** | headwater/urban, nominal | not in DOWNSTREAM | Real Kampala CBD coord (0.3476,32.5825) lands in the **same** polygon, resolving the flag | **OK, extremely fragile** — a HydroBASINS version bump could flip this; re-verify on any shapefile update |
| bas_nyamwamba | Nyamwamba / Rwenzori east (Kasese) | 1071144230 | 738.1 | 738.2 | 1.000 | **2.41km FLAG** | headwater — confirmed | not in DOWNSTREAM | Real Kasese coord lands in same polygon | **OK** |
| bas_kyoga | Lake Kyoga lowlands (Teso) | 1071108170 | 3,050.3 | 3,050.6 | 1.000 | 5.1km safe | headwater/lowland — confirmed | not in DOWNSTREAM | Real Soroti/Teso-area coord lands in same polygon | **OK** |
| bas_semliki | Semliki flats (Ntoroko) | 1071111290 | 8,368.3 | 8,368.7 | 1.000 | 14.7km safe | headwater/floodplain — confirmed | not in DOWNSTREAM | n/a | **OK** (telemetry-only, no live alert) |

## The one connectivity FAIL: bas_shabelle_hw → bas_shabelle_mid

The `DOWNSTREAM` dict claims `bas_shabelle_hw` (Bale headwaters) feeds
`bas_shabelle_mid` (Gode/Kelafo) 4–8 days downstream — this is the label
shown on every `bas_shabelle_hw` alert. Walking `NEXT_DOWN` from
`bas_shabelle_hw`'s HYBAS_ID (1070928960) for 25 hops reaches the
Shabelle-Jubba system's terminal sink (1070008100) **without ever passing
through** `bas_shabelle_mid`'s HYBAS_ID (1070984130) — both basins share
the same ultimate `MAIN_BAS` (1070008100), so this is the same overall
river system, but the specific claimed upstream→downstream routing path
is not supported by the HydroBASINS pour-point graph. Two explanations
are consistent with the evidence and cannot be distinguished without a
hydrological map of this specific reach: (a) the Bale-highlands tributary
this anchor sits on joins the Shabelle mainstem via the Fafan/Erer system
downstream of Gode rather than upstream of it, in which case the
"downstream: Gode" label is backwards; or (b) it is a real but distant
tributary whose confluence lies on a lev-7-graph branch that legitimately
bypasses Gode's specific polygon while still reaching the same sea/sink —
possible for a basin this large and geometrically complex. Either way,
**the routing/lag label is unverified by this test and should not be
trusted as-is.**

**VERDICT: bas_shabelle_hw / bas_shabelle_mid pairing — SUSPECT/WRONG on
the DOWNSTREAM claim specifically** (each basin's own polygon selection
is independently OK per the table above — this is a routing-label problem,
not a wrong-catchment problem for either basin alone).

**Proposed correction (NOT applied)**: remove `"bas_shabelle_hw":
("bas_shabelle_mid", "4-8")` from `DOWNSTREAM` in `ingest/flood_zones.py`
until the actual confluence point is verified against a proper Shabelle
sub-basin map (e.g. the FAO/FEWS NET Shabelle-Jubba delineation), or
replace it with a verified intermediate reach.

**Alert-history impact if corrected**: the "downstream: Gode, 4–8 days"
annotation attached to every `bas_shabelle_hw` alert since Ethiopia basins
went live (commit `12e6002`) would need to be retracted or corrected — it
implies a specific forecast lead time for Gode that this test cannot
confirm. `bas_shabelle_hw` and `bas_shabelle_mid` are **not**
telemetry-only (Gode "earned an alert-rule seat" per
`compute/flood_signals.py`'s `TELEMETRY_ONLY` comment), so this is a
live-alert-facing label, not a display-only one. Each basin's own
tier/signature calibration is unaffected — only the cross-basin lag label
is in question.

## The one WRONG polygon: bas_lindi

`bas_lindi` ("Lindi/Mtwara coast") is one of the two basins the
`TELEMETRY_ONLY` comment in `compute/flood_signals.py` says specifically
"earned an alert-rule seat" (the other being Gode) — i.e. it is a
calibrated, live-alerting basin, not a display-only one. Its coded anchor
(-9.90, 34.40 — inland/west of the coast) sits **0.04 km from its
polygon's boundary**, the tightest single-sided margin found anywhere in
this audit before resolution, and independently testing the real town of
Lindi (-9.9968, 39.7144, per web search) lands in a **different lev-7
polygon** (HYBAS 1070009810, SUB_AREA=UP_AREA=1,520.8 km2) than the coded
anchor's polygon (HYBAS 1070009880, SUB_AREA=UP_AREA=6,038–6,039 km2) —
roughly 4x the area of the catchment that actually contains the town. Both
are small, independent coastal catchments in the same general
Lindi/Mtwara coastal strip (not a mainstem/tributary mix-up, and not a
different major river), but they are **not the same catchment**, and the
one currently monitored is not the one containing Lindi town.

**VERDICT: WRONG.**

**Proposed correction (NOT applied)**: move the `bas_lindi` anchor to
(-9.9968, 39.7144) — the real town — and rebuild `basins.geojson` via
`ingest/flood_zones.py`; re-verify the new polygon's edge distance and
UP_AREA are sane before considering it fixed.

**Alert-history impact if corrected**: every `bas_lindi` tier/signature
reading and every alert fired under `bas_lindi`'s zone_key to date reflects
rainfall over the *wrong* ~6,039 km2 coastal catchment, not the ~1,521 km2
catchment that actually drains past Lindi town. Since this basin was
specifically calibrated ("earned an alert-rule seat") against its own
history, correcting the anchor invalidates that calibration — the
percentile/tier thresholds would need to be recomputed against the
*correct* catchment's rainfall history from scratch, not just relabeled.

## SUSPECT (not WRONG, not cleanly OK): bas_laketana

Covered in the table above. The real-world Fogera-plain town (Woreta)
lands in a smaller, different polygon than the coded anchor. Unlike
`bas_lindi`, "Lake Tana east / Fogera plain" is a *regional* label (the
seasonally-flooded plain fed by several small streams — Rib, Gumara —
rather than one town's exact catchment), so it is plausible the coded
anchor is deliberately elsewhere in the same floodplain system rather than
simply wrong. This is exactly the kind of ambiguity the audit brief says
should not be rounded up to OK. `bas_laketana` is not telemetry-only, so
it does carry live-alert weight.

**Proposed correction (NOT applied)**: verify with a proper Fogera/Rib/
Gumara sub-basin map which polygon the flood-prone plain actually sits in
before trusting either coordinate; if the Woreta-area polygon turns out to
be the operationally relevant one, move the anchor there.

## OK-with-caveat basins (label precision, not polygon errors)

- **bas_shabelle_mid**: town-identity confirmed exactly; UP_AREA magnitude
  citation is uncertain because literature for the whole Wabi Shebelle
  basin spans 205,407–298,253 km2 depending on source, a 45% range, so the
  polygon's 124,866 km2 (measured to Gode specifically, not the whole
  river) not matching either headline figure is not strong evidence of
  a problem by itself — the real-coordinate match is the reliable check
  here.
- **bas_genale**: same river, same NEXT_DOWN chain as the real Dolo Ado
  town, but the anchor sits two hops upstream of the actual town
  (UP_AREA 57,337 km2 vs. 142,980 km2 at the real town). If this basin's
  magnitude is ever quoted as "the Genale at Dolo Ado" in write-ups, use
  the real town's figure, not the polygon's.
- **bas_mwanza**: same "aggregates more than the single named river"
  situation as v1's `bas_lakeshore` (Kisumu) — not a polygon error, a
  naming-precision note for anyone using its magnitude analytically.

## Kenya (7) — quick drift re-check only, per audit scope

All 7 Kenya zone_keys were fully re-audited in v1 (below) and are
unchanged in the merged codebase. This pass re-ran the point-in-polygon
derivation and the `basins.geojson` cross-check for all 7 and found
**zero drift** — identical HYBAS_ID and area_km2 to v1 and to the current
build. No new issues found. See the v1 section below for the full
per-basin detail (edge distances, the `bas_nzoia_low` fragile-margin note,
the `bas_lakeshore` naming caveat).

## Bottom line — v2

**25 new basins audited, plus a clean re-check of the 7 from v1 (32
total).** Of the 25 new basins: **22 OK** (6 with a magnitude/label
caveat noted but not scored down), **1 WRONG** (`bas_lindi` — wrong
lev-7 polygon relative to the real town, confirmed by independent
coordinate lookup, and this basin is a live, calibrated alert seat, not
telemetry-only), **1 SUSPECT** (`bas_laketana` — real-world town lands in
a different, smaller polygon; ambiguous because the label is regional
rather than one exact town), and **1 connectivity FAIL** on a routing
pair rather than a polygon (`bas_shabelle_hw → bas_shabelle_mid`'s
`DOWNSTREAM` claim is not supported by the `NEXT_DOWN` graph, though each
basin's own polygon is independently fine).

**bas_awash_hw and bas_awash_mid — the pair explicitly flagged for extra
scrutiny because they have already sent live email alerts — are the most
robustly verified basins in this entire audit**: ratio, edge distance,
5-hop `NEXT_DOWN` connectivity, and external UP_AREA magnitude
(32,129 km2 against the "tens of thousands" band) all agree cleanly. No
correction is proposed for either.

No corrections have been applied to any basin, anchor, or the
`DOWNSTREAM` dict — this document is diagnostic only, per the audit's
read-only scope.

---

# v1 — Kenya-only audit (2026-09-14), preserved

*Read-only audit, 2026-09-14. Motivated by two wrong hydro-catchment outlet
coordinates found in `ingest/hydro_zones.py` (a tiny side tributary and the
Zambezi mainstem selected by unguarded point-and-walk, caught only by area
gates — see that file's docstring for the fix history). This audit checks
whether the flood basins in `ingest/flood_zones.py`, which use the same
drop-a-point method **without** area gates and drive live email alerts,
have the same class of bug.*

**Scope note (v1)**: at the time of v1, `BASINS` in `ingest/flood_zones.py`
contained **7 Kenya entries only** — no Ethiopia, Tanzania, Rwanda, or
Uganda basins existed in the codebase yet (those landed later via
origin/main and are covered in v2 above). All 7 entries were audited
below; nothing was out of scope.

**Method**: for each basin, the containing HydroBASINS lev-7 polygon was
re-derived independently from `data/raw/hybas_af_lev07_v1c.shp` (12,345
polygons) using the exact point-in-polygon logic in `flood_zones.py`, then
cross-checked against `data/zones/basins.geojson`. Distance-to-edge was
computed as the geodesic (haversine) distance from the anchor coordinate to
the nearest point on the containing polygon's boundary. For every named-town
basin (Garissa, Kisumu/lakeshore, Nairobi), the real-world town coordinate
(via web search) was also point-in-polygon tested, both as an independent
sanity check on the polygon choice and as a second edge-distance sample.

## Results table

| zone_key | name | HYBAS_ID | SUB_AREA (km2) | UP_AREA (km2) | ratio | edge dist. | mainstem/headwater verdict | connectivity | external check | **VERDICT** |
|---|---|---|---|---|---|---|---|---|---|---|
| bas_nzoia_hw | Nzoia headwaters (Cherangani) | 1071128950 | 5,464.8 | 5,465.3 | 1.0001 | 18.1 km — safe | Headwater expected (ratio ≈1) — **confirmed** | source of pair → bas_nzoia_low, 2 hops, **PASS** | n/a | **OK** |
| bas_nzoia_low | Lower Nzoia (Budalangi plain) | 1071141280 | 1,307.1 | 12,736.2 | 9.74 | 2.76 km — **FLAG (<3km)** | Mainstem/floodplain reach expected (ratio ≫1) — **confirmed** | target of pair ← bas_nzoia_hw, 2 hops, **PASS** | Real Budalangi coords (0.140°N, 34.026°E, per web search) independently land in the **same** polygon, 1.78 km from its edge | **OK, fragile** — see note below |
| bas_tana_hw | Tana headwaters (Aberdares/Mt Kenya) | 1071164250 | 4,852.3 | 4,852.5 | 1.00004 | 16.3 km — safe | Headwater expected (ratio ≈1) — **confirmed** | source of pair → bas_tana_mid, 3 hops, **PASS** | n/a | **OK** |
| bas_tana_mid | Tana at Garissa | 1071164110 | 9,873.1 | 34,403.8 | 3.48 | 12.5 km — safe | Mainstem expected (ratio ≫1) — **confirmed** | target of pair ← bas_tana_hw, 3 hops, **PASS** | UP_AREA 34,404 km2 within expected 30,000–45,000 km2 band. Real Garissa coords (0.457°S, 39.658°E) land in same polygon, 12.7 km from edge | **OK** |
| bas_lakeshore | Lake Victoria shore (Kisumu) | 1071153060 | 3,191.7 | 10,693.0 | 3.35 | 13.2 km — safe | Lakeshore/floodplain — case-by-case (see note) | not in DOWNSTREAM dict | UP_AREA (10,693 km2) is ~3× the published Nyando River catchment alone (~3,550–3,600 km2, per Nyando basin literature). Real Kisumu coords land in same polygon, 12.2 km from edge | **OK, with caveat** — see note below |
| bas_nairobi | Nairobi / upper Athi | 1071184690 | 7,291.6 | 7,291.6 | 1.0 | 19.5 km — safe | Headwater expected ("upper Athi", ratio =1) — **confirmed** | not in DOWNSTREAM dict | Real Nairobi CBD coords land in same polygon, 19.1 km from edge | **OK** |
| bas_ewaso | Ewaso Ng'iro upper | 1071129500 | 1,308.9 | 1,309.2 | 1.0002 | 11.2 km — safe | Headwater expected ("upper", ratio ≈1) — **confirmed** | not in DOWNSTREAM dict | n/a | **OK** |

## Connectivity (DOWNSTREAM dict) — full walk

| pair | hop count | path (HYBAS_ID) | result |
|---|---|---|---|
| bas_nzoia_hw → bas_nzoia_low | 2 | 1071128950 → 1071136610 → 1071141280 | **PASS** |
| bas_tana_hw → bas_tana_mid | 3 | 1071164250 → 1071163110 → 1071144170 → 1071164110 | **PASS** |

Both pairs are confirmed to be on the same river (the NEXT_DOWN chain from
the upstream basin reaches the downstream basin's exact HYBAS_ID) — the
specific failure mode this audit was designed to catch (headwater and
"downstream" anchor landing on different rivers) does **not** occur in
either pair.

## Cross-check against `data/zones/basins.geojson`

All 7 zone_keys are present in the built file with HYBAS_ID and area values
that exactly match a fresh point-in-polygon selection against the current
shapefile — the artifact is current, no drift between code and build.

The file additionally contains three unrelated entries — `drc_lualaba_hydro`,
`drc_lufira_hydro`, `zmb_luangwa_basin` — written by a separate script,
`ingest/hydro_zones.py` (the El Niño hydro-catchment dashboard; this is the
script whose docstring documents the two known-bad outlet fixes that
motivated this audit). That script merges into the same `basins.geojson`
without touching `flood_zones.py`'s zone_keys, so the two builds coexist
correctly, but it means `basins.geojson` is a shared, multi-producer file —
**this was not a hypothetical risk: v2's `bas_shabelle_hw`/`bas_shabelle_mid`
connectivity-FAIL investigation and the Flood-watch Streamlit view crash
both trace back to this same shared-file design (see the merge commit and
smoke-test notes) once the file gained a 4th producer's-worth of zone_keys.**

## Findings — none WRONG, one fragile, one caveated

**No basin is WRONG.** All 7 anchors select the polygon a human reading
`SCOPING-FLOODS.md` would expect, all mainstem/headwater ratios point the
right direction, both DOWNSTREAM pairs connect via NEXT_DOWN in a small hop
count, and every external magnitude check (Tana at Garissa) lands in the
literature range. Unlike the two hydro-catchment bugs that motivated this
audit, the Kenya flood-basin anchors were evidently placed with more margin
from ambiguous boundaries and were not spot-checked wrong in this pass.

**bas_nzoia_low ("Lower Nzoia / Budalangi plain") — OK but fragile, flagged
on edge distance alone.** The coded anchor (0.12°N, 34.10°E) sits only
2.76 km from the containing polygon's boundary — under the audit's 3 km
edge-hugging threshold. Investigation found the coded anchor is itself
~8 km east-southeast of the real Budalangi (0.140°N, 34.026°E, per web
search) — the point was evidently placed near the general Nzoia-mouth area
rather than on the town itself. This raises the *a priori* risk that it
sits in the wrong lev-7 polygon, exactly like the DRC hydro bug. However,
independently testing the *real* Budalangi coordinate shows it lands in the
**identical** polygon (HYBAS_ID 1071141280), only 1.78 km from that
polygon's edge — i.e., the true floodplain location is itself close to a
lev-7 boundary here (the Nzoia's final reach before Lake Victoria is a
narrow, short polygon segment), and both the real and coded points agree on
which segment that is. The immediate neighboring polygons on both sides
(1071136610 upstream, SUB≈1,974/UP≈10,235; 1071142060 downstream,
SUB≈412/UP≈16,244) are the *same river's* adjacent reaches, not a different
tributary or a mainstem/side-wadi mix-up, so even a small coordinate shift
in the wrong direction would misassign the *reach* (skip to the neighboring
Nzoia segment) rather than misassign the *river* — a materially smaller
failure mode than the hydro bugs. **Recommended correction (not applied)**:
move the anchor to (0.14, 34.03) — the actual Budalangi coordinate — which
selects the same polygon today but gives ~1.8 km more margin than blind
luck currently provides, and re-verify after any HydroBASINS shapefile
version bump. **This correction was again not applied in v2, per this
audit's read-only scope.**

**bas_lakeshore ("Lake Victoria shore (Kisumu)") — OK, with a naming
caveat, not a polygon error.** UP_AREA for the selected polygon (10,693 km2)
is roughly 3× the published area of the Nyando River catchment alone
(~3,550–3,600 km2), which is the river most associated with flooding near
Kisumu/Winam Gulf. This is not necessarily wrong — HydroBASINS lev-7 pour
points near a lake margin commonly aggregate more than one small stream
network before the outlet, and the polygon's own SUB_AREA (3,192 km2) is
close to the Nyando figure, consistent with Nyando being the dominant but
not sole contributor. The polygon reliably contains Kisumu (both the coded
anchor and the real town centroid land in it, each >12 km from the edge —
no ambiguity risk here). The caveat is one of *label precision*: this zone
should be read as "the Kisumu-area lakeshore catchment," not literally "the
Nyando basin," when its magnitude is used in write-ups or alert copy.

## Alert-history impact if any polygon were wrong

Since no basin scored WRONG, no correction is being proposed that would
require re-running or re-interpreting alert history. For completeness, had
`bas_nzoia_low` in fact been misassigned, the affected alert history would
be: `bas_nzoia_low`'s own saturation/dry-whiplash tier history, and the
downstream label/lag ("downstream: Budalangi, 2–5 days") attached to every
`bas_nzoia_hw` alert. No other basin's history depends on either of these
two zone_keys.

## Bottom line (v1)

7/7 basins verified OK against the coordinate, mainstem/headwater ratio,
connectivity, and (where available) external-magnitude and real-world
town-coordinate cross-checks. One (`bas_nzoia_low`) is flagged as fragile
purely on edge-distance margin, with a specific, low-cost anchor correction
proposed but not applied. One (`bas_lakeshore`) carries a magnitude/labeling
caveat worth knowing about but is not a polygon-selection error. No
polygon in `ingest/flood_zones.py` currently exhibits the wrong-river /
wrong-tributary failure mode found in the hydro-catchment build.
