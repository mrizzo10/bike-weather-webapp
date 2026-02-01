# Bike Weather Checker - Web App

A web application that allows users to sign up for daily bike weather emails based on their location.

## Features

- Simple signup with email and location
- Daily 6 AM emails with 5-day bike weather forecast
- Personalized forecasts based on user's city/state
- One-click unsubscribe
- Supports multiple subscribers

## Local Development

1. Install dependencies:
```bash
cd webapp
pip install -r requirements.txt
```

2. Copy the `.env` file from the parent directory or create one:
```bash
cp ../.env .env
```

Required environment variables:
- `OPENWEATHER_API_KEY` - Get free at https://openweathermap.org/api
- `EMAIL_FROM` - Your Gmail address
- `EMAIL_PASSWORD` - Gmail App Password (not your regular password)
- `FLASK_SECRET_KEY` - Any random string for session security
- `APP_URL` - Your deployed URL (for unsubscribe links)

3. Run the development server:
```bash
python app.py
```

4. Visit http://localhost:5000

## Deploying to Render (Recommended)

Render offers free hosting with built-in cron jobs for daily emails.

1. Push this code to a GitHub repository

2. Go to [render.com](https://render.com) and sign up

3. Click "New" → "Blueprint"

4. Connect your GitHub repo

5. Render will auto-detect the `render.yaml` and set up:
   - Web service (the signup page)
   - Cron job (daily emails at 6 AM EST)

6. Add your environment variables in the Render dashboard:
   - `OPENWEATHER_API_KEY`
   - `EMAIL_FROM`
   - `EMAIL_PASSWORD`

7. Deploy!

## Deploying to Railway

1. Install Railway CLI: `npm install -g @railway/cli`

2. Login: `railway login`

3. Create project: `railway init`

4. Deploy: `railway up`

5. Add environment variables in Railway dashboard

6. For daily emails, set up a cron job using Railway's scheduler

## Manual Daily Emails

If your hosting doesn't support cron, you can trigger emails manually:

```bash
python app.py send-emails
```

Or set up an external cron service (like cron-job.org) to call your server.

## Admin Endpoints

- `/admin/subscribers?key=YOUR_ADMIN_KEY` - List all subscribers (JSON)
- `/preview?city=Boston&state=MA` - Preview email for any location

Set `ADMIN_KEY` environment variable for admin access.

## Database

Uses SQLite stored in `subscribers.db`. For production, consider upgrading to PostgreSQL.

## Tech Stack

- Flask (Python web framework)
- SQLite (database)
- OpenWeatherMap API (weather data)
- Gmail SMTP (email delivery)
