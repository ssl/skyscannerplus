import json
import glob
from datetime import datetime
from typing import List, Dict, Any
import re

def extract_route_from_filename(filename: str) -> tuple:
    match = re.search(r'complete_(\d{8})_(\d{8})', filename)
    if match:
        return match.group(1), match.group(2), None, None
    return None, None, None, None

def parse_datetime(dt_str: str) -> datetime:
    if dt_str:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    return None

def extract_flight_data(json_data: dict, filename: str) -> List[Dict[str, Any]]:
    flights = []
    dep_date, ret_date, origin_code, dest_code = extract_route_from_filename(filename)
    
    if 'itineraries' not in json_data:
        return flights
    
    itineraries = json_data['itineraries']
    
    agents = {}
    if 'agents' in itineraries:
        for agent in itineraries['agents']:
            agents[agent['id']] = agent['name']
    
    airline_map = {}
    if 'filterStats' in itineraries and 'carriers' in itineraries['filterStats']:
        for carrier in itineraries['filterStats']['carriers']:
            airline_map[carrier['id']] = {
                'name': carrier['name'],
                'code': carrier.get('alternateId', ''),
                'logo': carrier.get('logoUrl', '')
            }
    
    if 'results' in itineraries:
        for item in itineraries['results']:
            if 'legs' in item:
                flight_info = process_itinerary(item, airline_map, agents, dep_date, ret_date, origin_code, dest_code, filename)
                if flight_info:
                    flights.append(flight_info)
    
    return flights

def process_itinerary(item: dict, airline_map: dict, agents: dict, dep_date: str, ret_date: str, origin_code: str, dest_code: str, filename: str) -> Dict[str, Any]:
    legs = item['legs']
    
    if not legs:
        return None
    
    outbound_leg = legs[0]
    return_leg = legs[1] if len(legs) > 1 else {}
    
    actual_origin = outbound_leg.get('origin', {}).get('displayCode', 'N/A')
    actual_destination = outbound_leg.get('destination', {}).get('displayCode', 'N/A')
    
    cheapest_price = 0
    cheapest_formatted = "N/A"
    all_booking_options = []
    
    if 'pricingOptions' in item and item['pricingOptions']:
        valid_options = [opt for opt in item['pricingOptions'] if opt.get('price', {}).get('amount', 0) > 0]
        
        if valid_options:
            sorted_options = sorted(valid_options, key=lambda x: x.get('price', {}).get('amount', float('inf')))
            
            if sorted_options:
                cheapest_price = sorted_options[0]['price']['amount']
                cheapest_formatted = f"€ {cheapest_price:.0f}"
                
                for option in sorted_options[:5]:
                    price = option['price']['amount']
                    agent_names = [agents.get(agent_id, agent_id) for agent_id in option.get('agentIds', [])]
                    if agent_names:
                        all_booking_options.append(f"€{price:.0f} ({', '.join(agent_names)})")
    
    if cheapest_formatted == "N/A" and 'price' in item:
        price_data = item['price']
        if isinstance(price_data, dict):
            raw_price = price_data.get('raw', 0)
            if raw_price > 0:
                cheapest_formatted = price_data.get('formatted', f"€ {raw_price:.0f}")
                cheapest_price = raw_price
    
    if cheapest_price <= 0:
        return None
    
    outbound_airlines = get_airline_info(outbound_leg, airline_map)
    return_airlines = get_airline_info(return_leg, airline_map) if return_leg else "N/A"
    
    outbound_duration = outbound_leg.get('durationInMinutes', 0)
    return_duration = return_leg.get('durationInMinutes', 0) if return_leg else 0
    total_duration = outbound_duration + return_duration
    
    outbound_stops = outbound_leg.get('stopCount', 0)
    return_stops = return_leg.get('stopCount', 0) if return_leg else 0
    
    outbound_stop_countries = get_stop_countries(outbound_leg)
    return_stop_countries = get_stop_countries(return_leg) if return_leg else ""
    
    outbound_dep = format_datetime(outbound_leg.get('departure'))
    outbound_arr = format_datetime(outbound_leg.get('arrival'))
    return_dep = format_datetime(return_leg.get('departure')) if return_leg else "N/A"
    return_arr = format_datetime(return_leg.get('arrival')) if return_leg else "N/A"
    
    return {
        'route': f"{actual_origin} → {actual_destination}",
        'departure_date': format_date(dep_date),
        'return_date': format_date(ret_date),
        'price': cheapest_formatted,
        'price_raw': cheapest_price,
        'booking_options': '; '.join(all_booking_options[:3]) if all_booking_options else "N/A",
        'outbound_airline': outbound_airlines,
        'return_airline': return_airlines,
        'outbound_departure': outbound_dep,
        'outbound_arrival': outbound_arr,
        'outbound_duration': format_duration(outbound_duration),
        'outbound_duration_raw': outbound_duration,
        'outbound_stops': outbound_stops,
        'outbound_direct': "Yes" if outbound_stops == 0 else "No",
        'outbound_stop_countries': outbound_stop_countries,
        'return_departure': return_dep,
        'return_arrival': return_arr,
        'return_duration': format_duration(return_duration) if return_duration > 0 else "N/A",
        'return_duration_raw': return_duration,
        'return_stops': return_stops,
        'return_direct': "Yes" if return_stops == 0 else "No" if return_leg else "N/A",
        'return_stop_countries': return_stop_countries,
        'total_duration': format_duration(total_duration),
        'total_duration_raw': total_duration,
        'total_flights': len(legs),
        'itinerary_id': item.get('id', 'N/A')
    }

def get_stop_countries(leg: dict) -> str:
    if not leg or leg.get('stopCount', 0) == 0:
        return ""
    
    if 'segments' not in leg or len(leg['segments']) < 2:
        return ""
    
    stop_info = []
    segments = leg['segments']
    
    for i in range(len(segments) - 1):
        dest_country = segments[i]['destination'].get('country', 'Unknown')
        dest_code = segments[i]['destination'].get('displayCode', 'Unknown')
        
        try:
            arrival_time = parse_datetime(segments[i]['arrival'])
            departure_time = parse_datetime(segments[i + 1]['departure'])
            
            if arrival_time and departure_time:
                layover_minutes = int((departure_time - arrival_time).total_seconds() / 60)
                layover_hours = layover_minutes // 60
                layover_mins = layover_minutes % 60
                
                if layover_hours > 0:
                    layover_str = f"{layover_hours}h {layover_mins}m"
                else:
                    layover_str = f"{layover_mins}m"
                
                stop_info.append(f"{dest_code} ({dest_country}, {layover_str} layover)")
            else:
                stop_info.append(f"{dest_code} ({dest_country})")
        except:
            stop_info.append(f"{dest_code} ({dest_country})")
    
    return ", ".join(stop_info)

def get_airline_info(leg: dict, airline_map: dict) -> str:
    if not leg or 'carriers' not in leg:
        return "N/A"
    
    carriers = leg['carriers']
    airlines = []
    
    if 'marketing' in carriers:
        for carrier in carriers['marketing']:
            carrier_id = carrier.get('id')
            if carrier_id in airline_map:
                airlines.append(airline_map[carrier_id]['name'])
            else:
                airlines.append(carrier.get('name', f"ID:{carrier_id}"))
    
    return " + ".join(airlines) if airlines else "N/A"

def format_datetime(dt_str: str) -> str:
    if not dt_str:
        return "N/A"
    try:
        dt = parse_datetime(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A"
    except:
        return dt_str

def format_date(date_str: str) -> str:
    if not date_str or len(date_str) != 8:
        return date_str or "N/A"
    try:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    except:
        return date_str

def format_duration(minutes: int) -> str:
    if minutes <= 0:
        return "N/A"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"

def generate_html_report(flights: List[Dict[str, Any]], output_file: str = "skyscannerplus_report.html"):
    total_flights = len(flights)
    total_routes = len(set(f['route'] for f in flights))
    
    prices = []
    for flight in flights:
        if isinstance(flight['price_raw'], (int, float)) and flight['price_raw'] > 0:
            prices.append(flight['price_raw'])
    
    avg_price = f"€ {sum(prices) / len(prices):.0f}" if prices else "N/A"
    
    direct_count = sum(1 for f in flights if f['outbound_direct'] == 'Yes' and f['return_direct'] in ['Yes', 'N/A'])
    direct_flights = direct_count
    
    table_rows = ""
    for flight in flights:
        direct_out_class = 'direct-yes' if flight['outbound_direct'] == 'Yes' else 'direct-no'
        direct_ret_class = 'direct-yes' if flight['return_direct'] == 'Yes' else ('direct-no' if flight['return_direct'] == 'No' else '')
        
        table_rows += f"""
                    <tr>
                        <td class="route">{flight['route']}</td>
                        <td>{flight['departure_date']}</td>
                        <td>{flight['return_date']}</td>
                        <td class="price">{flight['price']}</td>
                        <td class="booking-options">{flight['booking_options']}</td>
                        <td class="airline">{flight['outbound_airline']}</td>
                        <td>{flight['outbound_departure']}</td>
                        <td>{flight['outbound_arrival']}</td>
                        <td class="duration">{flight['outbound_duration']}</td>
                        <td class="{direct_out_class}">{flight['outbound_direct']}</td>
                        <td class="stops">{flight['outbound_stop_countries'] if flight['outbound_stops'] > 0 else 'Direct'}</td>
                        <td class="airline">{flight['return_airline']}</td>
                        <td>{flight['return_departure']}</td>
                        <td>{flight['return_arrival']}</td>
                        <td class="duration">{flight['return_duration']}</td>
                        <td class="{direct_ret_class}">{flight['return_direct']}</td>
                        <td class="stops">{flight['return_stop_countries'] if flight['return_stops'] > 0 else ('Direct' if flight['return_direct'] == 'Yes' else 'N/A')}</td>
                        <td class="duration">{flight['total_duration']}</td>
                    </tr>"""
    
    flights_json = json.dumps(flights, indent=2)
    
    generation_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>skyscannerplus</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 100%;
            margin: 0 auto;
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 2.5em;
            font-weight: 300;
        }}
        .stats {{
            padding: 20px;
            background-color: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .stat-number {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}
        .stat-label {{
            color: #666;
            margin-top: 5px;
        }}
        .filters {{
            padding: 20px;
            background-color: #fff;
            border-bottom: 1px solid #dee2e6;
        }}
        .filter-group {{
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .filter-group label {{
            font-weight: 500;
            color: #333;
        }}
        .filter-group input, .filter-group select {{
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }}
        .table-container {{
            overflow-x: auto;
            padding: 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 8px;
            text-align: left;
            font-weight: 500;
            cursor: pointer;
            user-select: none;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        th:hover {{
            background: linear-gradient(135deg, #5a6fd8 0%, #6a4190 100%);
        }}
        th.sorted-asc::after {{
            content: ' ↑';
            color: #ffd700;
        }}
        th.sorted-desc::after {{
            content: ' ↓';
            color: #ffd700;
        }}
        td {{
            padding: 12px 8px;
            border-bottom: 1px solid #eee;
            vertical-align: top;
        }}
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        tr:hover {{
            background-color: #e3f2fd;
        }}
        .direct-yes {{
            color: #28a745;
            font-weight: bold;
        }}
        .direct-no {{
            color: #dc3545;
            font-weight: bold;
        }}
        .price {{
            font-weight: bold;
            color: #2c3e50;
        }}
        .route {{
            font-weight: 500;
            color: #495057;
        }}
        .airline {{
            color: #6c757d;
            font-size: 12px;
        }}
        .duration {{
            color: #495057;
            font-weight: 500;
        }}
        .stops {{
            color: #e74c3c;
            font-size: 12px;
            font-style: italic;
        }}
        .booking-options {{
            color: #3498db;
            font-size: 12px;
        }}
        .footer {{
            padding: 20px;
            text-align: center;
            background-color: #f8f9fa;
            color: #666;
            border-top: 1px solid #dee2e6;
        }}
        .export-btn {{
            padding: 8px 16px; background: #667eea; color: white; border: none;
            border-radius: 4px; cursor: pointer; font-weight: 600;
        }}
        .export-btn:disabled {{ opacity: .6; cursor: wait; }}
        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8em;
            }}
            .filter-group {{
                flex-direction: column;
                align-items: stretch;
            }}
            table {{
                font-size: 11px;
            }}
            th, td {{
                padding: 8px 4px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✈️ skyscannerplus</h1>
        </div>
        
        <div class="stats">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number" id="total-flights">{total_flights}</div>
                    <div class="stat-label">Total Flight Options</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="total-routes">{total_routes}</div>
                    <div class="stat-label">Different Routes</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="avg-price">{avg_price}</div>
                    <div class="stat-label">Average Price</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="direct-flights">{direct_flights}</div>
                    <div class="stat-label">Direct Flights</div>
                </div>
            </div>
        </div>
        
        <div class="filters">
            <div class="filter-group">
                <label for="routeFilter">Filter by Route:</label>
                <select id="routeFilter">
                    <option value="">All Routes</option>
                </select>
                
                <label for="airlineFilter">Filter by Airline:</label>
                <select id="airlineFilter">
                    <option value="">All Airlines</option>
                </select>
                
                <label for="directFilter">Direct Flights Only:</label>
                <input type="checkbox" id="directFilter">
                
                <label for="priceFilter">Max Price (€):</label>
                <input type="number" id="priceFilter" placeholder="e.g. 1000">
                
                <label for="outboundDurationFilter">Max Outbound Duration (hours):</label>
                <input type="number" id="outboundDurationFilter" placeholder="e.g. 12">
                
                <label for="returnDurationFilter">Max Return Duration (hours):</label>
                <input type="number" id="returnDurationFilter" placeholder="e.g. 12">
                
                <label for="totalDurationFilter">Max Total Duration (hours):</label>
                <input type="number" id="totalDurationFilter" placeholder="e.g. 24">
                
                <button onclick="clearFilters()" style="padding: 8px 16px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer;">Clear Filters</button>

                <button id="exportBtn" class="export-btn" onclick="exportImage()">📸 Exporteer als afbeelding</button>
            </div>
        </div>
        
        <div class="table-container">
            <table id="flightTable">
                <thead>
                    <tr>
                        <th onclick="sortTable(0)">Route</th>
                        <th onclick="sortTable(1)">Departure Date</th>
                        <th onclick="sortTable(2)">Return Date</th>
                        <th onclick="sortTable(3)">Price</th>
                        <th onclick="sortTable(4)">Booking Options</th>
                        <th onclick="sortTable(5)">Outbound Airline</th>
                        <th onclick="sortTable(6)">Outbound Departure</th>
                        <th onclick="sortTable(7)">Outbound Arrival</th>
                        <th onclick="sortTable(8)">Outbound Duration</th>
                        <th onclick="sortTable(9)">Outbound Direct</th>
                        <th onclick="sortTable(10)">Outbound Stops</th>
                        <th onclick="sortTable(11)">Return Airline</th>
                        <th onclick="sortTable(12)">Return Departure</th>
                        <th onclick="sortTable(13)">Return Arrival</th>
                        <th onclick="sortTable(14)">Return Duration</th>
                        <th onclick="sortTable(15)">Return Direct</th>
                        <th onclick="sortTable(16)">Return Stops</th>
                        <th onclick="sortTable(17)">Total Duration</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>Generated on {generation_time} | Total flights analyzed: {total_flights}</p>
        </div>
    </div>

    <script>
        let currentSort = {{ column: -1, direction: 'asc' }};
        let allFlights = {flights_json};
        let filteredFlights = [...allFlights];

        function sortTable(columnIndex) {{
            const direction = currentSort.column === columnIndex && currentSort.direction === 'asc' ? 'desc' : 'asc';
            currentSort = {{ column: columnIndex, direction: direction }};

            document.querySelectorAll('th').forEach(th => {{
                th.classList.remove('sorted-asc', 'sorted-desc');
            }});

            const th = document.querySelectorAll('th')[columnIndex];
            th.classList.add(direction === 'asc' ? 'sorted-asc' : 'sorted-desc');

            filteredFlights.sort((a, b) => {{
                let aVal = Object.values(a)[columnIndex];
                let bVal = Object.values(b)[columnIndex];

                if (columnIndex === 3) {{
                    aVal = parseFloat(a.price_raw) || 0;
                    bVal = parseFloat(b.price_raw) || 0;
                }}

                if (columnIndex === 1 || columnIndex === 2) {{
                    aVal = new Date(aVal);
                    bVal = new Date(bVal);
                }}

                if (columnIndex === 8) {{
                    aVal = parseInt(a.outbound_duration_raw) || 0;
                    bVal = parseInt(b.outbound_duration_raw) || 0;
                }}
                if (columnIndex === 14) {{
                    aVal = parseInt(a.return_duration_raw) || 0;
                    bVal = parseInt(b.return_duration_raw) || 0;
                }}
                if (columnIndex === 17) {{
                    aVal = parseInt(a.total_duration_raw) || 0;
                    bVal = parseInt(b.total_duration_raw) || 0;
                }}

                if (aVal < bVal) return direction === 'asc' ? -1 : 1;
                if (aVal > bVal) return direction === 'asc' ? 1 : -1;
                return 0;
            }});

            renderTable();
        }}

        function renderTable() {{
            const tbody = document.querySelector('#flightTable tbody');
            tbody.innerHTML = '';

            filteredFlights.forEach(flight => {{
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td class="route">${{flight.route}}</td>
                    <td>${{flight.departure_date}}</td>
                    <td>${{flight.return_date}}</td>
                    <td class="price">${{flight.price}}</td>
                    <td class="booking-options">${{flight.booking_options}}</td>
                    <td class="airline">${{flight.outbound_airline}}</td>
                    <td>${{flight.outbound_departure}}</td>
                    <td>${{flight.outbound_arrival}}</td>
                    <td class="duration">${{flight.outbound_duration}}</td>
                    <td class="${{flight.outbound_direct === 'Yes' ? 'direct-yes' : 'direct-no'}}">${{flight.outbound_direct}}</td>
                    <td class="stops">${{flight.outbound_stop_countries}}</td>
                    <td class="airline">${{flight.return_airline}}</td>
                    <td>${{flight.return_departure}}</td>
                    <td>${{flight.return_arrival}}</td>
                    <td class="duration">${{flight.return_duration}}</td>
                    <td class="${{flight.return_direct === 'Yes' ? 'direct-yes' : (flight.return_direct === 'No' ? 'direct-no' : '')}}">${{flight.return_direct}}</td>
                    <td class="stops">${{flight.return_stop_countries}}</td>
                    <td class="duration">${{flight.total_duration}}</td>
                `;
                tbody.appendChild(row);
            }});
        }}

        function applyFilters() {{
            const routeFilter = document.getElementById('routeFilter').value;
            const airlineFilter = document.getElementById('airlineFilter').value;
            const directFilter = document.getElementById('directFilter').checked;
            const priceFilter = parseFloat(document.getElementById('priceFilter').value) || Infinity;
            const outboundDurationFilter = parseInt(document.getElementById('outboundDurationFilter').value) || Infinity;
            const returnDurationFilter = parseInt(document.getElementById('returnDurationFilter').value) || Infinity;
            const totalDurationFilter = parseInt(document.getElementById('totalDurationFilter').value) || Infinity;

            filteredFlights = allFlights.filter(flight => {{
                const routeMatch = !routeFilter || flight.route.includes(routeFilter);
                const airlineMatch = !airlineFilter || 
                    flight.outbound_airline.toLowerCase().includes(airlineFilter.toLowerCase()) ||
                    flight.return_airline.toLowerCase().includes(airlineFilter.toLowerCase());
                const directMatch = !directFilter || 
                    (flight.outbound_direct === 'Yes' && (flight.return_direct === 'Yes' || flight.return_direct === 'N/A'));
                const priceMatch = parseFloat(flight.price_raw) <= priceFilter;
                const outboundDurationMatch = flight.outbound_duration_raw <= (outboundDurationFilter * 60);
                const returnDurationMatch = flight.return_duration_raw <= (returnDurationFilter * 60);
                const totalDurationMatch = flight.total_duration_raw <= (totalDurationFilter * 60);

                return routeMatch && airlineMatch && directMatch && priceMatch && outboundDurationMatch && returnDurationMatch && totalDurationMatch;
            }});

            renderTable();
            updateStats();
        }}

        function clearFilters() {{
            document.getElementById('routeFilter').value = '';
            document.getElementById('airlineFilter').value = '';
            document.getElementById('directFilter').checked = false;
            document.getElementById('priceFilter').value = '';
            document.getElementById('outboundDurationFilter').value = '';
            document.getElementById('returnDurationFilter').value = '';
            document.getElementById('totalDurationFilter').value = '';
            filteredFlights = [...allFlights];
            renderTable();
            updateStats();
        }}

        function updateStats() {{
            document.getElementById('total-flights').textContent = filteredFlights.length;
        }}

        function populateFilters() {{
            const routes = [...new Set(allFlights.map(f => f.route))].sort();
            const airlines = [...new Set(allFlights.flatMap(f => [f.outbound_airline, f.return_airline]))].sort();

            const routeSelect = document.getElementById('routeFilter');
            routes.forEach(route => {{
                const option = document.createElement('option');
                option.value = route;
                option.textContent = route;
                routeSelect.appendChild(option);
            }});

            const airlineSelect = document.getElementById('airlineFilter');
            airlines.forEach(airline => {{
                if (airline !== 'N/A') {{
                    const option = document.createElement('option');
                    option.value = airline;
                    option.textContent = airline;
                    airlineSelect.appendChild(option);
                }}
            }});
        }}

        document.getElementById('routeFilter').addEventListener('change', applyFilters);
        document.getElementById('airlineFilter').addEventListener('change', applyFilters);
        document.getElementById('directFilter').addEventListener('change', applyFilters);
        document.getElementById('priceFilter').addEventListener('input', applyFilters);
        document.getElementById('outboundDurationFilter').addEventListener('input', applyFilters);
        document.getElementById('returnDurationFilter').addEventListener('input', applyFilters);
        document.getElementById('totalDurationFilter').addEventListener('input', applyFilters);

        const NL_MONTHS = ['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'];
        const EXPORT_MAX = 50;
        const FONT = "'Segoe UI', -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif";

        function fmtDT(s) {{
            if (!s || s === 'N/A') return '?';
            const [d, t] = s.split(' ');
            const [y, m, day] = d.split('-').map(Number);
            return `${{day}} ${{NL_MONTHS[m - 1]}} ${{t || ''}}`.trim();
        }}
        function fmtDur(min) {{
            if (!min) return '?';
            return `${{Math.floor(min / 60)}}u${{String(min % 60).padStart(2, '0')}}`;
        }}
        function tripDays(f) {{
            const a = (f.departure_date && f.departure_date !== 'N/A') ? f.departure_date : (f.outbound_departure || '').split(' ')[0];
            const b = (f.return_date && f.return_date !== 'N/A') ? f.return_date : (f.return_departure || '').split(' ')[0];
            const n = Math.round((new Date(b) - new Date(a)) / 86400000);
            return isNaN(n) ? '' : `${{n}} dagen`;
        }}
        function stopsText(f, which) {{
            const stops = f[which + '_stops'];
            if (!stops) return null;
            const txt = (f[which + '_stop_countries'] || '')
                .replace(/\\(([^,()]+), ([^)]*?) layover\\)/g, '($2)')
                .replace(/(\\d+)h (\\d+)m/g, (m, h, mm) => `${{h}}u${{mm.padStart(2, '0')}}`);
            return `${{stops}} stop: ${{txt}}`;
        }}
        function filterSummary() {{
            const parts = [];
            const v = id => document.getElementById(id).value;
            if (v('priceFilter')) parts.push(`max € ${{v('priceFilter')}}`);
            if (v('outboundDurationFilter')) parts.push(`heen max ${{v('outboundDurationFilter')}}u`);
            if (v('returnDurationFilter')) parts.push(`terug max ${{v('returnDurationFilter')}}u`);
            if (v('totalDurationFilter')) parts.push(`totaal max ${{v('totalDurationFilter')}}u vliegen`);
            if (document.getElementById('directFilter').checked) parts.push('alleen direct');
            if (v('airlineFilter')) parts.push(v('airlineFilter'));
            return parts.join(' · ');
        }}
        function exportList() {{
            let list = [...filteredFlights];
            if (currentSort.column === -1) list.sort((a, b) => a.price_raw - b.price_raw);
            const seen = new Set();
            return list.filter(f => {{
                const k = f.itinerary_id + '|' + f.price_raw;
                if (seen.has(k)) return false;
                seen.add(k); return true;
            }});
        }}

        function drawRun(ctx, x, y, pieces, maxX) {{
            for (const p of pieces) {{
                ctx.font = `${{p.weight || 400}} ${{p.size || 14}}px ${{FONT}}`;
                ctx.fillStyle = p.color || '#1f2933';
                let t = p.text;
                if (x + ctx.measureText(t).width > maxX) {{
                    while (t.length && x + ctx.measureText(t + '…').width > maxX) t = t.slice(0, -1);
                    ctx.fillText(t + '…', x, y);
                    return;
                }}
                ctx.fillText(t, x, y);
                x += ctx.measureText(t).width;
            }}
        }}
        function legPieces(f, which) {{
            const sep = {{ text: '  ·  ', color: '#9aa5b1' }};
            const st = stopsText(f, which);
            return [
                {{ text: `${{fmtDT(f[which + '_departure'])}} → ${{fmtDT(f[which + '_arrival'])}}` }},
                sep, {{ text: fmtDur(f[which + '_duration_raw']), weight: 700 }},
                sep, st ? {{ text: st, color: '#c2410c', size: 13 }} : {{ text: 'Direct', color: '#15803d', weight: 700, size: 13 }},
                sep, {{ text: f[which + '_airline'] || '', color: '#52606d', size: 13 }},
            ];
        }}

        function drawExport(list, total) {{
            const W = 1000, HEAD = 112, ROW = 96, FOOT = 56, PAD = 28, SCALE = 2;
            const H = HEAD + 12 + list.length * ROW + FOOT;
            const canvas = document.createElement('canvas');
            canvas.width = W * SCALE; canvas.height = H * SCALE;
            const ctx = canvas.getContext('2d');
            ctx.scale(SCALE, SCALE);
            ctx.textBaseline = 'alphabetic';

            ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);
            const g = ctx.createLinearGradient(0, 0, W, HEAD);
            g.addColorStop(0, '#667eea'); g.addColorStop(1, '#764ba2');
            ctx.fillStyle = g; ctx.fillRect(0, 0, W, HEAD);

            const route = list[0] ? list[0].route : '';
            drawRun(ctx, PAD, 52, [{{ text: `✈  ${{route}}  ·  ${{list.length}} beste opties`, size: 30, weight: 700, color: '#ffffff' }}], W - PAD);
            const summary = filterSummary();
            drawRun(ctx, PAD, 84, [{{ text: (summary ? summary + '  ·  ' : '') + 'retour, prijs per persoon', size: 16, color: 'rgba(255,255,255,0.9)' }}], W - PAD);

            list.forEach((f, i) => {{
                const top = HEAD + 12 + i * ROW;
                if (i % 2 === 1) {{ ctx.fillStyle = '#f8f9fc'; ctx.fillRect(0, top, W, ROW); }}
                drawRun(ctx, PAD, top + 40, [{{ text: `#${{i + 1}}`, size: 15, weight: 700, color: '#9aa5b1' }}], 80);
                drawRun(ctx, 76, top + 44, [{{ text: `€ ${{Math.round(f.price_raw)}}`, size: 28, weight: 700 }}], 200);
                drawRun(ctx, 76, top + 68, [{{ text: tripDays(f), size: 14, weight: 700, color: '#667eea' }}], 200);
                drawRun(ctx, 210, top + 38, [{{ text: 'Heen', weight: 700, color: '#52606d' }}], 262);
                drawRun(ctx, 262, top + 38, legPieces(f, 'outbound'), W - PAD);
                drawRun(ctx, 210, top + 68, [{{ text: 'Terug', weight: 700, color: '#52606d' }}], 262);
                drawRun(ctx, 262, top + 68, legPieces(f, 'return'), W - PAD);
                ctx.fillStyle = '#e5e7eb'; ctx.fillRect(PAD, top + ROW - 1, W - 2 * PAD, 1);
            }});

            const foot = (total > list.length ? `Top ${{list.length}} van ${{total}} resultaten  ·  ` : '') +
                'Skyscanner-prijzen opgehaald op {generation_time} — prijzen kunnen inmiddels veranderd zijn.';
            drawRun(ctx, PAD, H - 22, [{{ text: foot, size: 12, color: '#9aa5b1' }}], W - PAD);
            return canvas;
        }}

        function exportImage() {{
            const list = exportList();
            if (!list.length) {{ alert('Geen resultaten om te exporteren.'); return; }}
            const canvas = drawExport(list.slice(0, EXPORT_MAX), list.length);
            canvas.toBlob(blob => {{
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `vluchten_${{new Date().toISOString().slice(0, 10)}}.png`;
                document.body.appendChild(a); a.click(); a.remove();
                setTimeout(() => URL.revokeObjectURL(a.href), 2000);
            }}, 'image/png');
        }}

        populateFilters();
        renderTable();
    </script>
</body>
</html>"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"HTML report generated: {output_file}")
    return output_file

def main():
    print("Searching for complete_*.json files...")
    json_files = glob.glob("complete_*.json")
    
    if not json_files:
        print("Error: No complete_*.json files found in current directory")
        return
    
    print(f"Found {len(json_files)} JSON files to process")
    
    all_flights = []
    processed_files = 0
    
    for filename in json_files:
        print(f"Processing {filename}...")
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                flights = extract_flight_data(json_data, filename)
                all_flights.extend(flights)
                processed_files += 1
                print(f"Extracted {len(flights)} flight options")
        except Exception as e:
            print(f"Error processing {filename}: {str(e)}")
    
    print(f"Files processed: {processed_files}/{len(json_files)}")
    print(f"Total flight options: {len(all_flights)}")
    
    if all_flights:
        print(f"Generating HTML report...")
        output_file = generate_html_report(all_flights, f"skyscannerplus_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
        print(f"Analysis complete! Open {output_file} in your browser to view the results.")
        
        routes = set(f['route'] for f in all_flights)
        airlines = set()
        for f in all_flights:
            if f['outbound_airline'] != 'N/A':
                airlines.add(f['outbound_airline'])
            if f['return_airline'] != 'N/A':
                airlines.add(f['return_airline'])
        
        print(f"Unique routes: {len(routes)}")
        print(f"Airlines found: {len(airlines)}")
    else:
        print("No flight data could be extracted from the JSON files")

if __name__ == "__main__":
    main()
