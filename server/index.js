// server/index.js
import express from "express";
import { spawn } from "child_process";
import path from "path";
import fs from "fs";

const app = express();
app.use(express.json());

const ROOT = process.cwd();
const PY = process.platform === "win32" ? "python" : "python3"; // change to your python cmd if needed
const SCRIPT = path.resolve(ROOT, "generate_from_weglide.py");

app.post("/api/run", (req, res) => {
  const {
    day,
    start,
    goal,
    corridor_km = 30,
    min_net = 1.2,
    max_nodes = 30,
    per_leg_floor = 1200,
    mc = 0.0,
    wind = 8.0,
    wdir = 260.0,
    wair = 0.0,
    chain_thermals = false,
    outfile = "src/data/plan.json",
  } = req.body || {};

  if (!Array.isArray(start) || start.length !== 3 || !Array.isArray(goal) || goal.length !== 3) {
    return res.status(400).json({ ok: false, error: "start and goal must be [lat, lon, heightMSL]" });
  }

  const absOut = path.resolve(ROOT, outfile);
  fs.mkdirSync(path.dirname(absOut), { recursive: true });

  const args = [
    SCRIPT,
    ...(day ? ["--day", day] : []),
    "--start", String(start[0]), String(start[1]), String(start[2]),
    "--goal",  String(goal[0]),  String(goal[1]),  String(goal[2]),
    "--corridor-km", String(corridor_km),
    "--min-net", String(min_net),
    "--max-nodes", String(max_nodes),
    "--per-leg-floor", String(per_leg_floor),
    "--mc", String(mc),
    "--wind", String(wind),
    "--wdir", String(wdir),
    "--wair", String(wair),
    "--outfile", absOut,
  ];
  if (chain_thermals) args.push("--chain-thermals");

  console.log("Running:", PY, args.join(" ")); // helpful debug

  const py = spawn(PY, args, { shell: false });
  let stdout = "", stderr = "";

  py.stdout.on("data", d => stdout += d.toString());
  py.stderr.on("data", d => stderr += d.toString());
  py.on("error", err => res.status(500).json({ ok: false, error: `Failed to start Python: ${err.message}` }));
  py.on("close", code => {
    if (code !== 0) {
      return res.status(500).json({ ok: false, error: `No Possible route code: ${code}`, stdout, stderr });
    }
    try {
      const json = JSON.parse(fs.readFileSync(absOut, "utf-8"));
      return res.json({ ok: true, plan: json, log: stdout });
    } catch (e) {
      return res.status(500).json({ ok: false, error: `could not read outfile`, details: e?.message, stdout, stderr });
    }
  });
});

const PORT = process.env.PORT || 5174;
app.listen(PORT, () => console.log(`Server listening on http://localhost:${PORT}`));
