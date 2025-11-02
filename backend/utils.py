import os
from dataclasses import dataclass
from typing import Tuple, Dict

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box

@dataclass
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

def _strip_inline_comment(val: str) -> str:
    """Remove inline comments and surrounding quotes/spaces."""
    # split on first '#' to drop trailing comments
    val = val.split('#', 1)[0]
    val = val.strip().strip('"').strip("'")
    return val

def load_env() -> Dict[str, str]:
    """
    Minimal .env loader that:
      - ignores lines starting with '#'
      - supports inline comments after values
      - merges with real environment variables (real env wins)
    """
    env: Dict[str, str] = {}
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = _strip_inline_comment(v)
                env[k] = v
    # merge os.environ on top (system env has priority)
    env |= {k: v for k, v in os.environ.items()}
    return env

def get_bbox(env: Dict[str, str]) -> BBox:
    return BBox(
        float(env.get("BBOX_MIN_LON")),
        float(env.get("BBOX_MIN_LAT")),
        float(env.get("BBOX_MAX_LON")),
        float(env.get("BBOX_MAX_LAT")),
    )

def _utm_epsg_for(lon: float, lat: float) -> int:
    """
    Pick a UTM EPSG code for given lon/lat.
    Northern hemisphere: EPSG:326xx, Southern: EPSG:327xx.
    Falls back to Web Mercator elsewhere if something goes wrong.
    """
    zone = int((lon + 180) // 6) + 1
    if zone < 1: zone = 1
    if zone > 60: zone = 60
    return 32600 + zone if lat >= 0 else 32700 + zone

def grid_1km_wgs84(bbox: BBox, res_m: int = 1000) -> gpd.GeoDataFrame:
    """
    Build ~1 km square grid in a metric CRS, then reproject to WGS84.
    Centroids are computed in the metric CRS (accurate), then transformed.
    """
    wgs84 = 4326

    # Create the bbox polygon in WGS84
    poly_wgs84 = box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
    bbox_gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[poly_wgs84], crs=wgs84)

    # Choose a local projected CRS (UTM) based on bbox center; fallback to 3857
    center_lon = (bbox.min_lon + bbox.max_lon) / 2.0
    center_lat = (bbox.min_lat + bbox.max_lat) / 2.0
    try:
        local_epsg = _utm_epsg_for(center_lon, center_lat)
        bbox_proj = bbox_gdf.to_crs(local_epsg)
        local_crs = local_epsg
    except Exception:
        local_crs = 3857
        bbox_proj = bbox_gdf.to_crs(local_crs)

    poly_proj = bbox_proj.geometry.iloc[0]
    minx, miny, maxx, maxy = poly_proj.bounds

    # Build grid in meters (ensure coverage up to the max bound)
    xs = np.arange(minx, maxx, res_m, dtype=float)
    ys = np.arange(miny, maxy, res_m, dtype=float)

    cells = [box(x, y, x + res_m, y + res_m) for x in xs for y in ys]
    grid_proj = gpd.GeoDataFrame(
        {"cell_id": np.arange(len(cells), dtype=int)},
        geometry=cells,
        crs=local_crs,
    )

    # Compute centroids in projected CRS, then convert to WGS84
    centroids_proj = grid_proj.geometry.centroid
    centroids_geo = gpd.GeoSeries(centroids_proj, crs=local_crs).to_crs(wgs84)

    # Reproject grid polygons to WGS84 for output
    grid = grid_proj.to_crs(wgs84)

    # Attach centroid lon/lat
    grid["lon"] = centroids_geo.x
    grid["lat"] = centroids_geo.y

    return grid

def now_iso_truncated() -> str:
    """
    Return current UTC timestamp truncated to 15 minutes, formatted as ISO8601 with trailing 'Z'.
    Ensures no '+00:00Z' duplication.
    """
    ts = pd.Timestamp.utcnow().floor("15min")
    # format without offset, then append Z
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

def hour_of_day_utc() -> int:
    return pd.Timestamp.utcnow().hour
