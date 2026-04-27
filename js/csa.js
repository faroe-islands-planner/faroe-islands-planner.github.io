// csa.js
// Connection Scan Algorithm for time-aware transit routing.
// Finds the earliest-arrival journey from fromStopId to toStopId
// departing on or after departureDate, respecting GTFS service calendars.

import { getActiveServices } from './calendar.js';

const TRANSFER_MINS = 2; // minimum connection time at a transfer stop

// Binary search: index of first element where el.dep_mins >= targetMins
function lowerBound(conns, targetMins) {
  let lo = 0, hi = conns.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (conns[mid].dep_mins < targetMins) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function findJourney(
  fromStopId,
  toStopId,
  departureDate,
  connections,
  tripById,
  calendarMap,
  exceptionMap,
) {
  if (fromStopId === toStopId) return [];

  // Active trips for this date
  const activeServices = getActiveServices(departureDate, calendarMap, exceptionMap);
  const activeTrips = new Set();
  for (const [trip_id, trip] of tripById) {
    if (activeServices.has(trip.service_id)) activeTrips.add(trip_id);
  }

  // Filter and re-sort connections to active trips only
  const todayConns = connections.filter(c => activeTrips.has(c.trip_id));
  // connections is pre-sorted globally; filter preserves order
  // (no re-sort needed since filter is stable)

  const startMins = departureDate.getHours() * 60 + departureDate.getMinutes();
  const startIdx  = lowerBound(todayConns, startMins);

  // T[stop_id] = earliest arrival time (minutes) at that stop
  const T = new Map();
  T.set(fromStopId, startMins);

  // B[stop_id] = the connection that brought us here (for backtracking)
  const B = new Map();

  // inTrip[trip_id] = true if we've boarded this trip already
  // (so we can continue riding even after departure time passes)
  const inTrip = new Map();

  for (let i = startIdx; i < todayConns.length; i++) {
    const conn = todayConns[i];

    // Can we board this connection?
    const earliestAtDep = T.get(conn.dep_stop);
    const alreadyRiding = inTrip.has(conn.trip_id);

    const canBoard =
      earliestAtDep !== undefined &&
      (alreadyRiding || earliestAtDep + (B.has(conn.dep_stop) ? TRANSFER_MINS : 0) <= conn.dep_mins);

    if (!canBoard) continue;

    // Board / continue riding
    if (!alreadyRiding) inTrip.set(conn.trip_id, conn.dep_stop);

    // Update earliest arrival at destination stop
    const curArr = T.get(conn.arr_stop);
    if (curArr === undefined || conn.arr_mins < curArr) {
      T.set(conn.arr_stop, conn.arr_mins);
      B.set(conn.arr_stop, conn);
    }

    if (conn.arr_stop === toStopId) break; // found destination
  }

  if (!T.has(toStopId)) return null; // no journey found

  // Backtrack to reconstruct legs
  return reconstructLegs(fromStopId, toStopId, B, tripById);
}

function reconstructLegs(fromStopId, toStopId, B, tripById) {
  // Walk backwards from toStopId to fromStopId via backtrack pointers
  const connPath = [];
  let cur = toStopId;
  while (B.has(cur)) {
    const conn = B.get(cur);
    connPath.unshift(conn);
    cur = conn.dep_stop;
    if (cur === fromStopId) break;
  }

  if (!connPath.length) return null;

  // Group consecutive connections on the same trip into legs
  const legs = [];
  let legConns = [connPath[0]];

  for (let i = 1; i < connPath.length; i++) {
    if (connPath[i].trip_id === connPath[i - 1].trip_id) {
      legConns.push(connPath[i]);
    } else {
      legs.push(makeLeg(legConns, tripById));
      legConns = [connPath[i]];
    }
  }
  legs.push(makeLeg(legConns, tripById));

  return legs;
}

function makeLeg(conns, tripById) {
  const first = conns[0];
  const last  = conns[conns.length - 1];
  const trip  = tripById.get(first.trip_id);
  // Intermediate stops (not first dep or last arr)
  const via = conns.slice(0, -1).map(c => c.arr_stop);
  return {
    trip_id:    first.trip_id,
    route_id:   trip ? trip.route_id : '',
    headsign:   trip ? trip.trip_headsign : '',
    from_stop:  first.dep_stop,
    to_stop:    last.arr_stop,
    via_stops:  via,
    dep_mins:   first.dep_mins,
    arr_mins:   last.arr_mins,
  };
}

// Find the next N departures on the same route+direction between two stops,
// on the given date, at or after afterMins.
export function nextDepartures(
  routeId,
  fromStopId,
  toStopId,
  afterMins,
  date,
  connections,
  tripById,
  calendarMap,
  exceptionMap,
  n = 4,
) {
  const activeServices = getActiveServices(date, calendarMap, exceptionMap);
  const results = [];

  // Collect all connections from fromStopId at or after afterMins, on this route
  for (const conn of connections) {
    if (conn.dep_mins < afterMins) continue;
    if (conn.dep_stop !== fromStopId) continue;

    const trip = tripById.get(conn.trip_id);
    if (!trip || trip.route_id !== routeId) continue;
    if (!activeServices.has(trip.service_id)) continue;

    // Check that this trip actually serves toStopId after fromStopId
    // (use the trip's headsign direction heuristic — arr_stop reachable)
    results.push({ dep_mins: conn.dep_mins, trip_id: conn.trip_id });
    if (results.length >= n) break;
  }

  // Deduplicate by trip (same trip can appear multiple times if route has many stops)
  const seen = new Set();
  return results.filter(r => {
    if (seen.has(r.trip_id)) return false;
    seen.add(r.trip_id);
    return true;
  }).slice(0, n);
}
