// build_road_shapes.mjs
// Precomputes the road (and car-ferry) path between every pair of consecutive
// stops in the GTFS feed, so the maps can draw routes along the roads instead of
// straight lines. Writes data/road_shapes.json, which js/gtfs-loader.js loads.
//
// Usage (from anywhere, Node 18+):   node scripts/build_road_shapes.mjs [--refresh]
// Re-run after regenerating the GTFS or moving a stop. A segment already in
// road_shapes.json is reused while its end points still match the stops'
// coordinates; segments that fell back to straight lines are retried.
// --refresh re-routes everything.
//
// Routing: the public OSRM demo server (OpenStreetMap data, car profile), at
// most 1 request/second per its usage policy:
// https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
// Passenger-only ferries (Mykines, Fugloy, Skúvoy) aren't in the car profile, so
// ferry segments the car profile can't do are retried with the foot profile of
// FOSSGIS's OSRM (the router behind openstreetmap.org), which follows their lanes.
// A result is only kept when it looks right (see rejectReason()); otherwise the
// segment is stored as null and the map draws a straight line for it.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT     = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT      = path.join(ROOT, 'data', 'road_shapes.json');
const OSRM_CAR  = 'https://router.project-osrm.org/route/v1/driving';
const OSRM_FOOT = 'https://routing.openstreetmap.de/routed-foot/route/v1/driving';
const REFRESH   = process.argv.includes('--refresh');

const MAX_SNAP_M       = 300; // stop must be this close to a routable road/ferry
const MAX_SNAP_FERRY_M = 500; // ferry piers are often a few hundred metres from the village point
const MAX_ROAD_RATIO  = 5;    // road km / straight km — above this, assume a bad snap
                              // (fjords and tunnels legitimately reach ~4–5×)
const MIN_FERRY_SHARE = 0.6;  // ferry segments must actually go by ferry
const SIMPLIFY_M      = 6;    // Douglas–Peucker tolerance

// Load the feed with the site's own loader, so stop_ids (after alias merging)
// and coordinates are exactly what the browser uses.
const realFetch = globalThis.fetch;
globalThis.fetch = async (p, opts) => /^https?:/.test(p)
  ? realFetch(p, opts)
  : { ok: fs.existsSync(path.join(ROOT, p)), status: 200,
      text: async () => fs.readFileSync(path.join(ROOT, p), 'utf8'),
      json: async () => ({ segments: {} }) };
const { loadGTFS } = await import(pathToFileURL(path.join(ROOT, 'js', 'gtfs-loader.js')));
const { stopById, routeById, tripById, stopTimesForTrip } = await loadGTFS();
globalThis.fetch = realFetch;

// Undirected stop pairs → whether any route crossing it is a ferry
const pairs = new Map(); // "a|b" (a < b) → { ferry, routes:Set }
for (const [trip_id, stops] of stopTimesForTrip) {
  const route = routeById.get(tripById.get(trip_id).route_id);
  for (let i = 0; i < stops.length - 1; i++) {
    if (stops[i + 1].bad_hop) continue; // impossible hop in the data: never drawn
    const [a, b] = [stops[i].stop_id, stops[i + 1].stop_id].sort();
    if (a === b || !stopById.has(a) || !stopById.has(b)) continue;
    const p = pairs.get(`${a}|${b}`) ?? { ferry: false, routes: new Set() };
    p.ferry ||= route.route_type === 4;
    p.routes.add(route.route_id);
    pairs.set(`${a}|${b}`, p);
  }
}

const existing = !REFRESH && fs.existsSync(OUT)
  ? JSON.parse(fs.readFileSync(OUT, 'utf8')).segments
  : {};

// ── geometry helpers ──────────────────────────────────────────────────────────
function haversineM([lat1, lon1], [lat2, lon2]) {
  const t = Math.PI / 180, dLat = (lat2 - lat1) * t, dLon = (lon2 - lon1) * t;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * t) * Math.cos(lat2 * t) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

// Douglas–Peucker on a local equirectangular projection (metres)
function simplify(pts, tolM) {
  if (pts.length < 3) return pts;
  const lat0 = pts[0][0] * Math.PI / 180;
  const xy = pts.map(([lat, lon]) => [lon * 111320 * Math.cos(lat0), lat * 110540]);
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [s, e] = stack.pop();
    const [x1, y1] = xy[s], [x2, y2] = xy[e];
    const len = Math.hypot(x2 - x1, y2 - y1) || 1e-9;
    let maxD = 0, idx = -1;
    for (let i = s + 1; i < e; i++) {
      const d = Math.abs((x2 - x1) * (y1 - xy[i][1]) - (x1 - xy[i][0]) * (y2 - y1)) / len;
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (maxD > tolM) { keep[idx] = 1; stack.push([s, idx], [idx, e]); }
  }
  return pts.filter((_, i) => keep[i]);
}

const round5 = ([lat, lon]) => [Math.round(lat * 1e5) / 1e5, Math.round(lon * 1e5) / 1e5];

// ── routing ───────────────────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function route(base, a, b) {
  const url = `${base}/${a.lon},${a.lat};${b.lon},${b.lat}?overview=full&geometries=geojson&steps=true`;
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await realFetch(url, { headers: { 'User-Agent': 'transit-route-shape-builder/1.0' } });
    if (res.status === 429 || res.status >= 500) { await sleep(5000 * (attempt + 1)); continue; }
    return res.json();
  }
  throw new Error(`OSRM kept failing for ${a.stop_id} → ${b.stop_id}`);
}

// Returns the reason to reject the OSRM result, or '' to accept it.
function rejectReason(j, a, b, isFerry) {
  if (j.code !== 'Ok' || !j.routes?.length) return `no route (${j.code})`;
  const snap = Math.max(...j.waypoints.map(w => w.distance));
  if (snap > (isFerry ? MAX_SNAP_FERRY_M : MAX_SNAP_M)) return `stop ${Math.round(snap)} m from nearest road/ferry`;
  const r = j.routes[0];
  const straight = haversineM([a.lat, a.lon], [b.lat, b.lon]);
  if (straight > 500 && r.distance / straight > MAX_ROAD_RATIO) {
    return `road ${(r.distance / 1000).toFixed(1)} km vs straight ${(straight / 1000).toFixed(1)} km`;
  }
  if (isFerry) {
    const ferryM = r.legs[0].steps.filter(s => s.mode === 'ferry').reduce((s, x) => s + x.distance, 0);
    if (ferryM < MIN_FERRY_SHARE * r.distance) return `only ${Math.round(100 * ferryM / r.distance)}% by ferry`;
  }
  return '';
}

const segments = {};
const rejected = [];
let fetched = 0;
const samePoint = (p, s) => p[0] === round5([s.lat, s.lon])[0] && p[1] === round5([s.lat, s.lon])[1];

for (const [key, info] of [...pairs].sort(([x], [y]) => x.localeCompare(y))) {
  const [a, b] = key.split('|').map(id => stopById.get(id));
  const cached = existing[key];
  if (cached && samePoint(cached[0], a) && samePoint(cached[cached.length - 1], b)) {
    segments[key] = cached;
    continue;
  }
  if (fetched++) await sleep(1100);
  let j = await route(OSRM_CAR, a, b);
  let why = rejectReason(j, a, b, info.ferry);
  if (why && info.ferry) {
    await sleep(1100);
    const foot = await route(OSRM_FOOT, a, b);
    const whyFoot = rejectReason(foot, a, b, true);
    if (whyFoot) why += `; foot profile: ${whyFoot}`;
    else { j = foot; why = ''; }
  }
  if (why) {
    segments[key] = null;
    rejected.push(`  ${a.stop_name} ↔ ${b.stop_name} (route ${[...info.routes].join(', ')}): ${why}`);
  } else {
    // OSRM starts/ends at the snapped road point; pin the ends to the stops so
    // the line meets the stop markers.
    const pts = j.routes[0].geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    segments[key] = simplify([[a.lat, a.lon], ...pts, [b.lat, b.lon]], SIMPLIFY_M).map(round5);
  }
  process.stdout.write(`\r  routed ${fetched} new segment(s)…`);
}

fs.writeFileSync(OUT, JSON.stringify({
  _about: 'Road/ferry path between consecutive stops, keyed "stopA|stopB" (sorted, path runs A→B). ' +
          'null = no usable route, drawn as a straight line. Built by scripts/build_road_shapes.mjs ' +
          'from OpenStreetMap data via OSRM.',
  segments,
}));

const ok = Object.values(segments).filter(Boolean).length;
console.log(`\nWrote ${path.relative(ROOT, OUT)}: ${ok}/${pairs.size} segments follow roads/ferries ` +
            `(${(fs.statSync(OUT).size / 1024).toFixed(0)} KB).`);
if (rejected.length) console.log(`Straight lines kept for ${rejected.length} segment(s):\n${rejected.join('\n')}`);
