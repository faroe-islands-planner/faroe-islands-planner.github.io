#!/usr/bin/env python3
from __future__ import annotations
"""
Fetch the live ssl.fo timetable pages and save a local verification snapshot.

The snapshot intentionally stores multiple views of the same source data:
  - raw_html/: exact HTML fetched from ssl.fo
  - parsed_routes/: parsed JSON produced by scrape_ssl.py
  - extracted_tables/: human-readable Markdown tables extracted from the HTML
  - route_overview_links.csv: route links discovered on SSL's overview page
  - direction_audit.csv: coarse endpoint-direction summary per parsed route
  - summary.md: scan summary and local-vs-live differences
"""

import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DATA_DIR = REPO_DIR / "data"

sys.path.insert(0, str(SCRIPT_DIR))
import scrape_ssl  # noqa: E402

OVERVIEW_URL = "https://www.ssl.fo/en/timetable/route-overview"
SNAPSHOT_ROOT = DATA_DIR / "live_ssl_snapshot"


def safe_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unnamed"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return "_No rows._\n"

    width = max(len(row) for row in rows)
    padded = []
    for row in rows:
        cells = [html.unescape((cell or "").replace("\n", " ")).strip() for cell in row]
        cells += [""] * (width - len(cells))
        padded.append(cells)

    def cell(value: str) -> str:
        return value.replace("|", "\\|")

    out = []
    out.append("| " + " | ".join(cell(c) for c in padded[0]) + " |")
    out.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in padded[1:]:
        out.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(out) + "\n"


def route_id_from_slug(slug: str) -> str:
    match = re.search(r"/(\d+)-", "/" + slug)
    return match.group(1) if match else "unknown"


def route_type_from_slug(slug: str) -> str:
    if slug.startswith("ferry/"):
        return "ferry"
    if slug.startswith("bus/300") or slug.startswith("bus/350"):
        return "airport"
    if slug.startswith("bus/401"):
        return "express"
    return "bus"


def fetch(url: str) -> requests.Response:
    response = requests.get(url, headers=scrape_ssl.HEADERS, timeout=30)
    response.raise_for_status()
    return response


def discover_route_links() -> list[dict[str, str]]:
    response = fetch(OVERVIEW_URL)
    links = sorted(set(
        match.rstrip("/")
        for match in re.findall(
            r"href=(?:[\"'])?(/en/timetable/(?:bus|ferry)/[^\s\"'<>]+)",
            response.text,
        )
    ))
    discovered = []
    for path in links:
        slug = path.replace("/en/timetable/", "")
        discovered.append({
            "route_id": route_id_from_slug(slug),
            "route_type": route_type_from_slug(slug),
            "slug": slug,
            "url": f"{scrape_ssl.BASE_URL}/en/timetable/{slug}",
        })
    return discovered


def configured_routes() -> list[dict[str, str]]:
    return [
        {
            "route_id": route["id"],
            "route_type": route["type"],
            "slug": route["slug"],
            "url": f"{scrape_ssl.BASE_URL}/en/timetable/{route['slug']}",
        }
        for route in scrape_ssl.ROUTES
    ]


def endpoint_summary(parsed: dict) -> tuple[Counter, bool]:
    endpoints = Counter()
    for section in parsed.get("sections", []):
        stops = section.get("stops", [])
        if len(stops) >= 2:
            endpoints[(stops[0], stops[-1])] += len(section.get("trips", []))
    has_reverse = any((b, a) in endpoints for a, b in endpoints if a != b)
    return endpoints, has_reverse


def local_scrape_diff(route_id: str, parsed: dict) -> str:
    local_path = DATA_DIR / "scraped" / f"route_{route_id}.json"
    if not local_path.exists():
        return "missing-local"
    try:
        local = json.loads(local_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "local-json-error"
    return "same" if canonical_json(local) == canonical_json(parsed) else "different"


def save_route(snapshot_dir: Path, route: dict, source_kind: str) -> dict:
    response = fetch(route["url"])
    parsed = scrape_ssl.parse_page(response.text, route["route_id"], route["route_type"])
    tables = scrape_ssl.extract_section_headings_and_tables(response.text)

    route_key = f"{route['route_id']}_{safe_name(route['slug'])}"
    raw_path = snapshot_dir / "raw_html" / f"{route_key}.html"
    parsed_path = snapshot_dir / "parsed_routes" / f"{route_key}.json"
    tables_path = snapshot_dir / "extracted_tables" / f"{route_key}.md"

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(response.text, encoding="utf-8")
    write_json(parsed_path, parsed)

    md = [
        f"# Route {route['route_id']} - {route['slug']}",
        "",
        f"- Source: {route['url']}",
        f"- Source kind: {source_kind}",
        f"- Parsed sections: {len(parsed.get('sections', []))}",
        f"- Parsed trips: {sum(len(s.get('trips', [])) for s in parsed.get('sections', []))}",
        "",
    ]
    for idx, item in enumerate(tables, start=1):
        rows = item.get("rows", [])
        md.extend([
            f"## Extracted Table {idx}",
            "",
            f"- Heading: {item.get('heading') or ''}",
            f"- Rows: {len(rows)}",
            f"- Looks like timetable: {scrape_ssl.looks_like_timetable(rows)}",
            f"- Looks like ferry format: {scrape_ssl.looks_like_ferry_format(rows)}",
            "",
            markdown_table(rows),
            "",
        ])
    tables_path.parent.mkdir(parents=True, exist_ok=True)
    tables_path.write_text("\n".join(md), encoding="utf-8")

    endpoints, has_reverse = endpoint_summary(parsed)
    return {
        "route_id": route["route_id"],
        "route_type": route["route_type"],
        "slug": route["slug"],
        "url": route["url"],
        "source_kind": source_kind,
        "status_code": response.status_code,
        "sections": len(parsed.get("sections", [])),
        "trips": sum(len(s.get("trips", [])) for s in parsed.get("sections", [])),
        "tables": len(tables),
        "endpoint_pairs": dict((f"{a} -> {b}", count) for (a, b), count in endpoints.items()),
        "has_reverse_endpoint_pair": has_reverse,
        "local_scrape_diff": local_scrape_diff(route["route_id"], parsed),
        "raw_html": str(raw_path.relative_to(snapshot_dir)),
        "parsed_json": str(parsed_path.relative_to(snapshot_dir)),
        "extracted_tables": str(tables_path.relative_to(snapshot_dir)),
    }


def write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    snapshot_dir = SNAPSHOT_ROOT / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    configured = configured_routes()
    discovered = discover_route_links()
    configured_slugs = {route["slug"] for route in configured}
    extra = [route for route in discovered if route["slug"] not in configured_slugs]
    routes_to_fetch = [(route, "configured") for route in configured]
    routes_to_fetch.extend((route, "discovered-extra") for route in extra)

    write_json(snapshot_dir / "manifest.json", {
        "generated_at_local": timestamp,
        "ssl_route_overview_url": OVERVIEW_URL,
        "configured_route_count": len(configured),
        "discovered_route_count": len(discovered),
        "extra_discovered_route_count": len(extra),
        "snapshot_note": "Raw SSL HTML plus scraper-extracted tables and parsed JSON for manual verification.",
    })
    write_csv_rows(
        snapshot_dir / "route_overview_links.csv",
        ["route_id", "route_type", "slug", "url", "in_config"],
        [
            {**route, "in_config": route["slug"] in configured_slugs}
            for route in discovered
        ],
    )

    summaries = []
    errors = []
    for index, (route, source_kind) in enumerate(routes_to_fetch, start=1):
        label = f"{route['route_id']} {route['slug']}"
        print(f"[{index}/{len(routes_to_fetch)}] {label}")
        try:
            summaries.append(save_route(snapshot_dir, route, source_kind))
        except Exception as exc:
            errors.append({
                "route_id": route["route_id"],
                "slug": route["slug"],
                "url": route["url"],
                "source_kind": source_kind,
                "error": repr(exc),
            })
        if index < len(routes_to_fetch):
            time.sleep(0.25)

    write_json(snapshot_dir / "route_summaries.json", summaries)
    write_json(snapshot_dir / "errors.json", errors)

    write_csv_rows(
        snapshot_dir / "direction_audit.csv",
        [
            "route_id", "route_type", "slug", "source_kind", "sections", "trips",
            "has_reverse_endpoint_pair", "endpoint_pairs", "local_scrape_diff",
        ],
        [
            {
                "route_id": row["route_id"],
                "route_type": row["route_type"],
                "slug": row["slug"],
                "source_kind": row["source_kind"],
                "sections": row["sections"],
                "trips": row["trips"],
                "has_reverse_endpoint_pair": row["has_reverse_endpoint_pair"],
                "endpoint_pairs": json.dumps(row["endpoint_pairs"], ensure_ascii=False),
                "local_scrape_diff": row["local_scrape_diff"],
            }
            for row in summaries
        ],
    )

    summary_lines = [
        "# Live SSL Snapshot",
        "",
        f"- Generated at: `{timestamp}`",
        f"- Configured routes fetched: `{len(configured)}`",
        f"- Extra routes discovered from SSL overview: `{len(extra)}`",
        f"- Fetch/parse errors: `{len(errors)}`",
        "",
        "## Extra Discovered Routes",
        "",
    ]
    if extra:
        for route in extra:
            summary_lines.append(f"- Route `{route['route_id']}`: [{route['slug']}]({route['url']})")
    else:
        summary_lines.append("- None")

    changed = [row for row in summaries if row["source_kind"] == "configured" and row["local_scrape_diff"] != "same"]
    one_way = [row for row in summaries if not row["has_reverse_endpoint_pair"]]

    summary_lines.extend([
        "",
        "## Local Scrape Differences",
        "",
    ])
    if changed:
        for row in changed:
            summary_lines.append(
                f"- Route `{row['route_id']}` `{row['slug']}`: `{row['local_scrape_diff']}` "
                f"({row['sections']} sections, {row['trips']} trips)"
            )
    else:
        summary_lines.append("- None")

    summary_lines.extend([
        "",
        "## Endpoint Direction Audit",
        "",
        "This is a coarse check based on first/last parsed stop per section. Loops and branch routes need manual review.",
        "",
    ])
    for row in one_way:
        summary_lines.append(
            f"- Route `{row['route_id']}` `{row['slug']}`: no reversed endpoint pair found; "
            f"pairs = `{json.dumps(row['endpoint_pairs'], ensure_ascii=False)}`"
        )

    (snapshot_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"\nWrote snapshot to {snapshot_dir}")
    if errors:
        print(f"Completed with {len(errors)} error(s); see errors.json")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
