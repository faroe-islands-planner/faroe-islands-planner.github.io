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
INDEX_HTML = os.path.join(os.path.dirname(__file__), "..", "index.html")
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
# Stop coordinates from index.html
# ---------------------------------------------------------------------------

def load_coords_from_html():
    """Extract COORDS object from index.html using regex."""
    try:
        html = open(INDEX_HTML, encoding="utf-8").read()
    except FileNotFoundError:
        return {}
    m = re.search(r'const COORDS\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not m:
        return {}
    coords_text = m.group(1)
    # Remove JS comments
    coords_text = re.sub(r'//[^\n]*', '', coords_text)
    # Parse key: [lat, lon] pairs
    coords = {}
    for km in re.finditer(r'"([^"]+)"\s*:\s*\[([^\]]+)\]', coords_text):
        name = km.group(1).strip()
        parts = km.group(2).split(',')
        try:
            lat, lon = float(parts[0].strip()), float(parts[1].strip())
            coords[name] = (lat, lon)
        except (ValueError, IndexError):
            pass
    return coords


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
    # Strings that look like table labels, not real stop names
    STOP_BLOCKLIST = {
        "", "arrival oyrarbakka", "departure from oyrarbakka",
        "to klaksvík", "to tórshavn", "to klaksvik", "to torshavn",
    }

    all_stop_names = set()
    for route in routes_data:
        for sec in route.get("sections", []):
            for name in sec.get("stops", []):
                if name.strip().lower() not in STOP_BLOCKLIST:
                    all_stop_names.add(name)
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

    print("Loading stop coordinates from index.html...")
    coords = load_coords_from_html()
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
                for stop_name in stops:
                    t = times.get(stop_name)
                    stop_info = stop_registry.get(stop_name)
                    if stop_info is None:
                        continue  # skip unknown stops
                    if t is None:
                        # Stop not served on this trip — skip it
                        continue
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
