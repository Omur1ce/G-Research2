// src/App.jsx
import React, { useMemo, useState, useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, Tooltip } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Small helpers
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
  // Plan state (loaded from file initially so the map isn't empty)
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(false);
  const [log, setLog] = useState("");

  // Simple form state
  const [form, setForm] = useState({
    day: "2025-07-15",
    startLat: 46.0950,
    startLon: 11.3000,
    startH:   1600,
    goalLat:  46.1300,
    goalLon:  11.5200,
    goalH:    1200,
    corridor_km: 20,
    min_net: 1.0,
    max_nodes: 20,
    per_leg_floor: 1200,
    mc: 0.0,
    wind: 0.0,
    wdir: 0.0,
    wair: -0.3,
    chain_thermals: false,
  });

  // Load existing plan.json once on mount (optional)
  useEffect(() => {
    fetch("/src/data/plan.json")
      .then(r => r.ok ? r.json() : null)
      .then(j => j && setPlan(j))
      .catch(() => {});
  }, []);

  const nodes = plan?.nodes || {};
  const path  = plan?.path  || [];
  const steps = plan?.steps || [];
  const thermals = plan?.thermals || [];

  const bounds = useMemo(() => {
    const latlngs = Object.values(nodes)
      .filter(n => Number.isFinite(n.lat) && Number.isFinite(n.lon))
      .map(n => [n.lat, n.lon]);
    return latlngs.length ? L.latLngBounds(latlngs) : null;
  }, [nodes]);

  const runPlanner = async (e) => {
    e?.preventDefault?.();
    setLoading(true);
    setLog("");
    try {
      const payload = {
        day: form.day || undefined,
        start: [Number(form.startLat), Number(form.startLon), Number(form.startH)],
        goal:  [Number(form.goalLat),  Number(form.goalLon),  Number(form.goalH)],
        corridor_km: Number(form.corridor_km),
        min_net: Number(form.min_net),
        max_nodes: Number(form.max_nodes),
        per_leg_floor: Number(form.per_leg_floor),
        mc: Number(form.mc),
        wind: Number(form.wind),
        wdir: Number(form.wdir),
        wair: Number(form.wair),
        chain_thermals: !!form.chain_thermals,
        outfile: "src/data/plan.json",
      };

      const resp = await fetch("/api/run", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });

      // Try JSON first; if it fails, read text for diagnostics
      let data;
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("application/json")) {
        data = await resp.json();
      } else {
        const text = await resp.text();
        throw new Error(`Non-JSON response (status ${resp.status}):\n${text}`);
      }

      if (!resp.ok || !data?.ok) {
        const msg = data?.error || "Planner error";
        setLog((data?.stderr || data?.stdout) ? `${msg}\n\nSTDERR:\n${data.stderr}\n\nSTDOUT:\n${data.stdout}` : msg);
        throw new Error(msg);
      }

      setPlan(data.plan);
      setLog(data.log || "");
    } catch (err) {
      console.error(err);
      setLog(String(err?.message || err));
      alert("Run failed: " + (err?.message || "unknown error"));
    } finally {
      setLoading(false);
    }
  };


  return (
    <div style={{ position: "fixed", inset: 0 }}>
      {/* Left-side controls */}
      <div style={{
        position:"absolute", left:12, top:12, zIndex:1000,
        background:"rgba(255,255,255,0.98)", borderRadius:10, padding:12,
        width:340, boxShadow:"0 2px 12px rgba(0,0,0,0.2)", fontFamily:"system-ui, Segoe UI, Roboto, Arial"
      }}>
        <div style={{ fontWeight:700, fontSize:16, marginBottom:8 }}>Route Inputs</div>
        <form onSubmit={runPlanner} style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:8 }}>
          <label style={{ gridColumn:"span 2" }}>
            Day (UTC)
            <input value={form.day} onChange={e=>setForm({...form, day:e.target.value})}
              style={inpStyle} placeholder="YYYY-MM-DD" />
          </label>

          <fieldset style={{ gridColumn:"span 2", border:"1px solid #e5e7eb", borderRadius:8, padding:8 }}>
            <legend style={{ padding:"0 6px" }}>Start</legend>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:8 }}>
              <label>Lat<input type="number" step="0.00001" value={form.startLat} onChange={e=>setForm({...form, startLat:e.target.value})} style={inpStyle}/></label>
              <label>Lon<input type="number" step="0.00001" value={form.startLon} onChange={e=>setForm({...form, startLon:e.target.value})} style={inpStyle}/></label>
              <label>H (m)<input type="number" step="1" value={form.startH} onChange={e=>setForm({...form, startH:e.target.value})} style={inpStyle}/></label>
            </div>
          </fieldset>

          <fieldset style={{ gridColumn:"span 2", border:"1px solid #e5e7eb", borderRadius:8, padding:8 }}>
            <legend style={{ padding:"0 6px" }}>Goal</legend>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:8 }}>
              <label>Lat<input type="number" step="0.00001" value={form.goalLat} onChange={e=>setForm({...form, goalLat:e.target.value})} style={inpStyle}/></label>
              <label>Lon<input type="number" step="0.00001" value={form.goalLon} onChange={e=>setForm({...form, goalLon:e.target.value})} style={inpStyle}/></label>
              <label>Req H (m)<input type="number" step="1" value={form.goalH} onChange={e=>setForm({...form, goalH:e.target.value})} style={inpStyle}/></label>
            </div>
          </fieldset>

          <label>Corridor km<input type="number" step="1" value={form.corridor_km} onChange={e=>setForm({...form, corridor_km:e.target.value})} style={inpStyle}/></label>
          <label>Min net<input type="number" step="0.1" value={form.min_net} onChange={e=>setForm({...form, min_net:e.target.value})} style={inpStyle}/></label>
          <label>Max nodes<input type="number" step="1" value={form.max_nodes} onChange={e=>setForm({...form, max_nodes:e.target.value})} style={inpStyle}/></label>
          <label>Per-leg floor<input type="number" step="1" value={form.per_leg_floor} onChange={e=>setForm({...form, per_leg_floor:e.target.value})} style={inpStyle}/></label>

          <label>MC<input type="number" step="0.1" value={form.mc} onChange={e=>setForm({...form, mc:e.target.value})} style={inpStyle}/></label>
          <label>Wind ms<input type="number" step="0.1" value={form.wind} onChange={e=>setForm({...form, wind:e.target.value})} style={inpStyle}/></label>
          <label>Wind from°<input type="number" step="1" value={form.wdir} onChange={e=>setForm({...form, wdir:e.target.value})} style={inpStyle}/></label>
          <label>w_air ms<input type="number" step="0.1" value={form.wair} onChange={e=>setForm({...form, wair:e.target.value})} style={inpStyle}/></label>

          <label style={{ gridColumn:"span 2" }}>
            <input type="checkbox" checked={form.chain_thermals} onChange={e=>setForm({...form, chain_thermals:e.target.checked})}/>
            {" "}Allow chaining thermals
          </label>

          <div style={{ gridColumn:"span 2", display:"flex", gap:8 }}>
            <button type="submit" disabled={loading} style={btnStyle}>{loading ? "Running..." : "Run planner"}</button>
            <button type="button" disabled={loading} style={btnGhost} onClick={()=>setForm(f=>({...f, startLat:46.0950, startLon:11.3000, startH:1600, goalLat:46.1300, goalLon:11.5200, goalH:1200}))}>Example coords</button>
          </div>

          {!!log && (
            <pre style={{ gridColumn:"span 2", margin:0, padding:8, background:"#0b1020", color:"#d1d5db", borderRadius:6, maxHeight:160, overflow:"auto" }}>
{log}
            </pre>
          )}
        </form>
      </div>

      {/* Map */}
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

        {/* Path nodes */}
        {path.map((nid, i) => {
          const n = nodes[nid];
          if (!n) return null;
          const isStart = i === 0;
          const isGoal  = i === path.length - 1;
          const icon    = isStart ? startIcon : (isGoal ? goalIcon : thermalIcon);
          return (
            <Marker key={`path-${nid}`} position={[n.lat, n.lon]} icon={icon}>
              <Popup><NodePopup id={nid} node={n} isStart={isStart} isGoal={isGoal} /></Popup>
              <Tooltip direction="top" offset={[0,-12]} opacity={0.9} permanent>{nid}</Tooltip>
            </Marker>
          );
        })}

        {/* All corridor thermals */}
        {thermals.map(t => {
          const color = t.used_in_path ? "#f59e0b" : "#facc15";
          const icon = dotIcon(color);
          return (
            <Marker key={`th-${t.id}`} position={[t.lat, t.lon]} icon={icon}>
              <Popup>
                <div style={{ minWidth:220 }}>
                  <div style={{ fontWeight:700, marginBottom:6 }}>
                    Thermal — {t.id}
                  </div>
                  <div>Lat/Lon: {(+t.lat).toFixed(5)}, {(+t.lon).toFixed(5)}</div>
                  <div>Net: {t.net_ms.toFixed(2)} m/s</div>
                  <div>Ceiling: {t.ceiling_msl ? `${round0(t.ceiling_msl)} m MSL` : "—"}</div>
                  {t.used_in_path && <div style={{ color:"#b45309", fontWeight:600 }}>Used in route</div>}
                </div>
              </Popup>
              <Tooltip direction="top" offset={[0,-12]} opacity={0.9} permanent>{t.id}</Tooltip>
            </Marker>
          );
        })}
      </MapContainer>

      {/* Footer summary */}
      <div style={{
        position:"absolute", left:370, bottom:12, background:"rgba(255,255,255,0.95)",
        borderRadius:8, boxShadow:"0 2px 10px rgba(0,0,0,0.15)", padding:"8px 12px",
        fontFamily:"system-ui, -apple-system, Segoe UI, Roboto, Arial"
      }}>
        <div style={{ fontWeight:600, marginBottom:4 }}>Thermal-aided Route</div>
        <div>Path: {path.join(" → ")}</div>
        {"total_time_s" in (plan || {}) && (
          <div>Total time: {sToMin(plan.total_time_s)} min</div>
        )}
        {"final_arrival_h_msl" in (plan || {}) && (
          <div>Final arrival height: {round0(plan.final_arrival_h_msl)} m MSL</div>
        )}
      </div>
    </div>
  );
}

const inpStyle = {
  width:"100%", padding:"6px 8px", border:"1px solid #d1d5db", borderRadius:6,
  fontFamily:"inherit", fontSize:14
};
const btnStyle = {
  background:"#1d4ed8", color:"#fff", border:"none", borderRadius:6, padding:"8px 12px",
  fontWeight:600, cursor:"pointer"
};
const btnGhost = {
  background:"#f3f4f6", color:"#111827", border:"1px solid #e5e7eb", borderRadius:6, padding:"8px 12px",
  fontWeight:600, cursor:"pointer"
};

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
