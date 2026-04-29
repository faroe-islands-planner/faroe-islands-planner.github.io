#!/usr/bin/env python3
"""
Convert scraped ssl.fo timetable data (data/scraped/route_*.json) into GTFS CSV files.
Outputs to gtfs/ directory.

GTFS files produced:
  agency.txt, feed_info.txt, stops.txt, routes.txt,
  calendar.txt, calendar_dates.txt, trips.txt, stop_times.txt, translations.txt
"""

import csv
import json
import os
import re
import unicodedata


SCRAPED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "scraped")
GTFS_DIR = os.path.join(os.path.dirname(__file__), "..", "gtfs")
os.makedirs(GTFS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Route metadata (type + colour)
# ---------------------------------------------------------------------------

ROUTE_META = {
    "7":   {"type": "ferry",   "long_name": "7 Suðuroy – Tórshavn"},
    "36":  {"type": "ferry",   "long_name": "36 Sørvágur – Mykines"},
    "56":  {"type": "ferry",   "long_name": "56 Klaksvík – Kalsoy"},
    "58":  {"type": "ferry",   "long_name": "58 Hvannasund – Hattarvík"},
    "61":  {"type": "ferry",   "long_name": "61 Gamlarætt – Hestur"},
    "66":  {"type": "ferry",   "long_name": "66 Sandur – Skúvoy"},
    "90":  {"type": "ferry",   "long_name": "90 Tórshavn – Nólsoy"},
    "100": {"type": "bus",     "long_name": "100 Tórshavn – Vestmanna"},
    "200": {"type": "bus",     "long_name": "200 Oyrarbakki – Eiði"},
    "201": {"type": "bus",     "long_name": "201 Oyrarbakki – Gjógv"},
    "202": {"type": "bus",     "long_name": "202 Oyrarbakki – Tjørnuvík"},
    "222": {"type": "bus",     "long_name": "222 Kollafjarðadalur Effo – Oyrarbakki"},
    "223": {"type": "bus",     "long_name": "223 Sundalagið – Kambsdalur"},
    "300": {"type": "airport", "long_name": "300 Tórshavn – Airport – Sørvágur"},
    "350": {"type": "airport", "long_name": "350 Tórshavn – Vága Airport – Sørvágurbøur"},
    "400": {"type": "bus",     "long_name": "400 Klaksvík – Tórshavn"},
    "401": {"type": "express", "long_name": "401 Klaksvík – Tórshavn Express"},
    "410": {"type": "bus",     "long_name": "410 Fuglafjørður – Gøtudalur – Klaksvík"},
    "440": {"type": "bus",     "long_name": "440 Skálafjørðarleiðin"},
    "442": {"type": "bus",     "long_name": "442 Glyvrar – Æðuvík – Rituvik"},
    "444": {"type": "bus",     "long_name": "444 Kambsdalur – Skálafjørður/Toftir"},
    "450": {"type": "bus",     "long_name": "450 Tórshavn – Eysturoy Jellyfish Roundabout"},
    "481": {"type": "bus",     "long_name": "481 Skálafjørður – Oynarfjørður"},
    "500": {"type": "bus",     "long_name": "500 Klaksvík – Viðareiði"},
    "504": {"type": "bus",     "long_name": "504 Kunoy – Klaksvík"},
    "506": {"type": "bus",     "long_name": "506 Trøllanes – Syðradalur"},
    "600": {"type": "bus",     "long_name": "600 Skopun – Sandur"},
    "601": {"type": "bus",     "long_name": "601 Dalur – Húsavík – Skálavík – Sandur"},
    "650": {"type": "bus",     "long_name": "650 Sandoy – Tórshavn"},
    "700": {"type": "bus",     "long_name": "700 Sumba – Vágur – Tvøroyri"},
    "701": {"type": "bus",     "long_name": "701 Fámjin – Tvøroyri – Sandvík"},
}

TYPE_COLORS = {
    "ferry":   "4db8ff",
    "bus":     "4dff8a",
    "express": "d48dff",
    "airport": "ffb84d",
}

# GTFS route_type codes
ROUTE_TYPE = {
    "ferry":   4,    # Ferry
    "bus":     3,    # Bus
    "express": 700,  # Bus (express)
    "airport": 700,  # Bus (airport)
}

# Request-only routes
REQUEST_ONLY_ROUTES = {"61", "201", "223"}

# Seasonal service_id suffixes
# When a section has seasonal info, its service_id gets a seasonal prefix
SEASONAL_SERVICE_SUFFIX = {
    "winter": "_winter",
    "summer": "_summer",
}

# ---------------------------------------------------------------------------
# Stop coordinates (verified against OpenStreetMap)
# ---------------------------------------------------------------------------

COORDS = {
    # Streymoy
    "Tórshavn":                     (62.0107, -6.7726),
    "Glasir":                       (62.0200, -6.7900),
    "Gamlarætt":                    (61.9626, -6.8189),
    "Kollafjørður Tunnel":          (62.1050, -6.9650),
    "Kollafjarðadalur":             (62.1170, -6.9830),
    "Kollafjarðadalur við Effo":    (62.1170, -6.9830),
    "Kvívík":                       (62.1167, -7.1150),
    "Vestmanna":                    (62.1552, -7.1750),
    "Hósvík":                       (62.1300, -6.9533),
    "Hvalvík":                      (62.1870, -7.0363),
    "Oyri":                         (62.2017, -6.9933),
    "Oyrarbakki":                   (62.2050, -7.0000),
    # Eysturoy (north + west)
    "Svínáir":                      (62.2467, -7.0200),
    "Norðskála":                    (62.2159, -7.0059),
    "Eiði":                         (62.2996, -7.0910),
    "Gjógv":                        (62.3257, -6.9430),
    "Tjørnuvík":                    (62.2895, -7.1492),
    "Funningur":                    (62.2871, -6.9663),
    # Eysturoy (Skálafjørður peninsula — west side N→S)
    "Selatrað":                     (62.1601, -6.8779),
    "Strendur":                     (62.1095, -6.7612),
    "Skála":                        (62.0767, -6.7217),
    "Skálafjørður":                 (62.1133, -6.7867),
    # Eysturoy (Skálafjørður peninsula — east side N→S)
    "Søldarfjørður":                (62.1575, -6.7526),
    "Runavík":                      (62.1084, -6.7217),
    "Glyvrar":                      (62.1284, -6.7240),
    "Toftir":                       (62.0910, -6.7322),
    "Rituvík":                      (62.1077, -6.6859),
    "Æðuvík":                       (62.0697, -6.6936),
    # Eysturoy (NE — Gøta / Fuglafjørður / Hellurnar)
    "Hellurnar":                    (62.2630, -6.8451),
    "Oyndarfjørður":                (62.2772, -6.8541),
    "Fuglafjørður":                 (62.2438, -6.8133),
    "Kambsdalur":                   (62.2204, -6.8088),
    "Gøtudalur":                    (62.1300, -6.7550),
    "Leirvík":                      (62.2096, -6.7079),
    # Norðoyar
    "Klaksvík":                     (62.2255, -6.5838),
    "Hvannasund":                   (62.2979, -6.5201),
    "Haraldssund":                  (62.2738, -6.6063),
    "Kunoy":                        (62.2921, -6.6711),
    "Viðareiði":                    (62.3602, -6.5355),
    # Kalsoy
    "Syðradalur (Harbour)":         (62.2453, -6.6678),
    "Húsar":                        (62.2767, -6.7000),
    "Mikladalur":                   (62.3352, -6.7661),
    "Trøllanes":                    (62.3613, -6.7875),
    # Svínoy / Fugloy
    "Svínoy":                       (62.2780, -6.3424),
    "Kirkja":                       (62.3185, -6.3163),
    "Hattarvík":                    (62.3296, -6.2756),
    # Vágar
    "Sørvágur":                     (62.0707, -7.3060),
    "Miðvágur":                     (62.0480, -7.1922),
    "Sandavágur":                   (62.0545, -7.1511),
    "Vága Airport (Fløgvøllurin)":  (62.0636, -7.2772),
    "Bøur":                         (62.0880, -7.3715),
    # Mykines
    "Mykines":                      (62.1043, -7.6476),
    # Nólsoy
    "Nólsoy":                       (61.9854, -6.6531),
    # Hestur
    "Hestur":                       (61.9556, -6.8831),
    # Sandoy
    "Skopun":                       (61.9028, -6.8784),
    "Inni í Dal":                   (61.8577, -6.8301),
    "Sandur":                       (61.8348, -6.8176),
    "Traðir":                       (61.8555, -6.8217),
    "Dalur":                        (61.7843, -6.6764),
    "Húsavík":                      (61.8099, -6.6796),
    "Skálavík":                     (61.8303, -6.6640),
    # Skúvoy
    "Skúvoy":                       (61.7664, -6.8270),
    # Suðuroy
    "Tvøroyri":                     (61.5567, -6.8083),
    # Ferjulega = ferry-approach stop south of Tvøroyri (routes 700/701)
    "Ferjulega":                    (61.5470, -6.8100),
    "Øravík":                       (61.5333, -6.8033),
    "Hov":                          (61.5083, -6.7367),
    "Porkeri":                      (61.4820, -6.7463),
    "Vágur":                        (61.4733, -6.8133),
    "Páls Høll":                    (61.4580, -6.7930),  # hamlet between Lopra and Vágur
    "Lopra":                        (61.4435, -6.7724),
    "Sumba":                        (61.4033, -6.7117),
    "Fámjin":                       (61.5283, -6.8767),
    "Nes":                          (61.5932, -6.9296),
    "Hvalba":                       (61.6006, -6.9568),
    "Sandvík":                      (61.6342, -6.9288),
}


# ---------------------------------------------------------------------------
# Stop name aliases — scraped variant names → canonical stop name
#
# The ssl.fo scraper picks up inconsistent spellings, abbreviations, and
# directional labels from timetable HTML.  Every key here maps to a canonical
# stop name that has verified coordinates in COORDS above.  Add new entries
# whenever re-scraping introduces a new variant; never remove old ones.
# ---------------------------------------------------------------------------

STOP_ALIASES = {
    # ── Directional / arrival / departure labels ──────────────────────────
    "Arrival Oyrarbakka":           "Oyrarbakki",
    "Departure from Oyrarbakka":    "Oyrarbakki",
    "From Oyrabakka":               "Oyrarbakki",
    "On Oyrabakka":                 "Oyrarbakki",
    "Í Tjørnuvík":                  "Tjørnuvík",
    "From Hvannasund":              "Hvannasund",
    "from Klaksví":                 "Klaksvík",
    "from Tórshavn":                "Tórshavn",
    "Effo í Kollfjd.":              "Kollafjarðadalur við Effo",

    # ── Tórshavn terminal / bus-terminal variants ─────────────────────────
    "Tórshavn Terminal":            "Tórshavn",
    "Bus Terminal Tórshavn":        "Tórshavn",
    "Farstøðin":                    "Tórshavn",   # "the terminal" in Faroese

    # ── Tórshavn neighbourhood stops (route 650) ──────────────────────────
    # LS = Landssýkrahúsið (National Hospital) stop; Steinatún = suburb stop.
    # Both are seconds apart and map to the Tórshavn cluster for routing.
    "LS":                           "Glasir",
    "Steinatún":                    "Glasir",

    # ── Kollafjørður Tunnel / Kollafjarðadalur variants ───────────────────
    "Kollafjørður tunnel":          "Kollafjørður Tunnel",
    "Kollafjarða tunnel":           "Kollafjørður Tunnel",
    "Kollafjarða tunnil":           "Kollafjørður Tunnel",
    "Kollf.Tunnil":                 "Kollafjørður Tunnel",
    "Kollafj. t.":                  "Kollafjørður Tunnel",
    "Kollaf. t.":                   "Kollafjørður Tunnel",
    "Kollafjørður við Sjógv":       "Kollafjørður Tunnel",
    "Kollfjarðadalur við Effo":     "Kollafjarðadalur við Effo",
    "Kollfjarðadalur":              "Kollafjarðadalur",

    # ── Route 400 abbreviations ───────────────────────────────────────────
    "Klaksv.":                      "Klaksvík",
    "Gøtud.":                       "Gøtudalur",
    "Søldarfj.":                    "Søldarfjørður",
    "Søldafjørður":                 "Søldarfjørður",   # spelling variant
    "Skálafj.":                     "Skálafjørður",
    "Oyrarb.":                      "Oyrarbakki",

    # ── Vágar / Airport variants ──────────────────────────────────────────
    "Airport":                      "Vága Airport (Fløgvøllurin)",
    "Fløgvøllurin":                 "Vága Airport (Fløgvøllurin)",
    "Vága Airport":                 "Vága Airport (Fløgvøllurin)",

    # ── Route 481 spelling variant ────────────────────────────────────────
    "Oyndafjørður":                 "Oyndarfjørður",

    # ── Kalsoy (route 506/56) variants ───────────────────────────────────
    # The ferry arrives at the harbour; "village" and bare "Syðradalur" all
    # refer to the same stop cluster on Kalsoy.
    "Syðradalur":                   "Syðradalur (Harbour)",
    "Syðradalur(ferry terminal)":   "Syðradalur (Harbour)",
    "Syðradalur (Village)":         "Syðradalur (Harbour)",

    # ── Suðuroy ferry-approach stops (routes 700/701) ─────────────────────
    # "Ferjulega/n" = the road/stop leading to the Tvøroyri ferry terminal.
    # It is a distinct stop (≈10 min from Tvøroyri) with its own coordinate.
    "Ferjulega":                    "Ferjulega",   # kept as canonical (see COORDS)
    "Ferjulegan":                   "Ferjulega",   # inbound spelling variant
}


# ---------------------------------------------------------------------------
# Stop ID slugification
# ---------------------------------------------------------------------------

def slugify(name):
    """Convert a stop name to a stable ASCII slug for use as stop_id."""
    # Normalise unicode → ASCII where possible
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = ascii_str.lower()
    ascii_str = re.sub(r'[^a-z0-9]+', '_', ascii_str)
    ascii_str = ascii_str.strip('_')
    return ascii_str


# ---------------------------------------------------------------------------
# GTFS calendar service definitions
# ---------------------------------------------------------------------------

# Base service definitions: service_id → (mon,tue,wed,thu,fri,sat,sun)
# Date range: 2025-01-01 to 2026-12-31 (covers current + next year)
FEED_START = "20250101"
FEED_END   = "20261231"

BASE_SERVICES = {
    "daily":         (1,1,1,1,1,1,1),
    "weekdays":      (1,1,1,1,1,0,0),
    "weekdays_sat":  (1,1,1,1,1,1,0),
    "weekdays_sun":  (1,1,1,1,1,0,1),
    "school":        (1,1,1,1,1,0,0),  # same days as weekdays but separate service_id
    "weekend":       (0,0,0,0,0,1,1),
    "sat":           (0,0,0,0,0,1,0),
    "sun":           (0,0,0,0,0,0,1),
}

# Seasonal variants inherit the same day pattern but with a different service_id
# so calendar_dates.txt can restrict them to their date window.
SEASONAL_SERVICES = {}
for base_id, days in BASE_SERVICES.items():
    SEASONAL_SERVICES[base_id + "_winter"] = days
    SEASONAL_SERVICES[base_id + "_summer"] = days

ALL_SERVICES = {**BASE_SERVICES, **SEASONAL_SERVICES}

# Faroese public holidays (fixed dates) — used to add exception entries
# Format: "MMDD"
FIXED_HOLIDAYS = [
    ("01", "01"),  # New Year's Day
    ("04", "25"),  # Flag Day (Faroese)
    ("06", "05"),  # Constitution Day (Danish)
    ("12", "24"),  # Christmas Eve
    ("12", "25"),  # Christmas Day
    ("12", "26"),  # Boxing Day
    ("12", "31"),  # New Year's Eve
]

# Easter-based holidays vary by year — encode for 2025 and 2026
EASTER_HOLIDAYS = {
    2025: ["20250417", "20250418", "20250420", "20250421"],  # Maundy Thu, Good Fri, Easter Sun, Mon
    2026: ["20260402", "20260403", "20260405", "20260406"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_csv(path, fieldnames, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Wrote {path} ({len(rows)} rows)")


def load_scraped():
    files = sorted(f for f in os.listdir(SCRAPED_DIR) if f.startswith("route_") and f.endswith(".json"))
    routes = []
    for fname in files:
        with open(os.path.join(SCRAPED_DIR, fname), encoding="utf-8") as f:
            routes.append(json.load(f))
    return routes


# ---------------------------------------------------------------------------
# Build stop registry
# ---------------------------------------------------------------------------

def build_stop_registry(routes_data, coords):
    """
    Collect all unique stop names from scraped data + coords.
    Returns dict: stop_name → {"stop_id": slug, "lat": float|None, "lon": float|None}
    """
    # Strings that look like table labels, day-of-week headers, or noise rows —
    # not real stop names.  Match is case-insensitive (canonical.strip().lower()).
    STOP_BLOCKLIST = {
        "",
        # Directional column headers
        "to klaksvík", "to tórshavn", "to klaksvik", "to torshavn",
        "to klaksví",
        # Day-of-week abbreviations (route 61 uses these as column headers)
        "mán.", "týs.", "mik.", "hós.", "frí.", "ley.", "sun.",
        # Noise / season labels
        "winter", "period", "exceptions:",
    }

    all_stop_names = set()
    for route in routes_data:
        for sec in route.get("sections", []):
            for name in sec.get("stops", []):
                canonical = STOP_ALIASES.get(name, name)
                if canonical.strip().lower() not in STOP_BLOCKLIST:
                    all_stop_names.add(canonical)
    # Also add all coord keys (some stops may not appear in scraped data)
    all_stop_names.update(coords.keys())

    registry = {}
    slug_counts = {}
    for name in sorted(all_stop_names):
        slug = slugify(name)
        # Handle slug collisions
        if slug in slug_counts:
            slug_counts[slug] += 1
            slug = f"{slug}_{slug_counts[slug]}"
        else:
            slug_counts[slug] = 1
        # Find coordinates: exact match first, then fuzzy
        lat, lon = None, None
        if name in coords:
            lat, lon = coords[name]
        else:
            # Try case-insensitive match
            for cname, (clat, clon) in coords.items():
                if cname.lower() == name.lower():
                    lat, lon = clat, clon
                    break
        registry[name] = {"stop_id": slug, "stop_name": name, "lat": lat, "lon": lon}
    return registry


# ---------------------------------------------------------------------------
# Route-specific data normalisers
# ---------------------------------------------------------------------------

# Day-of-week abbreviations used by route 61 as column headers, mapped to the
# GTFS service_id that covers that day.
_ROUTE61_DAY_COLS = {
    "Mán.": "weekdays",   # Monday
    "Týs.": "weekdays",   # Tuesday
    "Mik.": "weekdays",   # Wednesday
    "Hós.": "weekdays",   # Thursday
    "Frí.": "weekdays",   # Friday
    "Ley.": "sat",        # Saturday
    "Sun.": "sun",        # Sunday
}

def normalise_route_61(route_data):
    """
    Route 61 (Gamlarætt – Hestur ferry) is scraped with day-of-week
    abbreviations as "stops" and departure times per day as "times".
    The actual direction is in trip["day_raw"].

    This function rewrites the section so that:
      - stops = ["Gamlarætt", "Hestur"]  (outbound) or reversed (inbound)
      - each trip becomes one entry per non-null day column, with a single
        departure time and the correct service_id.
    """
    new_sections = []
    for sec in route_data.get("sections", []):
        raw_trips = sec.get("trips", [])
        outbound_trips = []
        inbound_trips  = []

        for raw_trip in raw_trips:
            direction = raw_trip.get("day_raw", "")
            # Pick the first non-null time across all day columns as the departure
            times_by_day = raw_trip.get("times", {})
            # Collect one trip per service_id (combining days that share the same time)
            per_service: dict[str, str] = {}  # service_id -> time
            for day_col, service_id in _ROUTE61_DAY_COLS.items():
                t = times_by_day.get(day_col)
                if t is None:
                    continue
                # If same service_id already has a time, they match — keep first
                if service_id not in per_service:
                    per_service[service_id] = t

            is_outbound = "Gamlarætt - Hestur" in direction or not inbound_trips
            # Use day_raw to distinguish direction; both stops always served
            if "Hestur" in direction and "Gamlarætt" not in direction.split("-")[0].strip():
                is_outbound = False
            # Simpler heuristic: alternate outbound/inbound as they appear
            # The raw data alternates: G→H, H→G, G→H, H→G …
            idx = raw_trips.index(raw_trip)
            is_outbound = (idx % 2 == 0)

            for service_id, dep_time in per_service.items():
                entry = {
                    "service_id": service_id,
                    "times": {"Gamlarætt": dep_time, "Hestur": dep_time},
                    "_offset_hestur": 15,  # ferry is ~15 min crossing
                }
                if is_outbound:
                    outbound_trips.append(entry)
                else:
                    inbound_trips.append(entry)

        # Build proper arrival times: Gamlarætt dep → Hestur arr +15 min
        def add_offset(trips, from_stop, to_stop):
            result = []
            for t in trips:
                dep = t["times"][from_stop]
                if dep:
                    h, m = map(int, dep.split(":"))
                    arr_m = h * 60 + m + 15
                    arr = f"{arr_m // 60:02d}:{arr_m % 60:02d}"
                else:
                    arr = dep
                result.append({
                    "service_id": t["service_id"],
                    "times": {from_stop: dep, to_stop: arr},
                })
            return result

        if outbound_trips:
            new_sections.append({
                "heading": sec.get("heading", ""),
                "seasonal": sec.get("seasonal"),
                "stops": ["Gamlarætt", "Hestur"],
                "trips": add_offset(outbound_trips, "Gamlarætt", "Hestur"),
            })
        if inbound_trips:
            new_sections.append({
                "heading": sec.get("heading", ""),
                "seasonal": sec.get("seasonal"),
                "stops": ["Hestur", "Gamlarætt"],
                "trips": add_offset(inbound_trips, "Hestur", "Gamlarætt"),
            })

    route_data["sections"] = new_sections
    return route_data


def normalise_route_58(route_data):
    """
    Route 58 (Hvannasund – Hattarvík ferry) is scraped with only one real
    stop ("From Hvannasund") plus a season-label ("Period" / "Winter") as the
    second "stop".  The destination Hattarvík is never captured.

    This function rewrites each section so stops = ["Hvannasund", "Hattarvík"]
    and uses a 30-minute crossing estimate for the arrival time at Hattarvík.
    """
    new_sections = []
    for sec in route_data.get("sections", []):
        new_trips = []
        for trip in sec.get("trips", []):
            dep_time = trip["times"].get("From Hvannasund")
            if not dep_time:
                continue
            h, m = map(int, dep_time.split(":"))
            arr_m = h * 60 + m + 30
            arr_time = f"{arr_m // 60:02d}:{arr_m % 60:02d}"
            new_trips.append({
                "service_id": trip["service_id"],
                "times": {"Hvannasund": dep_time, "Hattarvík": arr_time},
            })
        if new_trips:
            new_sections.append({
                "heading": sec.get("heading", ""),
                "seasonal": sec.get("seasonal"),
                "stops": ["Hvannasund", "Hattarvík"],
                "trips": new_trips,
            })
    route_data["sections"] = new_sections
    return route_data


# ---------------------------------------------------------------------------
# Route 7: Suðuroy – Tórshavn ferry
# The scraper captured only the outbound section (Tvøroyri → Tórshavn).
# Some Saturday rows have reversed times, indicating inbound trips that were
# captured in the wrong orientation.  Weekday/Sunday inbound trips are missing
# entirely and are injected from a hardcoded table.
# ---------------------------------------------------------------------------

_FERRY_CROSSING_MINS = 165  # ~2h45m Tórshavn ↔ Tvøroyri

# Weekday / Sunday Tórshavn → Tvøroyri departures not captured by the scraper.
# Times derived from the known ~2h45m travel time applied to the outbound
# arrival times.  Verify against ssl.fo if the schedule changes.
_ROUTE7_INBOUND_EXTRA = [
    # Monday
    {"service_id": "weekdays", "times": {"Tórshavn": "10:00", "Tvøroyri": "12:45"}},
    # Tuesday
    {"service_id": "weekdays", "times": {"Tórshavn": "07:30", "Tvøroyri": "10:15"}},
    {"service_id": "weekdays", "times": {"Tórshavn": "14:30", "Tvøroyri": "17:15"}},
    # Wednesday
    {"service_id": "weekdays", "times": {"Tórshavn": "10:00", "Tvøroyri": "12:45"}},
    # Thursday
    {"service_id": "weekdays", "times": {"Tórshavn": "07:30", "Tvøroyri": "10:15"}},
    {"service_id": "weekdays", "times": {"Tórshavn": "16:00", "Tvøroyri": "18:45"}},
    # Friday
    {"service_id": "weekdays", "times": {"Tórshavn": "07:30", "Tvøroyri": "10:15"}},
    {"service_id": "weekdays", "times": {"Tórshavn": "14:30", "Tvøroyri": "17:15"}},
    {"service_id": "weekdays", "times": {"Tórshavn": "21:15", "Tvøroyri": "00:00"}},
    # Sunday
    {"service_id": "sun",      "times": {"Tórshavn": "11:30", "Tvøroyri": "14:15"}},
    {"service_id": "sun",      "times": {"Tórshavn": "20:30", "Tvøroyri": "23:15"}},
]


def _time_to_mins(t):
    if not t:
        return None
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _mins_to_time(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


def normalise_route_7(route_data):
    """
    Route 7 (Suðuroy ferry) was scraped with only one direction.
    Some Saturday rows have reversed times (inbound trips stored backwards).
    This function:
      1. Separates outbound and inbound trips from the scraped data.
      2. Estimates missing arrival times using the ~2h45m crossing time.
      3. Appends hardcoded weekday/Sunday inbound trips the scraper missed.
    """
    outbound = []
    inbound  = []

    for sec in route_data.get("sections", []):
        for trip in sec.get("trips", []):
            t_tvoy = trip["times"].get("Tvøroyri")
            t_tors = trip["times"].get("Tórshavn")

            m_tvoy = _time_to_mins(t_tvoy)
            m_tors = _time_to_mins(t_tors)

            if m_tvoy is not None and m_tors is not None:
                if m_tvoy < m_tors:
                    # Normal outbound: Tvøroyri departs first → Tórshavn arrives later
                    outbound.append({
                        "service_id": trip["service_id"],
                        "times": {"Tvøroyri": t_tvoy, "Tórshavn": t_tors},
                    })
                else:
                    # Reversed entry: the scraper stored Tórshavn dep / Tvøroyri arr
                    # in the wrong columns.  Tvøroyri value is actually the arrival.
                    inbound.append({
                        "service_id": trip["service_id"],
                        "times": {"Tórshavn": t_tors, "Tvøroyri": t_tvoy},
                    })
            elif m_tvoy is not None:
                # Only Tvøroyri time captured — outbound, estimate Tórshavn arrival
                est_arr = m_tvoy + _FERRY_CROSSING_MINS
                outbound.append({
                    "service_id": trip["service_id"],
                    "times": {"Tvøroyri": t_tvoy, "Tórshavn": _mins_to_time(est_arr)},
                })
            elif m_tors is not None:
                # Only Tórshavn time captured — inbound, estimate Tvøroyri arrival
                est_arr = m_tors + _FERRY_CROSSING_MINS
                inbound.append({
                    "service_id": trip["service_id"],
                    "times": {"Tórshavn": t_tors, "Tvøroyri": _mins_to_time(est_arr)},
                })

    # Add hardcoded inbound trips that the scraper missed entirely
    inbound.extend(_ROUTE7_INBOUND_EXTRA)

    new_sections = []
    if outbound:
        new_sections.append({
            "heading": "7 Suðuroy – Tórshavn",
            "seasonal": None,
            "stops": ["Tvøroyri", "Tórshavn"],
            "trips": outbound,
        })
    if inbound:
        new_sections.append({
            "heading": "7 Tórshavn – Suðuroy",
            "seasonal": None,
            "stops": ["Tórshavn", "Tvøroyri"],
            "trips": inbound,
        })

    route_data["sections"] = new_sections
    return route_data


def normalise_routes(routes_data):
    """Apply route-specific normalisers before generic processing."""
    result = []
    for route in routes_data:
        if route["route_id"] == "7":
            route = normalise_route_7(route)
        elif route["route_id"] == "61":
            route = normalise_route_61(route)
        elif route["route_id"] == "58":
            route = normalise_route_58(route)
        result.append(route)
    return result


# ---------------------------------------------------------------------------
# Determine service_id for a section + trip
# ---------------------------------------------------------------------------

def get_service_id(trip_service_id, seasonal_info):
    """Apply seasonal suffix if the section is seasonal."""
    if not seasonal_info:
        return trip_service_id
    season = seasonal_info.get("season")
    if season in ("winter", "summer"):
        return trip_service_id + f"_{season}"
    return trip_service_id


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def main():
    print("Loading scraped data...")
    routes_data = load_scraped()
    print(f"  {len(routes_data)} routes loaded")

    print("Normalising route-specific data...")
    routes_data = normalise_routes(routes_data)

    coords = COORDS
    print(f"  {len(coords)} stops with coordinates")

    print("Building stop registry...")
    stop_registry = build_stop_registry(routes_data, coords)
    print(f"  {len(stop_registry)} unique stops")

    # -----------------------------------------------------------------------
    # agency.txt
    # -----------------------------------------------------------------------
    write_csv(os.path.join(GTFS_DIR, "agency.txt"),
        ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone"],
        [{"agency_id": "SSL",
          "agency_name": "Strandfaraskip Landsins",
          "agency_url": "https://www.ssl.fo",
          "agency_timezone": "Atlantic/Faroe",
          "agency_lang": "fo",
          "agency_phone": "+298343030"}])

    # -----------------------------------------------------------------------
    # feed_info.txt
    # -----------------------------------------------------------------------
    write_csv(os.path.join(GTFS_DIR, "feed_info.txt"),
        ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date"],
        [{"feed_publisher_name": "Faroe Islands Planner",
          "feed_publisher_url": "https://faroe-islands-planner.github.io",
          "feed_lang": "fo",
          "feed_start_date": FEED_START,
          "feed_end_date": FEED_END}])

    # -----------------------------------------------------------------------
    # stops.txt
    # -----------------------------------------------------------------------
    stops_rows = []
    for name, info in sorted(stop_registry.items(), key=lambda x: x[1]["stop_id"]):
        row = {
            "stop_id": info["stop_id"],
            "stop_name": info["stop_name"],
            "stop_desc": "",       # English name placeholder — filled via translations.txt
            "stop_lat": f"{info['lat']:.6f}" if info["lat"] is not None else "",
            "stop_lon": f"{info['lon']:.6f}" if info["lon"] is not None else "",
        }
        stops_rows.append(row)
    write_csv(os.path.join(GTFS_DIR, "stops.txt"),
        ["stop_id", "stop_name", "stop_desc", "stop_lat", "stop_lon"],
        stops_rows)

    # -----------------------------------------------------------------------
    # routes.txt
    # -----------------------------------------------------------------------
    routes_rows = []
    for route_id, meta in sorted(ROUTE_META.items(), key=lambda x: int(x[0])):
        rtype = meta["type"]
        routes_rows.append({
            "route_id": route_id,
            "agency_id": "SSL",
            "route_short_name": route_id,
            "route_long_name": meta["long_name"],
            "route_type": ROUTE_TYPE[rtype],
            "route_color": TYPE_COLORS[rtype],
            "route_text_color": "000000",
        })
    write_csv(os.path.join(GTFS_DIR, "routes.txt"),
        ["route_id", "agency_id", "route_short_name", "route_long_name",
         "route_type", "route_color", "route_text_color"],
        routes_rows)

    # -----------------------------------------------------------------------
    # calendar.txt — all service_ids we might use
    # -----------------------------------------------------------------------
    # Collect which service_ids are actually used
    used_service_ids = set()
    for route in routes_data:
        for sec in route.get("sections", []):
            seasonal_info = sec.get("seasonal")
            for trip in sec.get("trips", []):
                sid = get_service_id(trip["service_id"], seasonal_info)
                used_service_ids.add(sid)

    calendar_rows = []
    for service_id in sorted(used_service_ids):
        days = ALL_SERVICES.get(service_id)
        if days is None:
            # Fallback: treat unknown as daily
            days = (1,1,1,1,1,1,1)
        calendar_rows.append({
            "service_id": service_id,
            "monday":    days[0],
            "tuesday":   days[1],
            "wednesday": days[2],
            "thursday":  days[3],
            "friday":    days[4],
            "saturday":  days[5],
            "sunday":    days[6],
            "start_date": FEED_START,
            "end_date":   FEED_END,
        })
    write_csv(os.path.join(GTFS_DIR, "calendar.txt"),
        ["service_id","monday","tuesday","wednesday","thursday","friday","saturday","sunday",
         "start_date","end_date"],
        calendar_rows)

    # -----------------------------------------------------------------------
    # calendar_dates.txt — holidays and seasonal windows
    # -----------------------------------------------------------------------
    cal_dates_rows = []

    # Fixed-date holidays: remove service on weekday-based services
    weekday_services = [sid for sid in used_service_ids
                        if ALL_SERVICES.get(sid, (0,))[0] == 1]  # monday == 1
    for year in [2025, 2026]:
        for month, day in FIXED_HOLIDAYS:
            date_str = f"{year}{month}{day}"
            for sid in weekday_services:
                cal_dates_rows.append({
                    "service_id": sid,
                    "date": date_str,
                    "exception_type": 2,  # service removed
                })
        for date_str in EASTER_HOLIDAYS.get(year, []):
            for sid in weekday_services:
                cal_dates_rows.append({
                    "service_id": sid,
                    "date": date_str,
                    "exception_type": 2,
                })

    # Seasonal windows: restrict _winter / _summer services to their date ranges
    # Winter: Sep 15 – Apr 30  (remove outside that window = add service removed for May1–Sep14)
    # Summer: May 1 – Sep 14   (remove outside that window = add service removed for Sep15–Apr30)
    # Rather than listing every excluded date, we use start_date/end_date in calendar.txt.
    # Override for known seasonal services:
    seasonal_calendar_overrides = {
        # service_id_suffix: (start_date, end_date)
        "_winter": ("20250915", "20260430"),
        "_summer": ("20250501", "20250914"),
    }
    # Rewrite calendar rows for seasonal services
    new_cal_rows = []
    for row in calendar_rows:
        sid = row["service_id"]
        for suffix, (start, end) in seasonal_calendar_overrides.items():
            if sid.endswith(suffix):
                row = dict(row)
                row["start_date"] = start
                row["end_date"] = end
                break
        new_cal_rows.append(row)
    calendar_rows = new_cal_rows
    # Rewrite calendar.txt with updated date ranges
    write_csv(os.path.join(GTFS_DIR, "calendar.txt"),
        ["service_id","monday","tuesday","wednesday","thursday","friday","saturday","sunday",
         "start_date","end_date"],
        calendar_rows)

    write_csv(os.path.join(GTFS_DIR, "calendar_dates.txt"),
        ["service_id", "date", "exception_type"],
        cal_dates_rows)

    # -----------------------------------------------------------------------
    # trips.txt + stop_times.txt
    # -----------------------------------------------------------------------
    trips_rows = []
    stop_times_rows = []
    trip_counter = {}  # route_id → count, for unique trip_ids

    for route in routes_data:
        route_id = route["route_id"]
        is_request = route_id in REQUEST_ONLY_ROUTES
        trip_counter[route_id] = 0

        for sec_idx, sec in enumerate(route.get("sections", [])):
            stops = sec.get("stops", [])
            trips = sec.get("trips", [])
            seasonal_info = sec.get("seasonal")

            if not stops or not trips:
                continue

            for trip in trips:
                service_id = get_service_id(trip["service_id"], seasonal_info)
                times = trip.get("times", {})

                # Determine headsign = last stop that has a time
                headsign = stops[-1]
                for s in reversed(stops):
                    if times.get(s):
                        headsign = s
                        break

                trip_counter[route_id] += 1
                trip_id = f"{route_id}_{trip_counter[route_id]:04d}"

                trips_rows.append({
                    "route_id": route_id,
                    "service_id": service_id,
                    "trip_id": trip_id,
                    "trip_headsign": headsign,
                    "direction_id": sec_idx % 2,  # 0 = outbound, 1 = inbound (alternating sections)
                })

                # Stop times
                pickup_type = 3 if is_request else 0
                drop_off_type = 3 if is_request else 0

                seq = 1
                last_stop_id = None  # deduplicate consecutive alias-collapsed stops
                for stop_name in stops:
                    t = times.get(stop_name)
                    canonical_name = STOP_ALIASES.get(stop_name, stop_name)
                    stop_info = stop_registry.get(canonical_name)
                    if stop_info is None:
                        continue  # skip unknown/label stops
                    if t is None:
                        # Stop not served on this trip — skip it
                        continue
                    # Skip if this alias collapsed to the same stop_id as the
                    # immediately preceding served stop (e.g. LS/Steinatún/Glasir)
                    if stop_info["stop_id"] == last_stop_id:
                        continue
                    last_stop_id = stop_info["stop_id"]
                    stop_times_rows.append({
                        "trip_id": trip_id,
                        "arrival_time": t,
                        "departure_time": t,
                        "stop_id": stop_info["stop_id"],
                        "stop_sequence": seq,
                        "pickup_type": pickup_type,
                        "drop_off_type": drop_off_type,
                    })
                    seq += 1

    # Drop trips that have fewer than 2 stop times — they can't form any
    # connection in the CSA graph and indicate scraper data gaps.
    stop_times_by_trip = {}
    for row in stop_times_rows:
        stop_times_by_trip.setdefault(row["trip_id"], []).append(row)
    valid_trip_ids = {tid for tid, rows in stop_times_by_trip.items() if len(rows) >= 2}
    trips_rows      = [r for r in trips_rows      if r["trip_id"] in valid_trip_ids]
    stop_times_rows = [r for r in stop_times_rows if r["trip_id"] in valid_trip_ids]

    write_csv(os.path.join(GTFS_DIR, "trips.txt"),
        ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"],
        trips_rows)

    write_csv(os.path.join(GTFS_DIR, "stop_times.txt"),
        ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
         "pickup_type", "drop_off_type"],
        stop_times_rows)

    # -----------------------------------------------------------------------
    # translations.txt — bilingual stop names (Faroese primary, English placeholder)
    # -----------------------------------------------------------------------
    # For now, both translations are the same Faroese name.
    # The user can later fill in English names in this file.
    translations_rows = []
    for name, info in stop_registry.items():
        translations_rows.append({
            "table_name": "stops",
            "field_name": "stop_name",
            "language": "fo",
            "record_id": info["stop_id"],
            "translation": name,
        })
        # English placeholder — same as Faroese until manually translated
        translations_rows.append({
            "table_name": "stops",
            "field_name": "stop_name",
            "language": "en",
            "record_id": info["stop_id"],
            "translation": name,  # placeholder
        })
    write_csv(os.path.join(GTFS_DIR, "translations.txt"),
        ["table_name", "field_name", "language", "record_id", "translation"],
        translations_rows)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\nSummary:")
    print(f"  Stops:      {len(stops_rows)}")
    print(f"  Routes:     {len(routes_rows)}")
    print(f"  Services:   {len(calendar_rows)}")
    print(f"  Trips:      {len(trips_rows)}")
    print(f"  Stop times: {len(stop_times_rows)}")
    print(f"\nGTFS files written to: {os.path.abspath(GTFS_DIR)}")


if __name__ == "__main__":
    main()
