# get_thermal.py
from weglide_client import WeGlideClient
from datetime import datetime, timezone
import argparse, json, csv

def utc_midnight_ts(day_str: str | None) -> int:
    if day_str:
        y, m, d = map(int, day_str.split("-"))
        dt = datetime(y, m, d, 0, 0, 0, tzinfo=timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        dt = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())

def to_iso(ts) -> str | None:
    try:
        return datetime.utcfromtimestamp(float(ts)).replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None

def normalize_item(item):
    """
    Handle both array payloads and dict payloads.
    Array shape (observed):
      [ id, lon, lat, alt_base_m, alt_top_m, t_start_unix, t_end_unix ]
    """
    # Array case
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        lon = float(item[1]); lat = float(item[2])
        rec = {
            "lat": lat,
            "lon": lon,
        }
        if len(item) >= 4: rec["alt_base_m"] = item[3]
        if len(item) >= 5: rec["alt_top_m"]  = item[4]
        if len(item) >= 6: rec["t_start"]    = to_iso(item[5])
        if len(item) >= 7: rec["t_end"]      = to_iso(item[6])
        # optional id
        try: rec["id"] = int(item[0])
        except Exception: pass
        return rec

    # Dict fallback (if Weglide changes format / some days differ)
    if isinstance(item, dict):
        lat = item.get("lat") or item.get("latitude") or item.get("y")
        lon = item.get("lon") or item.get("lng") or item.get("longitude") or item.get("x")
        if lat is None or lon is None:
            return None
        return {"lat": float(lat), "lon": float(lon)}

    return None

def write_csv(rows, path="thermals.csv"):
    fields = ["lat","lon","alt_base_m","alt_top_m","t_start","t_end","id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

def write_geojson(rows, path="thermals.geojson"):
    fc = {"type": "FeatureCollection", "features": []}
    for r in rows:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None: continue
        props = {k: v for k, v in r.items() if k not in ("lat","lon")}
        fc["features"].append({
            "type":"Feature",
            "geometry":{"type":"Point","coordinates":[lon,lat]},
            "properties":props
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)

def write_leaflet(rows, path="thermals_map.html"):
    pts = []
    for r in rows:
        if r.get("lat") is None or r.get("lon") is None: continue
        label = f"alt {r.get('alt_base_m','?')}→{r.get('alt_top_m','?')} m\\n{r.get('t_start','?')}–{r.get('t_end','?')}"
        pts.append({"lat":r["lat"], "lon":r["lon"], "label":label})
    if pts:
        c = pts[len(pts)//2]
        center_lat, center_lon = c["lat"], c["lon"]
    else:
        center_lat, center_lon = 51.0, 0.0
    js = json.dumps(pts)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Thermals Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>html,body,#map{{height:100%;margin:0}}</style>
</head>
<body>
<div id="map"></div>
<script>
const map = L.map('map').setView([{center_lat}, {center_lon}], 7);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 12, attribution: '&copy; OpenStreetMap'
}}).addTo(map);
const pts = {js};
for (const p of pts) {{
  L.circleMarker([p.lat, p.lon]).addTo(map).bindPopup(p.label);
}}
</script>
</body>
</html>""".replace("{center_lat}", str(center_lat)).replace("{center_lon}", str(center_lon))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

def main():
    ap = argparse.ArgumentParser(description="Fetch WeGlide thermals and export CSV/GeoJSON/HTML map")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args()

    ts = utc_midnight_ts(args.day)
    wg = WeGlideClient()
    thermals = wg.get_thermals(time_unix=ts)
    if not isinstance(thermals, list):
        thermals = [thermals]

    rows = []
    for item in thermals:
        rec = normalize_item(item)
        if rec: rows.append(rec)

    write_csv(rows)
    write_geojson(rows)
    write_leaflet(rows)

    print(f"Wrote {len(rows)} thermal points → thermals.csv, thermals.geojson, thermals_map.html")
    print("RAW SAMPLE:", json.dumps(thermals[:2], indent=2))

if __name__ == "__main__":
    main()