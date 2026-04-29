// app.js
// UI wiring: loads GTFS data, populates dropdowns, handles journey planner form,
// renders results, and initialises the network map and route browser.

import { loadGTFS }          from './gtfs-loader.js';
import { initNetworkMap, updateMap } from './map.js';
import { findJourneys, nextDepartures } from './csa.js';

// ssl.fo timetable URL slugs (keyed by route_id)
const ROUTE_SLUGS = {
  '7':   'ferry/7-suduroy-torshavn',
  '36':  'ferry/36-soervagur-mykines',
  '56':  'ferry/56-klaksvik-kalsoy',
  '58':  'ferry/58-hvannasund-hattarvik',
  '61':  'ferry/61-gamlaraett-hestur',
  '66':  'ferry/66-sandur-skuvoy',
  '90':  'ferry/90-torshavn-nolsoy',
  '100': 'bus/100-torshavn-vestmanna',
  '200': 'bus/200-oyrarbakki-eidi',
  '201': 'bus/201-oyrarbakki-gjogv',
  '202': 'bus/202-oyrarbakki-tjoernuvik',
  '222': 'bus/222-kollafjardadalur-effo-oyrarbakki',
  '223': 'bus/223-sundalagid-kambsdalur',
  '300': 'bus/300-torshavn-airport-soervagur',
  '350': 'bus/350-torshavn-vaga-airport-soervagurboe',
  '400': 'bus/400-klaksvik-torshavn',
  '401': 'bus/401-klaksvik-torshavn-express-bus-valid-from-dec-1',
  '410': 'bus/410-fuglafj-goetudalur-klaksvik',
  '440': 'bus/440-skalafjardarleidin',
  '442': 'bus/442-glyvrar-aeduvik-rituvik',
  '444': 'bus/444-kambsdalur-skalafjoerdurtoftir',
  '450': 'bus/450-torshavn-eysturoy-jellyfish-roundabout',
  '481': 'bus/481-skalafjoerdur-oyndarfjoerdur',
  '500': 'bus/500-klaksvik-vidareidi',
  '504': 'bus/504-kunoy-klaksvik',
  '506': 'bus/506-troellanes-sydradalur',
  '600': 'bus/600-skopun-sandur',
  '601': 'bus/601-dalur-husavik-skalavik-sandur',
  '650': 'bus/650-sandoy-torshavn',
  '700': 'bus/700-sumba-vagur-tvoeroyri',
  '701': 'bus/701-famjin-tvoeroyri-sandvik',
};

const BASE_URL = 'https://www.ssl.fo/en/timetable/';

const ROUTE_TYPE_LABEL = {
  3:   'Bus',
  4:   'Ferry',
  700: 'Express / Airport Bus',
};

function routeTypeClass(route) {
  if (route.route_type === 4) return 'ferry';
  if (route.route_type === 700) {
    // distinguish express vs airport by color
    if (route.route_color === 'd48dff') return 'express';
    return 'airport';
  }
  return 'bus';
}

// ── Date / time helpers (kept identical to the original index.html) ───────────
function pad2(n) { return String(n).padStart(2, '0'); }

function setNow() {
  const d = new Date();
  document.getElementById('travel-date').value =
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  document.getElementById('travel-time').value =
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function getTravelTime() {
  const dateStr = document.getElementById('travel-date').value;
  const timeStr = document.getElementById('travel-time').value;
  if (!dateStr || !timeStr) return new Date();
  const [y, mo, da] = dateStr.split('-').map(Number);
  const [h, mi]     = timeStr.split(':').map(Number);
  return new Date(y, mo - 1, da, h, mi);
}

// ── Departure rendering ───────────────────────────────────────────────────────
const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function renderDeparturePills(deps, minsToHHMM) {
  if (!deps || !deps.length) {
    return '<div class="no-sched">No upcoming departures found</div>';
  }
  const now = getTravelTime();
  const todayMins = now.getHours() * 60 + now.getMinutes();

  return deps.map((d, i) => {
    const diff = d.dep_mins - todayMins;
    let dayTag = '';
    if (diff < 0 || diff > 1440) {
      // next day territory — show day name
      const futureDate = new Date(now);
      futureDate.setMinutes(futureDate.getMinutes() + Math.max(diff, 0));
      dayTag = `<span class="dep-day">${DAY_NAMES[futureDate.getDay()]}</span>`;
    }
    return `<span class="dep-time${i === 0 ? ' next' : ''}">${minsToHHMM(d.dep_mins)}${dayTag}</span>`;
  }).join('');
}

// ── Journey result rendering ──────────────────────────────────────────────────
function renderJourneyCard(legs, fromName, toName, gtfs, isActive) {
  const { stopById, routeById, connections, tripById, calendarMap, exceptionMap, minsToHHMM } = gtfs;
  const date = getTravelTime();

  const transfers = legs.length - 1;

  const arrMins = legs[legs.length - 1].arr_mins;
  const depMins = legs[0].dep_mins;
  const totalMins = arrMins - depMins;
  const durationStr = totalMins >= 60
    ? `${Math.floor(totalMins / 60)}h ${totalMins % 60}m`
    : `${totalMins}m`;
  const xferLabel = transfers === 0 ? 'Direct' : transfers + ' transfer' + (transfers > 1 ? 's' : '');
  const xferCls   = transfers > 0 ? ' has-xfer' : '';

  // Route badges for collapsed header (one per leg)
  const routeBadges = legs.map(leg => {
    const route = routeById.get(leg.route_id);
    const cls   = route ? routeTypeClass(route) : 'bus';
    return `<span class="jc-badge badge-${cls}">${leg.route_id}</span>`;
  }).join('');

  let html = `<div class="journey-card${isActive ? ' active' : ''}">
  <div class="journey-card-header">
    <div class="jc-time">${minsToHHMM(depMins)} → ${minsToHHMM(arrMins)}</div>
    ${routeBadges}
    <div class="jc-dur">${durationStr}</div>
    <div class="jc-xfer${xferCls}">${xferLabel}</div>
  </div>
  <div class="journey-card-body">`;

  legs.forEach((leg, i) => {
    const route   = routeById.get(leg.route_id);
    const typeCls = route ? routeTypeClass(route) : 'bus';
    const label   = route ? (ROUTE_TYPE_LABEL[route.route_type] || 'Bus') : 'Bus';
    const slug    = ROUTE_SLUGS[leg.route_id] || '';

    const fromStop = stopById.get(leg.from_stop);
    const toStop   = stopById.get(leg.to_stop);
    const fromName = fromStop ? fromStop.stop_name : leg.from_stop;
    const toName   = toStop   ? toStop.stop_name   : leg.to_stop;

    const viaNames = leg.via_stops
      .map(id => { const s = stopById.get(id); return s ? s.stop_name : id; });
    const viaText = viaNames.length ? `via ${viaNames.join(' · ')}` : '';

    // Next departures from this stop on this route
    const deps = nextDepartures(
      leg.route_id, leg.from_stop, leg.to_stop,
      leg.dep_mins, date,
      connections, tripById, calendarMap, exceptionMap,
      4,
    );

    html += `
      <div class="leg">
        <div class="leg-badge badge-${typeCls}">${leg.route_id}</div>
        <div class="leg-body">
          <div class="leg-route">${label} · ${route ? route.route_long_name.replace(/^\d+ /, '') : leg.route_id}</div>
          <div class="leg-direction">
            <strong>${minsToHHMM(leg.dep_mins)}</strong> ${fromName}
            → <strong>${minsToHHMM(leg.arr_mins)}</strong> ${toName}
          </div>
          ${viaText ? `<div class="leg-via">${viaText}</div>` : ''}
          <div class="leg-departures">
            <div class="leg-departures-label">Next departures from ${fromName}</div>
            ${renderDeparturePills(deps, minsToHHMM)}
          </div>
          ${slug ? `<a class="leg-timetable" href="${BASE_URL}${slug}" target="_blank" rel="noopener">Full timetable ↗</a>` : ''}
        </div>
      </div>`;

    if (i < legs.length - 1) {
      const transferStop = stopById.get(leg.to_stop);
      const transferName = transferStop ? transferStop.stop_name : leg.to_stop;
      html += `<div class="transfer-arrow">↕ Transfer at ${transferName}</div>`;
    }
  });

  html += `</div></div>`; // close journey-card-body and journey-card
  return html;
}

// ── Route browser ─────────────────────────────────────────────────────────────
function buildRouteBrowser(routeById) {
  const ferryGrid = document.getElementById('ferry-grid');
  const busGrid   = document.getElementById('bus-grid');

  // Sort routes numerically
  const sorted = [...routeById.values()].sort(
    (a, b) => Number(a.route_id) - Number(b.route_id)
  );

  let fc = 0, bc = 0;
  for (const route of sorted) {
    const slug    = ROUTE_SLUGS[route.route_id] || '';
    const typeCls = routeTypeClass(route);
    const label   = ROUTE_TYPE_LABEL[route.route_type] || 'Bus';
    const isFerry = route.route_type === 4;

    const card = document.createElement('a');
    card.className = 'route-card';
    card.href      = slug ? `${BASE_URL}${slug}` : '#';
    card.target    = '_blank';
    card.rel       = 'noopener noreferrer';
    card.dataset.search = `${route.route_id} ${route.route_long_name}`.toLowerCase();
    card.dataset.type   = typeCls;

    card.innerHTML = `
      <div class="route-badge badge-${typeCls}">${route.route_id}</div>
      <div class="route-info">
        <div class="route-name">${route.route_long_name.replace(/^\d+ /, '')}</div>
        <div class="route-type">${label}</div>
      </div>
      <div class="route-arrow">›</div>`;

    (isFerry ? ferryGrid : busGrid).appendChild(card);
    isFerry ? fc++ : bc++;
  }

  document.getElementById('ferry-count').textContent = fc;
  document.getElementById('bus-count').textContent   = bc;
}

function filterRoutes() {
  const q = document.getElementById('search').value.toLowerCase();
  let fc = 0, bc = 0;
  document.querySelectorAll('.route-card').forEach(c => {
    const show = !q || c.dataset.search.includes(q);
    c.classList.toggle('hidden', !show);
    if (show) c.dataset.type === 'ferry' ? fc++ : bc++;
  });
  document.getElementById('ferry-count').textContent = fc;
  document.getElementById('bus-count').textContent   = bc;
  document.getElementById('ferry-section').classList.toggle('hidden', fc === 0);
  document.getElementById('bus-section').classList.toggle('hidden', bc === 0);
  document.getElementById('no-results').classList.toggle('hidden', fc + bc > 0);
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  setNow();

  const loadingEl = document.getElementById('gtfs-loading');

  let gtfs;
  try {
    gtfs = await loadGTFS();
  } catch (err) {
    if (loadingEl) loadingEl.textContent = 'Failed to load schedule data. Please refresh.';
    console.error('GTFS load failed:', err);
    return;
  }

  if (loadingEl) loadingEl.remove();

  const { stopById, routeById, connections, tripById, calendarMap, exceptionMap } = gtfs;

  // ── Populate dropdowns ───────────────────────────────────────────────────
  const fromSel = document.getElementById('from-stop');
  const toSel   = document.getElementById('to-stop');

  const sortedStops = [...stopById.values()].sort((a, b) =>
    a.stop_name.localeCompare(b.stop_name)
  );

  for (const stop of sortedStops) {
    for (const sel of [fromSel, toSel]) {
      const opt = document.createElement('option');
      opt.value = stop.stop_id;
      opt.textContent = stop.stop_name;
      sel.appendChild(opt);
    }
  }

  // ── Journey planner ──────────────────────────────────────────────────────
  function planJourney() {
    const fromId = fromSel.value;
    const toId   = toSel.value;
    const out    = document.getElementById('journey-result');

    if (!fromId || !toId) {
      out.innerHTML = '<div class="journey-error">Please select both a start and destination.</div>';
      return;
    }
    if (fromId === toId) {
      out.innerHTML = '<div class="journey-error">Start and destination are the same.</div>';
      return;
    }

    const date = getTravelTime();
    const journeyList = findJourneys(fromId, toId, date, connections, tripById, calendarMap, exceptionMap, 5);

    const fromStop = stopById.get(fromId);
    const toStop   = stopById.get(toId);
    const fromName = fromStop ? fromStop.stop_name : fromId;
    const toName   = toStop   ? toStop.stop_name   : toId;

    if (!journeyList.length) {
      out.innerHTML = `<div class="journey-error">
        No service found from <strong>${fromName}</strong> to <strong>${toName}</strong>
        at the selected time. Try a different time or check if these stops are connected.
      </div>`;
      document.getElementById('journey-map').classList.add('hidden');
      return;
    }

    out.innerHTML = journeyList
      .map((legs, i) => renderJourneyCard(legs, fromName, toName, gtfs, i === 0))
      .join('');

    // Map shows the first (active) journey
    updateMap(journeyList[0], stopById, routeById);

    // Click-to-expand: clicking a card makes it active, updates the map
    out.querySelectorAll('.journey-card').forEach((card, i) => {
      card.addEventListener('click', () => {
        out.querySelectorAll('.journey-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        updateMap(journeyList[i], stopById, routeById);
      });
    });
  }

  // Wire up the button and the "Now" button
  document.querySelector('.find-btn').addEventListener('click', planJourney);
  document.querySelector('.now-btn').addEventListener('click', setNow);
  document.getElementById('swap-btn').addEventListener('click', () => {
    const tmp = fromSel.value;
    fromSel.value = toSel.value;
    toSel.value = tmp;
  });

  // ── Route browser ────────────────────────────────────────────────────────
  buildRouteBrowser(routeById);
  document.getElementById('search').addEventListener('input', filterRoutes);

  // ── Network map ──────────────────────────────────────────────────────────
  initNetworkMap(gtfs);
}

main();
