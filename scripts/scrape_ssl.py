#!/usr/bin/env python3
from __future__ import annotations
"""
Scrape timetable data from ssl.fo for all Faroe Islands bus and ferry routes.
Outputs one JSON file per route to data/scraped/route_<id>.json.

Two table formats exist on ssl.fo:
  - Bus format:   rows = trips, col[0] = day-type (x/6/7/x6/x67/etc), cols[1+] = stop times
  - Ferry format: cols = days-of-week (Monday–Sunday), rows alternate stop name / departure times
"""

import json
import html as html_lib
import os
import re
import sys
import time
import requests

BASE_URL = "https://www.ssl.fo"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "scraped")
os.makedirs(OUT_DIR, exist_ok=True)

ROUTES = [
    {"id": "7",   "type": "ferry",   "slug": "ferry/7-suduroy-torshavn"},
    {"id": "36",  "type": "ferry",   "slug": "ferry/36-soervagur-mykines"},
    {"id": "56",  "type": "ferry",   "slug": "ferry/56-klaksvik-kalsoy"},
    {"id": "58",  "type": "ferry",   "slug": "ferry/58-hvannasund-hattarvik"},
    {"id": "61",  "type": "ferry",   "slug": "ferry/61-gamlaraett-hestur"},
    {"id": "66",  "type": "ferry",   "slug": "ferry/66-sandur-skuvoy"},
    {"id": "90",  "type": "ferry",   "slug": "ferry/90-torshavn-nolsoy"},
    {"id": "100", "type": "bus",     "slug": "bus/100-torshavn-vestmanna"},
    {"id": "200", "type": "bus",     "slug": "bus/200-oyrarbakki-eidi"},
    {"id": "201", "type": "bus",     "slug": "bus/201-oyrarbakki-gjogv"},
    {"id": "202", "type": "bus",     "slug": "bus/202-oyrarbakki-tjoernuvik"},
    {"id": "222", "type": "bus",     "slug": "bus/222-kollafjardadalur-effo-oyrarbakki"},
    {"id": "223", "type": "bus",     "slug": "bus/223-sundalagid-kambsdalur"},
    {"id": "300", "type": "airport", "slug": "bus/300-torshavn-airport-soervagur"},
    {"id": "350", "type": "airport", "slug": "bus/350-torshavn-vaga-airport-soervagurboe"},
    {"id": "400", "type": "bus",     "slug": "bus/400-klaksvik-torshavn"},
    {"id": "401", "type": "express", "slug": "bus/401-klaksvik-torshavn-express-bus-valid-from-dec-1"},
    {"id": "410", "type": "bus",     "slug": "bus/410-fuglafj-goetudalur-klaksvik"},
    {"id": "440", "type": "bus",     "slug": "bus/440-skalafjardarleidin"},
    {"id": "442", "type": "bus",     "slug": "bus/442-glyvrar-aeduvik-rituvik"},
    {"id": "444", "type": "bus",     "slug": "bus/444-kambsdalur-skalafjoerdurtoftir"},
    {"id": "450", "type": "bus",     "slug": "bus/450-torshavn-eysturoy-jellyfish-roundabout"},
    {"id": "481", "type": "bus",     "slug": "bus/481-skalafjoerdur-oyndarfjoerdur"},
    {"id": "500", "type": "bus",     "slug": "bus/500-klaksvik-vidareidi"},
    {"id": "504", "type": "bus",     "slug": "bus/504-kunoy-klaksvik"},
    {"id": "506", "type": "bus",     "slug": "bus/506-troellanes-sydradalur"},
    {"id": "600", "type": "bus",     "slug": "bus/600-skopun-sandur"},
    {"id": "601", "type": "bus",     "slug": "bus/601-dalur-husavik-skalavik-sandur"},
    {"id": "650", "type": "bus",     "slug": "bus/650-sandoy-torshavn"},
    {"id": "700", "type": "bus",     "slug": "bus/700-sumba-vagur-tvoeroyri"},
    {"id": "701", "type": "bus",     "slug": "bus/701-famjin-tvoeroyri-sandvik"},
]

HEADERS = {"User-Agent": "Mozilla/5.0 (FaroeIslandsPlannerScraper/1.0)"}
DAY_COLS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# HTML table extractor (handles unclosed <tr>/<td> tags)
# ---------------------------------------------------------------------------

def parse_table_html(table_html):
    """
    Parse a single <table>...</table> HTML chunk into a list of rows.
    Each row is a list of cell text strings.
    Handles the ssl.fo pattern of unclosed <tr> and <td>/<th> tags by flushing
    the current cell/row whenever a new <tr> or <td>/<th> opens.
    """
    rows = []
    current_row = []
    current_cell_parts = []
    in_cell = False
    depth = 0  # nested table depth guard

    # Use a simple tokeniser: find all tags and text nodes
    token_re = re.compile(r'<([^>]+)>|([^<]+)', re.DOTALL)

    for m in token_re.finditer(table_html):
        tag_content, text_content = m.group(1), m.group(2)

        if tag_content is not None:
            tag_content_stripped = tag_content.strip()
            is_closing = tag_content_stripped.startswith('/')
            tag_name = tag_content_stripped.lstrip('/').split()[0].lower() if tag_content_stripped else ''

            if tag_name == 'table':
                if is_closing:
                    depth -= 1
                else:
                    depth += 1
                continue

            if depth > 1:
                continue  # ignore nested tables

            if tag_name == 'tr':
                if not is_closing:
                    # Flush current cell and row
                    if in_cell:
                        current_row.append(''.join(current_cell_parts).strip())
                        current_cell_parts = []
                        in_cell = False
                    if current_row:
                        rows.append(current_row)
                    current_row = []
                else:
                    # Explicit </tr>: flush cell, save row
                    if in_cell:
                        current_row.append(''.join(current_cell_parts).strip())
                        current_cell_parts = []
                        in_cell = False
                    if current_row:
                        rows.append(current_row)
                    current_row = []

            elif tag_name in ('td', 'th'):
                if not is_closing:
                    # Flush previous cell if open
                    if in_cell:
                        current_row.append(''.join(current_cell_parts).strip())
                        current_cell_parts = []
                    in_cell = True
                else:
                    # Explicit </td>: flush cell
                    if in_cell:
                        current_row.append(''.join(current_cell_parts).strip())
                        current_cell_parts = []
                        in_cell = False
            # Ignore other tags (span, sup, etc.) — their text is still captured

        elif text_content is not None and in_cell:
            # Only accumulate text when inside a cell
            t = text_content  # keep whitespace for now; strip later
            current_cell_parts.append(t)

    # Flush any remaining cell/row
    if in_cell and current_cell_parts:
        current_row.append(''.join(current_cell_parts).strip())
    if current_row:
        rows.append(current_row)

    return rows


def extract_section_headings_and_tables(html):
    """
    Walk the HTML linearly and return a list of {"heading": str|None, "rows": [[...]]}
    where heading is the nearest preceding h1–h4 text before each <table>.

    Handles ssl.fo's malformed HTML (no </tr> or </td> closing tags).
    """
    # We need to split on: <h1..4> ... </h1..4>  and  <table...> ... </table>
    # Tables on ssl.fo DO have </table> closing tags (confirmed).
    # Walk char by char to handle nesting and missing close tags.

    items = []
    last_heading = None
    i = 0
    n = len(html)

    while i < n:
        # Look for next <
        lt = html.find('<', i)
        if lt == -1:
            break

        # Peek at tag name
        gt = html.find('>', lt)
        if gt == -1:
            break
        tag_raw = html[lt+1:gt]
        tag_name_m = re.match(r'/?([a-zA-Z][a-zA-Z0-9]*)', tag_raw)
        if not tag_name_m:
            i = gt + 1
            continue
        tag_name = tag_name_m.group(1).lower()

        if tag_name in ('h1', 'h2', 'h3', 'h4') and not tag_raw.startswith('/'):
            # Find closing tag
            close_tag = f'</{tag_name}>'
            close_pos = html.lower().find(close_tag, gt)
            if close_pos == -1:
                i = gt + 1
                continue
            heading_html = html[lt:close_pos + len(close_tag)]
            heading_text = re.sub(r'<[^>]+>', '', heading_html)
            heading_text = re.sub(r'\s+', ' ', heading_text).strip()
            # Decode common HTML entities
            heading_text = heading_text.replace('&#xED;', 'í').replace('&#xF3;', 'ó') \
                .replace('&#xF8;', 'ø').replace('&#xE1;', 'á').replace('&#xFA;', 'ú') \
                .replace('&amp;', '&').replace('&#xF0;', 'ð').replace('&#xFE;', 'þ') \
                .replace('&nbsp;', ' ').strip()
            last_heading = heading_text
            i = close_pos + len(close_tag)

        elif tag_name == 'table' and not tag_raw.startswith('/'):
            # Find matching </table> — handle nesting
            depth = 1
            search_pos = gt + 1
            while depth > 0 and search_pos < n:
                next_open = html.lower().find('<table', search_pos)
                next_close = html.lower().find('</table>', search_pos)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    depth += 1
                    search_pos = next_open + 1
                else:
                    depth -= 1
                    search_pos = next_close + 8  # len('</table>')
            table_end = search_pos
            table_html = html[lt:table_end]
            rows = parse_table_html(table_html)
            if rows:
                items.append({"heading": last_heading, "rows": rows})
                last_heading = None  # heading consumed by this table
            i = table_end

        else:
            i = gt + 1

    return items


# ---------------------------------------------------------------------------
# Time normalisation
# ---------------------------------------------------------------------------

TIME_RE = re.compile(r'\b(\d{1,2})[:.:](\d{2})\b')

def clean_time(raw: str):
    """Extract HH:MM from a cell that may have notes/superscripts."""
    raw = re.sub(r'<[^>]+>', '', raw)  # strip any residual HTML
    m = TIME_RE.search(raw)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if h > 29 or mn > 59:
        return None
    return f"{h:02d}:{mn:02d}"


def is_time_cell(raw: str) -> bool:
    return clean_time(raw) is not None


# ---------------------------------------------------------------------------
# Day-type parsing (bus format: column 0)
# ---------------------------------------------------------------------------

def parse_day_types(raw: str) -> list[str]:
    """
    Parse the day-type cell from bus tables.
    x  = Mon–Fri (weekdays)
    6  = Saturday
    7  = Sunday
    x6 = Mon–Sat
    x67 = all week
    67 = Sat+Sun
    Returns list of day strings: subset of ["x","6","7"]
    """
    raw = raw.strip().lower()
    days = []
    if "x" in raw:
        days.append("x")
    if "6" in raw:
        days.append("6")
    if "7" in raw:
        days.append("7")
    return days or ["x"]  # default to weekday if unrecognised


def day_types_to_service_id(day_types: list[str], is_school: bool = False) -> str:
    has_x = "x" in day_types
    has_6 = "6" in day_types
    has_7 = "7" in day_types
    if has_x and has_6 and has_7:
        return "daily"
    if has_x and has_6:
        return "weekdays_sat"
    if has_x and has_7:
        return "weekdays_sun"
    if has_x:
        return "school" if is_school else "weekdays"
    if has_6 and has_7:
        return "weekend"
    if has_6:
        return "sat"
    if has_7:
        return "sun"
    return "weekdays"


# ---------------------------------------------------------------------------
# Request-only detection
# ---------------------------------------------------------------------------

def is_request_only(route_id: str) -> bool:
    return route_id in {"61", "201", "223"}


# ---------------------------------------------------------------------------
# Bus-format table parser
# ---------------------------------------------------------------------------

def split_duplicate_stop_columns(stop_columns):
    """
    Split route-shaped headers such as A, B, C, B, A into A→B→C and
    C→B→A. The incoming columns are already filtered to real stop labels.
    """
    stop_names = [clean_split_stop_label(stop) for _, stop in stop_columns]
    first_repeat_idx = None
    seen = set()
    for idx, stop in enumerate(stop_names):
        if stop in seen:
            first_repeat_idx = idx
            break
        seen.add(stop)

    if first_repeat_idx is None:
        return [stop_columns]

    pivot_idx = first_repeat_idx - 1
    if pivot_idx <= 0 or pivot_idx >= len(stop_columns) - 1:
        return [stop_columns]

    return [
        stop_columns[:pivot_idx + 1],
        stop_columns[pivot_idx:],
    ]


def parse_bus_table_sections(rows, route_id, is_school=False):
    """
    rows[0]: colspan title row (skip)
    rows[1]: header row — col[0]="Day" or empty, col[1+] = stop names
    rows[2+]: data rows — col[0]=day_type, col[1] optional note, col[2+] times
              OR col[0]=day_type, col[1+]=times (no note column)

    Returns {"stops": [...], "trips": [...]}
    where each trip = {"service_id": ..., "times": {"StopName": "HH:MM"|None, ...}}
    """
    if len(rows) < 2:
        return {"stops": [], "trips": []}

    # Find header row: first row where col[1] looks like a stop name (not a time)
    header_row_idx = 1
    for i, row in enumerate(rows):
        if len(row) > 1 and row[0].strip().lower() in ("day", "dag", ""):
            header_row_idx = i
            break

    header = rows[header_row_idx]
    # col[0] = "Day"/"Dag"/"", remaining cols = stop names
    raw_header_cells = [c.strip() for c in header[1:]]

    # Detect if there is a "notes" column (col[1] of data rows is non-time text
    # like footnote numbers) — check first data row
    has_note_col = False
    for row in rows[header_row_idx + 1:]:
        if len(row) > 2 and not is_time_cell(row[1]) and not row[1].strip() == "":
            # col[1] might be a note indicator like "1)" or "F)"
            # check if it's consistently non-time
            note_candidates = [r[1] for r in rows[header_row_idx + 1:] if len(r) > 1]
            non_time = sum(1 for c in note_candidates if not is_time_cell(c))
            has_note_col = (non_time / max(len(note_candidates), 1)) > 0.6
        break

    # If header has an extra col at position 1 that is blank/note, adjust
    if has_note_col and raw_header_cells and not is_time_cell(raw_header_cells[0]):
        raw_header_cells = raw_header_cells[1:]  # drop note column from stops

    stop_columns = [
        (idx, clean_stop_name(html_lib.unescape(stop)))
        for idx, stop in enumerate(raw_header_cells)
        if stop and not is_time_cell(stop)
    ]
    sections = []

    for section_columns in split_duplicate_stop_columns(stop_columns):
        stops = [stop for _, stop in section_columns]
        trips = []
        for row in rows[header_row_idx + 1:]:
            if not row:
                continue
            day_raw = row[0].strip()
            day_types = parse_day_types(day_raw)
            service_id = day_types_to_service_id(day_types, is_school)

            # Extract time cells while preserving alignment with the header.
            time_cells = row[1:]
            if has_note_col and time_cells:
                time_cells = time_cells[1:]

            times = {}
            for cell_idx, stop in section_columns:
                val = time_cells[cell_idx].strip() if cell_idx < len(time_cells) else ""
                times[stop] = clean_time(val)

            # Skip rows where no times were found (e.g., deviation/notes rows)
            if not any(v for v in times.values()):
                continue

            trips.append({
                "service_id": service_id,
                "day_raw": day_raw,
                "times": times,
            })

        sections.append({"stops": stops, "trips": trips})

    return sections


def parse_bus_table(rows, route_id, is_school=False):
    sections = parse_bus_table_sections(rows, route_id, is_school)
    return sections[0] if sections else {"stops": [], "trips": []}


def clean_split_stop_label(label: str) -> str:
    label = html_lib.unescape(label)
    label = clean_stop_name(label)
    label = re.sub(r"(?i)^arrival\s+(?:at\s+|to\s+)?", "", label).strip()
    label = re.sub(r"(?i)^departure\s+from\s+", "", label).strip()
    label = re.sub(r"(?i)^departure\s+", "", label).strip()
    label = re.sub(r"(?i)^from\s+", "", label).strip()
    label = re.sub(r"(?i)^on\s+", "", label).strip()
    label = re.sub(r"(?i)^í\s+", "", label).strip()
    if label in ("Oyrarbakka", "Oyrabakka"):
        label = "Oyrarbakki"
    return label


def split_paired_direction_bus_table(rows):
    """
    SSL sometimes places two directions side-by-side in one bus table:

      Dag | Eiði | Arrival Oyrarbakka | Departure from Oyrarbakka | Eiði

    Split that into two ordinary bus tables before parsing:
      Dag | Eiði      | Oyrarbakki
      Dag | Oyrarbakki | Eiði
    """
    if len(rows) < 2:
        return [rows]

    header_row_idx = None
    for i, row in enumerate(rows):
        first_cell = clean_stop_name(html_lib.unescape(row[0])).lower() if row else ""
        if len(row) > 1 and first_cell in ("day", "dag", ""):
            header_row_idx = i
            break
    if header_row_idx is None:
        return [rows]

    header = rows[header_row_idx]
    split_abs_idx = None
    for idx, cell in enumerate(header[1:], start=1):
        clean_cell = clean_stop_name(html_lib.unescape(cell or ""))
        if re.match(r"(?i)^(departure\s+from|from)\s+", clean_cell):
            split_abs_idx = idx
            break
    if split_abs_idx is None or split_abs_idx <= 1 or split_abs_idx >= len(header):
        return [rows]
    if split_abs_idx - 1 != len(header) - split_abs_idx:
        return [rows]

    parts = [(1, split_abs_idx), (split_abs_idx, len(header))]
    split_tables = []

    for start, end in parts:
        new_rows = []
        for row_idx, row in enumerate(rows):
            if row_idx < header_row_idx:
                new_rows.append(row)
                continue

            first_cell = row[0] if row else ""
            selected = [row[i] if i < len(row) else "" for i in range(start, end)]
            if row_idx == header_row_idx:
                selected = [clean_split_stop_label(cell) for cell in selected]
            new_rows.append([first_cell, *selected])
        split_tables.append(new_rows)

    return split_tables


# ---------------------------------------------------------------------------
# Ferry-format table parser (days-of-week columns)
# ---------------------------------------------------------------------------

def parse_ferry_table(rows):
    """
    rows[0]: title row (skip)
    rows[1]: header — col[0]="From:", col[1..7] = Mon..Sun
    rows[2+]: alternating stop-name row and time row pairs
              OR interleaved: each row has stop in col[0] and times in col[1..7]

    Returns {"stops": [...], "trips": [...]}
    trips keyed by (stop, day_index) → time
    """
    if len(rows) < 3:
        return {"stops": [], "trips": []}

    # Find header row with day names
    day_col_map = {}  # col_index → day_name
    header_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if cell.strip().lower() in ("monday", "tuesday", "wednesday", "thursday",
                                         "friday", "saturday", "sunday"):
                day_col_map[j] = cell.strip()
                header_idx = i

    if not day_col_map:
        return {"stops": [], "trips": []}

    # Data rows after header
    data_rows = rows[header_idx + 1:]

    # Each row: col[0]=stop_name, col[j]=time_for_day_j
    # Multiple rows with the same direction are different trips on the same day
    # Collect: stop_name → {day_name → [time1, time2, ...]}
    stop_day_times = {}
    stop_order = []

    for row in data_rows:
        if not row:
            continue
        stop_name = row[0].strip()
        if not stop_name or stop_name.lower() in ("from:", "from", "til", "to:"):
            continue
        # Skip deviation/notes rows
        if any(w in stop_name.lower() for w in ("deviation", "changes", "day:")):
            continue

        if stop_name not in stop_day_times:
            stop_day_times[stop_name] = {}
            stop_order.append(stop_name)

        for col_idx, day_name in day_col_map.items():
            val = row[col_idx].strip() if col_idx < len(row) else ""
            t = clean_time(val)
            if t:
                stop_day_times[stop_name].setdefault(day_name, []).append(t)

    if not stop_order:
        return {"stops": [], "trips": []}

    # Reconstruct trips: align times across stops by position
    # For each day, zip the time-lists across stops to form trips
    # days: Monday=0..Sunday=6 → service_id mapping
    day_service = {
        "Monday": "weekdays", "Tuesday": "weekdays", "Wednesday": "weekdays",
        "Thursday": "weekdays", "Friday": "weekdays",
        "Saturday": "sat", "Sunday": "sun",
    }

    trips = []
    all_days = list(day_col_map.values())
    # Group Mon–Fri together where times are identical
    for day_name in all_days:
        # Find max trips on this day across all stops
        max_trips = max(
            len(stop_day_times.get(s, {}).get(day_name, []))
            for s in stop_order
        )
        for trip_idx in range(max_trips):
            times = {}
            for s in stop_order:
                day_times = stop_day_times.get(s, {}).get(day_name, [])
                times[s] = day_times[trip_idx] if trip_idx < len(day_times) else None
            if any(v for v in times.values()):
                trips.append({
                    "service_id": day_service.get(day_name, "daily"),
                    "day_raw": day_name,
                    "times": times,
                })

    # Deduplicate identical trips (same times, same day pattern)
    seen = set()
    unique_trips = []
    for t in trips:
        key = (t["service_id"], t["day_raw"], tuple(sorted(t["times"].items())))
        if key not in seen:
            seen.add(key)
            unique_trips.append(t)

    return {"stops": stop_order, "trips": unique_trips}


def ferry_day_service_id(day_name: str) -> str:
    return {
        "Monday": "mon",
        "Tuesday": "tue",
        "Wednesday": "wed",
        "Thursday": "thu",
        "Friday": "fri",
        "Saturday": "sat",
        "Sunday": "sun",
    }.get(day_name, "daily")


def parse_ferry_departure_sections(rows):
    """
    Parse ferry tables where each data row is a departure from the stop named
    in the first column. SSL commonly uses this compact layout for two harbors:

      From | Monday | Tuesday | ...
      A    | 06:00  | ...
      B    | 08:45  | ...
      A    | 11:30  | ...
      B    | 16:00  | ...

    Return one explicit section per direction. Arrival times are left blank
    here and filled by convert_to_gtfs.py using route-specific ferry durations.
    """
    if len(rows) < 3:
        return []

    day_col_map = {}
    header_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            day = clean_stop_name(html_lib.unescape(cell))
            if day in ("Monday", "Tuesday", "Wednesday", "Thursday",
                       "Friday", "Saturday", "Sunday"):
                day_col_map[j] = day
                header_idx = i

    if not day_col_map:
        return []

    departure_rows = []
    origins = []
    for row in rows[header_idx + 1:]:
        if not row:
            continue
        origin = clean_stop_name(html_lib.unescape(row[0]))
        if not origin or origin.lower() in ("from:", "from", "til", "to:"):
            continue
        if any(w in origin.lower() for w in ("deviation", "changes", "day:")):
            continue
        if not any(clean_time(row[col_idx]) if col_idx < len(row) else None
                   for col_idx in day_col_map):
            continue
        departure_rows.append((origin, row))
        if origin not in origins:
            origins.append(origin)

    if len(origins) != 2:
        return []

    sections = []
    for origin in origins:
        destination = origins[1] if origin == origins[0] else origins[0]
        trips = []
        seen = set()
        for row_origin, row in departure_rows:
            if row_origin != origin:
                continue
            for col_idx, day_name in day_col_map.items():
                val = row[col_idx].strip() if col_idx < len(row) else ""
                dep_time = clean_time(val)
                if not dep_time:
                    continue
                trip = {
                    "service_id": ferry_day_service_id(day_name),
                    "day_raw": day_name,
                    "times": {origin: dep_time, destination: None},
                }
                key = (trip["service_id"], trip["day_raw"], tuple(sorted(trip["times"].items())))
                if key in seen:
                    continue
                seen.add(key)
                trips.append(trip)
        if trips:
            sections.append({"stops": [origin, destination], "trips": trips})

    return sections


# ---------------------------------------------------------------------------
# Seasonal period parser
# ---------------------------------------------------------------------------

SEASONAL_RE = re.compile(
    r'(winter|summer|winther|vin(?:ter)?|sumar)',
    re.IGNORECASE
)
DATE_RANGE_RE = re.compile(
    r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*))'
    r'\s*(?:until|to|-)\s*'
    r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*))',
    re.IGNORECASE
)


def parse_seasonal_heading(heading: str):
    """Extract season name and date range from an h3 heading string."""
    if not heading:
        return None
    m_season = SEASONAL_RE.search(heading)
    if not m_season:
        return None
    season = "winter" if m_season.group(1).lower() in ("winter", "winther", "vinter") else "summer"
    m_dates = DATE_RANGE_RE.search(heading)
    date_range = None
    if m_dates:
        date_range = {"from": m_dates.group(1).strip(), "to": m_dates.group(2).strip()}
    return {"season": season, "date_range": date_range, "raw": heading}


# ---------------------------------------------------------------------------
# Deviation/holiday table parser
# ---------------------------------------------------------------------------

def parse_deviation_table(rows):
    """
    Parse the "Deviation" table that lists holiday schedule changes.
    rows[0]: ["Deviation", ""]  (title)
    rows[1]: ["Day:", "Changes:"]  (header)
    rows[2+]: [holiday_name, description]
    """
    deviations = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        day_cell = row[0].strip()
        change_cell = row[1].strip()
        if not day_cell or day_cell.lower() in ("deviation", "day:", "day", ""):
            continue
        if change_cell.lower() in ("changes:", "changes", ""):
            continue
        deviations.append({"day": day_cell, "change": change_cell})
    return deviations


def looks_like_deviation_table(rows):
    if not rows:
        return False
    for row in rows[:3]:
        for cell in row:
            text = cell.lower()
            if ("deviation" in text or "changes" in text or
                    "exceptions" in text or "broytingar" in text):
                return True
    return False


def looks_like_timetable(rows):
    """Heuristic: a real timetable has multiple time-like values."""
    time_count = sum(
        1 for row in rows for cell in row if is_time_cell(cell)
    )
    return time_count >= 4


def looks_like_ferry_format(rows):
    """Ferry format: header row has Monday/Tuesday/etc. columns."""
    if len(rows) < 2:
        return False
    for row in rows[:3]:
        for cell in row:
            if cell.strip().lower() in ("monday", "tuesday", "wednesday",
                                         "thursday", "friday", "saturday", "sunday"):
                return True
    return False


# ---------------------------------------------------------------------------
# Main page parser
# ---------------------------------------------------------------------------

def clean_stop_name(name):
    """Normalise a stop name: strip &nbsp;, collapse whitespace, decode entities."""
    name = html_lib.unescape(name)
    name = name.replace('\xa0', ' ').replace('&nbsp;', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def parse_page(html, route_id, route_type):
    items = extract_section_headings_and_tables(html)

    result = {
        "route_id": route_id,
        "route_type": route_type,
        "sections": [],
        "deviations": [],
    }

    # Extract page title (route name)
    title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
    if title_m:
        result["route_name"] = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

    is_school = route_id in {"201", "202", "223", "444", "442", "481", "504", "506"}

    for item in items:
        heading = item.get("heading") or ""
        rows = item.get("rows", [])

        if looks_like_deviation_table(rows):
            result["deviations"].extend(parse_deviation_table(rows))
            continue

        if not looks_like_timetable(rows):
            continue

        seasonal_info = parse_seasonal_heading(heading)

        if looks_like_ferry_format(rows):
            parsed_tables = parse_ferry_departure_sections(rows) or [parse_ferry_table(rows)]
        else:
            parsed_tables = []
            for split_rows in split_paired_direction_bus_table(rows):
                parsed_tables.extend(parse_bus_table_sections(split_rows, route_id, is_school))

        for parsed in parsed_tables:
            if not parsed["trips"]:
                continue

            # Clean stop names
            clean_stops = [clean_stop_name(s) for s in parsed["stops"]]
            name_map = {old: new for old, new in zip(parsed["stops"], clean_stops)}
            clean_trips = []
            for trip in parsed["trips"]:
                clean_trips.append({
                    "service_id": trip["service_id"],
                    "day_raw": trip["day_raw"],
                    "times": {name_map.get(k, k): v for k, v in trip["times"].items()},
                })

            section = {
                "heading": heading or None,
                "seasonal": seasonal_info,
                "stops": clean_stops,
                "trips": clean_trips,
            }
            result["sections"].append(section)

    return result


# ---------------------------------------------------------------------------
# Fetch and save
# ---------------------------------------------------------------------------

def fetch_route(route: dict) -> dict:
    url = f"{BASE_URL}/en/timetable/{route['slug']}"
    print(f"  Fetching {url} ...", end=" ", flush=True)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"{resp.status_code}")
    return parse_page(resp.text, route["id"], route["type"])


def main():
    routes_to_fetch = ROUTES
    if len(sys.argv) > 1:
        ids = set(sys.argv[1:])
        routes_to_fetch = [r for r in ROUTES if r["id"] in ids]

    for i, route in enumerate(routes_to_fetch):
        out_path = os.path.join(OUT_DIR, f"route_{route['id']}.json")
        print(f"[{i+1}/{len(routes_to_fetch)}] Route {route['id']}")
        try:
            data = fetch_route(route)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            n_sections = len(data["sections"])
            n_trips = sum(len(s["trips"]) for s in data["sections"])
            print(f"    → {n_sections} section(s), {n_trips} trip(s) saved to {out_path}")
        except Exception as e:
            print(f"    ERROR: {e}")
        if i < len(routes_to_fetch) - 1:
            time.sleep(0.5)  # polite delay

    print("\nDone.")


if __name__ == "__main__":
    main()
