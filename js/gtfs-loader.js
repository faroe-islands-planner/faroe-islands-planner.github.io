// gtfs-loader.js
// Fetches all GTFS CSV files in parallel, parses them, and builds in-memory
// indexes consumed by calendar.js, csa.js, map.js, and app.js.

function parseCSV(text) {
  const lines = text.trim().split('\n');
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map(h => h.trim());
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const vals = line.split(',');
    const obj = {};
    headers.forEach((h, idx) => { obj[h] = (vals[idx] || '').trim(); });
    rows.push(obj);
  }
  return rows;
}

// Data files are always revalidated with the server (an unchanged file costs a
// 304). Otherwise browsers may reuse a cached stops.txt next to a newer
// road_shapes.json — every route then starts with a straight line from the
// stop's old position.
const FETCH_OPTS = { cache: 'no-cache' };

async function fetchCSV(path) {
  const res = await fetch(path, FETCH_OPTS);
  if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status}`);
  return parseCSV(await res.text());
}

// Optional road/ferry geometry between consecutive stops, built by
// scripts/build_road_shapes.mjs. Returns Map "stopA|stopB" → [[lat, lon], …]
// (keys sorted, path runs A→B). If it's missing, the maps draw straight lines.
async function fetchRoadShapes(path) {
  try {
    const res = await fetch(path, FETCH_OPTS);
    if (!res.ok) return new Map();
    const { segments } = await res.json();
    return new Map(Object.entries(segments).filter(([, pts]) => pts));
  } catch (err) {
    console.warn('Road shapes unavailable, drawing straight lines:', err);
    return new Map();
  }
}

function timeMins(hhmm) {
  // "06:45" → 405, "25:10" → 1510 (GTFS allows >24h for after-midnight)
  if (!hhmm) return null;
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

// Scraping errors in stop_times.txt produce hops no vehicle can make: arriving
// before departing ("Eiði 07:20 → Oyrarbakki 06:40") or phantom tails like
// "Fuglafjørður 09:25 → Klaksvík 09:10 → Tórshavn 09:10" (26 km in 0 min).
// Real hops top out around 85 km/h in a straight line; timetable minutes are
// rounded, so every hop is given at least 1 minute. Besides nonsense journeys,
// backwards hops let the CSA back-pointers form a cycle, which froze the page.
const MAX_STRAIGHT_KMH = 150;

function isImpossibleHop(from, to, stopById) {
  if (from.dep_mins === null || to.arr_mins === null) return false;
  if (to.arr_mins < from.dep_mins) return true;
  const a = stopById.get(from.stop_id), b = stopById.get(to.stop_id);
  if (!a || !b) return false;
  const t = Math.PI / 180;
  const h = Math.sin((b.lat - a.lat) * t / 2) ** 2 +
            Math.cos(a.lat * t) * Math.cos(b.lat * t) * Math.sin((b.lon - a.lon) * t / 2) ** 2;
  const km = 2 * 6371 * Math.asin(Math.sqrt(h));
  return km / (Math.max(to.arr_mins - from.dep_mins, 1) / 60) > MAX_STRAIGHT_KMH;
}

function minsToHHMM(mins) {
  const h = Math.floor(mins / 60) % 24;
  const m = mins % 60;
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
}

export async function loadGTFS() {
  const BASE = 'gtfs/';

  const [stopRows, routeRows, tripRows, stRows, calRows, calDateRows, roadShapes] =
    await Promise.all([
      fetchCSV(BASE + 'stops.txt'),
      fetchCSV(BASE + 'routes.txt'),
      fetchCSV(BASE + 'trips.txt'),
      fetchCSV(BASE + 'stop_times.txt'),
      fetchCSV(BASE + 'calendar.txt'),
      fetchCSV(BASE + 'calendar_dates.txt'),
      fetchRoadShapes('data/road_shapes.json'),
    ]);

  // ── Build stopTimesForTrip first so we know which stop_ids are used ──────
  // Group stop_time rows by trip_id, sorted by stop_sequence
  const rawByTrip = new Map();
  for (const row of stRows) {
    if (!rawByTrip.has(row.trip_id)) rawByTrip.set(row.trip_id, []);
    rawByTrip.get(row.trip_id).push(row);
  }
  for (const rows of rawByTrip.values()) {
    rows.sort((a, b) => Number(a.stop_sequence) - Number(b.stop_sequence));
  }

  // stop_ids that actually appear in stop_times
  const usedStopIds = new Set(stRows.map(r => r.stop_id));

  // ── stopById: only stops with valid coords AND used in stop_times ─────────
  // After normalising names in stops.txt, many stop_ids share the same name
  // (they're the same physical stop with different scraper-generated IDs).
  // Build a name→primary_stop_id map: for each name keep the stop_id that
  // looks most canonical (fewest underscores / shortest), then remap all
  // alias stop_ids to the primary so routing and display stay consistent.
  const stopById = new Map();

  // Score a stop_id: lower = more canonical (prefer "klaksvik" over "from_klaksvi")
  function stopIdScore(id) {
    let s = 0;
    if (/^(from|on|to)_/.test(id)) s += 100;  // directional prefix = alias
    s += (id.match(/_/g) || []).length * 2;    // more underscores = more derived
    s += id.length;                             // shorter is cleaner
    return s;
  }

  // Pass 1: collect all valid stops grouped by stop_name
  const byName = new Map(); // stop_name -> [{stop_id, lat, lon}]
  for (const row of stopRows) {
    if (!usedStopIds.has(row.stop_id)) continue;
    const lat = parseFloat(row.stop_lat);
    const lon = parseFloat(row.stop_lon);
    if (isNaN(lat) || isNaN(lon)) continue;
    const name = row.stop_name;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push({ stop_id: row.stop_id, lat, lon });
  }

  // Pass 2: for each name pick the primary stop_id; build alias→primary map
  const aliasMap = new Map(); // alias stop_id -> primary stop_id
  for (const [name, candidates] of byName) {
    candidates.sort((a, b) => stopIdScore(a.stop_id) - stopIdScore(b.stop_id));
    const primary = candidates[0];
    stopById.set(primary.stop_id, {
      stop_id: primary.stop_id,
      stop_name: name,
      lat: primary.lat,
      lon: primary.lon,
    });
    for (const c of candidates) {
      aliasMap.set(c.stop_id, primary.stop_id);
    }
  }

  // Remap alias stop_ids in connections and stopTimesForTrip to primary stop_ids
  // so that CSA results always reference the canonical stop_id.
  function remap(id) { return aliasMap.get(id) ?? id; }

  // ── routeById ─────────────────────────────────────────────────────────────
  const routeById = new Map();
  for (const row of routeRows) {
    routeById.set(row.route_id, {
      route_id: row.route_id,
      route_short_name: row.route_short_name,
      route_long_name: row.route_long_name,
      route_type: Number(row.route_type),
      route_color: row.route_color || '888888',
      slug: row.route_url || '',
    });
  }

  // ── tripById ──────────────────────────────────────────────────────────────
  const tripById = new Map();
  for (const row of tripRows) {
    tripById.set(row.trip_id, {
      trip_id: row.trip_id,
      route_id: row.route_id,
      service_id: row.service_id,
      trip_headsign: row.trip_headsign || '',
      direction_id: row.direction_id,
    });
  }

  // ── Drop duplicate trips ──────────────────────────────────────────────────
  // The scraped feed lists some trips twice (same route, service days, stops
  // and times; 113 of them, mostly ferries), which showed up as repeated
  // times under "Next departures". Keep the first copy.
  const seenTrips = new Set();
  for (const [trip_id, rows] of rawByTrip) {
    const trip = tripById.get(trip_id);
    const key = [trip?.route_id, trip?.service_id,
                 ...rows.map(r => `${remap(r.stop_id)}@${r.arrival_time}/${r.departure_time}`)].join('|');
    if (seenTrips.has(key)) rawByTrip.delete(trip_id);
    else seenTrips.add(key);
  }

  // ── stopTimesForTrip (with minutes, stop_ids remapped to primary) ───────────
  // Each stop_time gets bad_hop = true when the hop from the previous stop is
  // impossible (see isImpossibleHop). Routing skips those hops and the network
  // map splits the trip there.
  const stopTimesForTrip = new Map();
  let impossibleHops = 0;
  for (const [trip_id, rows] of rawByTrip) {
    const sts = rows.map(r => ({
      stop_id: remap(r.stop_id),
      arr_mins: timeMins(r.arrival_time),
      dep_mins: timeMins(r.departure_time),
      stop_sequence: Number(r.stop_sequence),
      pickup_type: Number(r.pickup_type || 0),
      drop_off_type: Number(r.drop_off_type || 0),
      bad_hop: false,
    }));
    for (let i = 1; i < sts.length; i++) {
      sts[i].bad_hop = isImpossibleHop(sts[i - 1], sts[i], stopById);
      if (sts[i].bad_hop) impossibleHops++;
    }
    stopTimesForTrip.set(trip_id, sts);
  }
  if (impossibleHops) {
    console.warn(`GTFS: ignoring ${impossibleHops} stop-to-stop hops with impossible times (bad stop_times data)`);
  }

  // ── connections: sorted array for CSA (stop_ids remapped to primary) ────────
  // A connection is one hop: stop A at dep_mins → stop B at arr_mins, same trip.
  const connections = [];
  for (const [trip_id, stops] of stopTimesForTrip) {
    for (let i = 0; i < stops.length - 1; i++) {
      const from = stops[i];
      const to   = stops[i + 1];
      if (from.dep_mins === null || to.arr_mins === null) continue;
      if (to.bad_hop) continue;
      connections.push({
        dep_stop:    from.stop_id,   // already remapped
        dep_mins:    from.dep_mins,
        arr_stop:    to.stop_id,     // already remapped
        arr_mins:    to.arr_mins,
        trip_id,
        pickup_type: from.pickup_type,
      });
    }
  }
  connections.sort((a, b) => a.dep_mins - b.dep_mins);

  // ── calendarMap and exceptionMap ──────────────────────────────────────────
  const calendarMap = new Map();
  for (const row of calRows) {
    calendarMap.set(row.service_id, {
      // days[0]=monday … days[6]=sunday, matching GTFS column order
      days: [
        row.monday, row.tuesday, row.wednesday, row.thursday,
        row.friday, row.saturday, row.sunday,
      ],
      start: row.start_date,
      end:   row.end_date,
    });
  }

  const exceptionMap = new Map(); // "service_id:YYYYMMDD" → 1|2
  for (const row of calDateRows) {
    exceptionMap.set(`${row.service_id}:${row.date}`, Number(row.exception_type));
  }

  return {
    stopById,
    routeById,
    tripById,
    connections,
    stopTimesForTrip,
    calendarMap,
    exceptionMap,
    roadShapes,
    minsToHHMM,
  };
}
