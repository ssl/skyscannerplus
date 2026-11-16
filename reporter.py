"""Build an HTML fare report from the search responses saved by skyplus.py.

Usage:
    python reporter.py              # the latest run folder under results/
    python reporter.py results/ams-bkkt_20261116_230500
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPORT_DIR = Path(__file__).resolve().parent / "report"
RESULTS_DIR = Path("results")
RUN_INFO_FILE = "run.json"
SAVED_FILE_RE = re.compile(r"^(complete|partial)_(\d{8})_(\d{8})")

# One row per flight in the report's data block, in this order.
FIELDS = [
    "id", "dep", "ret", "price", "book",
    "o_from", "o_to", "o_air", "o_dep", "o_arr", "o_dur", "o_stops", "o_via",
    "r_from", "r_to", "r_air", "r_dep", "r_arr", "r_dur", "r_stops", "r_via",
]
MAX_BOOKING_OPTIONS = 3


# ---------------------------------------------------------------------------
# Reading a run folder
# ---------------------------------------------------------------------------

def find_run_dir(results_dir: Path = RESULTS_DIR, legacy_dir: Path = Path(".")) -> Optional[Path]:
    """The newest run folder with saved searches, or the working directory for old-style files."""
    if results_dir.is_dir():
        runs = [p for p in results_dir.iterdir() if p.is_dir() and list_searches(p)]
        if runs:
            return max(runs, key=lambda p: p.stat().st_mtime)
    if list_searches(legacy_dir):
        return legacy_dir
    return None


def list_searches(run_dir: Path) -> List[Dict[str, Any]]:
    """One saved search per date pair: a complete response beats a partial one, newer beats older."""
    chosen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for path in sorted(run_dir.glob("*.json")):
        match = SAVED_FILE_RE.match(path.name)
        if not match:
            continue
        kind, dep, ret = match.groups()
        entry = {"path": path, "kind": kind, "dep": _iso_date(dep), "ret": _iso_date(ret),
                 "mtime": path.stat().st_mtime}
        current = chosen.get((dep, ret))
        if current is None or _preference(entry) > _preference(current):
            chosen[(dep, ret)] = entry
    return sorted(chosen.values(), key=lambda e: (e["dep"], e["ret"]))


def _preference(entry: Dict[str, Any]) -> Tuple[int, float]:
    return (1 if entry["kind"] == "complete" else 0, entry["mtime"])


def _iso_date(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def load_run_info(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / RUN_INFO_FILE
    if not path.exists():
        return {}
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return info if isinstance(info, dict) else {}


# ---------------------------------------------------------------------------
# Extracting flights from one search response
# ---------------------------------------------------------------------------

def _number(value: Any) -> float:
    """A positive number, or 0 for anything missing, null or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value if value > 0 else 0


def _minute(dt_str: Any) -> Optional[str]:
    """A timestamp cut to 'YYYY-MM-DDTHH:MM', or None when it does not parse."""
    if not isinstance(dt_str, str) or not dt_str:
        return None
    try:
        parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M")


def _layover_minutes(arrival: Any, departure: Any) -> Optional[int]:
    start, end = _minute(arrival), _minute(departure)
    if not start or not end:
        return None
    minutes = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() // 60)
    return minutes if minutes >= 0 else None


def extract_stops(leg: Dict[str, Any]) -> List[List[Any]]:
    """Each stopover as [airport code, country, layover minutes or None]."""
    segments = leg.get("segments") or []
    if not leg.get("stopCount") or len(segments) < 2:
        return []
    stops = []
    for arriving, leaving in zip(segments, segments[1:]):
        place = arriving.get("destination") or {}
        stops.append([
            place.get("displayCode", "Unknown"),
            place.get("country", "Unknown"),
            _layover_minutes(arriving.get("arrival"), leaving.get("departure")),
        ])
    return stops


def extract_airlines(leg: Dict[str, Any], airline_names: Dict[Any, str]) -> Optional[str]:
    names = []
    for carrier in (leg.get("carriers") or {}).get("marketing") or []:
        carrier_id = carrier.get("id")
        name = airline_names.get(carrier_id) or carrier.get("name") or f"ID:{carrier_id}"
        if name not in names:
            names.append(name)
    return " + ".join(names) if names else None


def extract_leg(leg: Dict[str, Any], airline_names: Dict[Any, str], prefix: str) -> Dict[str, Any]:
    return {
        f"{prefix}_from": (leg.get("origin") or {}).get("displayCode", "N/A"),
        f"{prefix}_to": (leg.get("destination") or {}).get("displayCode", "N/A"),
        f"{prefix}_air": extract_airlines(leg, airline_names),
        f"{prefix}_dep": _minute(leg.get("departure")),
        f"{prefix}_arr": _minute(leg.get("arrival")),
        f"{prefix}_dur": int(_number(leg.get("durationInMinutes"))),
        f"{prefix}_stops": int(_number(leg.get("stopCount"))),
        f"{prefix}_via": extract_stops(leg),
    }


def extract_price(item: Dict[str, Any], agents: Dict[Any, str]) -> Tuple[float, List[List[Any]]]:
    """The cheapest price and up to three booking options as [price, agent names]."""
    options = []
    for option in item.get("pricingOptions") or []:
        amount = _number((option.get("price") or {}).get("amount"))
        if amount:
            options.append((amount, option))
    options.sort(key=lambda pair: pair[0])

    if not options:
        price = item.get("price")
        return (_number(price.get("raw")) if isinstance(price, dict) else 0), []

    booking = []
    for amount, option in options:
        names = [str(agents.get(agent_id, agent_id)) for agent_id in option.get("agentIds") or []]
        if names:
            booking.append([amount, ", ".join(names)])
        if len(booking) == MAX_BOOKING_OPTIONS:
            break
    return options[0][0], booking


def extract_flights(json_data: Dict[str, Any], dep_date: str, ret_date: str) -> List[Dict[str, Any]]:
    """Every priced itinerary in one search response, as flat dicts keyed by FIELDS."""
    itineraries = json_data.get("itineraries") or {}
    agents = {agent["id"]: agent["name"] for agent in itineraries.get("agents") or []
              if "id" in agent and "name" in agent}
    airline_names = {carrier["id"]: carrier["name"]
                     for carrier in (itineraries.get("filterStats") or {}).get("carriers") or []
                     if "id" in carrier and "name" in carrier}

    flights = []
    for item in itineraries.get("results") or []:
        legs = item.get("legs") or []
        if not legs:
            continue
        price, booking = extract_price(item, agents)
        if not price:
            continue
        flight = {"id": item.get("id", "N/A"), "dep": dep_date, "ret": ret_date,
                  "price": price, "book": booking}
        flight.update(extract_leg(legs[0], airline_names, "o"))
        if len(legs) > 1:
            flight.update(extract_leg(legs[1], airline_names, "r"))
        else:
            flight.update({f"r_{name}": None for name in
                           ("from", "to", "air", "dep", "arr", "dur", "stops", "via")})
        flights.append(flight)
    return flights


def collect_flights(searches: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """All flights from the saved searches, cheapest first, plus how many files were read."""
    flights: List[Dict[str, Any]] = []
    seen = set()
    processed = 0
    for search in searches:
        path = search["path"]
        print(f"Processing {path.name}...")
        try:
            json_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Error reading {path.name}: {e}")
            continue
        if not isinstance(json_data, dict):
            print(f"Error reading {path.name}: not a search response")
            continue
        found = extract_flights(json_data, search["dep"], search["ret"])
        processed += 1
        print(f"Extracted {len(found)} flight options")
        for flight in found:
            key = (flight["id"], flight["dep"], flight["ret"], flight["price"])
            if key not in seen:
                seen.add(key)
                flights.append(flight)
    flights.sort(key=lambda f: f["price"])
    return flights, processed


# ---------------------------------------------------------------------------
# Building the page
# ---------------------------------------------------------------------------

def build_payload(flights: List[Dict[str, Any]], searches: List[Dict[str, Any]],
                  run_info: Dict[str, Any], generated: datetime) -> Dict[str, Any]:
    config = run_info.get("config") or {}
    searched = run_info.get("started_at")
    if not searched and searches:
        searched = datetime.fromtimestamp(max(s["mtime"] for s in searches)).isoformat(timespec="seconds")
    return {
        "meta": {
            "generated": generated.isoformat(timespec="seconds"),
            "searched": searched,
            "adults": config.get("adults"),
            "children_ages": config.get("children_ages") or [],
            "cabin": config.get("cabin"),
            "pairs": len(searches),
            "partial_pairs": sum(1 for s in searches if s["kind"] == "partial"),
        },
        "fields": FIELDS,
        "rows": [[flight[field] for field in FIELDS] for flight in flights],
    }


def _inline_css() -> str:
    css = (REPORT_DIR / "report.css").read_text(encoding="utf-8")
    for name in ("tokens.css", "base.css"):
        css = css.replace(f'@import url("./{name}");', (REPORT_DIR / name).read_text(encoding="utf-8").strip())
    return css


def render_html(payload: Dict[str, Any], title: str = "Fare report, skyscannerplus") -> str:
    """One self-contained page: the template with its styles, script and data inlined."""
    # "<" never appears raw in the data block, so no value can close the script tag.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = (REPORT_DIR / "template.html").read_text(encoding="utf-8")
    parts = {
        "__TITLE__": html.escape(title),
        "/*__CSS__*/": _inline_css(),
        "/*__JS__*/": (REPORT_DIR / "report.js").read_text(encoding="utf-8"),
        "__DATA__": data,
    }
    # Split on the placeholders once, so text inside one part is never mistaken for another.
    pieces = re.split("(" + "|".join(re.escape(key) for key in parts) + ")", page)
    return "".join(parts.get(piece, piece) for piece in pieces)


def generate_report(run_dir: Path, output_file: Optional[Path] = None) -> Optional[Path]:
    searches = list_searches(run_dir)
    if not searches:
        print(f"Error: no saved searches (complete_*.json) found in {run_dir}")
        return None
    print(f"Found {len(searches)} saved searches in {run_dir}")

    flights, processed = collect_flights(searches)
    print(f"Files processed: {processed}/{len(searches)}")
    print(f"Total flight options: {len(flights)}")
    if not flights:
        print("No flight data could be extracted from the JSON files")
        return None

    now = datetime.now()
    if output_file is None:
        output_file = Path(f"skyscannerplus_report_{now.strftime('%Y%m%d_%H%M%S')}.html")
    payload = build_payload(flights, searches, load_run_info(run_dir), now)
    output_file.write_text(render_html(payload), encoding="utf-8")

    routes = {(f["o_from"], f["o_to"]) for f in flights}
    airlines = {name for f in flights for joined in (f["o_air"], f["r_air"]) if joined
                for name in joined.split(" + ")}
    print(f"Unique routes: {len(routes)}")
    print(f"Airlines found: {len(airlines)}")
    print(f"HTML report generated: {output_file}")
    return output_file


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", nargs="?", help="run folder to report on (default: the latest under results/)")
    parser.add_argument("-o", "--output", help="where to write the HTML report")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else find_run_dir()
    if run_dir is None or not run_dir.is_dir():
        print("Error: no run folder found. Run 'python skyplus.py' first, or pass a folder.")
        return 1

    output = generate_report(run_dir, Path(args.output) if args.output else None)
    if output is None:
        return 1
    print(f"Analysis complete! Open {output} in your browser to view the results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
