# meteomatics.py
import math
from datetime import datetime, timezone
from typing import Iterable, List, Tuple, Union, Dict, Optional


import numpy as np
import pandas as pd
import requests
from io import StringIO

BASE = "https://api.meteomatics.com"

# Extend as needed; keep only parameters you know your license supports
SUPPORTED_PARAMS = {
    "t_2m:C",
    "msl_pressure:hPa",
    "wind_speed_10m:ms",
    "wind_dir_10m:d",
    "cape:Jkg",
    # radiation (instantaneous W/m^2)
    "global_rad:W",
    "clear_sky_rad:W",
    "direct_rad:W",
    "diffuse_rad:W",
    "longwave_rad:W",
}

# Safe default list (replaces the old asr:W with global_rad:W)
DEFAULT_PARAMS = [
    "t_2m:C",
    "msl_pressure:hPa",
    "wind_speed_10m:ms",
    "wind_dir_10m:d",
    "cape:Jkg",
    "global_rad:W",
]

def _to_utc_iso(ts) -> str:
    """Accept pd.Timestamp | datetime | ISO str -> '%Y-%m-%dT%H:%M:%SZ' UTC."""
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, str):
        return _to_utc_iso(pd.Timestamp(ts))
    raise TypeError(f"Unsupported timestamp type: {type(ts)}")

def _join_coords(lats: Iterable[float], lons: Iterable[float]) -> str:
    return "+".join(f"{la:.6f},{lo:.6f}" for la, lo in zip(lats, lons))

def _clean_params(params: Iterable[str]) -> List[str]:
    cleaned, unknown = [], []
    for p in params:
        if p in SUPPORTED_PARAMS:
            cleaned.append(p)
        else:
            unknown.append(p)
    # auto-fix common alias: asr:W -> global_rad:W
    for u in unknown:
        if u.lower().startswith("asr"):
            cleaned.append("global_rad:W")
    if not cleaned:
        raise ValueError(f"No supported Meteomatics parameters left after filtering. Unknown: {unknown}")
    return cleaned

def _read_csv_smart(text: str) -> pd.DataFrame:
    # Meteomatics often uses ';' as the delimiter. Detect quickly.
    head = text[:400]
    sep = ";" if head.count(";") >= head.count(",") else ","
    return pd.read_csv(StringIO(text), sep=sep)

def fetch_on_points(
    ts,
    lats: Iterable[float],
    lons: Iterable[float],
    user: str,
    password: str,
    params: Optional[Iterable[str]] = None,
    timeout: int = 30,
    max_points_per_request: int = 150,  # keep URLs short and safe
) -> pd.DataFrame:
    """
    Query Meteomatics at a given timestamp for the provided points.
    Batches the request to avoid overlong URLs.
    Returns a DataFrame with one row per point and columns per parameter (+ metadata).
    """
    if params is None:
        params = DEFAULT_PARAMS
    cleaned = _clean_params(params)

    ts_str = _to_utc_iso(ts)
    param_str = ",".join(cleaned)

    lats = list(lats)
    lons = list(lons)
    assert len(lats) == len(lons), "lats and lons length mismatch"

    # Keep original order via an index
    idx = np.arange(len(lats))

    # Helper to iterate chunks
    def _chunks(seq, n):
        for i in range(0, len(seq), n):
            yield i, seq[i:i+n]

    frames = []
    for start_idx, idx_chunk in _chunks(idx, max_points_per_request):
        lat_chunk = [lats[i] for i in idx_chunk]
        lon_chunk = [lons[i] for i in idx_chunk]
        coords_str = _join_coords(lat_chunk, lon_chunk)
        url = f"{BASE}/{ts_str}/{param_str}/{coords_str}/csv"

        resp = requests.get(url, auth=(user, password), timeout=timeout)
        # If a proxy/server still complains about URL length, reduce max_points_per_request (e.g., 80)
        resp.raise_for_status()

        df_chunk = _read_csv_smart(resp.text)

        # Add friendlier param columns if needed
        for p in cleaned:
            matches = [c for c in df_chunk.columns if c.startswith(p)]
            if matches and p not in df_chunk.columns:
                df_chunk[p] = df_chunk[matches[0]]

        # Attach a stable order key by re-parsing lat/lon into a merge key
        # Meteomatics returns columns named like 'lat', 'lon' or variants; normalize them.
        lat_col = next((c for c in df_chunk.columns if c.lower() == "lat"), None)
        lon_col = next((c for c in df_chunk.columns if c.lower() == "lon"), None)
        if lat_col is None or lon_col is None:
            # Fall back: try to detect columns that look like latitude/longitude
            candidates = [c for c in df_chunk.columns if c.lower().endswith("(lat)") or c.lower().endswith("(lon)")]
            # If still missing, we just append in the incoming order (chunk-local)
        # Build a merge key that matches the input points (rounded to 6 decimals like we sent)
        df_chunk["__lat_key"] = df_chunk[lat_col].round(6) if lat_col else np.array(lat_chunk).round(6)
        df_chunk["__lon_key"] = df_chunk[lon_col].round(6) if lon_col else np.array(lon_chunk).round(6)

        # Map input order to keys
        key_map = pd.DataFrame({
            "__lat_key": np.array(lat_chunk).round(6),
            "__lon_key": np.array(lon_chunk).round(6),
            "__idx": idx_chunk
        })
        df_chunk = df_chunk.merge(key_map, on=["__lat_key","__lon_key"], how="left")

        frames.append(df_chunk)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Sort back to original point order
    if "__idx" in df.columns:
        df.sort_values("__idx", inplace=True)
        df.drop(columns=["__idx","__lat_key","__lon_key"], errors="ignore", inplace=True)

    return df


# ---------- Helpers your grid_service expects ----------

def wind_uv(
    speed_ms: Union[float, np.ndarray, pd.Series],
    dir_deg:  Union[float, np.ndarray, pd.Series],
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert wind speed (m/s) and direction-from (degrees, meteorological)
    to u, v (m/s) in the standard math/CF convention:
        u: +eastward, v: +northward.
    """
    rad = np.deg2rad(dir_deg)
    # meteorological direction = FROM theta (clockwise from north)
    # u = -speed * sin(theta), v = -speed * cos(theta)
    u = -np.asarray(speed_ms) * np.sin(rad)
    v = -np.asarray(speed_ms) * np.cos(rad)
    return u, v


def add_wind_uv(
    df: pd.DataFrame,
    speed_col: str = "wind_speed_10m:ms",
    dir_col:   str = "wind_dir_10m:d",
    u_col:     str = "u10:ms",
    v_col:     str = "v10:ms",
) -> pd.DataFrame:
    """
    Add u/v columns to a Meteomatics result DataFrame in-place (and return it).
    """
    if speed_col not in df.columns or dir_col not in df.columns:
        missing = [c for c in (speed_col, dir_col) if c not in df.columns]
        raise KeyError(f"Missing wind columns in API response: {missing}")

    u, v = wind_uv(df[speed_col].to_numpy(), df[dir_col].to_numpy())
    df[u_col] = u
    df[v_col] = v
    return df

def normalize_features(
    df: pd.DataFrame,
    exclude: Tuple[str, ...] = ("validdate", "time", "valid_time", "lat", "lon"),
    eps: float = 1e-9,
) -> pd.DataFrame:
    """
    Simple z-score normalization of numeric columns (per snapshot),
    leaving coordinate/time columns intact. Returns a NEW DataFrame.
    """
    out = df.copy()
    # Attempt to add u/v if the raw wind columns exist
    if "wind_speed_10m:ms" in out.columns and "wind_dir_10m:d" in out.columns:
        u, v = wind_uv(out["wind_speed_10m:ms"].values, out["wind_dir_10m:d"].values)
        out["wind_u_10m:ms"] = u
        out["wind_v_10m:ms"] = v

    num_cols = [c for c in out.columns if c not in exclude and pd.api.types.is_numeric_dtype(out[c])]
    for c in num_cols:
        mu = float(np.nanmean(out[c].values))
        sd = float(np.nanstd(out[c].values))
        out[c] = (out[c] - mu) / (sd + eps)
    return out
