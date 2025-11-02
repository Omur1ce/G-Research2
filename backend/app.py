# app.py
from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse

from grid_service import get_cached_grid
from thermals import grid_to_thermals  # expects a grid with thermal_score / climb_ms etc.

app = FastAPI(title="Soaring Grid API", version="0.2.0")

# CORS (relax now, restrict in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/grid")
def grid_geojson():
    """
    Return the scored grid as GeoJSON FeatureCollection of polygons.
    """
    gdf = get_cached_grid()
    # Ensure clean JSON (GeoPandas outputs dict-like JSON string)
    return JSONResponse(content=json.loads(gdf.to_json()))


@app.get("/thermals")
def thermals_geojson(
    min_score: float = Query(0.55, ge=0.0, le=1.0, description="Minimum thermal_score to keep"),
    top_k: Optional[int] = Query(250, ge=1, description="Keep at most K strongest thermals"),
    min_radius_m: int = Query(300, ge=50, le=5000, description="Minimum blob radius in meters"),
    max_radius_m: int = Query(1500, ge=100, le=10000, description="Maximum blob radius in meters"),
):
    """
    Convert the grid into thermal 'blobs' (points with radius & intensity).
    Output: GeoJSON FeatureCollection of Points with properties:
      - score        (0..1)
      - climb_ms     (estimated climb rate)
      - radius_m     (visual radius you can draw on a map)
      - cell_id, lat, lon (for reference)
    """
    grid = get_cached_grid()

    # grid_to_thermals handles sorting / dedup if implemented that way;
    # we additionally filter by score & cap top-k here for safety.
    thermals_gdf = grid_to_thermals(
        grid,
        min_score=min_score,
        min_radius_m=min_radius_m,
        max_radius_m=max_radius_m,
    )

    if top_k is not None and len(thermals_gdf) > top_k:
        thermals_gdf = thermals_gdf.sort_values(
            ["score", "climb_ms"], ascending=[False, False]
        ).head(top_k)

    return JSONResponse(content=json.loads(thermals_gdf.to_json()))


@app.get("/map", response_class=HTMLResponse)
def map_page():
    """
    Tiny client to visualize the grid and a smoother thermal layer.
    """
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Soaring Grid & Thermals</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    html,body,#map{height:100%;margin:0}
    .toolbar{
      position:absolute;z-index:9999;top:.5rem;left:.5rem;background:#fff;
      padding:.5rem .6rem;border-radius:.6rem;box-shadow:0 2px 10px rgba(0,0,0,.15);
      font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif
    }
    .toolbar label{font-size:.85rem;margin-right:.25rem}
    .toolbar .row{margin:.25rem 0}
    .chip{display:inline-block;padding:.15rem .5rem;border-radius:999px;background:#f5f5f5;margin-left:.25rem}
  </style>
</head>
<body>
<div id="map"></div>

<div class="toolbar">
  <div class="row">
    <label>Color by</label>
    <select id="propSelect">
      <option value="climb_ms">climb_ms</option>
      <option value="tpi">tpi</option>
      <option value="t_2m_C">t_2m_C</option>
      <option value="cape_Jkg">cape_Jkg</option>
      <option value="thermal_score">thermal_score</option>
    </select>
    <button id="fit">Fit</button>
    <span id="range" class="chip"></span>
  </div>
  <div class="row">
    <label><input type="checkbox" id="showThermals" checked/> Thermals</label>
    <label class="chip">
      min_score <input id="minScore" type="number" step="0.01" min="0" max="1" value="0.55" style="width:4rem">
    </label>
    <label class="chip">
      top_k <input id="topK" type="number" step="10" min="10" max="2000" value="250" style="width:5rem">
    </label>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map = L.map('map').setView([45.98, 11.10], 11);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '&copy; OSM'}).addTo(map);

let gridFC = null;
let gridLayer = null;
let thermalsFC = null;
let thermalsLayer = null;

function getColorScale(v, min, max){
  if (!isFinite(v)) return '#ccc';
  const t = Math.max(0, Math.min(1, (v - min) / (max - min || 1)));
  // 3-stop ramp: #f7fbff -> #6baed6 -> #08519c
  function lerp(a,b,t){return a + (b-a)*t}
  function hex(r,g,b){return '#' + [r,g,b].map(x=>x.toString(16).padStart(2,'0')).join('')}
  let r,g,b;
  if (t < .5){ const u=t*2; r=lerp(247,107,u); g=lerp(251,174,u); b=lerp(255,214,u); }
  else { const u=(t-.5)*2; r=lerp(107,8,u); g=lerp(174,81,u); b=lerp(214,156,u); }
  return hex(Math.round(r),Math.round(g),Math.round(b));
}

function drawGrid(prop){
  if (!gridFC) return;

  const vals = gridFC.features
    .map(f => f.properties?.[prop])
    .filter(v => typeof v === 'number' && isFinite(v));
  const min = Math.min(...vals), max = Math.max(...vals);
  document.getElementById('range').textContent = `${prop} [${min.toFixed(3)}…${max.toFixed(3)}]`;

  if (gridLayer) gridLayer.remove();

  gridLayer = L.geoJSON(gridFC, {
    style: f => {
      const v = f.properties?.[prop];
      return {
        color: '#333', weight: 0.35,
        fillOpacity: 0.55,
        fillColor: getColorScale((typeof v === 'number') ? v : NaN, min, max)
      };
    },
    onEachFeature: (f, l) => {
      const p = f.properties || {};
      const html = Object.entries(p).map(([k,v])=>`<b>${k}</b>: ${v}`).join('<br>');
      l.bindPopup(html);
    }
  }).addTo(map);
}

function drawThermals(){
  const show = document.getElementById('showThermals').checked;
  if (thermalsLayer) { thermalsLayer.remove(); thermalsLayer = null; }
  if (!show || !thermalsFC) return;

  // Smooth, non-square visuals: L.circle with meter radius
  thermalsLayer = L.layerGroup().addTo(map);
  for (const f of thermalsFC.features){
    const p = f.properties || {};
    const [lon, lat] = f.geometry.coordinates;
    const score = p.score ?? 0;
    const climb = p.climb_ms ?? 0;
    const radius = Math.max(100, Math.min(3000, p.radius_m ?? 800)); // clamp for sanity

    // opacity scaled by score; border darker for stronger
    const fillOpacity = 0.10 + 0.35 * Math.max(0, Math.min(1, score));
    const color = score > 0.8 ? '#0a5' : (score > 0.65 ? '#2aa' : '#36a');

    const c = L.circle([lat, lon], {
      radius: radius,
      color: color,
      weight: 1,
      fillColor: color,
      fillOpacity: fillOpacity
    }).addTo(thermalsLayer);

    const popup = Object.entries(p).map(([k,v])=>`<b>${k}</b>: ${v}`).join('<br>');
    c.bindPopup(popup);
  }
}

async function refreshThermals(){
  const q = new URLSearchParams({
    min_score: document.getElementById('minScore').value,
    top_k: document.getElementById('topK').value,
  }).toString();
  thermalsFC = await fetch('/thermals?' + q).then(r=>r.json());
  drawThermals();
}

async function init(){
  gridFC = await fetch('/grid').then(r=>r.json());
  drawGrid(document.getElementById('propSelect').value);

  await refreshThermals();

  document.getElementById('fit').onclick = () => {
    if (gridLayer) map.fitBounds(gridLayer.getBounds());
  };
  document.getElementById('propSelect').onchange = e => drawGrid(e.target.value);
  document.getElementById('showThermals').onchange = drawThermals;
  document.getElementById('minScore').onchange = refreshThermals;
  document.getElementById('topK').onchange = refreshThermals;
}
init();
</script>
</body>
</html>
"""
