# grid_service.py
from __future__ import annotations

import time
import numpy as np
import pandas as pd
import geopandas as gpd

from thermals import score_grid_for_thermals, grid_to_thermals

from utils import (
    load_env,
    get_bbox,
    grid_1km_wgs84,
)
from meteomatics import fetch_on_points, normalize_features, add_wind_uv
from prior import load_thermals_prior, kde_prior_for_hour
from tpi import tpi_from_live_and_prior, climb_from_tpi_and_flux


class GridCache:
    def __init__(self, minutes: int = 15):
        self.minutes = minutes
        self.last_ts = 0.0
        self.payload: gpd.GeoDataFrame | None = None


CACHE: GridCache | None = None
GRID_GDF: gpd.GeoDataFrame | None = None
PRIOR_GDF: gpd.GeoDataFrame | None = None


def build_grid_and_prior() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame | None]:
    """Build the static grid once and (optionally) load the thermals prior."""
    env = load_env()
    bbox = get_bbox(env)
    res_m = int(env.get("GRID_RES_M", "1000"))

    grid = grid_1km_wgs84(bbox, res_m=res_m)

    prior_gdf = None
    prior_path = env.get("PRIOR_GEOJSON")
    if prior_path and prior_path not in ("", "None"):
        prior_gdf = load_thermals_prior(prior_path)

    return grid, prior_gdf


def _current_timeslot_utc() -> pd.Timestamp:
    """
    Return a timezone-aware UTC timestamp floored to 15 minutes.
    """
    return pd.Timestamp.now(tz="UTC").floor("15min")


def compute_snapshot() -> gpd.GeoDataFrame:
    """
    Build one live snapshot of the grid with model features + derived fields.
    Returns a GeoDataFrame with geometry + columns:
      lat, lon, tpi, climb_ms, wind_u, wind_v, t_2m_C, t_850hPa_C,
      cape_Jkg, global_rad, total_cloud_cover_oktas, wind_speed_10m, wind_dir_10m
    (Only present if available from the API.)
    """
    env = load_env()
    user = env["METEO_USER"]
    pw = env["METEO_PASS"]
    ts = _current_timeslot_utc()

    # Ensure grid & prior are available
    global GRID_GDF, PRIOR_GDF
    if GRID_GDF is None or PRIOR_GDF is None:
        GRID_GDF, PRIOR_GDF = build_grid_and_prior()

    # Query live data on grid centroids
    lats = GRID_GDF["lat"].to_numpy()
    lons = GRID_GDF["lon"].to_numpy()

    live = fetch_on_points(ts, lats, lons, user, pw)
    live = normalize_features(live)  # keeps safe column names with underscores
    live = add_wind_uv(live)         # requires wind_speed_10m + wind_dir_10m if present

    # Build prior on this hour (KDE projected to grid)
    if PRIOR_GDF is not None and "hour" in PRIOR_GDF.columns:
        hour = int(ts.hour)
        pts_this_hour = PRIOR_GDF[PRIOR_GDF["hour"] == hour]
        grid_xy = np.vstack([GRID_GDF["lon"].values, GRID_GDF["lat"].values]).T
        prior01 = kde_prior_for_hour(pts_this_hour, grid_xy, bandwidth_km=2.0)
    else:
        # Weak uniform prior if none available
        prior01 = np.full(len(GRID_GDF), 0.2, dtype=float)

    # Terrain Prominence Index and climb proxy
    tpi = tpi_from_live_and_prior(live, prior01)
    climb = climb_from_tpi_and_flux(live, tpi)

    # Join back to geometry + copy selected columns if present
    out = GRID_GDF[["cell_id", "geometry", "lat", "lon"]].copy()
    out["tpi"] = tpi.values
    out["climb_ms"] = climb.values

    # Copy over a curated set of columns if they exist
    wanted_cols = [
        "wind_u", "wind_v",
        "wind_speed_10m", "wind_dir_10m",
        "t_2m_C", "t_850hPa_C",
        "cape_Jkg",
        "global_rad",                 # NOTE: replaces invalid 'asr:W'
        "total_cloud_cover_oktas",    # safe name (no colons)
    ]
    for col in wanted_cols:
        if col in live.columns:
            out[col] = live[col].values

    # Optionally add a thermal score here so /grid is ready for /thermals
    out = score_grid_for_thermals(out)

    return out


def get_cached_grid() -> gpd.GeoDataFrame:
    """Return a cached snapshot; refresh every CACHE_MINUTES."""
    global CACHE
    if CACHE is None:
        CACHE = GridCache(minutes=int(load_env().get("CACHE_MINUTES", "15")))

    now = time.time()
    if CACHE.payload is None or (now - CACHE.last_ts) > 60 * CACHE.minutes:
        gdf = compute_snapshot()
        CACHE.payload = gdf
        CACHE.last_ts = now

    return CACHE.payload
