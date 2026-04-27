// map.js
// Leaflet-based map rendering.
// initNetworkMap() draws all routes and stops on the network overview map.
// updateMap(legs) draws a specific journey on the journey planner map.

// GTFS route_type → display color (matches route_color in routes.txt)
// Fallback colors by route_type number
const TYPE_COLORS_BY_TYPE = {
  3:   '#4dff8a', // bus
  4:   '#4db8ff', // ferry
  700: '#ffb84d', // airport bus / express
};

function routeColor(route) {
  if (route.route_color && route.route_color !== '000000') {
    return '#' + route.route_color;
  }
  return TYPE_COLORS_BY_TYPE[route.route_type] || '#aaa';
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

  // Draw each route as a polyline (white halo + colored core)
  for (const [route_id, route] of routeById) {
    const color = routeColor(route);
    const stopObjs = getRouteStopSequence(route_id, tripById, stopTimesForTrip, stopById);
    const coords = stopObjs.map(s => [s.lat, s.lon]);
    if (coords.length < 2) continue;

    L.polyline(coords, {
      color: '#fff', weight: 6, opacity: 0.7,
      lineCap: 'round', lineJoin: 'round',
    }).addTo(map);

    L.polyline(coords, {
      color, weight: 3.5, opacity: 0.95,
      lineCap: 'round', lineJoin: 'round',
      dashArray: isFerry(route) ? '10 8' : null,
    })
      .bindTooltip(
        `${route.route_short_name} – ${route.route_long_name}`,
        { sticky: true, direction: 'top' }
      )
      .addTo(map);

    coords.forEach(c => allCoords.push(c));
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
        fillColor: '#1a3a5a',
        color: '#fff',
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
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
      maxZoom: 18,
    }
  ).addTo(leafletMap);
  L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 18, opacity: 0.85 }
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
        radius:      isEndpoint ? 9 : 6,
        fillColor:   color,
        color:       '#fff',
        weight:      isEndpoint ? 3 : 2,
        fillOpacity: 1,
      })
        .bindTooltip(stop.stop_name, { direction: 'top', offset: [0, -6] })
        .addTo(leafletMap);
      mapLayers.push(marker);
    });
  }

  if (allBounds.length) {
    leafletMap.fitBounds(allBounds, { padding: [28, 28], maxZoom: 12 });
  }
}
