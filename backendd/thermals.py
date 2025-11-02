# thermals.py
from __future__ import annotations
import numpy as np
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Point

def _col_or_zeros(df, name: str, n: int, dtype=float):
    """Return df[name] as a NumPy array; if missing, return zeros of length n."""
    if name in df.columns:
        return np.asarray(df[name], dtype=dtype)
    return np.zeros(n, dtype=dtype)

def _safe_minmax(x: np.ndarray) -> tuple[float, float]:
    x = x[~np.isnan(x)]
    if x.size == 0:
        return 0.0, 1.0
    lo, hi = float(np.min(x)), float(np.max(x))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return lo, lo + 1.0
    return lo, hi

def score_grid_for_thermals(g):
    n = len(g)
    cape = _col_or_zeros(g, "cape_Jkg", n, float)
    t2   = _col_or_zeros(g, "t_2m_C", n, float)
    rad  = _col_or_zeros(g, "global_rad", n, float)              # we standardized to global_rad
    tpi  = _col_or_zeros(g, "tpi", n, float)
    ws   = _col_or_zeros(g, "wind_speed_10m", n, float)

    # simple heuristic scoring (tweak as you wish)
    cape_z = np.clip(cape / 1000.0, 0, 1)                        # 0..1 scale
    rad_z  = np.clip(rad  / 600.0, 0, 1)                         # typical daytime range
    tpi_z  = np.clip((tpi - np.nanmin(tpi)) / (np.nanmax(tpi) - np.nanmin(tpi) + 1e-9), 0, 1)

    # penalize very high wind
    wind_pen = np.clip(1.0 - (ws / 12.0), 0, 1)                  # >12 m/s hurts thermalling

    score = 0.45*cape_z + 0.35*rad_z + 0.20*tpi_z
    score *= wind_pen

    g["thermal_score"] = score
    return g


def grid_to_thermals(
    scored_grid: gpd.GeoDataFrame,
    score_quantile: float = 0.90,
    min_cells_per_blob: int = 3,
) -> gpd.GeoDataFrame:
    """
    Select high-score cells, merge contiguous polygons, and emit one point per blob.
    Returns a GeoDataFrame of Point features with summary props.
    """
    if "thermal_score" not in scored_grid.columns:
        raise ValueError("grid_to_thermals expects 'thermal_score' column. Call score_grid_for_thermals first.")

    thr = float(np.nanquantile(scored_grid["thermal_score"].to_numpy(), score_quantile))
    hot = scored_grid[scored_grid["thermal_score"] >= thr].copy()
    if hot.empty:
        return gpd.GeoDataFrame(geometry=[], crs=scored_grid.crs)

    # Merge touching cells; unary_union returns (Multi)Polygon(s)
    merged = unary_union(hot.geometry.values)

    # Make iterable
    geoms = []
    if merged.geom_type == "Polygon":
        geoms = [merged]
    elif merged.geom_type == "MultiPolygon":
        geoms = list(merged.geoms)

    # For each blob, compute centroid and summary stats from member cells
    rows = []
    for poly in geoms:
        members = hot[hot.geometry.intersects(poly)]
        if len(members) < min_cells_per_blob:
            continue
        centroid = poly.representative_point()  # inside the polygon
        props = {
            "n_cells": int(len(members)),
            "score_mean": float(np.nanmean(members["thermal_score"])),
            "score_max": float(np.nanmax(members["thermal_score"])),
            "global_rad_mean": float(np.nanmean(members.get("global_rad", np.nan))),
            "cape_mean": float(np.nanmean(members.get("cape_Jkg", np.nan))),
            "tpi_mean": float(np.nanmean(members.get("tpi", np.nan))),
        }
        rows.append((centroid, props))

    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs=scored_grid.crs)

    out = gpd.GeoDataFrame(
        [p for _, p in rows],
        geometry=[geom for geom, _ in rows],
        crs=scored_grid.crs,
    )
    # convenience lat/lon columns for easy display
    out["lat"] = out.geometry.y
    out["lon"] = out.geometry.x
    return out
