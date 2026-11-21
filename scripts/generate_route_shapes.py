#!/usr/bin/env python3
"""
Generate local route geometry for the Leaflet maps.

Bus routes are snapped to OpenStreetMap roads through the public OSRM demo
server, segment by segment between consecutive GTFS stops. Ferry routes use
curated maritime waypoints so the map reads as sea passages rather than a
generic endpoint-to-endpoint ruler line.

Output:
  data/route_shapes.json

The generated file is intentionally committed/static-site friendly: the browser
only fetches local JSON and never calls a routing API at runtime.
"""

import csv
import heapq
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from shapely.geometry import LineString, Point, shape
from shapely.prepared import prep
from shapely.ops import unary_union


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GTFS_DIR = os.path.join(ROOT, "gtfs")
DATA_DIR = os.path.join(ROOT, "data")
OUT_PATH = os.path.join(DATA_DIR, "route_shapes.json")
CACHE_PATH = os.path.join(DATA_DIR, "route_shape_segments_cache.json")
LAND_PATH = os.path.join(DATA_DIR, "faroe_land_osm.geojson")

OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"
REQUEST_DELAY_SECONDS = 0.15

# Ferry timetables in this scraped feed mostly contain only endpoints. These
# channel hints are fed into a water-only pathfinder so the generated line stays
# over sea while still taking plausible ferry passages.
# Coordinates are [lat, lon], matching the generated JSON consumed by Leaflet.
FERRY_CHANNELS = {
    "7": [
        [61.555000, -6.790000],  # Krambatangi ferry approach
        [61.640000, -6.680000],
        [61.750000, -6.620000],
        [61.880000, -6.620000],
        [61.950000, -6.670000],
        [62.005000, -6.730000],  # Tórshavn harbor approach
    ],
    "36": [
        [62.071300, -7.307500],  # Sørvágur quay
        [62.078000, -7.390000],
        [62.091000, -7.510000],
        [62.101000, -7.600000],
        [62.104300, -7.647600],  # Mykines
    ],
    "56": [
        [62.225000, -6.587500],  # Klaksvík quay
        [62.235000, -6.615000],
        [62.241000, -6.646000],
        [62.245300, -6.667800],  # Syðradalur
    ],
    "58": [
        [62.296700, -6.518500],  # Hvannasund quay
        [62.292000, -6.435000],
        [62.291000, -6.360000],
        [62.307000, -6.285000],
        [62.329600, -6.275600],  # Hattarvík
    ],
    "61": [
        [61.960000, -6.820000],  # Gamlarætt harbor approach
        [61.958000, -6.835000],
        [61.956000, -6.855000],
        [61.956000, -6.880000],  # Hestur harbor approach
    ],
    "66": [
        [61.836500, -6.809500],  # Sandur quay
        [61.807000, -6.822000],
        [61.785000, -6.825000],
        [61.766400, -6.827000],  # Skúvoy
    ],
    "90": [
        [62.007800, -6.764800],  # Tórshavn ferry quay
        [62.004000, -6.724000],
        [61.996000, -6.684000],
        [61.985400, -6.653100],  # Nólsoy
    ],
}

WATER_GRID_STEP = 0.002
PATH_BOUNDS_PADDING = 0.050
LAND_GEOMETRY = None
PREPARED_LAND = None


def load_land_geometry():
    if not os.path.exists(LAND_PATH):
        return None
    with open(LAND_PATH, encoding="utf-8") as f:
        fc = json.load(f)
    return unary_union([shape(feature["geometry"]) for feature in fc["features"]])


def read_csv(name):
    path = os.path.join(GTFS_DIR, name)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def route_stop_sequences(routes, trips, stop_times):
    """Return the longest ordered stop sequence per route_id."""
    trips_by_route = {}
    for trip in trips:
        trips_by_route.setdefault(trip["route_id"], []).append(trip["trip_id"])

    stop_times_by_trip = {}
    for row in stop_times:
        stop_times_by_trip.setdefault(row["trip_id"], []).append(row)
    for rows in stop_times_by_trip.values():
        rows.sort(key=lambda r: int(r["stop_sequence"]))

    sequences = {}
    for route in routes:
        route_id = route["route_id"]
        best = []
        for trip_id in trips_by_route.get(route_id, []):
            rows = stop_times_by_trip.get(trip_id, [])
            if len(rows) > len(best):
                best = rows
        sequences[route_id] = [row["stop_id"] for row in best]
    return sequences


def dedupe_consecutive(items):
    out = []
    for item in items:
        if not out or out[-1] != item:
            out.append(item)
    return out


def read_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))


def straight_segment(a, b):
    return [[a["lat"], a["lon"]], [b["lat"], b["lon"]]]


def densify_path(points, steps_per_leg=10):
    densified = []
    for start, end in zip(points, points[1:]):
        for step in range(steps_per_leg):
            t = step / steps_per_leg
            lat = start[0] + (end[0] - start[0]) * t
            lon = start[1] + (end[1] - start[1]) * t
            if not densified or densified[-1] != [lat, lon]:
                densified.append([round(lat, 6), round(lon, 6)])
    densified.append(points[-1])
    return densified


def coord_distance(a, b):
    lat_scale = 111_000
    lon_scale = 111_000 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * lat_scale, (a[1] - b[1]) * lon_scale)


def point_in_land(point):
    if PREPARED_LAND is None:
        raise RuntimeError(f"Missing land geometry: {LAND_PATH}")
    return PREPARED_LAND.intersects(Point(point[1], point[0]))


def is_water_node(point, ports):
    return not point_in_land(point)


def line_touches_land(a, b):
    if PREPARED_LAND is None:
        raise RuntimeError(f"Missing land geometry: {LAND_PATH}")
    line = LineString([(a[1], a[0]), (b[1], b[0])])
    return PREPARED_LAND.intersects(line)


def grid_point(origin_lat, origin_lon, node):
    row, col = node
    return [
        round(origin_lat + row * WATER_GRID_STEP, 6),
        round(origin_lon + col * WATER_GRID_STEP, 6),
    ]


def nearest_water_node(point, origin_lat, origin_lon, ports):
    row = round((point[0] - origin_lat) / WATER_GRID_STEP)
    col = round((point[1] - origin_lon) / WATER_GRID_STEP)
    if is_water_node(grid_point(origin_lat, origin_lon, (row, col)), ports):
        return (row, col)

    max_radius = 30
    best = None
    best_dist = float("inf")
    for radius in range(1, max_radius + 1):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                if max(abs(dr), abs(dc)) != radius:
                    continue
                candidate = (row + dr, col + dc)
                candidate_point = grid_point(origin_lat, origin_lon, candidate)
                if not is_water_node(candidate_point, ports):
                    continue
                dist = coord_distance(point, candidate_point)
                if dist < best_dist:
                    best = candidate
                    best_dist = dist
        if best:
            return best
    return (row, col)


def nearest_water_point(point, max_radius=60):
    if not point_in_land(point):
        return [round(point[0], 6), round(point[1], 6)]

    origin_lat = point[0] - max_radius * WATER_GRID_STEP
    origin_lon = point[1] - max_radius * WATER_GRID_STEP
    center = (max_radius, max_radius)
    best = None
    best_dist = float("inf")

    for radius in range(1, max_radius + 1):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                if max(abs(dr), abs(dc)) != radius:
                    continue
                candidate = (center[0] + dr, center[1] + dc)
                candidate_point = grid_point(origin_lat, origin_lon, candidate)
                if point_in_land(candidate_point):
                    continue
                dist = coord_distance(point, candidate_point)
                if dist < best_dist:
                    best = candidate_point
                    best_dist = dist
        if best:
            return best

    return [round(point[0], 6), round(point[1], 6)]


def nearest_visible_water_point(point, approach, max_radius=60):
    origin_lat = point[0] - max_radius * WATER_GRID_STEP
    origin_lon = point[1] - max_radius * WATER_GRID_STEP
    center = (max_radius, max_radius)
    best = None
    best_dist = float("inf")

    candidates = [[round(point[0], 6), round(point[1], 6)]]
    for radius in range(1, max_radius + 1):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                if max(abs(dr), abs(dc)) != radius:
                    continue
                candidates.append(grid_point(origin_lat, origin_lon, (center[0] + dr, center[1] + dc)))

        for candidate_point in candidates:
            if point_in_land(candidate_point):
                continue
            if line_touches_land(candidate_point, approach):
                continue
            dist = coord_distance(point, candidate_point)
            if dist < best_dist:
                best = candidate_point
                best_dist = dist

        if best:
            return best
        candidates = []

    return nearest_water_point(point, max_radius=max_radius)


def simplify_grid_path(points):
    if len(points) <= 2:
        return points
    simplified = [points[0]]
    previous_direction = None
    for prev, current, nxt in zip(points, points[1:], points[2:]):
        direction = (
            round(nxt[0] - current[0], 6),
            round(nxt[1] - current[1], 6),
        )
        incoming = (
            round(current[0] - prev[0], 6),
            round(current[1] - prev[1], 6),
        )
        if previous_direction is None:
            previous_direction = incoming
        if direction != incoming:
            simplified.append(current)
        previous_direction = direction
    simplified.append(points[-1])
    return simplified


def simplify_water_path(points):
    if len(points) <= 2:
        return points

    simplified = [points[0]]
    anchor_idx = 0
    candidate_idx = 1
    while candidate_idx < len(points):
        if line_touches_land(points[anchor_idx], points[candidate_idx]):
            keep_idx = max(anchor_idx + 1, candidate_idx - 1)
            simplified.append(points[keep_idx])
            anchor_idx = keep_idx
            candidate_idx = anchor_idx + 1
        else:
            candidate_idx += 1

    if simplified[-1] != points[-1]:
        simplified.append(points[-1])
    return simplified


def water_path_between(start, end):
    min_lat = min(start[0], end[0]) - PATH_BOUNDS_PADDING
    max_lat = max(start[0], end[0]) + PATH_BOUNDS_PADDING
    min_lon = min(start[1], end[1]) - PATH_BOUNDS_PADDING
    max_lon = max(start[1], end[1]) + PATH_BOUNDS_PADDING

    rows = math.ceil((max_lat - min_lat) / WATER_GRID_STEP)
    cols = math.ceil((max_lon - min_lon) / WATER_GRID_STEP)
    ports = []
    start_node = nearest_water_node(start, min_lat, min_lon, ports)
    end_node = nearest_water_node(end, min_lat, min_lon, ports)

    queue = [(0, start_node)]
    came_from = {}
    cost_so_far = {start_node: 0}
    moves = [
        (-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1),
        (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
        (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)),
    ]

    while queue:
        _, current = heapq.heappop(queue)
        if current == end_node:
            break

        for dr, dc, move_cost in moves:
            nxt = (current[0] + dr, current[1] + dc)
            if nxt[0] < 0 or nxt[0] > rows or nxt[1] < 0 or nxt[1] > cols:
                continue
            point = grid_point(min_lat, min_lon, nxt)
            if not is_water_node(point, ports):
                continue
            current_point = grid_point(min_lat, min_lon, current)
            if line_touches_land(current_point, point):
                continue
            new_cost = cost_so_far[current] + move_cost
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + math.hypot(end_node[0] - nxt[0], end_node[1] - nxt[1])
                heapq.heappush(queue, (priority, nxt))
                came_from[nxt] = current

    if end_node not in cost_so_far:
        return [start, end]

    nodes = [end_node]
    while nodes[-1] != start_node:
        nodes.append(came_from[nodes[-1]])
    nodes.reverse()

    points = [grid_point(min_lat, min_lon, node) for node in nodes]
    return densify_path(simplify_water_path(simplify_grid_path(points)), steps_per_leg=4)


def water_route(points):
    route = []
    for start, end in zip(points, points[1:]):
        segment = water_path_between(start, end)
        if not route:
            route.extend(segment)
        else:
            route.extend(segment[1:])
    return route


def ferry_segment(route_id, a, b):
    channel = FERRY_CHANNELS.get(route_id)
    raw_start = [a["lat"], a["lon"]]
    raw_end = [b["lat"], b["lon"]]

    if not channel:
        start = nearest_water_point(raw_start)
        end = nearest_water_point(raw_end)
        sea_path = water_route([start, end])
        return [raw_start, *sea_path, raw_end]

    rough_start = nearest_water_point(raw_start)
    rough_end = nearest_water_point(raw_end)
    forward_distance = coord_distance(rough_start, channel[0]) + coord_distance(rough_end, channel[-1])
    reverse_distance = coord_distance(rough_start, channel[-1]) + coord_distance(rough_end, channel[0])
    if forward_distance <= reverse_distance:
        oriented_channel = channel
    else:
        oriented_channel = list(reversed(channel))

    start = nearest_visible_water_point(raw_start, oriented_channel[0])
    end = nearest_visible_water_point(raw_end, oriented_channel[-1])
    sea_path = water_route([start, *oriented_channel, end])
    return [raw_start, *sea_path, raw_end]


def count_land_crossings(path):
    return sum(1 for a, b in zip(path, path[1:]) if line_touches_land(a, b))


def fetch_osrm_segment(a, b):
    coords = f'{a["lon"]},{a["lat"]};{b["lon"]},{b["lat"]}'
    query = urllib.parse.urlencode({
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "false",
        "steps": "false",
    })
    url = f"{OSRM_BASE_URL}/{coords}?{query}"

    with urllib.request.urlopen(url, timeout=20) as res:
        payload = json.loads(res.read().decode("utf-8"))

    routes = payload.get("routes") or []
    if payload.get("code") != "Ok" or not routes:
        raise RuntimeError(payload.get("message") or payload.get("code") or "No route")

    # OSRM returns GeoJSON coordinates as [lon, lat]; Leaflet consumes [lat, lon].
    return [[lat, lon] for lon, lat in routes[0]["geometry"]["coordinates"]]


def segment_key(a_id, b_id):
    return f"{a_id}|{b_id}"


def append_segment(route_coords, segment):
    if not segment:
        return
    if not route_coords:
        route_coords.extend(segment)
        return
    route_coords.extend(segment[1:] if route_coords[-1] == segment[0] else segment)


def ferry_segment_land_crossings(segment):
    if len(segment) <= 3:
        return count_land_crossings(segment)
    sea_segment = segment[1:-1]
    return count_land_crossings(sea_segment)


def main():
    global LAND_GEOMETRY, PREPARED_LAND
    LAND_GEOMETRY = load_land_geometry()
    PREPARED_LAND = prep(LAND_GEOMETRY) if LAND_GEOMETRY is not None else None
    if LAND_GEOMETRY is None:
        raise RuntimeError(f"Missing detailed land polygon: {LAND_PATH}")

    stops = {
        row["stop_id"]: {
            "name": row["stop_name"],
            "lat": float(row["stop_lat"]),
            "lon": float(row["stop_lon"]),
        }
        for row in read_csv("stops.txt")
        if row.get("stop_lat") and row.get("stop_lon")
    }
    routes = read_csv("routes.txt")
    trips = read_csv("trips.txt")
    stop_times = read_csv("stop_times.txt")
    sequences = route_stop_sequences(routes, trips, stop_times)
    cache = read_cache()

    output = {
        "generated_by": "scripts/generate_route_shapes.py",
        "source": "GTFS stops plus OSRM road routing for buses and water-grid routing for ferries",
        "coordinate_order": "lat_lon",
        "routes": {},
    }

    failures = []

    for route in sorted(routes, key=lambda r: int(r["route_id"])):
        route_id = route["route_id"]
        route_type = int(route["route_type"])
        stop_ids = [sid for sid in dedupe_consecutive(sequences.get(route_id, [])) if sid in stops]
        if len(stop_ids) < 2:
            continue

        print(f"Route {route_id}: {len(stop_ids)} stops", flush=True)
        route_coords = []
        route_segments = {}

        for a_id, b_id in zip(stop_ids, stop_ids[1:]):
            a = stops[a_id]
            b = stops[b_id]
            key = segment_key(a_id, b_id)

            if route_type == 4:
                segment = ferry_segment(route_id, a, b)
                if ferry_segment_land_crossings(segment):
                    failures.append(f"{route_id}: ferry segment still intersects land: {a['name']} -> {b['name']}")
            elif key in cache:
                segment = cache[key]
            else:
                try:
                    segment = fetch_osrm_segment(a, b)
                    cache[key] = segment
                    write_cache(cache)
                    time.sleep(REQUEST_DELAY_SECONDS)
                except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                    failures.append(f"{route_id}: {a['name']} -> {b['name']}: {exc}")
                    segment = straight_segment(a, b)

            route_segments[key] = segment
            append_segment(route_coords, segment)

        output["routes"][route_id] = {
            "route_id": route_id,
            "route_type": route_type,
            "stop_ids": stop_ids,
            "geometry": route_coords,
            "segments": route_segments,
        }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote {OUT_PATH}")
    if failures:
        print("\nSegments that fell back to straight lines:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
