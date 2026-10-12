# Scoping procedure — CHIRPS crop monitor

This document tells you how this project was scoped. It is written in
Simplified Technical English (ASD-STE100). Follow the steps in
sequence to replicate the process for a new hazard layer or a new
country. The detailed decisions are in SCOPING.md, SCOPING-FLOODS.md
and SCOPING-CPI.md.

## 1. General procedure (all hazard layers)

Do these steps for each new hazard layer.

1. Write the objective as one sentence. Name the hazard, the unit of
   monitoring, and the lead time you want.
2. Name the users of the output. Write what decision the output must
   help. Here: sovereign-credit and food-security analysis.
3. Find the data sources first. Do not write code before this step.
   Make sure each source is free, has an API or stable URL, and has
   more than 20 years of history.
4. Choose the geographic unit for the hazard. Do not use one unit for
   all hazards. See sections 2, 3 and 4.
5. Write a scoping document before the code. Put in it: objective,
   geography, data table, signal design, calibration plan, outputs,
   phases with effort estimates, and known limits.
6. Build the ingest first. Backfill the full history. Then compute
   signals. Then calibrate. Then build the dashboard view.
7. Calibrate each signal against a catalog of documented events.
   Assemble the catalog from EM-DAT, FloodList and ReliefWeb. Version
   the catalog in the repository.
8. Report precision and recall for each country. Label the results
   "calibration", not "skill". The thresholds were fit on the same
   events.
9. Write the known limits in the scoping document. Do not remove a
   limit because it is unwelcome.
10. Automate the daily run with cron. Make each non-core ingest step
    non-fatal. Push the refreshed data to Git. The deployed dashboard
    reads the pushed data.

## 2. Crop (drought) layer

The unit is the FEWS NET crop-zone polygon.

1. Find the countries with rain-fed staple production and El Niño
   exposure. This project selected 11 countries.
2. For each country, find the main production areas. Use national
   statistics and FEWS NET reports.
3. For each production area, choose one anchor point (lat, lon).
   Make sure the point is inside a FEWS NET crop-zone polygon. Use
   the EWX WFS point-in-polygon query to make sure. If the point is
   not in a polygon, the API gives an error.
4. Write the anchor into ingest/config.py with the ISO3 code, a name,
   and the season calendar (start month, end month, per season).
5. Let the WFS lookup resolve the polygon. Do not draw polygons by
   hand. The polygon is the same mask FEWS NET uses for its WRSI
   products.
6. If the EWX server has no zonal statistics for the polygon, use the
   admin-1 boundary instead. Record this in VECTOR_OVERRIDES.
7. Ingest these datasets per zone from the EWX API: CHIRPS rainfall
   (pentad, month), WRSI (dekad, value and percent of median), and
   FLDAS soil moisture (month). Backfill from 1981.
8. Also ingest CHIRPS dekad rasters locally and compute zonal means.
   This gives SPI-1 and SPI-3 with a full climatology.
9. Set the drought light per zone from in-season WRSI, SPI-3 and soil
   moisture. The worst zone sets the country light.

Warning: use WRSI as percent of the zone median, not the absolute
value. The absolute value has a low bias in some zones. See
SCOPING-CPI.md section 9.

## 3. Flood layer

The unit is the river basin, not the crop zone. One crop zone cannot
see storms in other parts of a catchment.

1. Get basin polygons from HydroBASINS, level 7. The Pfafstetter code
   gives the upstream-downstream relations.
2. For each country, select the basins that contain the documented
   flood damage areas: flood plains, large cities, headwaters above
   them. Choose basins with an anchor point, the same method as crop
   zones.
3. Do not clip a basin at the border. Rain in the full catchment
   drives the river. A basin can be larger than the damage area. If
   the basin is much larger than the damage catchment, note this as a
   dilution limit.
4. Record headwater-to-downstream pairs with an approximate routing
   lag in days.
5. Ingest CHIRPS pentad rasters and compute basin means. Backfill
   from 1981. Add the CHIRPS-GEFS 5/10/15-day forecast per basin.
6. Design two signatures per basin, each with watch and alert tiers:
   saturation (wet catchment, then sustained heavy rain) and whiplash
   (dry catchment, then extreme rain).
7. Assemble a flood-event catalog per country, 1999 to now. Use
   month-scale windows. Add the date of peak damage per event when a
   source gives it. Grade the confidence of each date.
8. Calibrate the thresholds per basin and the regional rule per
   country with a grid search against the catalog. The regional rule
   is: at least N basins armed and M alerting, in the flood season.
9. Exclude a basin from the regional rule if its inclusion degrades
   precision without added recall. Keep it on the dashboard as
   telemetry.
10. Report precision, recall, and lead time to the damage peak per
    country. Subtract the data latency to get the operational lead.

## 4. Hydropower layer

The unit is the reservoir. This project monitors Lake Kariba (Zambia)
only.

1. Find the operator's public gauge data. Here: the Zambezi River
   Authority weekly level bulletins.
2. Ingest the level with a scraper. Seed the history from an archived
   project if one exists. Here: the elnino-hydro-dashboard archive,
   2017 to now.
3. Get the operating thresholds from the operator's rule curve:
   minimum operating level 475.50 m, severe-rationing boundary 478 m.
4. Compute the 4-week drawdown rate. Sustained drawdown above
   0.15 m/week puts the boundary in play before the seasonal refill.
5. Set the light from level, usable storage percent, and drawdown
   rate. Show the country gray until a reservoir is wired in. Gray
   means "no monitor", not "safe".

## 5. Checks before you call a layer done

- The backfill covers the full climatology period (1991-2020 at
  minimum).
- The backtest report prints per country: precision, recall, misses
  by name.
- The dashboard shows the data date as the period covered, not the
  file date.
- A stale feed goes gray on the dashboard. Gray is never green.
- The daily cron run completes when one upstream source is down.
