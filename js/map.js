// map.js
// Leaflet-based map rendering.
// initNetworkMap() draws all routes and stops on the network overview map.
// updateMap(legs) draws a specific journey on the journey planner map.

// Route 401 is the express service; it has route_type 700 like the airport buses.
const EXPRESS_ROUTE_ID = '401';

// 'ferry' | 'express' | 'airport' | 'bus' (also the badge-* CSS class in index.html)
export function routeCategory(route) {
  if (route.route_type === 4) return 'ferry';
  if (route.route_id === EXPRESS_ROUTE_ID) return 'express';
  if (route.route_type === 700) return 'airport';
  return 'bus';
}

// Line colour of a route = its category's --*-color variable in index.html, the
// same colour the legend and the badges use. Change a route colour there only.
const categoryColors = new Map();
function routeColor(route) {
  const cat = routeCategory(route);
  if (!categoryColors.has(cat)) {
    const css = getComputedStyle(document.documentElement).getPropertyValue(`--${cat}-color`).trim();
    categoryColors.set(cat, css || '#888');
  }
  return categoryColors.get(cat);
}

function isFerry(route) {
  return route.route_type === 4;
}

// Network-map line styles, drawn bottom → top. Where routes share a road, each
// wider line shows as a colored edge around the narrower one on top of it, so
// every route stays visible. Keep the .type-label line samples in index.html
// (legend, route list, journey legs) in step.
const NETWORK_LINE_STYLES = {
  express: { order: 0, weight: 10 },
  airport: { order: 1, weight: 6.5 },
  bus:     { order: 2, weight: 3 },
  ferry:   { order: 3, weight: 3, dashArray: '10 8' },
};

// Base map: OpenStreetMap's standard tiles — free, no API key.
// (CARTO's basemaps started requiring an API key and now return watermark tiles.)
// Usage policy: https://operations.osmfoundation.org/policies/tiles/
// The tiles are muted via CSS (.leaflet-tile-pane in index.html) so route colors stand out.
function addBaseLayer(map) {
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);
}

// Lat/lon path through a run of stops. Between each consecutive pair it follows
// the road (or car-ferry lane) from data/road_shapes.json when there is one, and
// falls back to a straight line otherwise. roadShapes keys are "stopA|stopB" in
// sorted order with the path running A→B, so the other direction is reversed.
function stopPath(stops, roadShapes) {
  const coords = [];
  stops.forEach((s, i) => {
    if (i === 0) { coords.push([s.lat, s.lon]); return; }
    const prev = stops[i - 1];
    const pts = roadShapes.get(`${prev.stop_id}|${s.stop_id}`)
             ?? roadShapes.get(`${s.stop_id}|${prev.stop_id}`)?.slice().reverse();
    if (pts) coords.push(...pts.slice(1)); // pts[0] is prev stop, already added
    else coords.push([s.lat, s.lon]);
  });
  return coords;
}

// The distinct stop patterns a route runs (branches, short turns), as arrays of
// stop objects. A pattern already contained in a longer one, in either direction,
// is dropped. Drawing only the longest trip left branch stops such as Lopra,
// Sandvík and Selatrað off the network map. Trips are split at impossible hops
// (bad_hop, see gtfs-loader.js) so a corrupted timetable tail isn't drawn as
// part of the route.
function getRoutePatterns(routeId, tripById, stopTimesForTrip, stopById) {
  const unique = new Map(); // "id|id|…" → stop_id[]
  for (const [trip_id, trip] of tripById) {
    if (trip.route_id !== routeId) continue;
    let run = [];
    const flush = () => { if (run.length >= 2) unique.set(run.join('|'), run); run = []; };
    for (const s of stopTimesForTrip.get(trip_id) || []) {
      if (!stopById.has(s.stop_id)) continue;
      if (s.bad_hop) flush();
      run.push(s.stop_id);
    }
    flush();
  }

  const key = ids => `|${ids.join('|')}|`;
  const kept = [];
  for (const ids of [...unique.values()].sort((a, b) => b.length - a.length)) {
    const fwd = key(ids);
    const rev = key([...ids].reverse());
    if (!kept.some(k => key(k).includes(fwd) || key(k).includes(rev))) kept.push(ids);
  }
  return kept.map(ids => ids.map(id => stopById.get(id)));
}

// ── Network map ───────────────────────────────────────────────────────────────
const HALO_OPACITY   = 0.85;
const DIMMED_OPACITY = 0.15;
const STOP_STYLE     = { radius: 4, fillColor: '#fff', color: '#0D0D0D', weight: 1.5, opacity: 1, fillOpacity: 1 };
const STOP_HOVER_TIP = { direction: 'top', offset: [0, -4] };
const STOP_LABEL_TIP = { direction: 'top', offset: [0, -6], permanent: true, className: 'map-stop-label' };

// Draws every route and stop. Clicking a line highlights its whole route (others
// fade, its stops get labels) and calls onRouteSelect(route, patterns), where
// patterns are the route's stop sequences, longest first; clicking it again, the
// empty map or Esc clears it and calls onRouteSelect(null).
// Returns { clearSelection }.
export function initNetworkMap(gtfs, { onRouteSelect = () => {} } = {}) {
  const { stopById, routeById, tripById, stopTimesForTrip, roadShapes } = gtfs;

  // One canvas renderer for every network layer; its click tolerance makes the
  // thin bus lines easy to hit.
  const renderer = L.canvas({ tolerance: 6 });

  const map = L.map('network-map', {
    zoomControl: true,
    minZoom: 7,
    maxZoom: 16,
  }).setView([62.0, -6.95], 9);

  addBaseLayer(map);

  const allCoords = [];
  const servedStops = new Map();      // stop_id → stop, every stop on any drawn pattern
  const patternsByRoute = new Map();  // route_id → stop[][] (longest first)
  const lines = [];                   // { route, coords, style, halo, core }

  for (const [route_id, route] of routeById) {
    const style = NETWORK_LINE_STYLES[routeCategory(route)];
    const patterns = getRoutePatterns(route_id, tripById, stopTimesForTrip, stopById);
    patternsByRoute.set(route_id, patterns);
    for (const stopObjs of patterns) {
      const coords = stopPath(stopObjs, roadShapes);
      lines.push({ route, coords, style });
      coords.forEach(c => allCoords.push(c));
      stopObjs.forEach(s => servedStops.set(s.stop_id, s));
    }
  }
  lines.sort((a, b) => a.style.order - b.style.order);

  // White halos all go beneath every colored line: they lift the routes off the
  // base map without cutting through the wider lines stacked underneath.
  for (const line of lines) {
    line.halo = L.polyline(line.coords, {
      renderer, interactive: false,
      color: '#fff', weight: line.style.weight + 3, opacity: HALO_OPACITY,
      lineCap: 'round', lineJoin: 'round',
    }).addTo(map);
  }

  // Colored lines: express (widest, bottom) → airport → bus (thinnest) → ferry
  for (const line of lines) {
    const { route } = line;
    line.core = L.polyline(line.coords, {
      renderer, bubblingMouseEvents: false,
      color: routeColor(route), weight: line.style.weight, opacity: 1,
      lineCap: 'round', lineJoin: 'round',
      dashArray: line.style.dashArray ?? null,
    })
      .bindTooltip(
        route.route_long_name, // already starts with the route number
        { sticky: true, direction: 'top' }
      )
      .on('click', () => select(selectedId === route.route_id ? null : route.route_id))
      .addTo(map);
  }

  // Draw every stop that appears on at least one route
  const markers = [];
  for (const stop of servedStops.values()) {
    const marker = L.circleMarker([stop.lat, stop.lon], { renderer, bubblingMouseEvents: false, ...STOP_STYLE })
      .bindTooltip(stop.stop_name, STOP_HOVER_TIP)
      .addTo(map);
    markers.push({ marker, stop });
  }

  if (allCoords.length) {
    map.fitBounds(allCoords, { padding: [40, 40] });
  }

  // ── Route highlight ─────────────────────────────────────────────────────────
  let selectedId = null;

  function select(routeId) {
    selectedId = routeId;
    const route = routeId ? routeById.get(routeId) : null;
    const isSelected = line => line.route.route_id === routeId;

    for (const line of lines) {
      const on = !route || isSelected(line);
      line.halo.setStyle({ opacity: on ? HALO_OPACITY : DIMMED_OPACITY });
      line.core.setStyle({
        opacity: on ? 1 : DIMMED_OPACITY,
        weight: route && on ? Math.max(line.style.weight, 5) : line.style.weight,
      });
    }

    // Restore the normal stacking, lift the selected route above it, stops on top
    for (const line of lines) line.halo.bringToFront();
    for (const line of lines) line.core.bringToFront();
    if (route) {
      for (const line of lines) if (isSelected(line)) line.halo.bringToFront();
      for (const line of lines) if (isSelected(line)) line.core.bringToFront();
    }

    const routeStops = route ? new Set(patternsByRoute.get(routeId).flat().map(s => s.stop_id)) : null;
    for (const { marker, stop } of markers) {
      const onRoute = routeStops?.has(stop.stop_id);
      marker.setStyle(
        onRoute ? { ...STOP_STYLE, radius: 6, color: routeColor(route), weight: 2.5 }
        : route ? { ...STOP_STYLE, opacity: DIMMED_OPACITY * 2, fillOpacity: DIMMED_OPACITY * 2 }
        : STOP_STYLE
      );
      marker.unbindTooltip().bindTooltip(stop.stop_name, onRoute ? STOP_LABEL_TIP : STOP_HOVER_TIP);
      marker.bringToFront();
    }

    if (route) {
      const bounds = L.latLngBounds(lines.filter(isSelected).flatMap(l => l.coords));
      // extra bottom padding keeps the route clear of the info panel
      map.fitBounds(bounds, { paddingTopLeft: [50, 50], paddingBottomRight: [50, 170], maxZoom: 12 });
      onRouteSelect(route, patternsByRoute.get(routeId));
    } else {
      onRouteSelect(null);
    }
  }

  map.on('click', () => { if (selectedId) select(null); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && selectedId) select(null); });

  return { clearSelection: () => select(null) };
}

// ── Journey map ───────────────────────────────────────────────────────────────
let leafletMap = null;
let mapLayers  = [];

function ensureMap() {
  if (leafletMap) return;
  leafletMap = L.map('journey-map', { zoomControl: true }).setView([62.0, -6.95], 9);
  addBaseLayer(leafletMap);
}

export function updateMap(legs, stopById, routeById, roadShapes = new Map()) {
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
    const coords = stopPath(allStopIds.map(id => stopById.get(id)).filter(Boolean), roadShapes);

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
