// csa.js
// Connection Scan Algorithm for time-aware transit routing.
// Finds the earliest-arrival journey from fromStopId to toStopId
// departing on or after departureDate, respecting GTFS service calendars.

import { getActiveServices } from './calendar.js';

const TRANSFER_MINS = 0; // minimum connection time at a transfer stop

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

  // N[stop_id] = number of transfers on the best path to this stop
  const N = new Map();
  N.set(fromStopId, 0);

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

    // Compute transfer count for arriving at arr_stop via this connection.
    // A transfer occurs when the trip that brought us to dep_stop differs
    // from the current connection's trip.
    const transfersAtDep = N.get(conn.dep_stop) ?? 0;
    const tripAtDep      = B.get(conn.dep_stop)?.trip_id;  // undefined at origin
    const isNewTransfer  = tripAtDep !== undefined && conn.trip_id !== tripAtDep;
    const newTransfers   = transfersAtDep + (isNewTransfer ? 1 : 0);

    // Update if: (a) earlier arrival, or (b) same arrival with <= transfers.
    // The <= (not strict <) lets a later-processed same-trip connection overwrite
    // an earlier different-trip one, preserving continuation and avoiding a
    // forced future transfer.
    const curArr       = T.get(conn.arr_stop);
    const curTransfers = N.get(conn.arr_stop) ?? Infinity;
    const improvesByTime      = curArr === undefined || conn.arr_mins < curArr;
    const improvesByTransfers = curArr !== undefined
                             && conn.arr_mins === curArr
                             && newTransfers <= curTransfers;

    // When already riding, also update B (not T) so backtracking follows this
    // continuous trip rather than an earlier-arriving alien trip at this stop.
    const preservesContinuity = alreadyRiding
                             && curArr !== undefined
                             && conn.arr_mins <= curArr
                             && newTransfers <= curTransfers;

    if (improvesByTime) {
      T.set(conn.arr_stop, conn.arr_mins);
      B.set(conn.arr_stop, conn);
      N.set(conn.arr_stop, newTransfers);
    } else if (improvesByTransfers || preservesContinuity) {
      B.set(conn.arr_stop, conn);
      N.set(conn.arr_stop, newTransfers);
    }

    // Stop only when future connections cannot beat the best known arrival
    // (neither by time nor by transfers).
    if (conn.arr_stop === toStopId && conn.arr_mins > T.get(toStopId)) break;
  }

  if (!T.has(toStopId)) return null; // no journey found

  // Backtrack to reconstruct legs
  return reconstructLegs(fromStopId, toStopId, B, tripById);
}

function reconstructLegs(fromStopId, toStopId, B, tripById) {
  // Walk backwards from toStopId to fromStopId via backtrack pointers
  const connPath = [];
  let cur = toStopId;
  const visited = new Set();
  while (B.has(cur)) {
    // Back-pointer cycle (only possible with inconsistent data): give up on this
    // search instead of looping forever and freezing the page.
    if (visited.has(cur)) return null;
    visited.add(cur);
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
    on_demand:  conns.some(c => c.pickup_type === 3),
  };
}

const arrOf       = legs => legs[legs.length - 1].arr_mins;
const transfersOf = legs => legs.length - 1;

// Find up to maxResults journeys from fromStopId to toStopId, starting at or
// after departureDate, by repeatedly calling findJourney and advancing the
// start time past each result's departure minute.
//
// Each search starts after the previous result's departure, so every new journey
// departs later than all earlier ones. An earlier journey that the new one matches
// or beats on arrival time and transfer count is never the better choice, so it is
// dropped — e.g. two local buses to catch the same express you could board directly.
export function findJourneys(
  fromStopId,
  toStopId,
  departureDate,
  connections,
  tripById,
  calendarMap,
  exceptionMap,
  maxResults = 5,
) {
  const results = [];
  let fromMins = departureDate.getHours() * 60 + departureDate.getMinutes();

  // Results are same-day only (the UI shows times without dates). The search cap
  // bounds the work when many dominated journeys are skipped.
  for (let searches = 0; searches < maxResults * 6 && fromMins < 1440; searches++) {
    const date = new Date(departureDate);
    date.setHours(Math.floor(fromMins / 60), fromMins % 60, 0, 0);

    const legs = findJourney(fromStopId, toStopId, date, connections, tripById, calendarMap, exceptionMap);
    if (!legs || legs.length === 0) break;

    // List is full and this journey arrives later than the last one, so it
    // can't beat any of them: stop.
    if (results.length >= maxResults && arrOf(legs) > arrOf(results[results.length - 1])) break;

    for (let i = results.length - 1; i >= 0; i--) {
      if (arrOf(results[i]) >= arrOf(legs) && transfersOf(results[i]) >= transfersOf(legs)) {
        results.splice(i, 1);
      }
    }
    if (results.length < maxResults) results.push(legs);

    fromMins = legs[0].dep_mins + 1;
  }

  return results;
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
  stopTimesForTrip,
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

    // Verify toStopId is downstream of fromStopId in this trip's stop sequence
    const stops = stopTimesForTrip.get(conn.trip_id);
    if (!stops) continue;
    // Round trips (Eiði → Oyrarbakki → Eiði) visit a stop twice, so look for
    // toStopId after this particular visit to fromStopId.
    const fromIdx = stops.findIndex(s => s.stop_id === fromStopId && s.dep_mins === conn.dep_mins);
    if (fromIdx < 0 || !stops.slice(fromIdx + 1).some(s => s.stop_id === toStopId)) continue;

    // List each trip once, and each time once: overlapping timetables (e.g.
    // "daily" and "weekdays") and route variants can leave at the same minute.
    if (results.some(r => r.trip_id === conn.trip_id || r.dep_mins === conn.dep_mins)) continue;
    results.push({ dep_mins: conn.dep_mins, trip_id: conn.trip_id });
    if (results.length >= n) break;
  }

  return results;
}
