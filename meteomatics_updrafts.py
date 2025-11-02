#!/usr/bin/env python3
"""
meteomatics_updrafts.py
Fetch vertical wind ("omega" in Pa/s; negative = ascent) from Meteomatics.

Writes src/data/updrafts.json with:
  - meta (time, model, bbox, step, levels)
  - grids[level]  -> [{lat,lon,value}, ...] sorted by most negative (ascent)
  - top[level]    -> top-N strongest ascent cells

FEATURE: If a bbox (areal) request returns 400 (free tier usually disallows areal),
we automatically fall back to a point-scan: iterate grid points and query one location
per request (free tier allows single-location queries).

Usage:
  python meteomatics_updrafts.py --time 2025-07-15T12:00:00Z \
    --bbox "47.5,5.0 45.0,12.5" --levels 700 500 --step 0.1 \
    --top 50 --model mix --outfile src/data/updrafts.json \
    --user USER --password PASS

Env auth:
  METEOMATICS_USER, METEOMATICS_PASS
"""

from __future__ import annotations
import argparse, csv, io, json, os, sys, time, math
from typing import List, Dict, Any, Tuple
import requests

API_BASE = "https://api.meteomatics.com"

def parse_bbox(s: str) -> Tuple[float, float, float, float]:
    """
    Parse 'N,W S,E' (space between corners; comma between lat,lon).
    Example: '47.5,5.0 45.0,12.5' -> (47.5, 5.0, 45.0, 12.5)
    """
    try:
        nw, se = s.strip().split()
        n, w = [float(x) for x in nw.split(",")]
        s_, e = [float(x) for x in se.split(",")]
        return n, w, s_, e
    except Exception:
        raise argparse.ArgumentTypeError("bbox must be 'N,W S,E' (e.g., '47.5,5.0 45.0,12.5')")

def build_grid_location(n: float, w: float, s_: float, e: float, step_deg: float) -> str:
    """
    Meteomatics bbox grid syntax: <lat_N,lon_W>_<lat_S,lon_E>:<dlat x dlon>
    """
    return f"{n:.6f},{w:.6f}_{s_:.6f},{e:.6f}:{step_deg}x{step_deg}"

def level_param(level_hpa: int) -> str:
    # Vertical wind (omega) at a pressure level, in Pascal per second
    return f"wind_speed_w_{level_hpa}hPa:Pas"

def fetch_csv_areal(time_iso: str, params: List[str], location: str, auth: Tuple[str, str],
                    model: str = "mix", timeout: int = 60) -> requests.Response:
    url = f"{API_BASE}/{time_iso}/" + ",".join(params) + f"/{location}/csv?model={model}"
    r = requests.get(url, auth=auth, timeout=timeout)
    return r

def fetch_csv_point(time_iso: str, params: List[str], lat: float, lon: float, auth: Tuple[str, str],
                    model: str = "mix", timeout: int = 60) -> requests.Response:
    # one location (lat,lon) only
    url = f"{API_BASE}/{time_iso}/" + ",".join(params) + f"/{lat:.6f},{lon:.6f}/csv?model={model}"
    r = requests.get(url, auth=auth, timeout=timeout)
    return r

def parse_csv_rows(csv_text: str) -> List[Dict[str, Any]]:
    """
    Meteomatics CSV uses ';' separator and columns: validdate;parameter;lat;lon;value
    """
    rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=';'))
    for r in rows:
        # normalize
        try:
            r["lat"] = float(r["lat"])
            r["lon"] = float(r["lon"])
            r["value"] = float(r["value"])
        except Exception:
            r["value"] = float("nan")
    return rows

def grid_points(n: float, w: float, s_: float, e: float, step: float, max_points: int | None) -> List[Tuple[float,float]]:
    """Generate grid (lat,lon) points from bbox and step. Optionally limit to max_points."""
    if step <= 0:
        raise ValueError("step must be > 0")
    lats = []
    lat = n
    # go southward
    while lat + 1e-9 >= s_:
        lats.append(lat)
        lat = lat - step
        if lat < s_ and (n - s_) % step > 1e-9:
            # include exact south edge
            lats.append(s_)
            break
    lons = []
    lon = w
    while lon <= e + 1e-9:
        lons.append(lon)
        lon = lon + step
        if lon > e and (e - w) % step > 1e-9:
            lons.append(e)
            break
    pts = [(la, lo) for la in lats for lo in lons]
    if max_points is not None and len(pts) > max_points:
        # simple thinning: take roughly uniform subset
        stride = math.ceil(len(pts) / max_points)
        pts = pts[::stride]
    return pts

def build_output(rows: List[Dict[str, Any]], levels: List[int], top: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"levels": levels, "grids": {}, "top": {}}
    for lvl in levels:
        p = level_param(lvl)
        pts = [ {"lat": r["lat"], "lon": r["lon"], "value": r["value"]}
                for r in rows if r.get("parameter") == p ]
        pts_sorted = sorted(pts, key=lambda x: x["value"])  # most negative first
        out["grids"][str(lvl)] = pts_sorted
        out["top"][str(lvl)] = pts_sorted[:top] if top > 0 else []
    return out

def main():
    ap = argparse.ArgumentParser(description="Fetch Meteomatics vertical wind (omega, Pa/s) and write updrafts JSON.")
    ap.add_argument("--time", required=True, help="ISO time, e.g. 2025-07-15T12:00:00Z")
    ap.add_argument("--bbox", type=parse_bbox, required=True, help="Bounding box 'N,W S,E' (e.g. '47.5,5.0 45.0,12.5')")
    ap.add_argument("--levels", type=int, nargs="+", default=[700, 500], help="Pressure levels in hPa (e.g. 700 500)")
    ap.add_argument("--step", type=float, default=0.1, help="Grid step in degrees (e.g. 0.1 ≈ 11 km)")
    ap.add_argument("--model", default="mix", help="Model name (default: mix). Examples: icon-eu, gfs, ecmwf-ifs")
    ap.add_argument("--top", type=int, default=50, help="How many strongest updraft points to keep per level")
    ap.add_argument("--outfile", default="src/data/updrafts.json", help="Output JSON path")
    ap.add_argument("--user", help="Meteomatics username (fallback METEOMATICS_USER env)")
    ap.add_argument("--password", help="Meteomatics password (fallback METEOMATICS_PASS env)")
    # point-scan controls
    ap.add_argument("--max-points", type=int, default=400, help="Max grid points to sample in point-scan fallback")
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between point requests (free tier friendly)")
    args = ap.parse_args()

    user = args.user or os.getenv("METEOMATICS_USER")
    password = args.password or os.getenv("METEOMATICS_PASS")
    if not user or not password:
        print("ERROR: Provide --user/--password or set METEOMATICS_USER / METEOMATICS_PASS", file=sys.stderr)
        sys.exit(2)

    n, w, s_, e = args.bbox
    location = build_grid_location(n, w, s_, e, args.step)
    params = [level_param(lvl) for lvl in args.levels]
    auth = (user, password)

    print(f"Requesting Meteomatics omega @ {args.time} | levels {args.levels} hPa")
    print(f"  bbox N={n}, W={w}, S={s_}, E={e}, step={args.step} | model={args.model}")

    # Try AREAL request first
    rows: List[Dict[str, Any]] = []
    r = fetch_csv_areal(args.time, params, location, auth, model=args.model)
    if r.ok:
        rows = parse_csv_rows(r.text)
        print(f"Areal OK: received {len(rows)} rows")
    else:
        # If free tier, 400 is common for areal; print server message
        try:
            msg = r.text.strip()
        except Exception:
            msg = ""
        print(f"Areal request failed: {r.status_code} {r.reason} — {msg[:200]}", file=sys.stderr)
        print("Falling back to point-scan (single-location queries)...")

        pts = grid_points(n, w, s_, e, args.step, args.max_points)
        print(f"Point-scan over {len(pts)} points (max-points={args.max_points})")
        for idx, (la, lo) in enumerate(pts, 1):
            pr = fetch_csv_point(args.time, params, la, lo, auth, model=args.model)
            if pr.status_code == 401:
                print("Unauthorized (401) — check Meteomatics credentials", file=sys.stderr)
                sys.exit(3)
            if not pr.ok:
                # skip this point, but log minimal info
                print(f"  {idx:04d}/{len(pts)} {la:.3f},{lo:.3f} -> {pr.status_code} {pr.reason}", file=sys.stderr)
            else:
                rows.extend(parse_csv_rows(pr.text))
            if args.sleep > 0:
                time.sleep(args.sleep)

        print(f"Point-scan collected {len(rows)} rows")

    payload = {
        "meta": {
            "time": args.time,
            "model": args.model,
            "bbox": {"north": n, "west": w, "south": s_, "east": e},
            "step_deg": args.step,
            "param_units": "Pa/s (omega; negative = ascent)",
            "mode": "areal" if r.ok else "point-scan",
        }
    }
    payload.update(build_output(rows, args.levels, args.top))

    os.makedirs(os.path.dirname(args.outfile), exist_ok=True)
    with open(args.outfile, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    total_pts = sum(len(v) for v in payload["grids"].values())
    print(f"Wrote {args.outfile} with {total_pts} points across levels {args.levels}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
