"""V2.8: crop-area weights for zonal rainfall (SCOPING-V2.md).

Builds data/zones/weights.npz: per crop zone, a float32 array on the
CHIRPS Africa grid = boolean zone mask x GAEZ+ 2015 harvested area of
the zone's crop (5-arcmin, bilinearly regridded). chirps_raster's
zonal means then weight rainfall by where the crop actually grows
instead of counting every pixel equally — material for large mixed
polygons (eth_oromia_maize, the ZMB provinces), immaterial for
compact ones.

Zones whose polygon holds no mapped crop area fall back to the
uniform boolean mask (logged). Flood basins are untouched — basin
hydrology wants the plain pixel mean.

Run after any mask rebuild: python ingest/zone_weights.py
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import rasterio
import rasterio.transform
import rasterio.warp

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAEZ = ROOT / "data" / "raw" / "gaez2015"
MASKS_NPZ = ROOT / "data" / "zones" / "masks.npz"
WEIGHTS_NPZ = ROOT / "data" / "zones" / "weights.npz"

CROP_RASTER = {"maize": "Maize", "small grains": "Wheat",
               "sorghum": "Sorghum", "rice": "Rice"}


def main() -> int:
    sys.path.insert(0, str(ROOT / "ingest"))
    import db
    con = db.connect()
    zcrop = {r[0]: (r[1] or "maize").lower() for r in
             con.execute("SELECT zone_key, crop FROM zones")}

    z = np.load(MASKS_NPZ)
    bounds = tuple(z["__bounds__"])
    masks = {k: z[k] for k in z.files if k != "__bounds__"}
    shape = next(iter(masks.values())).shape
    dst_transform = rasterio.transform.from_bounds(*bounds, shape[1],
                                                   shape[0])

    regridded = {}
    for crop, name in CROP_RASTER.items():
        with rasterio.open(GAEZ / f"HarvArea_{name}_Total.tif") as src:
            dst = np.zeros(shape, dtype=np.float32)
            rasterio.warp.reproject(
                rasterio.band(src, 1), dst,
                dst_transform=dst_transform, dst_crs=src.crs,
                resampling=rasterio.warp.Resampling.bilinear,
                dst_nodata=0.0)
            regridded[crop] = np.where(np.isfinite(dst) & (dst > 0),
                                       dst, 0.0)

    out, uniform = {}, []
    for zk, m in masks.items():
        crop = zcrop.get(zk, "maize")
        w = (m.astype(np.float32)
             * regridded.get(crop, regridded["maize"]))
        if w.sum() <= 0:
            w = m.astype(np.float32)   # no mapped crop area: uniform
            uniform.append(zk)
        out[zk] = w
    np.savez_compressed(WEIGHTS_NPZ, **out,
                        __bounds__=np.array(bounds))
    print(f"weights built for {len(out)} zones on grid {shape}; "
          f"uniform fallback: {', '.join(uniform) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
