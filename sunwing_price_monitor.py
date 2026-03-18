#!/usr/bin/env python3
"""
Sunwing Price Monitor
- Tracks configured Sunwing pages for package prices
- Saves history to CSV
- Builds a simple HTML dashboard
- Works best as a scheduled task

Notes:
- Sunwing changes site structure from time to time; selectors may need updating.
- This tool monitors displayed prices and trends. Final price is only confirmed at checkout.
"""

from __future__ import annotations
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "origin_city": "Calgary",
    "travelers": 2,
    "currency": "CAD",
    "date_filters": {
        "start_date": "2026-11-01",
        "end_date": "2027-04-30"
    },
    "destinations": [
        {
            "name": "Cancun",
            "url": "https://www.sunwing.ca/en/destinations/mexico/cancun"
        },
        {
            "name": "Riviera Maya",
            "url": "https://www.sunwing.ca/en/destinations/mexico/riviera-maya"
        },
        {
            "name": "Puerto Vallarta",
            "url": "https://www.sunwing.ca/en/destinations/mexico/puerto-vallarta"
        },
        {
            "name": "Los Cabos",
            "url": "https://www.sunwing.ca/en/destinations/mexico/los-cabos"
        },
        {
            "name": "Riviera Nayarit",
            "url": "https://www.sunwing.ca/en/destinations/mexico/riviera-nayarit"
        }
    ],
    "extra_pages": [
        {
            "name": "Lowest Prices of the Year",
            "url": "https://www.sunwing.ca/en/promotion/packages/lowest-prices-of-the-year"
        },
        {
            "name": "Lowest Price Calendar",
            "url": "https://www.sunwing.ca/en/lowest-price-calendar"
        }
    ],
    "alert_threshold_per_adult": 1600
}


def ensure_config(path: Path) -> None:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")


def fetch(url: str, timeout: int = 30) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text: str) -> Optional[str]:
    text = normalize_spaces(text)
    # Examples: Mar 25, 2026 / April 8, 2026
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def within_range(iso_date: Optional[str], start_date: Optional[str], end_date: Optional[str]) -> bool:
    if not iso_date:
        return True
    d = dt.date.fromisoformat(iso_date)
    if start_date and d < dt.date.fromisoformat(start_date):
        return False
    if end_date and d > dt.date.fromisoformat(end_date):
        return False
    return True


def extract_candidates_from_text(html_text: str, page_name: str) -> List[Dict[str, Any]]:
    """
    Heuristic parser for the visible text patterns Sunwing exposes in server-rendered HTML.
    It looks for sequences like:
      Destination
      Date
      [n] days All Inclusive
      Save up to...
      Was $...
      $1675
      per adult
      taxes and fees incl.
      Resort Name
    """
    text = re.sub(r"<[^>]+>", "\n", html_text)
    text = text.replace("&nbsp;", " ")
    lines = [normalize_spaces(x) for x in text.splitlines()]
    lines = [x for x in lines if x]
    candidates: List[Dict[str, Any]] = []

    for i, line in enumerate(lines):
        if re.fullmatch(r"\$\d[\d,]*", line):
            price = int(line.replace("$", "").replace(",", ""))
            window = lines[max(0, i - 8): min(len(lines), i + 8)]
            joined = " | ".join(window)

            destination = None
            travel_date = None
            resort_name = None
            nights = None

            # Find nearest date before or after the price.
            for nearby in window:
                parsed = parse_date(nearby)
                if parsed:
                    travel_date = parsed
                    break

            # Nights
            for nearby in window:
                m = re.search(r"(\d+)\s+days\s+All Inclusive", nearby, re.I)
                if m:
                    nights = int(m.group(1))
                    break

            # Destination heuristics
            dest_patterns = [
                "Mexico", "Cancun", "Riviera Maya", "Puerto Vallarta",
                "Los Cabos", "Riviera Nayarit", "Punta Cana", "Jamaica",
                "Costa Rica", "Cuba", "Dominican Republic"
            ]
            for nearby in window:
                if any(dp.lower() in nearby.lower() for dp in dest_patterns):
                    destination = nearby
                    break

            # Resort name: look a line or two after the price/per-adult marker, else before.
            for offset in (3, 4, 2, -1, -2, 5):
                j = i + offset
                if 0 <= j < len(lines):
                    val = lines[j]
                    if (
                        not val.startswith("$")
                        and "per adult" not in val.lower()
                        and "taxes and fees" not in val.lower()
                        and "save up to" not in val.lower()
                        and "all inclusive" not in val.lower()
                        and parse_date(val) is None
                        and len(val) > 4
                    ):
                        resort_name = val
                        break

            if not resort_name and destination:
                resort_name = f"{destination} deal"

            candidates.append(
                {
                    "source_page": page_name,
                    "destination": destination or page_name,
                    "travel_date": travel_date,
                    "nights": nights,
                    "price_per_adult": price,
                    "resort_name": resort_name or "Unknown resort",
                    "raw_context": joined,
                }
            )

    # Deduplicate by (page, destination, date, price, resort)
    deduped = {}
    for c in candidates:
        key = (
            c["source_page"],
            c["destination"],
            c["travel_date"],
            c["price_per_adult"],
            c["resort_name"],
        )
        deduped[key] = c
    return list(deduped.values())


def scrape_page(page_name: str, url: str, start_date: Optional[str], end_date: Optional[str]) -> List[Dict[str, Any]]:
    html_text = fetch(url)
    candidates = extract_candidates_from_text(html_text, page_name)
    return [c for c in candidates if within_range(c.get("travel_date"), start_date, end_date)]


def append_history(csv_path: Path, rows: List[Dict[str, Any]], travelers: int) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists()
    fieldnames = [
        "checked_at",
        "source_page",
        "destination",
        "travel_date",
        "nights",
        "price_per_adult",
        "total_for_party",
        "resort_name",
        "raw_context",
    ]
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        checked_at = dt.datetime.now().isoformat(timespec="seconds")
        for row in rows:
            out = dict(row)
            out["checked_at"] = checked_at
            out["total_for_party"] = row["price_per_adult"] * travelers
            w.writerow(out)


def load_history(csv_path: Path) -> List[Dict[str, Any]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def summarize_latest(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not rows:
        return [], None
    latest = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["destination"], []).append(r)

    for dest, items in grouped.items():
        items.sort(key=lambda x: int(x["price_per_adult"]))
        latest.append(items[0])

    latest.sort(key=lambda x: int(x["price_per_adult"]))
    overall = latest[0] if latest else None
    return latest, overall


def price_history_by_destination(history: List[Dict[str, Any]]) -> Dict[str, List[Tuple[str, int]]]:
    by_dest: Dict[str, List[Tuple[str, int]]] = {}
    for row in history:
        by_dest.setdefault(row["destination"], []).append((row["checked_at"], int(row["price_per_adult"])))
    for dest in by_dest:
        by_dest[dest].sort(key=lambda x: x[0])
    return by_dest


def build_dashboard(
    output_path: Path,
    latest_rows: List[Dict[str, Any]],
    overall: Optional[Dict[str, Any]],
    history: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> None:
    title = "Sunwing Mexico Price Monitor"
    by_dest_hist = price_history_by_destination(history)

    def trend_text(dest: str, current: int) -> str:
        pts = by_dest_hist.get(dest, [])
        if len(pts) < 2:
            return "new"
        prev = pts[-2][1]
        diff = current - prev
        if diff == 0:
            return "no change"
        arrow = "↓" if diff < 0 else "↑"
        return f"{arrow} ${abs(diff)} vs last check"

    cards = []
    for row in latest_rows:
        price = int(row["price_per_adult"])
        total = price * int(config.get("travelers", 2))
        cards.append(f"""
        <div class="card">
          <div class="dest">{html.escape(row['destination'])}</div>
          <div class="price">${price:,}<span>/adult</span></div>
          <div class="total">2 people: ${total:,}</div>
          <div class="meta"><strong>Resort:</strong> {html.escape(row['resort_name'])}</div>
          <div class="meta"><strong>Travel date:</strong> {html.escape(row.get('travel_date') or 'not found')}</div>
          <div class="meta"><strong>Source:</strong> {html.escape(row['source_page'])}</div>
          <div class="trend">{html.escape(trend_text(row['destination'], price))}</div>
        </div>
        """)

    history_rows = []
    recent = sorted(history, key=lambda x: x["checked_at"], reverse=True)[:50]
    for row in recent:
        history_rows.append(f"""
        <tr>
          <td>{html.escape(row['checked_at'])}</td>
          <td>{html.escape(row['destination'])}</td>
          <td>{html.escape(row['resort_name'])}</td>
          <td>{html.escape(row.get('travel_date') or '')}</td>
          <td>${int(row['price_per_adult']):,}</td>
          <td>${int(row['total_for_party']):,}</td>
          <td>{html.escape(row['source_page'])}</td>
        </tr>
        """)

    overall_html = ""
    if overall:
        overall_html = f"""
        <div class="hero">
          <div class="hero-label">Cheapest current find</div>
          <div class="hero-main">{html.escape(overall['destination'])} — ${int(overall['price_per_adult']):,}/adult</div>
          <div class="hero-sub">{html.escape(overall['resort_name'])} | {html.escape(overall.get('travel_date') or 'date not found')}</div>
        </div>
        """

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
    font-family: Segoe UI, Arial, sans-serif;
    margin: 0;
    background: #0f172a;
    color: #e5e7eb;
}}
.wrap {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
}}
h1 {{
    margin: 0 0 6px 0;
    font-size: 28px;
}}
.sub {{
    color: #94a3b8;
    margin-bottom: 20px;
}}
.hero {{
    background: linear-gradient(135deg, #1d4ed8, #0f766e);
    padding: 18px 20px;
    border-radius: 16px;
    margin-bottom: 20px;
}}
.hero-label {{
    font-size: 13px;
    opacity: 0.9;
}}
.hero-main {{
    font-size: 28px;
    font-weight: 700;
    margin-top: 4px;
}}
.hero-sub {{
    margin-top: 6px;
    color: #dbeafe;
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
}}
.card {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 16px;
    padding: 16px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.25);
}}
.dest {{
    font-size: 19px;
    font-weight: 700;
    margin-bottom: 6px;
}}
.price {{
    font-size: 30px;
    font-weight: 800;
}}
.price span {{
    font-size: 14px;
    color: #93c5fd;
    margin-left: 6px;
}}
.total, .meta, .trend {{
    margin-top: 8px;
    color: #cbd5e1;
}}
.section-title {{
    margin: 30px 0 10px;
    font-size: 20px;
    font-weight: 700;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    background: #111827;
    border-radius: 14px;
    overflow: hidden;
}}
th, td {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #1f2937;
    font-size: 14px;
}}
th {{
    background: #0b1220;
    color: #93c5fd;
}}
.note {{
    margin-top: 20px;
    color: #94a3b8;
    font-size: 13px;
}}
code {{
    color: #fde68a;
}}
</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <div class="sub">Origin: {html.escape(config.get('origin_city', ''))} | Travelers: {config.get('travelers', 2)} | Date filter: {html.escape(config.get('date_filters', {}).get('start_date', ''))} to {html.escape(config.get('date_filters', {}).get('end_date', ''))}</div>
  {overall_html}
  <div class="grid">
    {''.join(cards) if cards else '<div class="card">No prices found on this run.</div>'}
  </div>

  <div class="section-title">Recent checks</div>
  <table>
    <thead>
      <tr>
        <th>Checked</th>
        <th>Destination</th>
        <th>Resort</th>
        <th>Travel date</th>
        <th>Price / adult</th>
        <th>Total for party</th>
        <th>Source</th>
      </tr>
    </thead>
    <tbody>
      {''.join(history_rows) if history_rows else '<tr><td colspan="7">No history yet.</td></tr>'}
    </tbody>
  </table>

  <div class="note">
    This tool monitors published prices and trends from Sunwing pages. Final price and availability are confirmed only at checkout.
    If Sunwing changes the HTML structure, the parser in <code>sunwing_price_monitor.py</code> may need a quick selector update.
  </div>
</div>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")


def run(config_path: Path, output_dir: Path) -> int:
    ensure_config(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)

    date_filters = config.get("date_filters", {})
    start_date = date_filters.get("start_date")
    end_date = date_filters.get("end_date")
    travelers = int(config.get("travelers", 2))

    pages = []
    pages.extend(config.get("destinations", []))
    pages.extend(config.get("extra_pages", []))

    all_rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    for page in pages:
        name = page["name"]
        url = page["url"]
        try:
            rows = scrape_page(name, url, start_date, end_date)
            for row in rows:
                # Skip obvious non-Mexico deals if destination pages include cross-site promos.
                if "mexico" in (url.lower() + " " + name.lower()):
                    mexico_ok = any(
                        term in (row.get("destination", "") + " " + row.get("resort_name", "")).lower()
                        for term in ["mexico", "cancun", "riviera maya", "puerto vallarta", "los cabos", "riviera nayarit"]
                    )
                    if not mexico_ok and name != "Lowest Price Calendar":
                        continue
                all_rows.append(row)
        except Exception as e:
            errors.append(f"{name}: {e}")

    history_file = output_dir / "sunwing_price_history.csv"
    append_history(history_file, all_rows, travelers)

    latest_rows, overall = summarize_latest(all_rows)
    dashboard_path = output_dir / "sunwing_dashboard.html"
    history = load_history(history_file)
    build_dashboard(dashboard_path, latest_rows, overall, history, config)

    print(f"Dashboard written to: {dashboard_path}")
    print(f"History written to:   {history_file}")
    if overall:
        print(f"Cheapest current find: {overall['destination']} | {overall['resort_name']} | ${overall['price_per_adult']}/adult")
        threshold = config.get("alert_threshold_per_adult")
        if threshold is not None and int(overall["price_per_adult"]) <= int(threshold):
            print(f"ALERT: current cheapest price is at or below threshold ${threshold}/adult")
    else:
        print("No price cards parsed on this run.")
    if errors:
        print("\nSome pages had errors:")
        for err in errors:
            print(f" - {err}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Sunwing package prices and build an HTML dashboard.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--output", default="output", help="Output folder for CSV and dashboard.")
    args = parser.parse_args()

    return run(Path(args.config), Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
