#!/usr/bin/env python3
"""
generate_from_weglide.py
Fetch WeGlide thermals for a given UTC day, convert to nodes, run A* planner, and write plan.json.
"""

from __future__ import annotations
import math, json, argparse, sys
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from weglide_client import WeGlideClient
# import your existing planner pieces:
#  - Node dataclass (id, lat, lon, thermal_net_ms, ceiling_msl)
#  - find_route_with_thermals (A* router)
#  - Polar, MetProvider, StepLog, RoutePlan
from generate import (
    Polar,
    MetProvider,
    Node,
    find_route_with_thermals as astar_best_path,
    StepLog,
    RoutePlan,
)

EARTH_R = 6371000.0

def haversine_m(lat1, lon1, lat2, lon2):
    φ1, λ1, φ2, λ2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dφ, dλ = φ2 - φ1, λ2 - λ1
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * EARTH_R * math.asin(math.sqrt(a))

def initial_bearing_deg(lat1, lon1, lat2, lon2):
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dλ = math.radians(lon2 - lon1)
    y = math.sin(dλ) * math.cos(φ2)
    x = math.cos(φ1)*math.sin(φ2) - math.sin(φ1)*math.cos(φ2)*math.cos(dλ)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def cross_track_distance_m(lat, lon, a_lat, a_lon, b_lat, b_lon):
    """Shortest distance from point P(lat,lon) to great-circle AB, in meters."""
    d13 = haversine_m(a_lat, a_lon, lat, lon) / EARTH_R
    tc13 = math.radians(initial_bearing_deg(a_lat, a_lon, lat, lon))
    tc12 = math.radians(initial_bearing_deg(a_lat, a_lon, b_lat, b_lon))
    return abs(math.asin(math.sin(d13) * math.sin(tc13 - tc12)) * EARTH_R)

def parse_day_to_unix(day_str: Optional[str]) -> int:
    if day_str:
        y, m, d = map(int, day_str.split("-"))
        dt = datetime(y, m, d, 0, 0, 0, tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())

def to_iso(ts) -> Optional[str]:
    try:
        # timezone-aware replacement for deprecated utcfromtimestamp
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None

def normalize_weglide_item(item) -> Optional[Dict[str, Any]]:
    # Flexible normalizer (array/dict)
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        rec = {"lon": float(item[1]), "lat": float(item[2])}
        if len(item) >= 4: rec["alt_base_m"] = item[3]
        if len(item) >= 5: rec["alt_top_m"]  = item[4]
        if len(item) >= 6: rec["t_start"]    = to_iso(item[5])
        if len(item) >= 7: rec["t_end"]      = to_iso(item[6])
        try: rec["id"] = int(item[0])
        except Exception: pass
        return rec
    if isinstance(item, dict):
        lat = item.get("lat") or item.get("latitude") or item.get("y")
        lon = item.get("lon") or item.get("lng") or item.get("longitude") or item.get("x")
        if lat is None or lon is None:
            return None
        out = {"lat": float(lat), "lon": float(lon)}
        for k in ("alt_base_m","alt_top_m","t_start","t_end","id"):
            if k in item: out[k] = item[k]
        return out
    return None

def estimate_net_ms(rec: Dict[str, Any], default_net: float = 1.8) -> float:
    """
    Estimate net climb from alt_top/base and time window; fallback to default.
    """
    alt_base = rec.get("alt_base_m")
    alt_top  = rec.get("alt_top_m")
    t_start  = rec.get("t_start")
    t_end    = rec.get("t_end")
    try:
        if alt_base is not None and alt_top is not None and t_start and t_end:
            t0 = datetime.fromisoformat(t_start).timestamp()
            t1 = datetime.fromisoformat(t_end).timestamp()
            dt = max(1.0, t1 - t0)
            dz = float(alt_top) - float(alt_base)
            return max(0.2, min(6.0, dz / dt))
    except Exception:
        pass
    return default_net

def corridor_filter(rows: List[Dict[str, Any]],
                    start, goal,
                    corridor_km: float,
                    max_nodes: int,
                    min_net: float) -> List[Dict[str, Any]]:
    """Finite capsule corridor + quality filter."""
    a_lat, a_lon = start["lat"], start["lon"]
    b_lat, b_lon = goal["lat"], goal["lon"]

    corridor_m = corridor_km * 1000.0
    seg_len_m = haversine_m(a_lat, a_lon, b_lat, b_lon)

    selected = []
    for r in rows:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            continue

        # 1) Within corridor distance to great-circle
        xtrack = cross_track_distance_m(lat, lon, a_lat, a_lon, b_lat, b_lon)
        if xtrack > corridor_m:
            continue

        # 2) Also within finite capsule around the segment
        da = haversine_m(a_lat, a_lon, lat, lon)
        db = haversine_m(b_lat, b_lon, lat, lon)
        if da > seg_len_m + corridor_m or db > seg_len_m + corridor_m:
            continue

        net = estimate_net_ms(r)
        if net < min_net:
            continue

        r["_net_ms"] = float(net)
        ceil = r.get("alt_top_m")
        if ceil is None and r.get("alt_base_m") is not None:
            ceil = float(r["alt_base_m"]) + 1000.0
        r["_ceiling"] = float(ceil) if ceil is not None else None

        selected.append(r)

    # Sort: nearest to START first, then higher net — so T1 is the closest thermal
    selected.sort(key=lambda r: (haversine_m(a_lat, a_lon, r["lat"], r["lon"]), -r["_net_ms"]))

    if max_nodes and len(selected) > max_nodes:
        selected = selected[:max_nodes]
    return selected

def main():
    ap = argparse.ArgumentParser(description="Generate plan.json using live WeGlide thermals")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--start", required=True, nargs=3, metavar=("LAT","LON","H_MSL"),
                    type=float, help="Start lat lon heightMSL")
    ap.add_argument("--goal", required=True, nargs=3, metavar=("LAT","LON","ARRIVE_H"),
                    type=float, help="Goal lat lon arrivalHeightMSL")
    ap.add_argument("--corridor-km", type=float, default=30.0, help="Half-width corridor (km)")
    ap.add_argument("--min-net", type=float, default=1.2, help="Minimum net thermal (m/s)")
    ap.add_argument("--max-nodes", type=int, default=30, help="Max thermal nodes to consider")
    ap.add_argument("--per-leg-floor", type=float, default=1200.0, help="Arrival floor required at end of each leg (MSL meters)")
    ap.add_argument("--chain-thermals", action="store_true",
                    help="If set, allow hops between thermals (multi-stop). Default is single thermal then GOAL.")
    ap.add_argument("--outfile", default="plan.json")
    # planner knobs
    ap.add_argument("--mc", type=float, default=0.0, help="MacCready (m/s)")
    ap.add_argument("--wind", type=float, default=8.0, help="Wind speed m/s")
    ap.add_argument("--wdir", type=float, default=260.0, help="Wind FROM deg")
    ap.add_argument("--wair", type=float, default=0.0, help="Background w_air m/s")
    args = ap.parse_args()

    ts = parse_day_to_unix(args.day)
    wg = WeGlideClient()
    raw = wg.get_thermals(time_unix=ts)
    rows = []
    for it in raw:
        rec = normalize_weglide_item(it)
        if rec: rows.append(rec)

    # --- Print all thermals fetched from WeGlide ---
    # print(f"\nFetched {len(rows)} thermals from WeGlide for {args.day or 'today'}:")
    # for i, r in enumerate(rows):
    #     lat = r.get("lat")
    #     lon = r.get("lon")
    #     base = r.get("alt_base_m")
    #     top = r.get("alt_top_m")
    #     net = estimate_net_ms(r)
    #     print(f"  {i+1:03d}: lat={lat:.4f}, lon={lon:.4f}, base={base}, top={top}, est_net={net:.2f} m/s")
    # print()

    start = {"lat": args.start[0], "lon": args.start[1], "h_msl": args.start[2]}
    goal  = {"lat": args.goal[0],  "lon": args.goal[1],  "h_req_msl": args.goal[2]}

    sel = corridor_filter(rows, start, goal,
                          corridor_km=args.corridor_km,
                          max_nodes=args.max_nodes,
                          min_net=args.min_net)

    # --- Print selected thermals after corridor filtering ---
    print(f"Selected {len(sel)} thermals inside {args.corridor_km:.1f} km corridor (min_net={args.min_net} m/s):")
    for i, r in enumerate(sel):
        print(f"  {i+1:03d}: lat={r['lat']:.4f}, lon={r['lon']:.4f}, net={r['_net_ms']:.2f} m/s, ceiling={r.get('_ceiling')}")
    print()

    # --- Build nodes once (START + thermals + GOAL) and a parallel export for the visualiser
    nodes_list: List[Node] = []
    nodes_list.append(Node("START", start["lat"], start["lon"], thermal_net_ms=0.0, ceiling_msl=None))

    thermals_export = []
    for idx, r in enumerate(sel, start=1):
        nid = f"T{idx}"
        nodes_list.append(Node(nid, r["lat"], r["lon"],
                               thermal_net_ms=float(r["_net_ms"]),
                               ceiling_msl=r.get("_ceiling")))
        thermals_export.append({
            "id": nid,
            "lat": r["lat"],
            "lon": r["lon"],
            "net_ms": float(r["_net_ms"]),
            "ceiling_msl": r.get("_ceiling"),
            "alt_base_m": r.get("alt_base_m"),
            "alt_top_m": r.get("alt_top_m"),
            "t_start": r.get("t_start"),
            "t_end": r.get("t_end"),
            "used_in_path": False,  # will be updated after solving
        })

    nodes_list.append(Node("GOAL", goal["lat"], goal["lon"], thermal_net_ms=0.0, ceiling_msl=None))

    # --- Build edges so thermals are actually usable ---
    thermal_ids = [n.id for n in nodes_list if n.id not in ("START", "GOAL")]
    edges: Dict[str, List[str]] = {"GOAL": []}
    edges["START"] = thermal_ids + ["GOAL"]  # allow direct, plus via any thermal
    if args.chain_thermals:
        for i in thermal_ids:
            edges[i] = [j for j in thermal_ids if j != i] + ["GOAL"]
    else:
        for i in thermal_ids:
            edges[i] = ["GOAL"]

    # --- Planner environment
    polar = Polar(a=0.3, b=0.005, c=0.0012, bug_factor=1.1)
    met   = MetProvider(wind_speed_ms=args.wind, wind_dir_from_deg=args.wdir, w_air_ms=args.wair)

    # --- Run A*
    nodes_dict = {n.id: n for n in nodes_list}
    plan_obj = astar_best_path(
        nodes=nodes_dict,
        edges=edges,
        start_id="START",
        goal_id="GOAL",
        start_h_msl=start["h_msl"],
        arrival_floor_each_leg_msl=args.per_leg_floor,
        polar=polar,
        met=met,
        mc_value_ms=args.mc,
    )

    # Mark used thermals for styling on the map
    used_ids = set(plan_obj.path) - {"START", "GOAL"}
    for t in thermals_export:
        if t["id"] in used_ids:
            t["used_in_path"] = True

    # --- Nodes JSON for front-end (includes ALL thermals, not just path)
    nodes_json = {
        n.id: {"lat": n.lat, "lon": n.lon, "thermal_net_ms": n.thermal_net_ms, "ceiling_msl": n.ceiling_msl}
        for n in nodes_list
    }

    # --- Build per-node pins with altitudes and labels
    pins: Dict[str, Dict[str, Any]] = {n.id: {"lat": n.lat, "lon": n.lon} for n in nodes_list}
    pins["START"]["start_h_msl"] = round(start["h_msl"])
    for s in plan_obj.steps:
        pins[s.from_id]["depart_h_msl"] = round(s.depart_h_msl)
        if s.climbed_m and s.climbed_m > 0:
            pins[s.from_id]["climb_m"] = round(s.climbed_m)
            pins[s.from_id]["climb_time_s"] = s.climb_time_s
        pins[s.to_id]["arrive_h_msl"] = round(s.arrive_h_msl)
    pins["GOAL"]["arrive_h_msl"] = round(plan_obj.final_arrival_h_msl)

    def make_label(nid: str, p: Dict[str, Any]) -> str:
        parts = []
        if "start_h_msl" in p:  parts.append(f"start {p['start_h_msl']} m")
        if "arrive_h_msl" in p: parts.append(f"arrive {p['arrive_h_msl']} m")
        if "climb_m" in p:      parts.append(f"climb {p['climb_m']} m")
        if "depart_h_msl" in p: parts.append(f"depart {p['depart_h_msl']} m")
        return f"{nid}\n" + " · ".join(parts) if parts else nid

    pin_labels: Dict[str, str] = {nid: make_label(nid, pdata) for nid, pdata in pins.items()}

    # --- Final plan payload
    plan = {
        "path": plan_obj.path,
        "total_time_s": plan_obj.total_time_s,
        "final_arrival_h_msl": plan_obj.final_arrival_h_msl,
        "steps": [s.__dict__ for s in plan_obj.steps],
        "nodes": nodes_json,         # ALL nodes incl. T1..Tk, not just path
        "edges": edges,
        "thermals": thermals_export, # ALL corridor thermals with used_in_path flag
        "pins": pins,
        "pin_labels": pin_labels,
        "params": {
            "mc": args.mc,
            "wind_ms": args.wind,
            "wind_from_deg": args.wdir,
            "w_air_ms": args.wair,
            "per_leg_floor_msl": args.per_leg_floor,
            "day": args.day or "today_utc",
            "corridor_km": args.corridor_km,
            "min_net": args.min_net,
            "max_nodes": args.max_nodes,
            "chain_thermals": args.chain_thermals,
        }
    }

    # Console preview
    print("\n=== Pin labels ===")
    for nid in plan["path"]:
        print(pin_labels.get(nid, nid))

    with open(args.outfile, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    print(f"Wrote {args.outfile} with path: {' -> '.join(plan_obj.path)}")

    # Human-readable step breakdown
    print("\n=== Route Steps ===")
    for s in plan_obj.steps:
        print(f"  {s.from_id} -> {s.to_id}: "
              f"climb={s.climbed_m:.0f} m in {s.climb_time_s/60:.1f} min, "
              f"cruise={s.cruise_time_s/60:.1f} min, "
              f"depart={s.depart_h_msl:.0f} m -> arrive={s.arrive_h_msl:.0f} m")

if __name__ == "__main__":
    sys.exit(main())
