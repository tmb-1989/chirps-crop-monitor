# Flood-basin polygon identity audit

*Read-only audit, 2026-09-14. Motivated by two wrong hydro-catchment outlet
coordinates found in `ingest/hydro_zones.py` (a tiny side tributary and the
Zambezi mainstem selected by unguarded point-and-walk, caught only by area
gates — see that file's docstring for the fix history). This audit checks
whether the flood basins in `ingest/flood_zones.py`, which use the same
drop-a-point method **without** area gates and drive live email alerts,
have the same class of bug.*

**Scope note**: `BASINS` in `ingest/flood_zones.py` currently contains
**7 Kenya entries only** — no Ethiopia, Tanzania, or Uganda basins exist in
the codebase yet (those are listed as future "extension countries" in
`SCOPING-FLOODS.md` §2, not yet built). All 7 entries were audited below;
nothing was out of scope.

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
worth keeping in mind if either script's `main()` behavior changes to
overwrite rather than merge.

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
version bump.

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

## Bottom line

7/7 basins verified OK against the coordinate, mainstem/headwater ratio,
connectivity, and (where available) external-magnitude and real-world
town-coordinate cross-checks. One (`bas_nzoia_low`) is flagged as fragile
purely on edge-distance margin, with a specific, low-cost anchor correction
proposed but not applied. One (`bas_lakeshore`) carries a magnitude/labeling
caveat worth knowing about but is not a polygon-selection error. No
polygon in `ingest/flood_zones.py` currently exhibits the wrong-river /
wrong-tributary failure mode found in the hydro-catchment build.
