# skyscannerplus

A flight scanner that searches for the best vacation flight deals across all possible date combinations with flexible duration requirements.

## Features

- **Flexible search**: Searches all possible departure dates within your specified range
- **Variable duration**: Finds trips with your target duration ± tolerance (e.g., 20 days ± 3 days)
- **Data collection**: Captures airline names, prices, times, stops, duration, and more
- **JSON storage**: Results are saved continuously as JSON files
- **Detailed report**: Built-in reporter tool to find best deals and compare options

The script calculates all valid departure and return date pairs based on your constraints, systematically visits each Skyscanner URL for different date combinations, and parses the search results to extract flight data.

## Configuration

Edit the `config.json`:

```python
config = {
    'from': 'ams',                    # Departure airport code
    'to': 'bkkt',                     # Destination airport code  
    'adults': 2,                      # Number of adult passengers
    'children': 0,                    # Number of child passengers
    'cabin': 'economy',               # Cabin class (economy/premium/business/first)
    
    # Vacation parameters
    'vacation_days': 21,              # Target vacation length in days
    'days_clearance': 3,              # Tolerance in days (± 3 = 18-24 days)
    
    # Search date range
    'search_start_date': '2025-07-29', # Earliest departure date
    'search_end_date': '2025-08-21',   # Latest departure date

    # Resume option - set to x to start from search #x or 1 to start from beginning
    'start_from_search': 1, # For when you need to (re)start from a different point
}
```

The above config will search for a vacation from Amsterdam (ams) to Bangkok (bkkt) somewhere between 29 july and 21 august, with a
duration of around 21 days with 3 days tolerance, so anywhere between 18-24 days. This example generates 25 different combinations, finding a
total of 28500+ flight combinations ranging between 950-5000 euro and 23 up to 82 hours of flight duration.

## Usage

```bash
python skyplus.py
```

### Report

```bash
python reporter.py
```

This will generate a HTML report including:

- **Table with all found flights**: Find the best deals with full details
- **Duration comparison**: Consider price/duration to define the best deal for you
- **Direct vs stops comparison**: Price differences between direct and connecting flights

## Requirements

- Python
- seleniumbase
- Chrome browser

Install dependencies:
```bash
pip install seleniumbase
```
