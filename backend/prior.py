import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sklearn.neighbors import KernelDensity

def load_thermals_prior(geojson_path: str) -> gpd.GeoDataFrame:
    """
    Load thermals.geojson as points with UTC hour. Expect fields:
    lon, lat, start_ts (epoch or iso) or similar. Adjust to your file schema.
    """
    gdf = gpd.read_file(geojson_path)
    # Heuristics to find lon/lat columns if geometry is present
    if gdf.geometry is not None:
        gdf = gdf.set_geometry("geometry")
        gdf["lon"] = gdf.geometry.x
        gdf["lat"] = gdf.geometry.y

    # Infer hour—try timestamp columns commonly found in your files
    # If you have explicit hour field, replace this with it.
    time_col = None
    for c in ["start_time", "start_ts", "t_start", "timestamp", "time"]:
        if c in gdf.columns:
            time_col = c
            break

    if time_col is not None:
        t = pd.to_datetime(gdf[time_col], utc=True, errors="coerce")
        gdf["hour"] = t.dt.hour
    else:
        # fallback: assume midday if missing
        gdf["hour"] = 12

    # Keep only lon/lat/hour
    return gdf[["lon","lat","hour"]].dropna()

def kde_prior_for_hour(points_df: pd.DataFrame, grid_xy: np.ndarray, bandwidth_km=2.0):
    """
    Simple 2D KDE on lon/lat treated as planar via small-area scaling.
    For better accuracy, project to metric CRS before KDE (left simple for MVP).
    """
    if len(points_df) < 50:
        # too few samples → return zeros
        return np.zeros(len(grid_xy))

    # crude scale lon/lat to km around mid-lat
    lat0 = points_df["lat"].mean() if not points_df.empty else 55.95
    km_per_deg_lat = 111.32
    km_per_deg_lon = km_per_deg_lat * np.cos(np.radians(lat0))

    X = np.vstack([
        (points_df["lon"].values) * km_per_deg_lon,
        (points_df["lat"].values) * km_per_deg_lat
    ]).T

    Y = np.vstack([
        grid_xy[:,0] * km_per_deg_lon,
        grid_xy[:,1] * km_per_deg_lat
    ]).T

    kde = KernelDensity(bandwidth=bandwidth_km, kernel="gaussian")
    kde.fit(X)
    log_dens = kde.score_samples(Y)
    dens = np.exp(log_dens)
    # normalize 0..1
    dens = (dens - dens.min()) / (dens.ptp() + 1e-9)
    return dens

