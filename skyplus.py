from seleniumbase import Driver
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
import os

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

current_search_saved = False
current_search_dep_date = None
current_search_ret_date = None

def intercept_response(request, response):
    global current_search_saved, current_search_dep_date, current_search_ret_date
    
    if '/g/radar/api/v2/web-unified-search' in request.url:
        try:
            response_body = response.body
            
            try:
                import gzip
                response_body = gzip.decompress(response_body)
            except:
                pass
            
            try:
                response_text = response_body.decode('utf-8')
            except:
                response_text = response_body.decode('latin-1')
            
            if '"status":"complete"' in response_text[:200] or '"status": "complete"' in response_text[:200]:
                if not current_search_saved and current_search_dep_date and current_search_ret_date:
                    try:
                        json_data = json.loads(response_text)
                        context = json_data.get('context', {})
                        session_id = context.get('sessionId', 'unknown')
                        
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        dep_str = current_search_dep_date.strftime('%Y%m%d')
                        ret_str = current_search_ret_date.strftime('%Y%m%d')
                        
                        short_session = session_id[:15] if session_id != 'unknown' else timestamp[-6:]
                        filename = f"complete_{dep_str}_{ret_str}_{short_session}.json"
                        
                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump(json_data, f, indent=2, ensure_ascii=False)
                        
                        print(f"Saved flights: {filename}")
                        current_search_saved = True
                        
                    except Exception as e:
                        print(f"Error saving: {e}")
            else:
                if '"status":"' in response_text[:200]:
                    try:
                        import re
                        status_match = re.search(r'"status":"([^"]*)"', response_text[:200])
                        if status_match:
                            status = status_match.group(1)
                            print(f"API Status: {status}")
                    except:
                        pass
            
        except Exception as e:
            print(f"Error processing response: {e}")

def search_flights_simple(config):
    global current_search_saved, current_search_dep_date, current_search_ret_date
    
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
    
    driver = Driver(
        wire=True, 
        uc=False, 
        ad_block=False,
        headless=False,
        incognito=False,
        guest_mode=False,
    )
    driver.response_interceptor = intercept_response
    
    try:
        import random
        
        print("Starting browser...")
        driver.set_window_size(1366, 768)
        
        driver.get("https://www.skyscanner.nl/")
        time.sleep(random.uniform(4, 7))
        
        if check_for_captcha(driver):
            wait_for_captcha_solve(driver)
        
        try:
            cookie_button = driver.find_element("id", "acceptCookieButton")
            if cookie_button.is_displayed():
                time.sleep(random.uniform(1.5, 3))
                driver.execute_script("arguments[0].scrollIntoView();", cookie_button)
                time.sleep(random.uniform(0.5, 1))
                cookie_button.click()
        except:
            pass
        
        for i, (dep_date, ret_date, vacation_days) in enumerate(date_combinations):
            actual_search_num = start_from + i
            
            print(f"\n[{actual_search_num}/{original_total}] Searching: {dep_date.strftime('%d/%m')} → {ret_date.strftime('%d/%m')} ({vacation_days} days)")
            
            current_search_saved = False
            current_search_dep_date = dep_date
            current_search_ret_date = ret_date

            dep_str = dep_date.strftime("%y%m%d")
            ret_str = ret_date.strftime("%y%m%d")
            
            url = f'https://www.skyscanner.nl/transport/vluchten/{config["from"]}/{config["to"]}/{dep_str}/{ret_str}/?adultsv2={config["adults"]}&cabinclass={config["cabin"]}&childrenv2={config["children"]}&inboundaltsenabled=false&outboundaltsenabled=false&preferdirects=false&rtn=1&qp_prevCurrency=EUR'
            
            print(f"Navigating to search page...")
            time.sleep(random.uniform(1, 2))
            driver.get(url)
            time.sleep(random.uniform(2, 4))
            
            if check_for_captcha(driver):
                wait_for_captcha_solve(driver)
                print("Retrying search after captcha solve...")
                time.sleep(random.uniform(1, 2))
                driver.get(url)
                time.sleep(random.uniform(2, 4))
            
            print(f"Waiting for complete API response...")
            
            wait_count = 0
            while not current_search_saved and wait_count < 60:
                time.sleep(1)
                wait_count += 1
                
                if check_for_captcha(driver):
                    print(f"Captcha detected during wait at {wait_count}s")
                    wait_for_captcha_solve(driver)
                    print("Retrying search after captcha solve...")
                    time.sleep(random.uniform(1, 2))
                    driver.get(url)
                    time.sleep(random.uniform(2, 4))
                    current_search_saved = False
                    wait_count = 0
                    continue
                
                if wait_count % 10 == 0:
                    print(f"Still waiting... ({wait_count}s elapsed)")
            
            if current_search_saved:
                print(f"Got complete response after {wait_count}s")
            else:
                print(f"No complete response after {wait_count}s - moving on")
            
            if i < len(date_combinations) - 1:
                delay = random.uniform(5, 10)
                print(f"Waiting {delay:.1f}s before next search...")
                time.sleep(delay)
    
    except Exception as e:
        print(f"Error during search: {e}")
        
    finally:
        print("Closing browser...")
        driver.quit()

def check_for_captcha(driver):
    try:
        current_url = driver.current_url
        if 'captcha-v2' in current_url:
            return True
        return False
    except:
        return False

def wait_for_captcha_solve(driver):
    print("\nCAPTCHA DETECTED!")
    print("Please solve the captcha manually in the browser window...")
    print("Waiting for captcha to be solved...")
    
    while True:
        try:
            current_url = driver.current_url
            if 'captcha-v2' not in current_url:
                print("Captcha solved! Continuing...")
                time.sleep(3)
                return True
            
            print("Still waiting for captcha... (check browser window)")
            time.sleep(5)
            
        except Exception as e:
            print(f"Error checking captcha status: {e}")
            time.sleep(5)

def main():
    search_flights_simple(config)

if __name__ == "__main__":
    main()