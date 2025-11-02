// src/App.jsx
import React, { useMemo } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, Tooltip } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import plan from "./data/plan.json";

const dotIcon = (hex="#1d4ed8") =>
  L.divIcon({
    className: "glide-dot",
    html: `<div style="width:12px;height:12px;border-radius:50%;background:${hex};
            border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);"></div>`,
    iconSize: [16,16], iconAnchor: [8,8],
  });

const startIcon   = dotIcon("#16a34a");  // green
const goalIcon    = dotIcon("#ef4444");  // red
const thermalIcon = dotIcon("#f59e0b");  // amber
const legColor = (climbed_m) => (climbed_m > 0 ? "#f59e0b" : "#2563eb");
const round0 = (x) => Math.round(x || 0);
const mToKm = (m) => (m/1000).toFixed(1);
const sToMin = (s) => (s/60).toFixed(1);

export default function App() {
  const nodes = plan?.nodes || {};
  const path  = plan?.path  || [];
  const steps = plan?.steps || [];

  const bounds = useMemo(() => {
    const latlngs = Object.values(nodes)
      .filter(n => Number.isFinite(n.lat) && Number.isFinite(n.lon))
      .map(n => [n.lat, n.lon]);
    return latlngs.length ? L.latLngBounds(latlngs) : null;
  }, [nodes]);

  return (
    <div style={{ position: "fixed", inset: 0 }}>
      <MapContainer
        center={[45.0, 5.3]}
        zoom={8}
        bounds={bounds || undefined}
        style={{ width: "100%", height: "100%" }}
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; OpenStreetMap contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {/* Legs */}
        {steps.map((s, idx) => {
          const a = nodes[s.from_id], b = nodes[s.to_id];
          if (!a || !b) return null;
          return (
            <Polyline
              key={`${s.from_id}-${s.to_id}-${idx}`}
              positions={[[a.lat, a.lon], [b.lat, b.lon]]}
              color={legColor(s.climbed_m || 0)}
              weight={4}
            >
              <Tooltip sticky>{s.from_id} → {s.to_id}</Tooltip>
              <Popup><LegPopup nodes={nodes} step={s} /></Popup>
            </Polyline>
          );
        })}

        {/* Nodes (in path order) */}
        {path.map((nid, i) => {
          const n = nodes[nid];
          if (!n) return null;
          const isStart = i === 0;
          const isGoal  = i === path.length - 1;
          const icon    = isStart ? startIcon : (isGoal ? goalIcon : thermalIcon);
          return (
            <Marker key={nid} position={[n.lat, n.lon]} icon={icon}>
              <Popup><NodePopup id={nid} node={n} isStart={isStart} isGoal={isGoal} /></Popup>
              <Tooltip direction="top" offset={[0,-12]} opacity={0.9} permanent>{nid}</Tooltip>
            </Marker>
          );
        })}
      </MapContainer>

      {/* Footer summary */}
      <div style={{
        position:"absolute", left:12, bottom:12, background:"rgba(255,255,255,0.95)",
        borderRadius:8, boxShadow:"0 2px 10px rgba(0,0,0,0.15)", padding:"8px 12px",
        fontFamily:"system-ui, -apple-system, Segoe UI, Roboto, Arial"
      }}>
        <div style={{ fontWeight:600, marginBottom:4 }}>Thermal-aided Route</div>
        <div>Path: {path.join(" → ")}</div>
        {"total_time_s" in plan && (
          <div>Total time: {sToMin(plan.total_time_s)} min</div>
        )}
        {"final_arrival_h_msl" in plan && (
          <div>Final arrival height: {round0(plan.final_arrival_h_msl)} m MSL</div>
        )}
      </div>
    </div>
  );
}

function NodePopup({ id, node, isStart, isGoal }) {
  return (
    <div style={{ minWidth:220 }}>
      <div style={{ fontWeight:700, marginBottom:6 }}>
        {isStart ? "START" : isGoal ? "GOAL" : "Thermal"} — {id}
      </div>
      <div>Lat/Lon: {(+node.lat).toFixed(5)}, {(+node.lon).toFixed(5)}</div>
      {node.thermal_net_ms > 0 ? (
        <>
          <div>Thermal net: {node.thermal_net_ms.toFixed(1)} m/s</div>
          <div>Ceiling: {node.ceiling_msl ? `${round0(node.ceiling_msl)} m MSL` : "—"}</div>
        </>
      ) : <div>No thermal lift</div>}
    </div>
  );
}

function LegPopup({ nodes, step }) {
  const a = nodes[step.from_id], b = nodes[step.to_id];
  if (!a || !b) return <div>Missing node(s)</div>;
  const distM = L.latLng(a.lat, a.lon).distanceTo(L.latLng(b.lat, b.lon));
  return (
    <div style={{ minWidth:260 }}>
      <div style={{ fontWeight:700, marginBottom:6 }}>{step.from_id} → {step.to_id}</div>
      <div>Distance: {mToKm(distM)} km</div>
      <div>Depart: {round0(step.depart_h_msl)} m → Arrive: {round0(step.arrive_h_msl)} m</div>
      <div>Climb: {round0(step.climbed_m)} m ({sToMin(step.climb_time_s)} min)</div>
      <div>Cruise time: {sToMin(step.cruise_time_s)} min</div>
      <div>Total leg time: {sToMin((step.climb_time_s||0) + (step.cruise_time_s||0))} min</div>
    </div>
  );
}
