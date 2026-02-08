#!/usr/bin/env python3
"""
Test suite for Bike Weather Checker
Run with: python tests.py
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestWeatherAnalysis(unittest.TestCase):
    """Test weather analysis logic"""

    def setUp(self):
        # Import here to avoid issues with missing env vars
        from app import analyze_biking_conditions, CONFIG
        self.analyze = analyze_biking_conditions
        self.CONFIG = CONFIG

    def test_empty_weather_data(self):
        """Should return empty list for no data"""
        result = self.analyze(None)
        self.assertEqual(result, [])

        result = self.analyze({})
        self.assertEqual(result, [])

        result = self.analyze({'list': []})
        self.assertEqual(result, [])

    def test_suitable_conditions_default_thresholds(self):
        """Should mark warm dry conditions as suitable with defaults"""
        weather_data = {
            'list': [{
                'dt': datetime(2026, 2, 8, 12, 0).timestamp(),
                'main': {'feels_like': 50},
                'weather': [{'main': 'Clear', 'description': 'clear sky'}]
            }]
        }
        result = self.analyze(weather_data)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['has_suitable_time'])

    def test_too_cold_no_precip(self):
        """Should mark cold dry conditions as unsuitable"""
        weather_data = {
            'list': [{
                'dt': datetime(2026, 2, 8, 12, 0).timestamp(),
                'main': {'feels_like': 25},
                'weather': [{'main': 'Clear', 'description': 'clear sky'}]
            }]
        }
        result = self.analyze(weather_data, min_temp_no_precip=33)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['has_suitable_time'])

    def test_rain_higher_threshold(self):
        """Should use higher threshold for rainy conditions"""
        weather_data = {
            'list': [{
                'dt': datetime(2026, 2, 8, 12, 0).timestamp(),
                'main': {'feels_like': 40},
                'weather': [{'main': 'Rain', 'description': 'light rain'}]
            }]
        }
        # 40°F is above 33 (no precip) but below 45 (with precip)
        result = self.analyze(weather_data, min_temp_no_precip=33, min_temp_with_precip=45)
        self.assertFalse(result[0]['has_suitable_time'])

        # Same temp but with lower rain threshold
        result = self.analyze(weather_data, min_temp_no_precip=33, min_temp_with_precip=35)
        self.assertTrue(result[0]['has_suitable_time'])

    def test_snow_not_suitable_by_default(self):
        """Should mark snow as unsuitable unless ride_in_snow is True"""
        weather_data = {
            'list': [{
                'dt': datetime(2026, 2, 8, 12, 0).timestamp(),
                'main': {'feels_like': 50},
                'weather': [{'main': 'Snow', 'description': 'light snow'}]
            }]
        }
        result = self.analyze(weather_data, ride_in_snow=False)
        self.assertFalse(result[0]['has_suitable_time'])

        result = self.analyze(weather_data, ride_in_snow=True)
        self.assertTrue(result[0]['has_suitable_time'])

    def test_outside_riding_hours_excluded(self):
        """Should exclude times outside riding hours (6am-7pm)"""
        weather_data = {
            'list': [
                {
                    'dt': datetime(2026, 2, 8, 3, 0).timestamp(),  # 3am - excluded
                    'main': {'feels_like': 50},
                    'weather': [{'main': 'Clear', 'description': 'clear'}]
                },
                {
                    'dt': datetime(2026, 2, 8, 12, 0).timestamp(),  # noon - included
                    'main': {'feels_like': 50},
                    'weather': [{'main': 'Clear', 'description': 'clear'}]
                },
                {
                    'dt': datetime(2026, 2, 8, 22, 0).timestamp(),  # 10pm - excluded
                    'main': {'feels_like': 50},
                    'weather': [{'main': 'Clear', 'description': 'clear'}]
                }
            ]
        }
        result = self.analyze(weather_data)
        # Day should exist with only the noon window
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['windows']), 1)
        self.assertEqual(result[0]['windows'][0]['hour'], 12)

    def test_custom_thresholds(self):
        """Should respect custom temperature thresholds"""
        weather_data = {
            'list': [{
                'dt': datetime(2026, 2, 8, 12, 0).timestamp(),
                'main': {'feels_like': 60},
                'weather': [{'main': 'Clear', 'description': 'clear sky'}]
            }]
        }
        # With high threshold, 60°F is not enough
        result = self.analyze(weather_data, min_temp_no_precip=70)
        self.assertFalse(result[0]['has_suitable_time'])

        # With lower threshold, 60°F is fine
        result = self.analyze(weather_data, min_temp_no_precip=50)
        self.assertTrue(result[0]['has_suitable_time'])


class TestEmailGeneration(unittest.TestCase):
    """Test email report generation"""

    def setUp(self):
        from app import generate_email_report
        self.generate = generate_email_report

    def test_email_contains_location(self):
        """Email should contain city and state"""
        html = self.generate([], "TestCity", "NY")
        self.assertIn("TestCity", html)
        self.assertIn("NY", html)

    def test_email_contains_custom_thresholds(self):
        """Email should show user's custom thresholds"""
        html = self.generate([], "TestCity", "NY", None,
                            min_temp_no_precip=40, min_temp_with_precip=55)
        self.assertIn("40°F", html)
        self.assertIn("55°F", html)

    def test_email_contains_settings_link(self):
        """Email should contain settings link placeholder"""
        html = self.generate([], "TestCity", "NY")
        self.assertIn("{settings_url}", html)

    def test_email_contains_unsubscribe_link(self):
        """Email should contain unsubscribe link placeholder"""
        html = self.generate([], "TestCity", "NY")
        self.assertIn("{unsubscribe_url}", html)

    def test_email_contains_venmo(self):
        """Email should contain tip jar link"""
        html = self.generate([], "TestCity", "NY")
        self.assertIn("venmo.com", html)

    def test_snow_note_when_enabled(self):
        """Email should show snow note when ride_in_snow is True"""
        html = self.generate([], "TestCity", "NY", None, 33, 45, ride_in_snow=True)
        self.assertIn("Snow: rideable", html)

        html = self.generate([], "TestCity", "NY", None, 33, 45, ride_in_snow=False)
        self.assertNotIn("Snow: rideable", html)

    def test_travel_hidden_when_all_days_suitable(self):
        """Travel section should be hidden when all days are suitable"""
        biking_windows = [
            {'date': '2026-02-08', 'day_name': 'Sunday', 'has_suitable_time': True,
             'suitable_count': 3, 'windows': []},
            {'date': '2026-02-09', 'day_name': 'Monday', 'has_suitable_time': True,
             'suitable_count': 3, 'windows': []},
        ]
        travel = {'drive': [{'city': 'Test', 'state': 'XX', 'drive_time': '1 hr',
                            'suitable_days': 2, 'best_temp': 50}], 'fly': []}

        html = self.generate(biking_windows, "TestCity", "NY", travel)
        self.assertNotIn("Travel to Ride", html)

    def test_travel_shown_when_some_days_unsuitable(self):
        """Travel section should show when some days are not suitable"""
        biking_windows = [
            {'date': '2026-02-08', 'day_name': 'Sunday', 'has_suitable_time': True,
             'suitable_count': 3, 'windows': []},
            {'date': '2026-02-09', 'day_name': 'Monday', 'has_suitable_time': False,
             'suitable_count': 0, 'windows': []},
        ]
        travel = {'drive': [{'city': 'Test', 'state': 'XX', 'drive_time': '1 hr',
                            'suitable_days': 2, 'best_temp': 50}], 'fly': []}

        html = self.generate(biking_windows, "TestCity", "NY", travel)
        self.assertIn("Travel to Ride", html)


class TestDistanceCalculation(unittest.TestCase):
    """Test distance and drive time calculations"""

    def setUp(self):
        from app import calculate_distance, estimate_drive_time
        self.calc_distance = calculate_distance
        self.calc_drive_time = estimate_drive_time

    def test_same_location_zero_distance(self):
        """Same coordinates should return ~0 distance"""
        dist = self.calc_distance(40.0, -74.0, 40.0, -74.0)
        self.assertAlmostEqual(dist, 0, places=1)

    def test_known_distance(self):
        """Test with known NYC to Philadelphia distance (~95 miles)"""
        # NYC: 40.7128, -74.0060
        # Philadelphia: 39.9526, -75.1652
        dist = self.calc_distance(40.7128, -74.0060, 39.9526, -75.1652)
        self.assertGreater(dist, 80)
        self.assertLess(dist, 110)

    def test_drive_time_format_hours_minutes(self):
        """Drive time should format as hours and minutes"""
        self.assertEqual(self.calc_drive_time(100), "2 hr")
        self.assertEqual(self.calc_drive_time(75), "1 hr 30 min")
        self.assertEqual(self.calc_drive_time(25), "30 min")


class TestCityLists(unittest.TestCase):
    """Test city configuration"""

    def setUp(self):
        from app import DRIVEABLE_CITIES, AIRPORT_CITIES
        self.driveable = DRIVEABLE_CITIES
        self.airports = AIRPORT_CITIES

    def test_driveable_cities_have_required_fields(self):
        """All driveable cities should have city, state, lat, lon"""
        for city in self.driveable:
            self.assertIn('city', city)
            self.assertIn('state', city)
            self.assertIn('lat', city)
            self.assertIn('lon', city)
            # Validate coordinate ranges
            self.assertGreater(city['lat'], 20)
            self.assertLess(city['lat'], 50)
            self.assertGreater(city['lon'], -130)
            self.assertLess(city['lon'], -60)

    def test_airport_cities_have_required_fields(self):
        """All airport cities should have city, state, airport, lat, lon"""
        for city in self.airports:
            self.assertIn('city', city)
            self.assertIn('state', city)
            self.assertIn('airport', city)
            self.assertIn('lat', city)
            self.assertIn('lon', city)
            # Airport codes should be 3 letters
            self.assertEqual(len(city['airport']), 3)

    def test_sufficient_city_variety(self):
        """Should have enough cities for variety"""
        self.assertGreaterEqual(len(self.driveable), 30)
        self.assertGreaterEqual(len(self.airports), 15)


class TestFlaskRoutes(unittest.TestCase):
    """Test Flask route handling"""

    def setUp(self):
        from app import app
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_index_returns_200(self):
        """Home page should return 200"""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_index_contains_form(self):
        """Home page should contain signup form"""
        response = self.client.get('/')
        self.assertIn(b'email', response.data)
        self.assertIn(b'city', response.data)
        self.assertIn(b'state', response.data)

    def test_index_contains_preference_sliders(self):
        """Home page should contain preference sliders"""
        response = self.client.get('/')
        self.assertIn(b'min_temp_no_precip', response.data)
        self.assertIn(b'min_temp_with_precip', response.data)
        self.assertIn(b'ride_in_snow', response.data)

    def test_invalid_settings_token_redirects(self):
        """Invalid settings token should redirect to home (requires DB)"""
        import os
        if not os.environ.get('DATABASE_URL'):
            self.skipTest("Skipping DB-dependent test - no DATABASE_URL")
        response = self.client.get('/settings/invalid_token_12345')
        self.assertIn(response.status_code, [302, 200])  # Redirect or flash message

    def test_invalid_unsubscribe_token_redirects(self):
        """Invalid unsubscribe token should redirect to home (requires DB)"""
        import os
        if not os.environ.get('DATABASE_URL'):
            self.skipTest("Skipping DB-dependent test - no DATABASE_URL")
        response = self.client.get('/unsubscribe/invalid_token_12345')
        self.assertEqual(response.status_code, 302)


def run_tests():
    """Run all tests and return success/failure"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestWeatherAnalysis))
    suite.addTests(loader.loadTestsFromTestCase(TestEmailGeneration))
    suite.addTests(loader.loadTestsFromTestCase(TestDistanceCalculation))
    suite.addTests(loader.loadTestsFromTestCase(TestCityLists))
    suite.addTests(loader.loadTestsFromTestCase(TestFlaskRoutes))

    # Run with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == '__main__':
    import sys
    success = run_tests()
    sys.exit(0 if success else 1)
