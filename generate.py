#!/usr/bin/env python3
"""
glide_route_ascii_astar.py — final-glide with thermal-aided routing (A*-style)

What this adds (on top of glide_route_ascii.py)
-----------------------------------------------
• A Node graph: each node is a waypoint with (lat, lon), optional thermal "net climb" (m/s) and ceiling (MSL).
• A route finder that moves from Start -> Goal along edges, using physics-based feasibility per edge.
• If a node has a thermal and you are too low to make the next edge safely, the solver "tops up"
  by circling at that node with net climb (up to the node's ceiling) to the minimum required height.
• Edge feasibility uses the same dh/ds integration with wind and airmass vertical motion (w_air).
• Cost minimized = total TIME (climb time + cruise time).

Run
---
python glide_route_ascii_astar.py
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict
import heapq
import json

# ---------------------------
# Constants & simple util
# ---------------------------

EARTH_RADIUS_M = 6371000.0
RHO0 = 1.225  # kg/m^3 at sea level, ISA

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return EARTH_RADIUS_M * c

def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dlam)
    th = math.degrees(math.atan2(y, x))
    return (th + 360.0) % 360.0

def destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> Tuple[float, float]:
    delta = distance_m / EARTH_RADIUS_M
    th = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)

    sinp1, cosp1 = math.sin(phi1), math.cos(phi1)
    sind, cosd = math.sin(delta), math.cos(delta)

    sinp2 = sinp1 * cosd + cosp1 * sind * math.cos(th)
    phi2 = math.asin(sinp2)
    y = math.sin(th) * sind * cosp1
    x = cosd - sinp1 * sinp2
    lam2 = lam1 + math.atan2(y, x)

    return (math.degrees(phi2), (math.degrees(lam2) + 540) % 360 - 180)

def isa_density_approx(h_m: float) -> float:
    T0 = 288.15
    L = 0.0065
    g0 = 9.80665
    R = 287.05
    T = max(180.0, T0 - L*h_m)
    p0 = 101325.0
    p = p0 * (T/T0) ** (g0/(L*R))
    rho = p / (R * T)
    return rho

# ---------------------------
# Glider polar & met
# ---------------------------

@dataclass
class Polar:
    a: float
    b: float
    c: float
    bug_factor: float = 1.0
    def sink_ms(self, V_ias: float) -> float:
        return self.bug_factor * (self.a + self.b * V_ias + self.c * V_ias**2)
    def maccready_speed_ias(self, M: float) -> float:
        A, B, C = self.c, self.b, (self.a + M)
        disc = B*B - 4*A*C
        if A <= 0 or disc <= 0:
            return max(25.0, B / (2*A)) if A > 0 else 35.0
        return max(10.0, (-B + math.sqrt(disc)) / (2*A))

@dataclass
class MetSample:
    wind_speed_ms: float
    wind_dir_from_deg: float
    w_air_ms: float
    rho: Optional[float] = None

class MetProvider:
    def __init__(self, wind_speed_ms: float = 8.0, wind_dir_from_deg: float = 260.0, w_air_ms: float = 0.0):
        self.ws = wind_speed_ms
        self.wdir = wind_dir_from_deg
        self.wair = w_air_ms
    def sample(self, lat: float, lon: float, h_msl: float) -> MetSample:
        return MetSample(self.ws, self.wdir, self.wair, rho=isa_density_approx(h_msl))

# ---------------------------
# Edge physics
# ---------------------------

def wind_along_track_ms(wind_speed_ms: float, wind_dir_from_deg: float, track_deg: float) -> float:
    blow_to_deg = (wind_dir_from_deg + 180.0) % 360.0
    delta = math.radians((blow_to_deg - track_deg + 540.0) % 360.0 - 180.0)
    return wind_speed_ms * math.cos(delta)

@dataclass
class LegSimResult:
    required_start_h_msl: float
    expected_arrival_h_msl: float
    travel_time_s: float
    distance_m: float

def simulate_leg_and_requirements(
    start_lat: float, start_lon: float, start_h_msl: float,
    end_lat: float, end_lon: float, arrival_floor_msl: float,
    polar: Polar, met: MetProvider,
    mc_value_ms: float = 0.0, step_m: float = 1000.0
) -> LegSimResult:
    """Integrate along edge; compute expected arrival height if starting at start_h_msl,
       the required start height to arrive at arrival_floor_msl, and cruise time.
    """
    total_D = haversine_m(start_lat, start_lon, end_lat, end_lon)
    track = initial_bearing_deg(start_lat, start_lon, end_lat, end_lon)
    n_steps = max(1, int(math.ceil(total_D / step_m)))
    ds = total_D / n_steps

    V_ias = polar.maccready_speed_ias(mc_value_ms)
    h = start_h_msl
    lat, lon = start_lat, start_lon
    t_s = 0.0

    for _ in range(n_steps):
        ms = met.sample(lat, lon, h)
        rho = ms.rho if ms.rho is not None else isa_density_approx(h)
        V_tas = V_ias * math.sqrt(RHO0 / max(0.3, rho))
        Vg_parallel = max(0.1, V_tas + wind_along_track_ms(ms.wind_speed_ms, ms.wind_dir_from_deg, track))

        sink = polar.sink_ms(V_ias)
        dh_dt = ms.w_air_ms - sink                  # m/s
        dt = ds / Vg_parallel                       # s
        dh = dh_dt * dt                             # m

        h += dh
        t_s += dt
        lat, lon = destination_point(lat, lon, track, ds)

    expected_arrival = h
    required_start = start_h_msl + (arrival_floor_msl - expected_arrival)
    return LegSimResult(required_start_h_msl=required_start,
                        expected_arrival_h_msl=expected_arrival,
                        travel_time_s=t_s,
                        distance_m=total_D)

# ---------------------------
# Graph model (nodes with thermals)
# ---------------------------

@dataclass
class Node:
    id: str
    lat: float
    lon: float
    thermal_net_ms: float = 0.0    # net climb achievable while circling (+up). If 0, no usable thermal.
    ceiling_msl: Optional[float] = None  # maximum MSL attainable here (e.g., cloudbase). None = no limit.

@dataclass
class StepLog:
    from_id: str
    to_id: str
    climbed_m: float
    climb_time_s: float
    cruise_time_s: float
    depart_h_msl: float
    arrive_h_msl: float

@dataclass
class RoutePlan:
    path: List[str]
    total_time_s: float
    final_arrival_h_msl: float
    steps: List[StepLog]

def find_route_with_thermals(
    nodes: Dict[str, Node],
    edges: Dict[str, List[str]],
    start_id: str,
    goal_id: str,
    start_h_msl: float,
    arrival_floor_each_leg_msl: float,
    polar: Polar,
    met: MetProvider,
    mc_value_ms: float = 0.0,
    step_m: float = 1000.0
) -> RoutePlan:
    """
    Dijkstra/A* (zero heuristic) over (node, altitude) state, minimizing total TIME.
    At each node, if altitude is insufficient to traverse an outgoing edge safely,
    the solver will climb the minimum amount at that node (if thermal available)
    up to ceiling, then evaluate the edge.
    """
    # Priority queue of (total_time, node_id, altitude_msl, path, steps)
    heap = []
    heapq.heappush(heap, (0.0, start_id, start_h_msl, [start_id], []))

    # Best known time to reach (node_id, discrete altitude bin) — we will coarsen altitude to 50 m bins
    seen = {}

    def alt_bin(h: float) -> int:
        return int(round(h / 50.0))

    while heap:
        t_so_far, nid, h_here, path, steps = heapq.heappop(heap)

        if nid == goal_id:
            return RoutePlan(path=path, total_time_s=t_so_far, final_arrival_h_msl=h_here, steps=steps)

        key = (nid, alt_bin(h_here))
        if key in seen and t_so_far >= seen[key]:
            continue
        seen[key] = t_so_far

        node = nodes[nid]
        for nb in edges.get(nid, []):
            nb_node = nodes[nb]

            # First: evaluate leg feasibility from current altitude WITHOUT climbing
            leg = simulate_leg_and_requirements(
                start_lat=node.lat, start_lon=node.lon, start_h_msl=h_here,
                end_lat=nb_node.lat, end_lon=nb_node.lon,
                arrival_floor_msl=arrival_floor_each_leg_msl,
                polar=polar, met=met, mc_value_ms=mc_value_ms, step_m=step_m
            )

            depart_h = h_here
            climb_m = 0.0
            climb_time = 0.0

            if h_here + 1e-6 < leg.required_start_h_msl:
                # Need to top-up at this node; only possible if it has usable thermal
                if node.thermal_net_ms <= 0.0:
                    # Cannot make this edge from here (no climb); skip
                    continue
                climb_needed = leg.required_start_h_msl - h_here

                # Ceiling cap if specified
                ceiling = node.ceiling_msl if node.ceiling_msl is not None else 1e9
                max_possible_climb = max(0.0, ceiling - h_here)
                if climb_needed > max_possible_climb + 1e-6:
                    # Even climbing to ceiling is insufficient -> edge not feasible
                    continue

                # Perform minimal climb to leg.required_start_h_msl at net climb rate
                climb_m = max(0.0, climb_needed)
                if node.thermal_net_ms <= 0.0:
                    continue  # safety
                climb_time = climb_m / node.thermal_net_ms  # seconds
                depart_h = h_here + climb_m

                # Recompute leg after the climb (start altitude changed)
                leg = simulate_leg_and_requirements(
                    start_lat=node.lat, start_lon=node.lon, start_h_msl=depart_h,
                    end_lat=nb_node.lat, end_lon=nb_node.lon,
                    arrival_floor_msl=arrival_floor_each_leg_msl,
                    polar=polar, met=met, mc_value_ms=mc_value_ms, step_m=step_m
                )

            # After feasibility, take the edge and push new state
            arrive_h = leg.expected_arrival_h_msl
            cruise_time = leg.travel_time_s
            new_time = t_so_far + climb_time + cruise_time
            new_path = path + [nb]
            new_steps = steps + [StepLog(from_id=nid, to_id=nb, climbed_m=climb_m, climb_time_s=climb_time,
                                         cruise_time_s=cruise_time, depart_h_msl=depart_h, arrive_h_msl=arrive_h)]
            heapq.heappush(heap, (new_time, nb, arrive_h, new_path, new_steps))

    # If we exhaust the heap without reaching goal
    raise RuntimeError("No feasible route to goal with given thermals and constraints.")

# ---------------------------
# Demo main()
# ---------------------------

def main():
    # Glider and met setup (same as before)
    polar = Polar(a=0.3, b=0.005, c=0.0012, bug_factor=1.1)
    met = MetProvider(wind_speed_ms=8.0, wind_dir_from_deg=260.0, w_air_ms=0.1)
    MC = 0.0

    # Safety floor for each leg (MSL). In practice you'd use terrain + margin + legal minima.
    per_leg_floor = 900.0

    # Build a tiny graph: Start -> T1 -> T2 -> Goal
    # Coordinates are illustrative around 45N, 5E.
    nodes = {
        "START": Node("START", 45.0000, 5.0000, thermal_net_ms=0.0),
        "T1":    Node("T1",    45.0700, 5.2500, thermal_net_ms=1.5, ceiling_msl=2400.0),
        "T2":    Node("T2",    45.1700, 5.5200, thermal_net_ms=2.0, ceiling_msl=2600.0),
        "GOAL":  Node("GOAL",  45.2500, 5.8000, thermal_net_ms=0.0),
    }
    edges = {
        "START": ["T1", "T2"],   # allow a long glide direct to T2 if possible
        "T1":    ["T2", "GOAL"],
        "T2":    ["GOAL"],
        "GOAL":  []
    }

    # Start altitude; goal arrival requirement (not directly used by the router which uses per-leg floor)
    start_h = 1800.0

    plan = find_route_with_thermals(
        nodes=nodes, edges=edges, start_id="START", goal_id="GOAL",
        start_h_msl=start_h, arrival_floor_each_leg_msl=per_leg_floor,
        polar=polar, met=met, mc_value_ms=MC, step_m=800.0
    )


    # Bundle nodes for the front-end
    nodes_json = { nid: dict(lat=n.lat, lon=n.lon, thermal_net_ms=n.thermal_net_ms, ceiling_msl=n.ceiling_msl)
                for nid, n in nodes.items() }

    with open("public/plan.json", "w", encoding="utf-8") as f:
        json.dump({
            "path": plan.path,
            "total_time_s": plan.total_time_s,
            "final_arrival_h_msl": plan.final_arrival_h_msl,
            "steps": [s.__dict__ for s in plan.steps],
            "nodes": nodes_json
        }, f, ensure_ascii=False, indent=2)


    # Print the plan
    print("=== Thermal-Aided Route Plan (minimizing total time) ===")
    print("Path:", " -> ".join(plan.path))
    print(f"Total time: {plan.total_time_s/60.0:.1f} min")
    print(f"Final arrival height: {plan.final_arrival_h_msl:.0f} m MSL")
    print("Steps:")
    for s in plan.steps:
        dist_km = haversine_m(nodes[s.from_id].lat, nodes[s.from_id].lon, nodes[s.to_id].lat, nodes[s.to_id].lon)/1000.0
        print(f"  {s.from_id} -> {s.to_id}: dist {dist_km:.1f} km | climb {s.climbed_m:.0f} m in {s.climb_time_s/60:.1f} min | "
              f"cruise {s.cruise_time_s/60:.1f} min | depart {s.depart_h_msl:.0f} m -> arrive {s.arrive_h_msl:.0f} m")

if __name__ == "__main__":
    main()
