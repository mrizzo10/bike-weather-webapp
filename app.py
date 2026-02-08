#!/usr/bin/env python3
"""
Bike Weather Web App
A web application that allows users to sign up for daily bike weather emails.
"""

import os
import secrets
import requests
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
import resend
from math import radians, sin, cos, sqrt, atan2
import time
import psycopg2
from psycopg2.extras import RealDictCursor

# Load environment variables
load_dotenv(Path(__file__).parent.parent / '.env')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(16))

# Database URL from environment (Render provides this automatically)
# Render uses 'postgres://' but psycopg2 requires 'postgresql://'
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Configuration
CONFIG = {
    "OPENWEATHER_API_KEY": os.environ.get("OPENWEATHER_API_KEY", ""),
    "RESEND_API_KEY": os.environ.get("RESEND_API_KEY", ""),
    "EMAIL_FROM": os.environ.get("EMAIL_FROM", ""),
    "EMAIL_REPLY_TO": os.environ.get("EMAIL_REPLY_TO", ""),
    "MIN_TEMP_NO_PRECIP": 33,
    "MIN_TEMP_WITH_PRECIP": 45,
    "RIDE_START_HOUR": 6,
    "RIDE_END_HOUR": 19,
}

# Cities with major airports (for flying)
AIRPORT_CITIES = [
    {"city": "Philadelphia", "state": "PA", "airport": "PHL", "lat": 39.9526, "lon": -75.1652},
    {"city": "Washington", "state": "DC", "airport": "DCA", "lat": 38.9072, "lon": -77.0369},
    {"city": "Boston", "state": "MA", "airport": "BOS", "lat": 42.3601, "lon": -71.0589},
    {"city": "Charlotte", "state": "NC", "airport": "CLT", "lat": 35.2271, "lon": -80.8431},
    {"city": "Atlanta", "state": "GA", "airport": "ATL", "lat": 33.7490, "lon": -84.3880},
    {"city": "Miami", "state": "FL", "airport": "MIA", "lat": 25.7617, "lon": -80.1918},
    {"city": "Tampa", "state": "FL", "airport": "TPA", "lat": 27.9506, "lon": -82.4572},
    {"city": "Orlando", "state": "FL", "airport": "MCO", "lat": 28.5383, "lon": -81.3792},
    {"city": "Raleigh", "state": "NC", "airport": "RDU", "lat": 35.7796, "lon": -78.6382},
    {"city": "Richmond", "state": "VA", "airport": "RIC", "lat": 37.5407, "lon": -77.4360},
]

# Driveable cities (no airport required, within ~6 hour drive of Northeast)
DRIVEABLE_CITIES = [
    {"city": "Baltimore", "state": "MD", "lat": 39.2904, "lon": -76.6122},
    {"city": "Annapolis", "state": "MD", "lat": 38.9784, "lon": -76.4922},
    {"city": "Rehoboth Beach", "state": "DE", "lat": 38.7210, "lon": -75.0760},
    {"city": "Cape May", "state": "NJ", "lat": 38.9351, "lon": -74.9060},
    {"city": "Atlantic City", "state": "NJ", "lat": 39.3643, "lon": -74.4229},
    {"city": "Lancaster", "state": "PA", "lat": 40.0379, "lon": -76.3055},
    {"city": "Gettysburg", "state": "PA", "lat": 39.8309, "lon": -77.2311},
    {"city": "Harrisburg", "state": "PA", "lat": 40.2732, "lon": -76.8867},
    {"city": "Wilmington", "state": "DE", "lat": 39.7391, "lon": -75.5398},
    {"city": "Norfolk", "state": "VA", "lat": 36.8508, "lon": -76.2859},
    {"city": "Virginia Beach", "state": "VA", "lat": 36.8529, "lon": -75.9780},
    {"city": "Charlottesville", "state": "VA", "lat": 38.0293, "lon": -78.4767},
    {"city": "Asheville", "state": "NC", "lat": 35.5951, "lon": -82.5515},
    {"city": "Outer Banks", "state": "NC", "lat": 35.9582, "lon": -75.6201},
    {"city": "Myrtle Beach", "state": "SC", "lat": 33.6891, "lon": -78.8867},
    {"city": "Charleston", "state": "SC", "lat": 32.7765, "lon": -79.9311},
    {"city": "Savannah", "state": "GA", "lat": 32.0809, "lon": -81.0912},
    {"city": "Providence", "state": "RI", "lat": 41.8240, "lon": -71.4128},
    {"city": "Portland", "state": "ME", "lat": 43.6591, "lon": -70.2568},
    {"city": "Burlington", "state": "VT", "lat": 44.4759, "lon": -73.2121},
]

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in miles using Haversine formula."""
    R = 3959  # Earth's radius in miles
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def estimate_drive_time(distance_miles):
    """Estimate drive time based on distance. Returns hours and minutes."""
    # Assume average 50 mph for highway driving with stops
    hours = distance_miles / 50
    h = int(hours)
    m = int((hours - h) * 60)
    if h == 0:
        return f"{m} min"
    elif m == 0:
        return f"{h} hr"
    else:
        return f"{h} hr {m} min"

def check_city_weather(city_info, home_lat, home_lon):
    """Check if a city has suitable biking weather."""
    weather_data = get_weather_forecast(city_info['lat'], city_info['lon'])
    if not weather_data or 'list' not in weather_data:
        return None

    daily_best = {}
    for item in weather_data['list']:
        dt = datetime.fromtimestamp(item['dt'])
        date_str = dt.strftime('%Y-%m-%d')
        hour = dt.hour

        if hour < CONFIG["RIDE_START_HOUR"] or hour >= CONFIG["RIDE_END_HOUR"]:
            continue

        feels_like = item['main']['feels_like']
        weather_main = item['weather'][0]['main'].lower() if item['weather'] else ''

        has_precip = 'rain' in weather_main or 'drizzle' in weather_main or 'snow' in weather_main
        is_snow = 'snow' in weather_main

        min_temp = CONFIG["MIN_TEMP_WITH_PRECIP"] if has_precip else CONFIG["MIN_TEMP_NO_PRECIP"]
        is_suitable = feels_like >= min_temp and not is_snow

        if date_str not in daily_best:
            daily_best[date_str] = {
                'date': date_str,
                'day_name': dt.strftime('%A'),
                'best_temp': feels_like,
                'has_suitable': is_suitable,
            }
        else:
            if feels_like > daily_best[date_str]['best_temp']:
                daily_best[date_str]['best_temp'] = feels_like
            if is_suitable:
                daily_best[date_str]['has_suitable'] = True

    suitable_count = sum(1 for d in daily_best.values() if d['has_suitable'])
    if suitable_count > 0:
        distance = calculate_distance(home_lat, home_lon, city_info['lat'], city_info['lon'])
        return {
            'city': city_info['city'],
            'state': city_info['state'],
            'airport': city_info.get('airport'),
            'distance_miles': round(distance),
            'drive_time': estimate_drive_time(distance),
            'suitable_days': suitable_count,
            'best_temp': max(d['best_temp'] for d in daily_best.values() if d['has_suitable']),
        }
    return None

def find_travel_destinations(home_lat, home_lon):
    """Find closest cities with good biking weather - both driveable and flyable."""
    drive_destinations = []
    fly_destinations = []

    # Check driveable cities
    for city in DRIVEABLE_CITIES:
        result = check_city_weather(city, home_lat, home_lon)
        if result:
            drive_destinations.append(result)
        time.sleep(0.15)  # Rate limit

    # Check airport cities
    for city in AIRPORT_CITIES:
        result = check_city_weather(city, home_lat, home_lon)
        if result:
            fly_destinations.append(result)
        time.sleep(0.15)  # Rate limit

    # Sort by distance
    drive_destinations.sort(key=lambda x: x['distance_miles'])
    fly_destinations.sort(key=lambda x: x['distance_miles'])

    return {
        'drive': drive_destinations[:3],  # Top 3 closest driveable
        'fly': fly_destinations[:3],      # Top 3 closest with airports
    }

def get_db():
    """Get database connection."""
    # Render requires SSL for PostgreSQL connections
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, sslmode='require')
    return conn

def init_db():
    """Initialize the PostgreSQL database."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            zip_code TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            verified INTEGER DEFAULT 0,
            verification_token TEXT,
            unsubscribe_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_email_sent TIMESTAMP,
            min_temp_no_precip INTEGER DEFAULT 33,
            min_temp_with_precip INTEGER DEFAULT 45,
            ride_in_snow INTEGER DEFAULT 0,
            settings_token TEXT
        )
    ''')
    # Add columns for existing installations
    for col, default in [
        ('min_temp_no_precip', '33'),
        ('min_temp_with_precip', '45'),
        ('ride_in_snow', '0'),
        ('settings_token', 'NULL'),
    ]:
        try:
            c.execute(f'ALTER TABLE subscribers ADD COLUMN {col} INTEGER DEFAULT {default}' if col != 'settings_token' else f'ALTER TABLE subscribers ADD COLUMN {col} TEXT')
            conn.commit()
        except psycopg2.errors.DuplicateColumn:
            conn.rollback()  # Clear error state only on failure
    conn.close()

def geocode_location(city, state, zip_code=None):
    """Convert city/state/zip to lat/lon using OpenWeatherMap geocoding."""
    api_key = CONFIG["OPENWEATHER_API_KEY"]

    # Try zip code first if provided
    if zip_code:
        url = f"http://api.openweathermap.org/geo/1.0/zip?zip={zip_code},US&appid={api_key}"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get('lat'), data.get('lon'), data.get('name', city)
        except:
            pass

    # Fall back to city/state
    query = f"{city},{state},US"
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=1&appid={api_key}"

    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data:
                return data[0]['lat'], data[0]['lon'], data[0].get('name', city)
    except:
        pass

    return None, None, None

def get_weather_forecast(lat, lon):
    """Fetch weather forecast for a location."""
    api_key = CONFIG["OPENWEATHER_API_KEY"]
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={api_key}&units=imperial"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except:
        return None

def analyze_biking_conditions(weather_data, min_temp_no_precip=33, min_temp_with_precip=45, ride_in_snow=False):
    """Analyze weather data and return suitable biking windows."""
    if not weather_data or 'list' not in weather_data:
        return []

    suitable_windows = []
    daily_forecasts = {}

    for item in weather_data['list']:
        dt = datetime.fromtimestamp(item['dt'])
        date_str = dt.strftime('%Y-%m-%d')

        if date_str not in daily_forecasts:
            daily_forecasts[date_str] = []
        daily_forecasts[date_str].append(item)

    for date_str, forecasts in daily_forecasts.items():
        day_windows = []

        for forecast in forecasts:
            dt = datetime.fromtimestamp(forecast['dt'])
            hour = dt.hour

            if hour < CONFIG["RIDE_START_HOUR"] or hour >= CONFIG["RIDE_END_HOUR"]:
                continue

            feels_like = forecast['main']['feels_like']

            has_precip = False
            precip_type = None

            weather_main = forecast['weather'][0]['main'].lower() if forecast['weather'] else ''
            weather_desc = forecast['weather'][0]['description'] if forecast['weather'] else ''

            if 'rain' in weather_main or 'drizzle' in weather_main or 'rain' in forecast:
                has_precip = True
                precip_type = 'rain'
            if 'snow' in weather_main or 'snow' in forecast:
                has_precip = True
                precip_type = 'snow'

            min_temp = min_temp_with_precip if has_precip else min_temp_no_precip
            # Check if conditions are suitable based on user preferences
            is_snow = precip_type == 'snow'
            is_suitable = feels_like >= min_temp and (not is_snow or ride_in_snow)

            day_windows.append({
                'time': dt.strftime('%I:%M %p'),
                'hour': hour,
                'feels_like': round(feels_like),
                'has_precip': has_precip,
                'precip_type': precip_type,
                'weather': weather_desc,
                'is_suitable': is_suitable,
            })

        if day_windows:
            suitable_count = sum(1 for w in day_windows if w['is_suitable'])
            suitable_windows.append({
                'date': date_str,
                'day_name': datetime.strptime(date_str, '%Y-%m-%d').strftime('%A'),
                'windows': day_windows,
                'has_suitable_time': suitable_count > 0,
                'suitable_count': suitable_count
            })

    return suitable_windows

def generate_email_report(biking_windows, city, state, travel_destinations=None,
                         min_temp_no_precip=33, min_temp_with_precip=45, ride_in_snow=False):
    """Generate HTML email report for a subscriber."""
    today = datetime.now().strftime('%A, %B %d, %Y')
    snow_note = " | Snow: rideable" if ride_in_snow else ""

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
            h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
            .day {{ background: #f8f9fa; border-radius: 10px; padding: 15px; margin: 15px 0; }}
            .day-header {{ font-size: 18px; font-weight: bold; color: #2c3e50; }}
            .suitable {{ color: #27ae60; }}
            .unsuitable {{ color: #e74c3c; }}
            .window {{ padding: 5px 10px; margin: 5px 0; border-left: 3px solid #27ae60; background: #e8f5e9; }}
            .temp {{ font-weight: bold; }}
            .legend {{ background: #eee; padding: 10px; border-radius: 5px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <h1>🚴 Bike Weather Report</h1>
        <p><strong>{city}, {state}</strong> - {today}</p>

        <div class="legend">
            <strong>Your Conditions:</strong>
            Feels like ≥{min_temp_no_precip}°F (no rain) or ≥{min_temp_with_precip}°F (with rain) = <span class="suitable">Good to ride!</span>{snow_note}
        </div>
    """

    good_days = []
    for day in biking_windows:
        day_emoji = "🌟" if day['has_suitable_time'] else "❌"

        html += f"""
        <div class="day">
            <div class="day-header">{day_emoji} {day['day_name']} ({day['date']})</div>
        """

        if day['has_suitable_time']:
            good_days.append(day['day_name'])
            html += f'<p class="suitable">Good riding windows ({day["suitable_count"]} slots):</p>'

            for window in day['windows']:
                if window['is_suitable']:
                    precip_info = f" 🌧️ {window['precip_type']}" if window['has_precip'] else ""
                    html += f"""
                    <div class="window">
                        <strong>{window['time']}</strong> -
                        <span class="temp">{window['feels_like']}°F</span>
                        {precip_info} ({window['weather']})
                    </div>
                    """
        else:
            html += '<p class="unsuitable">❌ No suitable biking weather</p>'
            if day['windows']:
                best = max(day['windows'], key=lambda w: w['feels_like'])
                html += f'<p>Best: {best["time"]} at {best["feels_like"]}°F</p>'

        html += "</div>"

    if good_days:
        html += f"""
        <h2>📊 Summary</h2>
        <p>Good days to ride: <strong>{', '.join(good_days)}</strong></p>
        """
    else:
        html += """
        <h2>📊 Summary</h2>
        <p>No ideal biking conditions in the next 5 days. Check back tomorrow!</p>
        """

    # Travel destinations section
    if travel_destinations:
        drive_options = travel_destinations.get('drive', [])
        fly_options = travel_destinations.get('fly', [])

        if drive_options or fly_options:
            html += """<h2>🗺️ Travel to Ride</h2>"""

            if drive_options:
                html += """
                <h3>🚗 Closest Drives</h3>
                <table style="width: 100%; border-collapse: collapse; margin: 10px 0;">
                    <tr style="background: #27ae60; color: white;">
                        <th style="padding: 8px; text-align: left;">City</th>
                        <th style="padding: 8px; text-align: center;">Drive Time</th>
                        <th style="padding: 8px; text-align: center;">Good Days</th>
                        <th style="padding: 8px; text-align: center;">Best Temp</th>
                    </tr>
                """
                for i, dest in enumerate(drive_options):
                    bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
                    html += f"""
                    <tr style="background: {bg};">
                        <td style="padding: 8px;"><strong>{dest['city']}, {dest['state']}</strong></td>
                        <td style="padding: 8px; text-align: center;">{dest['drive_time']}</td>
                        <td style="padding: 8px; text-align: center;">{dest['suitable_days']} days</td>
                        <td style="padding: 8px; text-align: center;">{dest['best_temp']:.0f}°F</td>
                    </tr>
                    """
                html += "</table>"

                # Highlight closest drive option
                closest_drive = drive_options[0]
                html += f"""
                <div style="background: #e8f5e9; border-left: 4px solid #27ae60; padding: 12px; margin: 10px 0;">
                    <strong>🚗 Closest Drive:</strong> {closest_drive['city']}, {closest_drive['state']}<br>
                    <span style="color: #666;">{closest_drive['drive_time']} drive • {closest_drive['suitable_days']} good biking days • Up to {closest_drive['best_temp']:.0f}°F</span>
                </div>
                """

            if fly_options:
                html += """
                <h3>✈️ Fly & Ride</h3>
                <table style="width: 100%; border-collapse: collapse; margin: 10px 0;">
                    <tr style="background: #3498db; color: white;">
                        <th style="padding: 8px; text-align: left;">City</th>
                        <th style="padding: 8px; text-align: center;">Airport</th>
                        <th style="padding: 8px; text-align: center;">Good Days</th>
                        <th style="padding: 8px; text-align: center;">Best Temp</th>
                    </tr>
                """
                for i, dest in enumerate(fly_options):
                    bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
                    html += f"""
                    <tr style="background: {bg};">
                        <td style="padding: 8px;"><strong>{dest['city']}, {dest['state']}</strong></td>
                        <td style="padding: 8px; text-align: center;">{dest['airport']}</td>
                        <td style="padding: 8px; text-align: center;">{dest['suitable_days']} days</td>
                        <td style="padding: 8px; text-align: center;">{dest['best_temp']:.0f}°F</td>
                    </tr>
                    """
                html += "</table>"

    html += """
        <div style="background: #fff8e7; border: 1px solid #f0e0c0; border-radius: 8px; padding: 15px; margin: 20px 0; text-align: center;">
            <p style="margin: 0; color: #5d4e37; font-size: 14px;">
                Loving your bike weather updates? ☕<br>
                <a href="https://venmo.com/u/Matt-Rizzo" style="color: #3d95ce; text-decoration: none; font-weight: bold;">Buy me a blonde roast with a dash of almond milk</a>
            </p>
        </div>
        <hr>
        <p style="color: #888; font-size: 12px;">
            Generated by Bike Weather Checker 🚴<br>
            <a href="{settings_url}">Change my settings</a> | <a href="{unsubscribe_url}">Unsubscribe</a>
        </p>
    </body>
    </html>
    """

    return html

def send_email(to_email, subject, html_content, unsubscribe_token, settings_token=None):
    """Send email to a subscriber using Resend."""
    app_url = os.environ.get('APP_URL', 'http://localhost:5000')
    html_content = html_content.replace('{unsubscribe_url}', f"{app_url}/unsubscribe/{unsubscribe_token}")
    if settings_token:
        html_content = html_content.replace('{settings_url}', f"{app_url}/settings/{settings_token}")
    else:
        html_content = html_content.replace('{settings_url}', app_url)

    resend.api_key = CONFIG['RESEND_API_KEY']

    try:
        params = {
            "from": CONFIG['EMAIL_FROM'],
            "to": [to_email],
            "subject": subject,
            "html": html_content
        }
        if CONFIG['EMAIL_REPLY_TO']:
            params["reply_to"] = CONFIG['EMAIL_REPLY_TO']
        response = resend.Emails.send(params)
        print(f"Email sent to {to_email}: {response}")
        return response.get('id') is not None
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")
        return False

# US States for the dropdown
US_STATES = [
    ('AL', 'Alabama'), ('AK', 'Alaska'), ('AZ', 'Arizona'), ('AR', 'Arkansas'),
    ('CA', 'California'), ('CO', 'Colorado'), ('CT', 'Connecticut'), ('DE', 'Delaware'),
    ('FL', 'Florida'), ('GA', 'Georgia'), ('HI', 'Hawaii'), ('ID', 'Idaho'),
    ('IL', 'Illinois'), ('IN', 'Indiana'), ('IA', 'Iowa'), ('KS', 'Kansas'),
    ('KY', 'Kentucky'), ('LA', 'Louisiana'), ('ME', 'Maine'), ('MD', 'Maryland'),
    ('MA', 'Massachusetts'), ('MI', 'Michigan'), ('MN', 'Minnesota'), ('MS', 'Mississippi'),
    ('MO', 'Missouri'), ('MT', 'Montana'), ('NE', 'Nebraska'), ('NV', 'Nevada'),
    ('NH', 'New Hampshire'), ('NJ', 'New Jersey'), ('NM', 'New Mexico'), ('NY', 'New York'),
    ('NC', 'North Carolina'), ('ND', 'North Dakota'), ('OH', 'Ohio'), ('OK', 'Oklahoma'),
    ('OR', 'Oregon'), ('PA', 'Pennsylvania'), ('RI', 'Rhode Island'), ('SC', 'South Carolina'),
    ('SD', 'South Dakota'), ('TN', 'Tennessee'), ('TX', 'Texas'), ('UT', 'Utah'),
    ('VT', 'Vermont'), ('VA', 'Virginia'), ('WA', 'Washington'), ('WV', 'West Virginia'),
    ('WI', 'Wisconsin'), ('WY', 'Wyoming'), ('DC', 'Washington DC')
]

@app.route('/')
def index():
    """Home page with signup form."""
    return render_template('index.html', states=US_STATES)

@app.route('/subscribe', methods=['POST'])
def subscribe():
    """Handle new subscription."""
    email = request.form.get('email', '').strip().lower()
    city = request.form.get('city', '').strip()
    state = request.form.get('state', '').strip()
    zip_code = request.form.get('zip_code', '').strip()

    # Get user preferences
    min_temp_no_precip = int(request.form.get('min_temp_no_precip', 33))
    min_temp_with_precip = int(request.form.get('min_temp_with_precip', 45))
    ride_in_snow = 1 if request.form.get('ride_in_snow') else 0

    if not email or not city or not state:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('index'))

    # Geocode the location
    lat, lon, resolved_city = geocode_location(city, state, zip_code)

    if lat is None or lon is None:
        flash('Could not find that location. Please check the city and state.', 'error')
        return redirect(url_for('index'))

    # Generate tokens
    verification_token = secrets.token_urlsafe(32)
    unsubscribe_token = secrets.token_urlsafe(32)
    settings_token = secrets.token_urlsafe(32)

    # Save to database
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO subscribers (email, city, state, zip_code, lat, lon,
                                     verification_token, unsubscribe_token, verified,
                                     min_temp_no_precip, min_temp_with_precip, ride_in_snow, settings_token)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)
        ''', (email, resolved_city or city, state, zip_code, lat, lon,
              verification_token, unsubscribe_token,
              min_temp_no_precip, min_temp_with_precip, ride_in_snow, settings_token))
        conn.commit()

        # Send welcome email with first forecast
        weather_data = get_weather_forecast(lat, lon)
        biking_windows = analyze_biking_conditions(weather_data, min_temp_no_precip, min_temp_with_precip, ride_in_snow)
        travel_destinations = find_travel_destinations(lat, lon)

        good_days = sum(1 for day in biking_windows if day['has_suitable_time'])
        subject = f"🚴 Welcome! {good_days} good biking day(s) this week in {resolved_city or city}!"

        html = generate_email_report(biking_windows, resolved_city or city, state, travel_destinations,
                                     min_temp_no_precip, min_temp_with_precip, ride_in_snow)
        email_sent = send_email(email, subject, html, unsubscribe_token, settings_token)

        if email_sent:
            flash(f'You\'re all set! Check your inbox for today\'s bike weather report for {resolved_city or city}, {state}. You\'ll get daily updates at 6 AM.', 'success')
        else:
            flash(f'Subscribed for {resolved_city or city}, {state}! Your first email will arrive shortly.', 'success')

    except psycopg2.IntegrityError:
        conn.rollback()
        flash('You\'re already subscribed! Check your inbox for your daily reports.', 'error')
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    """Unsubscribe a user."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT email, city FROM subscribers WHERE unsubscribe_token = %s', (token,))
    result = cur.fetchone()

    if result:
        cur.execute('DELETE FROM subscribers WHERE unsubscribe_token = %s', (token,))
        conn.commit()
        flash(f'You\'ve been unsubscribed. Sorry to see you go!', 'success')
    else:
        flash('Invalid unsubscribe link.', 'error')

    conn.close()
    return redirect(url_for('index'))

@app.route('/settings/<token>', methods=['GET', 'POST'])
def settings(token):
    """View and update subscriber settings."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM subscribers WHERE settings_token = %s', (token,))
    subscriber = cur.fetchone()

    if not subscriber:
        conn.close()
        flash('Invalid settings link.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()
        zip_code = request.form.get('zip_code', '').strip()
        min_temp_no_precip = int(request.form.get('min_temp_no_precip', 33))
        min_temp_with_precip = int(request.form.get('min_temp_with_precip', 45))
        ride_in_snow = 1 if request.form.get('ride_in_snow') else 0

        # Re-geocode if location changed
        if city != subscriber['city'] or state != subscriber['state'] or zip_code != (subscriber['zip_code'] or ''):
            lat, lon, resolved_city = geocode_location(city, state, zip_code)
            if lat is None or lon is None:
                flash('Could not find that location. Please check the city and state.', 'error')
                conn.close()
                return render_template('settings.html', subscriber=subscriber, states=US_STATES, token=token)
            city = resolved_city or city
        else:
            lat, lon = subscriber['lat'], subscriber['lon']

        cur.execute('''
            UPDATE subscribers
            SET city = %s, state = %s, zip_code = %s, lat = %s, lon = %s,
                min_temp_no_precip = %s, min_temp_with_precip = %s, ride_in_snow = %s
            WHERE settings_token = %s
        ''', (city, state, zip_code, lat, lon, min_temp_no_precip, min_temp_with_precip, ride_in_snow, token))
        conn.commit()

        flash('Your settings have been updated!', 'success')

        # Refresh subscriber data
        cur.execute('SELECT * FROM subscribers WHERE settings_token = %s', (token,))
        subscriber = cur.fetchone()

    conn.close()
    return render_template('settings.html', subscriber=subscriber, states=US_STATES, token=token)

@app.route('/preview')
def preview():
    """Preview email for a location (for testing)."""
    city = request.args.get('city', 'Eastchester')
    state = request.args.get('state', 'NY')
    min_temp_no_precip = int(request.args.get('min_temp_no_precip', 33))
    min_temp_with_precip = int(request.args.get('min_temp_with_precip', 45))
    ride_in_snow = request.args.get('ride_in_snow', '0') == '1'

    lat, lon, resolved_city = geocode_location(city, state)
    if lat is None:
        return "Location not found", 404

    weather_data = get_weather_forecast(lat, lon)
    biking_windows = analyze_biking_conditions(weather_data, min_temp_no_precip, min_temp_with_precip, ride_in_snow)
    travel_destinations = find_travel_destinations(lat, lon)
    html = generate_email_report(biking_windows, resolved_city or city, state, travel_destinations,
                                 min_temp_no_precip, min_temp_with_precip, ride_in_snow)

    return html.replace('{unsubscribe_url}', '#').replace('{settings_url}', '#')

@app.route('/admin/subscribers')
def list_subscribers():
    """List all subscribers (admin endpoint)."""
    # In production, add authentication here
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'dev-admin-key'):
        return "Unauthorized", 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT email, city, state, created_at FROM subscribers')
    subscribers = cur.fetchall()
    conn.close()

    return jsonify([dict(s) for s in subscribers])

@app.route('/admin/delete/<email>')
def admin_delete(email):
    """Delete a subscriber by email (admin endpoint)."""
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'dev-admin-key'):
        return "Unauthorized", 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM subscribers WHERE email = %s RETURNING email', (email,))
    deleted = cur.fetchone()
    conn.commit()
    conn.close()

    if deleted:
        return jsonify({"deleted": email})
    else:
        return jsonify({"error": "Email not found"}), 404

def send_daily_emails():
    """Send daily emails to all subscribers. Run this via cron/scheduler."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM subscribers WHERE verified = 1')
    subscribers = cur.fetchall()

    print(f"Sending daily emails to {len(subscribers)} subscribers...")

    for sub in subscribers:
        try:
            # Get subscriber preferences (with defaults for existing records)
            min_temp_no_precip = sub.get('min_temp_no_precip') or 33
            min_temp_with_precip = sub.get('min_temp_with_precip') or 45
            ride_in_snow = bool(sub.get('ride_in_snow'))
            settings_token = sub.get('settings_token')

            weather_data = get_weather_forecast(sub['lat'], sub['lon'])
            biking_windows = analyze_biking_conditions(weather_data, min_temp_no_precip, min_temp_with_precip, ride_in_snow)
            travel_destinations = find_travel_destinations(sub['lat'], sub['lon'])

            good_days = sum(1 for day in biking_windows if day['has_suitable_time'])

            if good_days > 0:
                subject = f"🚴 {good_days} good biking day(s) this week in {sub['city']}!"
            else:
                subject = f"🚴 Bike Weather Report for {sub['city']} - No ideal conditions"

            html = generate_email_report(biking_windows, sub['city'], sub['state'], travel_destinations,
                                         min_temp_no_precip, min_temp_with_precip, ride_in_snow)

            if send_email(sub['email'], subject, html, sub['unsubscribe_token'], settings_token):
                cur.execute('UPDATE subscribers SET last_email_sent = %s WHERE id = %s',
                           (datetime.now(), sub['id']))
                conn.commit()
                print(f"  ✓ Sent to {sub['email']}")
            else:
                print(f"  ✗ Failed: {sub['email']}")

        except Exception as e:
            print(f"  ✗ Error for {sub['email']}: {e}")

    conn.close()
    print("Daily email batch complete!")

# Initialize database on startup
if DATABASE_URL:
    try:
        init_db()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
else:
    print("Warning: DATABASE_URL not set. Database features disabled.")

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'send-emails':
        # Run daily email job
        send_daily_emails()
    else:
        # Run web server
        app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
