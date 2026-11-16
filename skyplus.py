"""Search Skyscanner for return flights across every date combination in config.json.

Each run saves its raw search responses in its own folder under results/, so
reporter.py never mixes prices from different runs or routes.
"""
import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

SEARCH_API_PATH = "/g/radar/api/v2/web-unified-search"
RESULTS_DIR = Path("results")
RUN_INFO_FILE = "run.json"
SAVED_FILE_RE = re.compile(r"^(complete|partial)_(\d{8})_(\d{8})\.json$")

SEARCH_TIMEOUT_SECONDS = 60
CAPTCHA_TIMEOUT_SECONDS = 300

DateCombination = Tuple[datetime, datetime, int]

INTERCEPTOR_JS = r"""
(function () {
  if (window.__skyCapInstalled) return;
  window.__skyCapInstalled = true;
  window.__skyStatus = null;
  window.__skyComplete = null;
  window.__skyBest = null;
  window.__skyBestLen = 0;
  var PATH = '__SEARCH_API_PATH__';
  function handle(text) {
    try {
      if (!text) return;
      if (text.length > (window.__skyBestLen || 0)) {
        window.__skyBest = text;
        window.__skyBestLen = text.length;
      }
      var head = text.slice(0, 400);
      var m = head.match(/"status"\s*:\s*"([^"]+)"/);
      if (m) {
        window.__skyStatus = m[1];
        if (m[1] === 'complete' && !window.__skyComplete) {
          window.__skyComplete = text;
        }
      }
    } catch (e) {}
  }
  var of = window.fetch;
  if (of) {
    window.fetch = function () {
      var args = arguments;
      var p = of.apply(this, args);
      try {
        var u = (typeof args[0] === 'string') ? args[0]
              : (args[0] && args[0].url) ? args[0].url : '';
        return p.then(function (resp) {
          try {
            var ru = (resp && resp.url) ? resp.url : u;
            if (ru && ru.indexOf(PATH) !== -1) {
              resp.clone().text().then(handle).catch(function () {});
            }
          } catch (e) {}
          return resp;
        });
      } catch (e) { return p; }
    };
    try { window.fetch.toString = function () { return of.toString(); }; } catch (e) {}
  }
  var oo = XMLHttpRequest.prototype.open;
  var os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    try { this.__skyUrl = url; } catch (e) {}
    return oo.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var xhr = this;
    try {
      this.addEventListener('load', function () {
        try {
          if (xhr.__skyUrl && xhr.__skyUrl.indexOf(PATH) !== -1) {
            handle(xhr.responseText);
          }
        } catch (e) {}
      });
    } catch (e) {}
    return os.apply(this, arguments);
  };
})();
""".replace("__SEARCH_API_PATH__", SEARCH_API_PATH)


class ConfigError(ValueError):
    """config.json is missing, unreadable or holds a value the search cannot use."""


class CaptchaTimeout(RuntimeError):
    """A captcha was not solved by hand within the time allowed."""


# ---------------------------------------------------------------------------
# Config and dates
# ---------------------------------------------------------------------------

def _parse_date(config: Dict[str, Any], key: str) -> datetime:
    try:
        return datetime.strptime(str(config[key]), "%Y-%m-%d")
    except ValueError:
        raise ConfigError(f"'{key}' must be a date written as YYYY-MM-DD, got {config[key]!r}.")


def validate_config(config: Dict[str, Any]) -> None:
    required = ["from", "to", "adults", "cabin", "vacation_days", "days_clearance",
                "search_start_date", "search_end_date"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ConfigError(f"Missing config keys: {', '.join(missing)}.")

    for key in ("from", "to", "cabin"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ConfigError(f"'{key}' must be a non-empty string.")

    for key in ("adults", "vacation_days", "days_clearance"):
        if not isinstance(config[key], int) or isinstance(config[key], bool):
            raise ConfigError(f"'{key}' must be a whole number.")
    if config["adults"] < 1:
        raise ConfigError("'adults' must be at least 1.")
    if config["days_clearance"] < 0:
        raise ConfigError("'days_clearance' cannot be negative.")
    if config["vacation_days"] - config["days_clearance"] < 1:
        raise ConfigError("'vacation_days' minus 'days_clearance' must leave a trip of at least 1 day.")

    start = _parse_date(config, "search_start_date")
    end = _parse_date(config, "search_end_date")
    if end < start:
        raise ConfigError("'search_end_date' is before 'search_start_date'.")
    if config.get("latest_departure_date"):
        _parse_date(config, "latest_departure_date")

    ages = config.get("children_ages", [])
    if not isinstance(ages, list) or not all(
            isinstance(age, int) and not isinstance(age, bool) and 0 <= age <= 17 for age in ages):
        raise ConfigError("'children_ages' must be a list of ages from 0 to 17, for example [4, 9].")
    if config.get("children") and not ages:
        raise ConfigError(
            "Skyscanner needs the age of each child, not a count. "
            "Replace 'children' with 'children_ages', for example [4, 9].")


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    path = Path(config_file)
    if not path.exists():
        raise ConfigError(f"Config file '{config_file}' not found. Please create a config.json file.")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file: {e}")
    if not isinstance(config, dict):
        raise ConfigError("config.json must hold one JSON object.")
    validate_config(config)
    return config


def calculate_date_combinations(config: Dict[str, Any]) -> List[DateCombination]:
    """Every (departure, return, trip length) that fits the configured window.

    The return date never passes search_end_date; departures stop at
    latest_departure_date when that is set.
    """
    start_date = _parse_date(config, "search_start_date")
    end_date = _parse_date(config, "search_end_date")
    last_departure = end_date
    if config.get("latest_departure_date"):
        last_departure = _parse_date(config, "latest_departure_date")

    min_days = config["vacation_days"] - config["days_clearance"]
    max_days = config["vacation_days"] + config["days_clearance"]

    combinations = []
    current_date = start_date
    while current_date <= last_departure:
        for vacation_length in range(min_days, max_days + 1):
            return_date = current_date + timedelta(days=vacation_length)
            if return_date <= end_date:
                combinations.append((current_date, return_date, vacation_length))
        current_date += timedelta(days=1)
    return combinations


def drop_past_departures(combinations: List[DateCombination], today: datetime) -> List[DateCombination]:
    return [c for c in combinations if c[0] >= today]


def build_search_url(config: Dict[str, Any], dep_str: str, ret_str: str) -> str:
    """Build a Skyscanner search URL from the config and dates (YYMMDD)."""
    # childrenv2 is a list of ages joined with "|", not a head count.
    children_value = quote("|".join(str(age) for age in config.get("children_ages", [])))
    params = [
        f'adultsv2={config["adults"]}',
        f'cabinclass={config["cabin"]}',
        f'childrenv2={children_value}',
        'ref=home',
        'rtn=1',
        'outboundaltsenabled=false',
        'inboundaltsenabled=false',
        'preferdirects=false',
    ]
    query = '&'.join(params)
    return (
        f'https://www.skyscanner.nl/transport/vluchten/'
        f'{config["from"]}/{config["to"]}/{dep_str}/{ret_str}/?{query}'
    )


# ---------------------------------------------------------------------------
# Run folders
# ---------------------------------------------------------------------------

def route_slug(config: Dict[str, Any]) -> str:
    return f'{config["from"]}-{config["to"]}'.lower()


def new_run_dir(config: Dict[str, Any], now: datetime, results_dir: Path = RESULTS_DIR) -> Path:
    return results_dir / f"{route_slug(config)}_{now.strftime('%Y%m%d_%H%M%S')}"


def latest_run_dir(config: Dict[str, Any], results_dir: Path = RESULTS_DIR) -> Optional[Path]:
    """The most recent run folder for this route, if there is one."""
    if not results_dir.is_dir():
        return None
    runs = sorted(p for p in results_dir.glob(f"{route_slug(config)}_*") if p.is_dir())
    return runs[-1] if runs else None


def saved_pairs(run_dir: Path) -> Set[Tuple[str, str]]:
    """Date pairs (YYYYMMDD, YYYYMMDD) that already have a complete response."""
    pairs = set()
    if run_dir.is_dir():
        for path in run_dir.iterdir():
            match = SAVED_FILE_RE.match(path.name)
            if match and match.group(1) == "complete":
                pairs.add((match.group(2), match.group(3)))
    return pairs


def pair_key(dep_date: datetime, ret_date: datetime) -> Tuple[str, str]:
    return dep_date.strftime("%Y%m%d"), ret_date.strftime("%Y%m%d")


def write_run_info(run_dir: Path, config: Dict[str, Any], total_combinations: int, now: datetime) -> None:
    info_path = run_dir / RUN_INFO_FILE
    if info_path.exists():
        return
    info_path.write_text(json.dumps({
        "config": config,
        "started_at": now.isoformat(timespec="seconds"),
        "total_combinations": total_combinations,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Browser capture
# ---------------------------------------------------------------------------

class ResponseCapture:
    """Capture Skyscanner search API responses and save them into a run folder."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.dep_date: Optional[datetime] = None
        self.ret_date: Optional[datetime] = None
        self.saved = False
        self.last_status: Optional[str] = None

    def reset(self, dep_date: datetime, ret_date: datetime) -> None:
        self.dep_date = dep_date
        self.ret_date = ret_date
        self.saved = False
        self.last_status = None

    def try_capture(self, driver) -> bool:
        """Save a complete response if one is available."""
        if self.saved:
            return True
        try:
            state = driver.cdp.evaluate(
                "(window.__skyStatus || '') + '|' + (window.__skyComplete ? '1' : '0')")
        except Exception:
            return False
        status, _, has_complete = str(state or "|0").rpartition("|")
        if status and status != self.last_status:
            self.last_status = status
            print(f"API Status: {status}")
        if has_complete != "1":
            return False

        try:
            response_text = driver.cdp.evaluate("window.__skyComplete || ''")
        except Exception as e:
            print(f"Could not read captured response: {e}")
            return False
        return self._save(response_text, "complete")

    def save_fallback(self, driver) -> bool:
        """Save the largest response as a partial result if it contains flights."""
        if self.saved:
            return True
        try:
            best = driver.cdp.evaluate("window.__skyBest || ''")
        except Exception:
            return False
        if not best:
            return False
        try:
            results = json.loads(best).get('itineraries', {}).get('results', [])
        except (ValueError, AttributeError):
            return False
        if not results:
            return False
        print(f"No 'complete' response captured - saving best partial "
              f"({len(results)} results).")
        return self._save(best, "partial")

    def _save(self, response_text: str, kind: str) -> bool:
        if self.saved or not response_text or not self.dep_date or not self.ret_date:
            return False
        try:
            json_data = json.loads(response_text)
        except ValueError as e:
            print(f"Error parsing {kind} response: {e}")
            return False

        dep_str, ret_str = pair_key(self.dep_date, self.ret_date)
        path = self.run_dir / f"{kind}_{dep_str}_{ret_str}.json"
        try:
            path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            print(f"Error saving: {e}")
            return False

        if kind == "complete":
            # A complete response replaces any partial one from an earlier attempt.
            (self.run_dir / f"partial_{dep_str}_{ret_str}.json").unlink(missing_ok=True)
        print(f"Saved flights: {path}")
        self.saved = True
        return True


def install_interceptor(driver) -> None:
    """Inject the interceptor into the current page."""
    try:
        driver.cdp.evaluate(INTERCEPTOR_JS)
    except Exception:
        pass


def register_early_interceptor(driver) -> None:
    """Register the interceptor to run before each page's scripts."""
    import mycdp.page as cdp_page

    driver.cdp.loop.run_until_complete(
        driver.cdp.page.send(
            cdp_page.add_script_to_evaluate_on_new_document(source=INTERCEPTOR_JS)
        )
    )


def pump(driver, seconds: float) -> None:
    """Run the CDP event loop for the given duration."""
    driver.cdp.loop.run_until_complete(asyncio.sleep(seconds))


def check_for_captcha(driver) -> bool:
    try:
        return 'captcha' in driver.cdp.get_current_url()
    except Exception:
        return False


def handle_captcha(driver, timeout: int = CAPTCHA_TIMEOUT_SECONDS) -> None:
    """Wait for a captcha to be solved by hand; raise CaptchaTimeout if it is not."""
    print("\n" + "=" * 60)
    print("CAPTCHA DETECTED - please solve it by hand in the browser.")
    print("Waiting for you to complete it...")
    print("=" * 60)

    waited = 0
    while waited < timeout:
        pump(driver, 3)
        waited += 3
        if not check_for_captcha(driver):
            print("Captcha cleared. Continuing...")
            pump(driver, 2)
            return
        if waited % 15 == 0:
            print(f"Still waiting for captcha solve... ({waited}s)")
    raise CaptchaTimeout(f"Captcha not cleared within {timeout}s.")


POPUP_CLOSE_SELECTORS = [
    'button[aria-label="Venster sluiten"]',
    'button[aria-label="Close"]',
    'button[aria-label="Sluiten"]',
]


def close_popups(driver) -> None:
    """Dismiss any promo/info popup by clicking its close (X) button."""
    for selector in POPUP_CLOSE_SELECTORS:
        try:
            if driver.cdp.is_element_visible(selector):
                driver.cdp.click_if_visible(selector)
                print("Closed a popup.")
                pump(driver, 0.5)
        except Exception:
            pass


def accept_cookies(driver) -> None:
    """Accept the cookie banner if it is visible."""
    try:
        if driver.cdp.is_element_visible("#acceptCookieButton"):
            driver.cdp.click_if_visible("#acceptCookieButton")
            print("Accepted cookies.")
            pump(driver, 0.5)
    except Exception:
        pass


def open_search(driver, capture: ResponseCapture, url: str,
                dep_date: datetime, ret_date: datetime) -> None:
    capture.reset(dep_date, ret_date)
    driver.cdp.get(url)
    install_interceptor(driver)
    pump(driver, random.uniform(2, 4))


def search_one(driver, capture: ResponseCapture, config: Dict[str, Any],
               dep_date: datetime, ret_date: datetime) -> bool:
    """Run one date combination. Returns True when a response was saved."""
    dep_str = dep_date.strftime("%y%m%d")
    ret_str = ret_date.strftime("%y%m%d")
    url = build_search_url(config, dep_str, ret_str)

    print("Navigating to search page...")
    pump(driver, random.uniform(0.5, 1))
    open_search(driver, capture, url, dep_date, ret_date)

    if check_for_captcha(driver):
        handle_captcha(driver)
        print("Retrying search after captcha...")
        open_search(driver, capture, url, dep_date, ret_date)

    close_popups(driver)
    accept_cookies(driver)
    install_interceptor(driver)

    try:
        landed = driver.cdp.get_current_url()
    except Exception:
        landed = ""
    if "oym=" in landed or f"/{dep_str}/{ret_str}/" not in landed:
        print(f"Redirected to month view instead of results "
              f"({landed}) - skipping this date combination.")
        return False

    print("Waiting for complete API response...")
    wait_count = 0
    reloaded = False
    while not capture.try_capture(driver) and wait_count < SEARCH_TIMEOUT_SECONDS:
        pump(driver, 1)
        wait_count += 1
        install_interceptor(driver)

        if check_for_captcha(driver):
            print(f"Captcha detected during wait at {wait_count}s")
            handle_captcha(driver)
            print("Retrying search after captcha...")
            open_search(driver, capture, url, dep_date, ret_date)
            wait_count = 0
            reloaded = False
            continue

        if not reloaded and wait_count == 6 and capture.last_status is None:
            print("No API activity seen - reloading to re-capture...")
            reloaded = True
            capture.reset(dep_date, ret_date)
            driver.cdp.reload()
            install_interceptor(driver)
            accept_cookies(driver)
            continue

        if wait_count % 5 == 0:
            accept_cookies(driver)
            close_popups(driver)
        if wait_count % 10 == 0:
            print(f"Still waiting... ({wait_count}s elapsed)")

    if capture.saved:
        print(f"Got complete response after {wait_count}s")
        return True
    if capture.save_fallback(driver):
        return True
    print(f"No complete response after {wait_count}s - moving on")
    return False


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_search(config: Dict[str, Any], resume: bool = False) -> Optional[Path]:
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    all_combinations = calculate_date_combinations(config)
    combinations = drop_past_departures(all_combinations, today)
    skipped_past = len(all_combinations) - len(combinations)
    if skipped_past:
        print(f"WARNING: skipping {skipped_past} combinations with a departure "
              f"date in the past (check search_start_date in config.json).")
    if not combinations:
        print("No date combinations left to search - all departures are in "
              "the past. Update search_start_date/search_end_date in "
              "config.json.")
        return None

    if config.get("start_from_search", 1) not in (None, 1):
        print("NOTE: 'start_from_search' is no longer used. Run "
              "'python skyplus.py --resume' to continue the last run.")

    run_dir = latest_run_dir(config) if resume else None
    if resume and run_dir is None:
        print(f"No earlier run found for {route_slug(config)} - starting a new one.")
    if run_dir is None:
        run_dir = new_run_dir(config, now)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_run_info(run_dir, config, len(combinations), now)

    done = saved_pairs(run_dir)
    todo = [(number, combo) for number, combo in enumerate(combinations, 1)
            if pair_key(combo[0], combo[1]) not in done]
    total = len(combinations)

    print(f"Found {total} date combinations to search")
    print(f"Saving results to {run_dir}")
    if done:
        print(f"RESUMING: {total - len(todo)} already saved, {len(todo)} to go")
    if not todo:
        print("Every date combination in this run is already saved.")
        return run_dir

    from seleniumbase import Driver

    capture = ResponseCapture(run_dir)
    driver = Driver(
        uc=True,
        headless=False,
        incognito=False,
        guest_mode=False,
    )

    try:
        print("Starting browser in CDP mode...")
        driver.activate_cdp_mode("https://www.skyscanner.nl/")
        register_early_interceptor(driver)

        driver.cdp.set_window_rect(0, 0, 1366, 768)
        pump(driver, random.uniform(4, 7))

        if check_for_captcha(driver):
            handle_captcha(driver)

        close_popups(driver)
        accept_cookies(driver)
        close_popups(driver)

        for position, (number, (dep_date, ret_date, vacation_days)) in enumerate(todo, 1):
            print(f"\n[{number}/{total}] Searching: "
                  f"{dep_date.strftime('%d/%m')} → {ret_date.strftime('%d/%m')} "
                  f"({vacation_days} days)")

            search_one(driver, capture, config, dep_date, ret_date)

            if position < len(todo):
                delay = random.uniform(3, 5)
                print(f"Waiting {delay:.1f}s before next search...")
                pump(driver, delay)

    except CaptchaTimeout as e:
        print(f"\n{e} Stopping here. Run 'python skyplus.py --resume' to continue this run.")
    except KeyboardInterrupt:
        print("\nStopped. Run 'python skyplus.py --resume' to continue this run.")
    finally:
        print("Closing browser...")
        driver.quit()

    return run_dir


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.json", help="path to the config file")
    parser.add_argument("--resume", action="store_true",
                        help="continue the latest run for this route, skipping date pairs already saved")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}")
        return 1

    run_dir = run_search(config, resume=args.resume)
    if run_dir is not None:
        print(f"\nDone. Run 'python reporter.py' to build the report from {run_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
