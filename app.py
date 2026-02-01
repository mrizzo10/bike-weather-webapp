#!/usr/bin/env python3
"""
Bike Weather Web App
A web application that allows users to sign up for daily bike weather emails.
"""

import os
import sqlite3
import secrets
import requests
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from math import radians, sin, cos, sqrt, atan2

# Load environment variables
load_dotenv(Path(__file__).parent.parent / '.env')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(16))

# Database path
DB_PATH = Path(__file__).parent / 'subscribers.db'

# Configuration
CONFIG = {
    "OPENWEATHER_API_KEY": os.environ.get("OPENWEATHER_API_KEY", ""),
    "EMAIL_FROM": os.environ.get("EMAIL_FROM", ""),
    "EMAIL_PASSWORD": os.environ.get("EMAIL_PASSWORD", ""),
    "SMTP_SERVER": os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
    "SMTP_PORT": int(os.environ.get("SMTP_PORT", "587")),
    "MIN_TEMP_NO_PRECIP": 33,
    "MIN_TEMP_WITH_PRECIP": 45,
    "RIDE_START_HOUR": 6,
    "RIDE_END_HOUR": 19,
}

def init_db():
    """Initialize the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            last_email_sent TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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

def analyze_biking_conditions(weather_data):
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

            min_temp = CONFIG["MIN_TEMP_WITH_PRECIP"] if has_precip else CONFIG["MIN_TEMP_NO_PRECIP"]
            is_suitable = feels_like >= min_temp and precip_type != 'snow'

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

def generate_email_report(biking_windows, city, state):
    """Generate HTML email report for a subscriber."""
    today = datetime.now().strftime('%A, %B %d, %Y')

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
            <strong>Conditions:</strong>
            Feels like ≥33°F (no rain) or ≥45°F (with rain) = <span class="suitable">Good to ride!</span>
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

    html += """
        <hr>
        <p style="color: #888; font-size: 12px;">
            Generated by Bike Weather Checker 🚴<br>
            <a href="{unsubscribe_url}">Unsubscribe</a>
        </p>
    </body>
    </html>
    """

    return html

def send_email(to_email, subject, html_content, unsubscribe_token):
    """Send email to a subscriber."""
    html_content = html_content.replace('{unsubscribe_url}',
        f"{os.environ.get('APP_URL', 'http://localhost:5000')}/unsubscribe/{unsubscribe_token}")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = CONFIG['EMAIL_FROM']
    msg['To'] = to_email

    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)

    try:
        with smtplib.SMTP(CONFIG['SMTP_SERVER'], CONFIG['SMTP_PORT']) as server:
            server.starttls()
            server.login(CONFIG['EMAIL_FROM'], CONFIG['EMAIL_PASSWORD'])
            server.sendmail(CONFIG['EMAIL_FROM'], to_email, msg.as_string())
        return True
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

    # Save to database
    conn = get_db()
    try:
        conn.execute('''
            INSERT INTO subscribers (email, city, state, zip_code, lat, lon,
                                     verification_token, unsubscribe_token, verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (email, resolved_city or city, state, zip_code, lat, lon,
              verification_token, unsubscribe_token))
        conn.commit()

        # Send welcome email with first forecast
        weather_data = get_weather_forecast(lat, lon)
        biking_windows = analyze_biking_conditions(weather_data)

        good_days = sum(1 for day in biking_windows if day['has_suitable_time'])
        subject = f"🚴 Welcome! {good_days} good biking day(s) this week in {resolved_city or city}!"

        html = generate_email_report(biking_windows, resolved_city or city, state)
        send_email(email, subject, html, unsubscribe_token)

        flash(f'Success! You\'re subscribed for {resolved_city or city}, {state}. Check your email!', 'success')

    except sqlite3.IntegrityError:
        flash('That email is already subscribed.', 'error')
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    """Unsubscribe a user."""
    conn = get_db()
    result = conn.execute('SELECT email, city FROM subscribers WHERE unsubscribe_token = ?', (token,)).fetchone()

    if result:
        conn.execute('DELETE FROM subscribers WHERE unsubscribe_token = ?', (token,))
        conn.commit()
        flash(f'You\'ve been unsubscribed. Sorry to see you go!', 'success')
    else:
        flash('Invalid unsubscribe link.', 'error')

    conn.close()
    return redirect(url_for('index'))

@app.route('/preview')
def preview():
    """Preview email for a location (for testing)."""
    city = request.args.get('city', 'Eastchester')
    state = request.args.get('state', 'NY')

    lat, lon, resolved_city = geocode_location(city, state)
    if lat is None:
        return "Location not found", 404

    weather_data = get_weather_forecast(lat, lon)
    biking_windows = analyze_biking_conditions(weather_data)
    html = generate_email_report(biking_windows, resolved_city or city, state)

    return html.replace('{unsubscribe_url}', '#')

@app.route('/admin/subscribers')
def list_subscribers():
    """List all subscribers (admin endpoint)."""
    # In production, add authentication here
    admin_key = request.args.get('key')
    if admin_key != os.environ.get('ADMIN_KEY', 'dev-admin-key'):
        return "Unauthorized", 401

    conn = get_db()
    subscribers = conn.execute('SELECT email, city, state, created_at FROM subscribers').fetchall()
    conn.close()

    return jsonify([dict(s) for s in subscribers])

def send_daily_emails():
    """Send daily emails to all subscribers. Run this via cron/scheduler."""
    conn = get_db()
    subscribers = conn.execute('SELECT * FROM subscribers WHERE verified = 1').fetchall()

    print(f"Sending daily emails to {len(subscribers)} subscribers...")

    for sub in subscribers:
        try:
            weather_data = get_weather_forecast(sub['lat'], sub['lon'])
            biking_windows = analyze_biking_conditions(weather_data)

            good_days = sum(1 for day in biking_windows if day['has_suitable_time'])

            if good_days > 0:
                subject = f"🚴 {good_days} good biking day(s) this week in {sub['city']}!"
            else:
                subject = f"🚴 Bike Weather Report for {sub['city']} - No ideal conditions"

            html = generate_email_report(biking_windows, sub['city'], sub['state'])

            if send_email(sub['email'], subject, html, sub['unsubscribe_token']):
                conn.execute('UPDATE subscribers SET last_email_sent = ? WHERE id = ?',
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
init_db()

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'send-emails':
        # Run daily email job
        send_daily_emails()
    else:
        # Run web server
        app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
