from seleniumbase import Driver
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
import os

SEARCH_API_PATH = "/g/radar/api/v2/web-unified-search"

# Injected via evaluate() (runs in the page's main world) after each navigation
# and re-run on every poll. It wraps fetch/XHR so responses from the search API
# are captured client-side and stashed on `window`. We avoid CDP's
# getResponseBody (which throws on the bodyless CORS-preflight response and
# tears down the whole CDP connection). The __skyCapInstalled guard makes
# re-injection a no-op; if the document reloaded, the globals are gone so it
# reinstalls cleanly.
INTERCEPTOR_JS = r"""
(function () {
  if (window.__skyCapInstalled) return;
  window.__skyCapInstalled = true;
  window.__skyStatus = null;
  window.__skyComplete = null;
  window.__skyBest = null;
  window.__skyBestLen = 0;
  var PATH = '/g/radar/api/v2/web-unified-search';
  function handle(text) {
    try {
      if (!text) return;
      // Keep the largest payload we've seen as a fallback (the complete
      // response is the biggest; a late-injected interceptor that missed the
      // transient "complete" poll can still salvage the fullest data).
      if (text.length > (window.__skyBestLen || 0)) {
        window.__skyBest = text;
        window.__skyBestLen = text.length;
      }
      var head = text.slice(0, 400);
      // The first "status" in the payload is context.status.
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
"""


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file '{config_file}' not found. Please create a config.json file.")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise Exception(f"Error reading config file: {e}")


config = load_config()


class FlightScanner:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.results = []
        self.results_file = f"flight_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        self.current_search = 0
        self.total_searches = 0

    def calculate_date_combinations(self) -> List[tuple]:
        start_date = datetime.strptime(f"{self.config['search_start_date']}", "%Y-%m-%d")
        end_date = datetime.strptime(f"{self.config['search_end_date']}", "%Y-%m-%d")

        target_days = self.config['vacation_days']
        days_tolerance = self.config['days_clearance']
        min_days = target_days - days_tolerance
        max_days = target_days + days_tolerance

        combinations = []
        current_date = start_date

        while current_date <= end_date:
            for vacation_length in range(min_days, max_days + 1):
                return_date = current_date + timedelta(days=vacation_length)
                if return_date <= end_date:
                    combinations.append((current_date, return_date, vacation_length))
            current_date += timedelta(days=1)

        return combinations

    def save_results_to_file(self):
        try:
            with open(self.results_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'search_config': self.config,
                    'total_combinations': self.total_searches,
                    'completed_searches': self.current_search,
                    'timestamp': datetime.now().isoformat(),
                    'results': self.results
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving results: {e}")

    def log_progress(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")


class ResponseCapture:
    """Captures Skyscanner search API responses by polling the page-side
    interceptor (see INTERCEPTOR_JS).

    In CDP Mode SeleniumBase drives Chrome through the DevTools Protocol
    (no Selenium WebDriver, no selenium-wire proxy), which is what keeps the
    bot-detection captcha from appearing. Selenium-wire's response_interceptor
    is not available here, and pulling bodies via CDP getResponseBody proved
    fragile, so instead we let the page itself stash the JSON on `window` and
    read it back with evaluate().
    """

    def __init__(self):
        self.dep_date = None
        self.ret_date = None
        self.saved = False
        self.last_status = None

    def reset(self, dep_date, ret_date):
        self.dep_date = dep_date
        self.ret_date = ret_date
        self.saved = False
        self.last_status = None

    def try_capture(self, driver) -> bool:
        """Check the page for a completed response; save it if present.
        Returns True once a complete response has been saved."""
        if self.saved:
            return True
        try:
            status = driver.cdp.evaluate("window.__skyStatus || ''")
            if status and status != self.last_status:
                self.last_status = status
                print(f"API Status: {status}")
        except Exception:
            pass

        try:
            has_complete = driver.cdp.evaluate("!!window.__skyComplete")
        except Exception:
            has_complete = False

        if not has_complete:
            return False

        try:
            response_text = driver.cdp.evaluate("window.__skyComplete || ''")
        except Exception as e:
            print(f"Could not read captured response: {e}")
            return False

        return self._save(response_text)

    def save_fallback(self, driver) -> bool:
        """Last-resort save when no 'complete' response was captured: use the
        largest response we saw, but only if it actually contains flight
        results. Better partial data than nothing on a missed complete poll."""
        if self.saved:
            return True
        try:
            best = driver.cdp.evaluate("window.__skyBest || ''")
        except Exception:
            return False
        if not best:
            return False
        try:
            json_data = json.loads(best)
        except Exception:
            return False
        results = json_data.get('itineraries', {}).get('results', [])
        if not results:
            return False
        print(f"No 'complete' response captured - saving best partial "
              f"({len(results)} results).")
        return self._save(best)

    def _save(self, response_text: str) -> bool:
        if self.saved or not response_text or not self.dep_date or not self.ret_date:
            return False
        try:
            json_data = json.loads(response_text)
        except Exception as e:
            print(f"Error parsing complete response: {e}")
            return False

        try:
            context = json_data.get('context', {})
            session_id = context.get('sessionId', 'unknown')

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            dep_str = self.dep_date.strftime('%Y%m%d')
            ret_str = self.ret_date.strftime('%Y%m%d')

            short_session = session_id[:15] if session_id != 'unknown' else timestamp[-6:]
            filename = f"complete_{dep_str}_{ret_str}_{short_session}.json"

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)

            print(f"Saved flights: {filename}")
            self.saved = True
            return True
        except Exception as e:
            print(f"Error saving: {e}")
            return False


def install_interceptor(driver):
    """Inject the fetch/XHR interceptor into the current page's main world.
    Idempotent thanks to the __skyCapInstalled guard, so it's safe to call
    repeatedly (which also re-installs it after a document reload)."""
    try:
        driver.cdp.evaluate(INTERCEPTOR_JS)
    except Exception:
        pass


def pump(driver, seconds: float):
    """Run the CDP event loop for `seconds` so queued network events and the
    body-fetch tasks they schedule actually get processed. CDP Mode's own
    sleep() is just time.sleep() and does NOT advance the loop."""
    driver.cdp.loop.run_until_complete(asyncio.sleep(seconds))


def check_for_captcha(driver) -> bool:
    try:
        current_url = driver.cdp.get_current_url()
        return 'captcha' in current_url
    except Exception:
        return False


def handle_captcha(driver, timeout=300):
    """Under CDP Mode the captcha can be solved by hand and it sticks (the
    page is not flagged as automated). So we just keep the CDP loop pumping
    and wait for the human to solve it, detecting completion via the URL."""
    print("\n" + "=" * 60)
    print("CAPTCHA DETECTED — please solve it by hand in the browser.")
    print("Waiting for you to complete it...")
    print("=" * 60)

    waited = 0
    while waited < timeout:
        pump(driver, 3)
        waited += 3
        if not check_for_captcha(driver):
            print("Captcha cleared. Continuing...")
            pump(driver, 2)
            return True
        if waited % 15 == 0:
            print(f"Still waiting for captcha solve... ({waited}s)")
    print("Captcha not cleared within timeout.")
    return False


# Locale-specific close button on Skyscanner promo/info popups (.nl site).
POPUP_CLOSE_SELECTORS = [
    'button[aria-label="Venster sluiten"]',
    'button[aria-label="Close"]',
    'button[aria-label="Sluiten"]',
]


def close_popups(driver):
    """Dismiss any promo/info popup by clicking its close (X) button."""
    for selector in POPUP_CLOSE_SELECTORS:
        try:
            if driver.cdp.is_element_visible(selector):
                driver.cdp.click_if_visible(selector)
                print("Closed a popup.")
                pump(driver, 0.5)
        except Exception:
            pass


def accept_cookies(driver):
    """Accept the cookie banner if present. It can appear on the homepage and
    also (re)appear on search-result pages, so this is called repeatedly."""
    try:
        if driver.cdp.is_element_visible("#acceptCookieButton"):
            driver.cdp.click_if_visible("#acceptCookieButton")
            print("Accepted cookies.")
            pump(driver, 0.5)
    except Exception:
        pass


def build_search_url(config, dep_str, ret_str):
    """Build the Skyscanner search URL. childrenv2 must be omitted entirely
    when there are 0 children (Skyscanner treats childrenv2=0 as 1 child)."""
    params = [
        f'adultsv2={config["adults"]}',
        f'cabinclass={config["cabin"]}',
    ]
    if int(config.get("children", 0)) > 0:
        params.append(f'childrenv2={config["children"]}')
    params += [
        'inboundaltsenabled=false',
        'outboundaltsenabled=false',
        'preferdirects=false',
        'rtn=1',
        'qp_prevCurrency=EUR',
    ]
    query = '&'.join(params)
    return (
        f'https://www.skyscanner.nl/transport/vluchten/'
        f'{config["from"]}/{config["to"]}/{dep_str}/{ret_str}/?{query}'
    )


def search_flights_simple(config):
    import random

    scanner = FlightScanner(config)
    date_combinations = scanner.calculate_date_combinations()
    original_total = len(date_combinations)

    print(f"Found {original_total} date combinations to search")

    start_from = config.get('start_from_search', 1)
    if start_from > 1:
        print(f"RESUMING from search #{start_from} (skipping first {start_from-1} searches)")
        date_combinations = date_combinations[start_from-1:]
        print(f"Will search {len(date_combinations)} remaining combinations")
        print(f"Search numbers will be: #{start_from} to #{original_total}")
    else:
        print(f"Starting from the beginning - search numbers: #1 to #{original_total}")

    capture = ResponseCapture()

    # uc=True enables undetected mode, required to activate CDP Mode below.
    driver = Driver(
        uc=True,
        headless=False,
        incognito=False,
        guest_mode=False,
    )

    try:
        print("Starting browser in CDP mode...")
        # Activating CDP Mode swaps the driver into DevTools-Protocol control,
        # which is the configuration that avoids the bot-detection captcha.
        driver.activate_cdp_mode("https://www.skyscanner.nl/")

        driver.cdp.set_window_rect(0, 0, 1366, 768)
        pump(driver, random.uniform(4, 7))

        if check_for_captcha(driver):
            handle_captcha(driver)

        close_popups(driver)
        accept_cookies(driver)
        close_popups(driver)

        for i, (dep_date, ret_date, vacation_days) in enumerate(date_combinations):
            actual_search_num = start_from + i

            print(f"\n[{actual_search_num}/{original_total}] Searching: "
                  f"{dep_date.strftime('%d/%m')} → {ret_date.strftime('%d/%m')} "
                  f"({vacation_days} days)")

            capture.reset(dep_date, ret_date)

            dep_str = dep_date.strftime("%y%m%d")
            ret_str = ret_date.strftime("%y%m%d")

            url = build_search_url(config, dep_str, ret_str)

            print("Navigating to search page...")
            pump(driver, random.uniform(1, 2))
            driver.cdp.get(url)
            install_interceptor(driver)
            pump(driver, random.uniform(2, 4))

            if check_for_captcha(driver):
                handle_captcha(driver)
                print("Retrying search after captcha...")
                capture.reset(dep_date, ret_date)
                driver.cdp.get(url)
                install_interceptor(driver)
                pump(driver, random.uniform(2, 4))

            close_popups(driver)
            accept_cookies(driver)
            install_interceptor(driver)

            print("Waiting for complete API response...")

            wait_count = 0
            reloaded = False
            while not capture.try_capture(driver) and wait_count < 60:
                pump(driver, 1)
                wait_count += 1
                # Re-inject in case the page reloaded (guard makes it a no-op
                # otherwise), so later polls are always captured.
                install_interceptor(driver)

                if check_for_captcha(driver):
                    print(f"Captcha detected during wait at {wait_count}s")
                    handle_captcha(driver)
                    print("Retrying search after captcha...")
                    capture.reset(dep_date, ret_date)
                    driver.cdp.get(url)
                    install_interceptor(driver)
                    pump(driver, random.uniform(2, 4))
                    wait_count = 0
                    reloaded = False
                    continue

                # If we've seen no API activity at all after a few seconds, the
                # search almost certainly completed before the interceptor was
                # injected (fast/cached result). Reload once to re-run it with
                # the interceptor already in place.
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
            else:
                capture.save_fallback(driver)
                if not capture.saved:
                    print(f"No complete response after {wait_count}s - moving on")

            if i < len(date_combinations) - 1:
                delay = random.uniform(5, 10)
                print(f"Waiting {delay:.1f}s before next search...")
                pump(driver, delay)

    except Exception as e:
        print(f"Error during search: {e}")

    finally:
        print("Closing browser...")
        driver.quit()


def main():
    search_flights_simple(config)


if __name__ == "__main__":
    main()
