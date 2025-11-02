// import React, { useEffect, useMemo, useState } from "react";
// import { MapContainer, TileLayer, Marker, Popup, Polyline, Tooltip } from "react-leaflet";
// import L from "leaflet";
// import "leaflet/dist/leaflet.css";


// /** ====== CONFIG ====== **/
// const PLAN_URL = (import.meta && import.meta.env && import.meta.env.BASE_URL
//   ? import.meta.env.BASE_URL
//   : "/") + "plan.json";

// const DEFAULT_CENTER = [45.0, 5.3];
// const DEFAULT_ZOOM = 8;

// /** Simple marker icons */
// const dotIcon = (hex = "#1d4ed8") =>
//   L.divIcon({
//     className: "glide-dot",
//     html: `<div style="
//       width:12px;height:12px;border-radius:50%;
//       background:${hex};border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);
//     "></div>`,
//     iconSize: [16, 16],
//     iconAnchor: [8, 8],
//   });

// const startIcon = dotIcon("#16a34a");   // green
// const goalIcon  = dotIcon("#ef4444");   // red
// const thermalIcon = dotIcon("#f59e0b"); // amber

// /** Format helpers */
// const mToKm = (m) => (m / 1000).toFixed(1);
// const sToMin = (s) => (s / 60).toFixed(1);
// const round0 = (x) => Math.round(x || 0);

// /** Choose polyline color per leg (climb needed?) */
// const legColor = (climbed_m) => (climbed_m > 0 ? "#f59e0b" : "#2563eb");

// export default function RouteMap() {
//   const [plan, setPlan] = useState(null);
//   const [error, setError] = useState("");

//   useEffect(() => {
//     let cancelled = false;

//     const url = `${PLAN_URL}?_=${Date.now()}`; // cache-bust for dev
//     fetch(url)
//       .then((r) => {
//         if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
//         return r.json();
//       })
//       .then((data) => {
//         if (cancelled) return;
//         // Validate minimally
//         if (!data || !data.nodes || !data.path || !data.steps) {
//           throw new Error("plan.json missing required fields (nodes/path/steps)");
//         }
//         setPlan(data);
//       })
//       .catch((e) => {
//         if (!cancelled) {
//           console.error("Failed to load plan.json:", e);
//           setError(String(e));
//         }
//       });

//     return () => { cancelled = true; };
//   }, []);

//   const bounds = useMemo(() => {
//     if (!plan?.nodes) return null;
//     const latlngs = Object.values(plan.nodes)
//       .filter((n) => Number.isFinite(n.lat) && Number.isFinite(n.lon))
//       .map((n) => [n.lat, n.lon]);
//     return latlngs.length ? L.latLngBounds(latlngs) : null;
//   }, [plan]);

//   return (
//     <div style={{ width: "100%", height: "100vh" }}>
//       <MapContainer
//         center={DEFAULT_CENTER}
//         zoom={DEFAULT_ZOOM}
//         style={{ width: "100%", height: "100%" }}
//         bounds={bounds || undefined}
//         scrollWheelZoom
//       >
//         <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

//         {plan && plan.nodes && plan.steps ? (
//           <>
//             {/* Draw leg polylines */}
//             {plan.steps.map((s, idx) => {
//               const a = plan.nodes[s.from_id];
//               const b = plan.nodes[s.to_id];
//               if (!a || !b) return null;
//               const latlngs = [[a.lat, a.lon], [b.lat, b.lon]];
//               const color = legColor(s.climbed_m || 0);

//               return (
//                 <Polyline key={`${s.from_id}-${s.to_id}-${idx}`} positions={latlngs} color={color} weight={4}>
//                   <Tooltip direction="center" sticky>
//                     {s.from_id} → {s.to_id}
//                   </Tooltip>
//                   <Popup>
//                     <LegPopup nodes={plan.nodes} step={s} />
//                   </Popup>
//                 </Polyline>
//               );
//             })}

//             {/* Node markers (path order) */}
//             {plan.path.map((nid, i) => {
//               const n = plan.nodes[nid];
//               if (!n) return null;
//               const isStart = i === 0;
//               const isGoal = i === plan.path.length - 1;
//               const icon = isStart ? startIcon : isGoal ? goalIcon : thermalIcon;
//               return (
//                 <Marker key={nid} position={[n.lat, n.lon]} icon={icon}>
//                   <Popup>
//                     <NodePopup id={nid} node={n} isStart={isStart} isGoal={isGoal} />
//                   </Popup>
//                   <Tooltip direction="top" offset={[0, -12]} opacity={0.9} permanent>
//                     {nid}
//                   </Tooltip>
//                 </Marker>
//               );
//             })}
//           </>
//         ) : null}
//       </MapContainer>

//       {/* Footer summary or errors */}
//       <div style={{
//         position: "absolute", left: 12, bottom: 12,
//         background: "rgba(255,255,255,0.95)", borderRadius: 8,
//         boxShadow: "0 2px 10px rgba(0,0,0,0.15)", padding: "8px 12px",
//         fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Arial"
//       }}>
//         <div style={{ fontWeight: 600, marginBottom: 4 }}>Thermal-aided Route</div>
//         {error ? (
//           <div style={{ color: "#b91c1c", maxWidth: 420 }}>
//             Failed to load <code>plan.json</code>: {error}<br/>
//             Ensure it’s placed in <code>public/plan.json</code> and open <a href="/plan.json" target="_blank" rel="noreferrer">/plan.json</a> directly.
//           </div>
//         ) : plan ? (
//           <>
//             <div>Path: {plan.path.join(" → ")}</div>
//             <div>Total time: {sToMin(plan.total_time_s)} min</div>
//             <div>Final arrival height: {round0(plan.final_arrival_h_msl)} m MSL</div>
//             <Legend />
//           </>
//         ) : (
//           <div>Loading route…</div>
//         )}
//       </div>
//     </div>
//   );
// }

// /** Popups */
// function NodePopup({ id, node, isStart, isGoal }) {
//   return (
//     <div style={{ minWidth: 220 }}>
//       <div style={{ fontWeight: 700, marginBottom: 6 }}>
//         {isStart ? "START" : isGoal ? "GOAL" : "Thermal"} — {id}
//       </div>
//       <div>Lat/Lon: {(+node.lat).toFixed(5)}, {(+node.lon).toFixed(5)}</div>
//       {node.thermal_net_ms > 0 ? (
//         <>
//           <div>Thermal net: {node.thermal_net_ms.toFixed(1)} m/s</div>
//           <div>Ceiling: {node.ceiling_msl ? `${round0(node.ceiling_msl)} m MSL` : "—"}</div>
//         </>
//       ) : (
//         <div>No thermal lift</div>
//       )}
//     </div>
//   );
// }

// function LegPopup({ nodes, step }) {
//   const a = nodes[step.from_id], b = nodes[step.to_id];
//   if (!a || !b) return <div>Missing node(s)</div>;
//   const distM = L.latLng(a.lat, a.lon).distanceTo(L.latLng(b.lat, b.lon));

//   return (
//     <div style={{ minWidth: 260 }}>
//       <div style={{ fontWeight: 700, marginBottom: 6 }}>
//         {step.from_id} → {step.to_id}
//       </div>
//       <div>Distance: {mToKm(distM)} km</div>
//       <div>Depart: {round0(step.depart_h_msl)} m → Arrive: {round0(step.arrive_h_msl)} m</div>
//       <div>Climb: {round0(step.climbed_m)} m ({sToMin(step.climb_time_s)} min)</div>
//       <div>Cruise time: {sToMin(step.cruise_time_s)} min</div>
//       <div>Total leg time: {sToMin((step.climb_time_s||0) + (step.cruise_time_s||0))} min</div>
//     </div>
//   );
// }

// /** Legend */
// function Legend() {
//   const swatch = (hex) => (
//     <span style={{
//       display: "inline-block", width: 14, height: 4, borderRadius: 2,
//       background: hex, marginRight: 6, verticalAlign: "middle"
//     }}/>
//   );
//   return (
//     <div style={{ marginTop: 6, fontSize: 12, color: "#374151" }}>
//       <div style={{ marginBottom: 2 }}>
//         {swatch("#2563eb")} Cruise leg
//       </div>
//       <div style={{ marginBottom: 2 }}>
//         {swatch("#f59e0b")} Leg after top-up climb
//       </div>
//       <div>
//         <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#16a34a", border: "2px solid #fff", boxShadow: "0 0 0 1px rgba(0,0,0,0.25)", marginRight: 6 }} />
//         START&nbsp;&nbsp;
//         <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#f59e0b", border: "2px solid #fff", boxShadow: "0 0 0 1px rgba(0,0,0,0.25)", marginRight: 6 }} />
//         THERMAL&nbsp;&nbsp;
//         <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#ef4444", border: "2px solid #fff", boxShadow: "0 0 0 1px rgba(0,0,0,0.25)", marginRight: 6 }} />
//         GOAL
//       </div>
//     </div>
//   );
// }
import { MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export default function RouteMap() {
  return (
    <div style={{ width: "100%", height: "100vh" }}>
      <MapContainer
        center={[45.0, 5.3]}
        zoom={8}
        style={{ width: "100%", height: "100%" }}
        scrollWheelZoom
      >
        <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      </MapContainer>
    </div>
  );
}

