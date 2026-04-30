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
  const { stopById, routeById, tripById, stopTimesForTrip } = gtfs;

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
      const coords = stopObjs.map(s => [s.lat, s.lon]);
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
      if (drawnStops.has(s.stop_id)) continue;
      drawnStops.add(s.stop_id);
      L.circleMarker([s.lat, s.lon], {
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

export function updateMap(legs, stopById, routeById) {
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
    const coords = allStopIds
      .map(id => stopById.get(id))
      .filter(Boolean)
      .map(s => [s.lat, s.lon]);

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
      const coord = [stop.lat, stop.lon];
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
