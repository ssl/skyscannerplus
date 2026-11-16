# skyscannerplus

A flight scanner that searches for the best vacation flight deals across all possible date combinations with flexible duration requirements.

## Features

- **Flexible search**: Searches all possible departure dates within your specified range
- **Variable duration**: Finds trips with your target duration ± tolerance (e.g., 20 days ± 3 days)
- **Data collection**: Captures airline names, prices, times, stops, duration, and more
- **JSON storage**: Each run saves its raw search responses in its own folder under `results/`
- **Resumable**: A stopped run continues where it left off with `--resume`
- **Detailed report**: Built-in reporter with a fare grid, filters, sorting and an image export

The script calculates all valid departure and return date pairs based on your constraints, systematically visits each Skyscanner URL for different date combinations, and saves the search results so the reporter can extract the flight data.

## Configuration

Edit `config.json`:

```json
{
    "from": "ams",
    "to": "bkkt",
    "adults": 2,
    "children_ages": [],
    "cabin": "economy",

    "vacation_days": 21,
    "days_clearance": 3,

    "search_start_date": "2025-07-29",
    "search_end_date": "2025-08-21"
}
```

| Key | Meaning |
| --- | --- |
| `from`, `to` | Departure and destination airport or city codes |
| `adults` | Number of adult passengers |
| `children_ages` | Age of each child, for example `[4, 9]`. Skyscanner needs ages, not a count |
| `cabin` | Cabin class (economy/premium/business/first) |
| `vacation_days` | Target vacation length in days |
| `days_clearance` | Tolerance in days (21 ± 3 = 18 to 24 days) |
| `search_start_date` | Earliest departure date |
| `search_end_date` | Latest return date: no trip ends after this day |
| `latest_departure_date` | Optional. Latest departure date, when it should be earlier than what the end date allows |

The above config will search for a vacation from Amsterdam (ams) to Bangkok (bkkt) that leaves on or after 29 july and is back by 21 august, with a
duration of around 21 days with 3 days tolerance, so anywhere between 18-24 days. This example generates 21 date combinations.

The config is checked before the browser starts, and a wrong value is reported with the key that needs fixing.

## Usage

```bash
python skyplus.py
```

Each run writes to a new folder, `results/<from>-<to>_<timestamp>/`, holding one `complete_<departure>_<return>.json` per date pair plus a `run.json` with the config used. A search that never finished loading is saved as `partial_...json` instead, so it is never mistaken for a full result.

If a run stops (a captcha that was not solved in time, Ctrl+C, a crash), continue it with:

```bash
python skyplus.py --resume
```

This reuses the latest run folder for the same route and skips every date pair that already has a complete result.

### Report

```bash
python reporter.py
```

This reads the latest run folder and generates a HTML report including:

- **Fare grid**: The cheapest fare for every departure date and trip length; select a cell to list its flights
- **Table with all found flights**: Find the best deals with full details, sortable by price, dates, duration, stops and airline
- **Filters**: Route, airline, direct only, max price and max flight durations; the figures at the top follow the filters
- **Image export**: The current selection as a PNG to share

To report on an older run, pass its folder: `python reporter.py results/ams-bkkt_20261116_230500`.

The page is one self-contained file. Its source lives in `report/` (template, styles, script) and is inlined by `reporter.py`.

## Requirements

- Python 3.9+
- seleniumbase
- Chrome browser

Install dependencies:
```bash
pip install -r requirements.txt
```

## Tests

```bash
python -m unittest discover -s tests -t .
```
