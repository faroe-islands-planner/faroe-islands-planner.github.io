// map.js
// Leaflet-based map rendering.
// initNetworkMap() draws all routes and stops on the network overview map.
// updateMap(legs) draws a specific journey on the journey planner map.

// Canonical display colors — must match the legend in index.html
// Palette drawn from Faroe Islands imagery: Atlantic ocean blue, coastal moss green,
// puffin-beak orange for high-priority express, site amber for airport.
const ROUTE_COLORS = {
  3:   '#3D7A56', // bus — coastal moss green (ground network)
  4:   '#1878B8', // ferry — Atlantic ocean blue (water routes)
  700: '#C4981A', // airport — golden amber (matches site accent)
};
// Route 401 is the express service — highest priority, most salient
const ROUTE_COLOR_OVERRIDES = {
  '401': '#E05E20', // express — puffin-beak orange (fast, stands out)
};

function routeColor(route) {
  return ROUTE_COLOR_OVERRIDES[route.route_id] || ROUTE_COLORS[route.route_type] || '#aaa';
}

function isFerry(route) {
  return route.route_type === 4;
}

function shapeCoordsForRoute(routeShapes, routeId, fallbackCoords) {
  const shape = routeShapes?.[routeId];
  return shape?.geometry?.length >= 2 ? shape.geometry : fallbackCoords;
}

function shapeCoordsForStops(routeShapes, routeId, stopIds, stopById) {
  const shape = routeShapes?.[routeId];
  const coords = [];

  function sameCoord(a, b) {
    return a && b && a[0] === b[0] && a[1] === b[1];
  }

  function append(segment) {
    if (!segment || segment.length < 2) return;
    if (!coords.length) {
      coords.push(...segment);
    } else {
      coords.push(...(sameCoord(coords[coords.length - 1], segment[0]) ? segment.slice(1) : segment));
    }
  }

  for (let i = 0; i < stopIds.length - 1; i++) {
    const fromId = stopIds[i];
    const toId = stopIds[i + 1];
    const forward = shape?.segments?.[`${fromId}|${toId}`];
    const reverse = shape?.segments?.[`${toId}|${fromId}`];

    if (forward) {
      append(forward);
    } else if (reverse) {
      append([...reverse].reverse());
    } else {
      const from = stopById.get(fromId);
      const to = stopById.get(toId);
      if (from && to) append([[from.lat, from.lon], [to.lat, to.lon]]);
    }
  }

  return coords;
}

function shapeCoordForStop(routeShapes, routeId, stopId, stopById) {
  const shape = routeShapes?.[routeId];
  const stop = stopById.get(stopId);
  const fallback = stop ? [stop.lat, stop.lon] : null;

  if (!shape?.segments) return fallback;

  for (const [key, segment] of Object.entries(shape.segments)) {
    if (!segment || segment.length < 2) continue;
    const [fromId, toId] = key.split('|');
    if (fromId === stopId) return segment[0];
    if (toId === stopId) return segment[segment.length - 1];
  }

  return fallback;
}

function routeStopCoord(routeShapes, routeId, stopId, stopById) {
  const coord = shapeCoordForStop(routeShapes, routeId, stopId, stopById);
  if (coord) return coord;
  const stop = stopById.get(stopId);
  return stop ? [stop.lat, stop.lon] : null;
}

function coordKey(coord) {
  return coord.map(n => Number(n).toFixed(5)).join(',');
}

// Build a de-duplicated ordered stop list for a route from its trips' stop_times.
// Uses the longest trip in either direction to get the full stop sequence.
function getRouteStopSequence(routeId, tripById, stopTimesForTrip, stopById) {
  let best = [];
  for (const [trip_id, trip] of tripById) {
    if (trip.route_id !== routeId) continue;
    const stops = stopTimesForTrip.get(trip_id) || [];
    if (stops.length > best.length) best = stops;
  }
  return best
    .map(s => stopById.get(s.stop_id))
    .filter(Boolean);
}

// ── Network map ───────────────────────────────────────────────────────────────
export function initNetworkMap(gtfs) {
  const { stopById, routeById, tripById, stopTimesForTrip, routeShapes } = gtfs;

  const map = L.map('network-map', {
    zoomControl: true,
    minZoom: 7,
    maxZoom: 16,
  }).setView([62.0, -6.95], 9);

  L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    {
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
        '© <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }
  ).addTo(map);

  const allCoords = [];

  // Draw routes in z-order: bus (bottom) → ferry → airport/express (top)
  // This ensures express/airport routes are visible on top of bus lines where they overlap.
  const DRAW_ORDER = [3, 4, 700];

  for (const typeGroup of DRAW_ORDER) {
    for (const [route_id, route] of routeById) {
      if (route.route_type !== typeGroup) continue;
      const color = routeColor(route);
      const stopObjs = getRouteStopSequence(route_id, tripById, stopTimesForTrip, stopById);
      const stopCoords = stopObjs.map(s => [s.lat, s.lon]);
      const coords = shapeCoordsForRoute(routeShapes, route_id, stopCoords);
      if (coords.length < 2) continue;

      const isHighPriority = route.route_type === 700; // airport/express
      const weight = isHighPriority ? 4.5 : 3.5;
      const dashArray = isFerry(route) ? '10 8' : (isHighPriority ? '6 4' : null);

      // White halo
      L.polyline(coords, {
        color: '#fff', weight: weight + 2.5, opacity: 0.7,
        lineCap: 'round', lineJoin: 'round',
      }).addTo(map);

      // Colored core
      L.polyline(coords, {
        color, weight, opacity: 0.95,
        lineCap: 'round', lineJoin: 'round',
        dashArray,
      })
        .bindTooltip(
          `${route.route_short_name} – ${route.route_long_name}`,
          { sticky: true, direction: 'top' }
        )
        .addTo(map);

      coords.forEach(c => allCoords.push(c));
    }
  }

  // Draw every stop that appears on at least one route
  const drawnStops = new Set();
  for (const [route_id] of routeById) {
    const stopObjs = getRouteStopSequence(route_id, tripById, stopTimesForTrip, stopById);
    for (const s of stopObjs) {
      const coord = routeStopCoord(routeShapes, route_id, s.stop_id, stopById);
      if (!coord) continue;
      const markerKey = `${s.stop_id}:${coordKey(coord)}`;
      if (drawnStops.has(markerKey)) continue;
      drawnStops.add(markerKey);
      L.circleMarker(coord, {
        radius: 4,
        fillColor: '#fff',
        color: '#0D0D0D',
        weight: 1.5,
        fillOpacity: 1,
      })
        .bindTooltip(s.stop_name, { direction: 'top', offset: [0, -4] })
        .addTo(map);
    }
  }

  if (allCoords.length) {
    map.fitBounds(allCoords, { padding: [40, 40] });
  }
}

// ── Journey map ───────────────────────────────────────────────────────────────
let leafletMap = null;
let mapLayers  = [];

function ensureMap() {
  if (leafletMap) return;
  leafletMap = L.map('journey-map', { zoomControl: true }).setView([62.0, -6.95], 9);
  L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    {
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
        '© <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }
  ).addTo(leafletMap);
}

export function updateMap(legs, stopById, routeById, routeShapes = {}) {
  const mapEl = document.getElementById('journey-map');
  mapEl.classList.remove('hidden');

  ensureMap();
  leafletMap.invalidateSize();

  mapLayers.forEach(l => l.remove());
  mapLayers = [];

  const allBounds = [];

  for (const leg of legs) {
    const route = routeById.get(leg.route_id);
    const color = route ? routeColor(route) : '#fff';
    const ferry = route ? isFerry(route) : false;

    const allStopIds = [leg.from_stop, ...leg.via_stops, leg.to_stop];
    const coords = shapeCoordsForStops(routeShapes, leg.route_id, allStopIds, stopById);

    if (coords.length >= 2) {
      const halo = L.polyline(coords, {
        color: '#000', weight: 8, opacity: 0.55,
        lineCap: 'round', lineJoin: 'round',
      }).addTo(leafletMap);
      const line = L.polyline(coords, {
        color, weight: 5, opacity: 1,
        lineCap: 'round', lineJoin: 'round',
        dashArray: ferry ? '12 10' : null,
      }).addTo(leafletMap);
      mapLayers.push(halo, line);
      coords.forEach(c => allBounds.push(c));
    }

    allStopIds.forEach((stopId, i) => {
      const stop = stopById.get(stopId);
      if (!stop) return;
      const coord = routeStopCoord(routeShapes, leg.route_id, stopId, stopById);
      if (!coord) return;
      allBounds.push(coord);
      const isEndpoint = i === 0 || i === allStopIds.length - 1;
      const marker = L.circleMarker(coord, {
        radius:      isEndpoint ? 10 : 6,
        fillColor:   isEndpoint ? '#fff' : color,
        color:       isEndpoint ? color : '#fff',
        weight:      isEndpoint ? 3 : 2,
        fillOpacity: 1,
      })
        .bindTooltip(stop.stop_name, {
          direction: 'top',
          offset: [0, -8],
          permanent: isEndpoint,
          className: 'map-stop-label',
        })
        .addTo(leafletMap);
      mapLayers.push(marker);
    });
  }

  if (allBounds.length) {
    leafletMap.fitBounds(allBounds, { padding: [28, 28], maxZoom: 12 });
  }
}
