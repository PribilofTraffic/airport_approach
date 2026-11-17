#!/usr/bin/env python3
# Weather Service for Pribilof Traffic - Handles METAR data
# By Codeium Engineering Team, refined by Grok
from typing import Dict, Any
import requests
import pytz
import json
import os
import sys
import time
import logging
import fcntl
import sqlite3

import config
from fileio import write_json
from datetime import datetime
from datetime import timedelta
from math import cos, sin, radians

class WeatherService:
    def __init__(self):
        # API endpoints - Using NOAA Weather API (api.weather.gov) instead of aviationweather.gov
        # Changed after IP address change caused blocking by aviationweather.gov
        self.metar_url = "https://api.weather.gov/stations/{}/observations/latest"
        self.alaska_tz = pytz.timezone(config.TIMEZONE)
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        # How the service identifies itself to api.weather.gov, which asks
        # every client for a User-Agent, and how long to wait for an answer.
        self.user_agent = os.getenv('USER_AGENT', 'approach-service-collector/1.1')
        self.fetch_timeout = int(os.getenv('FETCH_TIMEOUT', '10'))
        
        # Rate limiting settings
        self.max_retries = 3  # Maximum number of retries per API call
        self.retry_delay = 60  # Seconds to wait between retries
        self.error_count = 0   # Track consecutive errors
        
        self.CLOUD_TYPES = {
            'SKC': 'Clear skies', 'CLR': 'Clear skies', 'FEW': 'Few clouds',
            'SCT': 'Scattered clouds', 'BKN': 'Broken clouds', 'OVC': 'Overcast'
        }
        
        self.WEATHER_CODES = {
            'BR': 'Mist', 'FG': 'Fog', 'RA': 'Rain', 'SN': 'Snow', 'UP': 'Unknown precipitation',
            'HZ': 'Haze', 'DZ': 'Drizzle', 'RASN': 'Rain and snow', 'SHRA': 'Rain showers',
            'SHSN': 'Snow showers', 'FZRA': 'Freezing rain', 'FZDZ': 'Freezing drizzle'
        }
        
        # The airports to follow come from airports.json
        self.weather_locations = {
            station: [f"{self.base_dir}/{station}/weather.json"]
            for station in config.STATIONS
        }
        self.runway_configs = config.RUNWAY_CONFIGS
        self.last_categories = {}
        for station in self.weather_locations:
            self.last_categories[station] = {
                "category": "N/A",
                "since": datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
            }
        self.sensor_status = {}
        for station in self.weather_locations:
            status_file = f"{self.base_dir}/{station}/sensor_status.json"
            try:
                with open(status_file, 'r') as f:
                    self.sensor_status[station] = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self.sensor_status[station] = {
                    "wind": {"status": "online", "offline_since": None},
                    "temperature": {"status": "online", "offline_since": None},
                    "visibility": {"status": "online", "offline_since": None},
                    "clouds": {"status": "online", "offline_since": None},
                    "pressure": {"status": "online", "offline_since": None}
                }
                write_json(status_file, self.sensor_status[station], indent=4)

        self.metar_history_hours = int(os.getenv('METAR_HISTORY_HOURS', '12'))

    def _convert_to_alaska_time(self, time_str):
        try:
            day = int(time_str[:2])
            hour = int(time_str[2:4])
            minute = int(time_str[4:6]) if len(time_str) > 4 else 0
            now = datetime.now(pytz.utc)
            if day > now.day:
                month = now.month - 1 if now.month > 1 else 12
                year = now.year if now.month > 1 else now.year - 1
            else:
                month = now.month
                year = now.year
            dt = datetime(year, month, day, hour, minute, tzinfo=pytz.utc)
            ak_time = dt.astimezone(self.alaska_tz)
            return ak_time.strftime(config.TIME_FORMAT)
        except Exception as e:
            logging.error(f"Error converting time {time_str}: {str(e)}")
            return datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)

    def _ensure_single_instance(self):
        """Ensure only one instance of the service is running."""
        try:
            lockfile = "/tmp/weather_service.lock"
            self.lock_file = open(lockfile, 'w')
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logging.error("Another instance is already running")
            sys.exit(1)

    def _get_metar_history_db_path(self, station: str) -> str:
        return os.path.join(self.base_dir, station, 'metar_history.db')

    def _ensure_metar_history_db(self, station: str) -> None:
        db_path = self._get_metar_history_db_path(station)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metar_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    observed_at_local TEXT,
                    raw_metar TEXT,
                    category TEXT,
                    visibility_sm REAL,
                    ceiling_ft INTEGER,
                    wind_dir_deg INTEGER,
                    wind_speed_kt INTEGER,
                    wind_gust_kt INTEGER,
                    conditions_json TEXT,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE(station, observed_at_utc)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metar_obs_station_time ON metar_observations(station, observed_at_utc);"
            )
            conn.commit()
        finally:
            conn.close()

    def _parse_visibility_sm_value(self, visibility_str: str):
        if visibility_str in (None, "N/A"):
            return None
        try:
            return float(str(visibility_str).split()[0])
        except Exception:
            return None

    def _parse_wind_speed_kt_value(self, wind_speed_str: str):
        if wind_speed_str in (None, "N/A"):
            return None
        try:
            return int(float(str(wind_speed_str).replace('kt', '')))
        except Exception:
            return None

    def _parse_wind_gust_kt_value(self, wind_gusts_str: str):
        if wind_gusts_str in (None, "N/A"):
            return None
        try:
            return int(float(str(wind_gusts_str).replace('kt', '')))
        except Exception:
            return None

    def _parse_wind_dir_deg_value(self, wind_direction_str: str):
        if wind_direction_str in (None, "N/A", "Variable"):
            return None
        try:
            return int(str(wind_direction_str).split('°')[0])
        except Exception:
            return None

    def _record_metar_observation(self, station: str, metar_data: Dict[str, Any]) -> None:
        observed_at_utc = metar_data.get('observed_at_utc')
        if not observed_at_utc:
            return

        self._ensure_metar_history_db(station)
        db_path = self._get_metar_history_db_path(station)

        visibility_sm = self._parse_visibility_sm_value(metar_data.get('visibility'))
        ceiling_ft = metar_data.get('ceiling_ft')
        wind_dir_deg = self._parse_wind_dir_deg_value(metar_data.get('wind_direction'))
        wind_speed_kt = self._parse_wind_speed_kt_value(metar_data.get('wind_speed'))
        wind_gust_kt = self._parse_wind_gust_kt_value(metar_data.get('wind_gusts'))
        conditions_json = json.dumps(metar_data.get('weather_conditions', []))
        created_at_utc = datetime.now(pytz.UTC).isoformat()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO metar_observations (
                    station, observed_at_utc, observed_at_local, raw_metar, category,
                    visibility_sm, ceiling_ft, wind_dir_deg, wind_speed_kt, wind_gust_kt,
                    conditions_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    station,
                    observed_at_utc,
                    metar_data.get('time'),
                    metar_data.get('raw_metar'),
                    metar_data.get('flight_category'),
                    visibility_sm,
                    ceiling_ft,
                    wind_dir_deg,
                    wind_speed_kt,
                    wind_gust_kt,
                    conditions_json,
                    created_at_utc,
                )
            )
            conn.commit()
        finally:
            conn.close()

    def _write_metar_history_json(self, station: str) -> None:
        self._ensure_metar_history_db(station)
        db_path = self._get_metar_history_db_path(station)
        out_path = os.path.join(self.base_dir, station, 'metar_history.json')
        cutoff = (datetime.now(pytz.UTC) - timedelta(hours=self.metar_history_hours)).isoformat()

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT observed_at_utc, raw_metar, category, visibility_sm, ceiling_ft,
                       wind_dir_deg, wind_speed_kt, wind_gust_kt, conditions_json
                FROM metar_observations
                WHERE station = ? AND observed_at_utc >= ?
                ORDER BY observed_at_utc DESC
                """,
                (station, cutoff)
            ).fetchall()
        finally:
            conn.close()

        items = []
        for (observed_at_utc, raw_metar, category, visibility_sm, ceiling_ft,
             wind_dir_deg, wind_speed_kt, wind_gust_kt, conditions_json) in rows:
            try:
                obs_dt = datetime.fromisoformat(observed_at_utc)
            except Exception:
                continue

            observed_utc_str = obs_dt.astimezone(pytz.UTC).strftime('%H:%MZ')

            try:
                conditions = json.loads(conditions_json) if conditions_json else []
            except Exception:
                conditions = []

            items.append({
                'time_utc': observed_utc_str,
                'observed_at_utc': observed_at_utc,
                'category': category,
                'conditions': conditions,
                'wind_dir_deg': wind_dir_deg,
                'wind_speed_kt': wind_speed_kt,
                'wind_gust_kt': wind_gust_kt,
                'visibility_sm': visibility_sm,
                'ceiling_ft': ceiling_ft,
                'raw_metar': raw_metar,
            })

        write_json(out_path, items, indent=2)

    def _update_weather_json(self, station: str, metar_data: Dict[str, Any]) -> None:
        """Update the weather JSON files with new METAR data."""
        try:
            for location in self.weather_locations[station]:
                try:
                    temp_file = f"{location}.tmp"
                    with open(temp_file, 'w') as f:
                        json.dump(metar_data, f, indent=4)
                    os.replace(temp_file, location)
                    logging.info(f"Updated {location} with METAR data")
                except Exception as e:
                    logging.error(f"Error writing to {location}: {str(e)}")
                    try:
                        os.remove(temp_file)
                    except:
                        pass
        except Exception as e:
            logging.error(f"Error in _update_weather_json for {station}: {str(e)}")

    def _construct_metar_from_noaa(self, station_id, props):
        """Construct a METAR string from NOAA structured data."""
        try:
            # Extract timestamp and format it - use built-in datetime
            timestamp_str = props['timestamp'].replace('Z', '+00:00')  # Make it timezone aware
            timestamp = datetime.fromisoformat(timestamp_str)
            time_str = timestamp.strftime("%d%H%M") + "Z"

            # Start building METAR
            metar_parts = [f"METAR {station_id.upper()}", time_str, "AUTO"]

            # Wind
            if props.get('windDirection', {}).get('value') and props.get('windSpeed', {}).get('value'):
                # Convert wind speed from km/h to knots
                wind_speed_kt = int(props['windSpeed']['value'] / 1.852)
                wind_dir = int(props['windDirection']['value'])
                wind_str = f"{wind_dir:03d}{wind_speed_kt:02d}KT"
                metar_parts.append(wind_str)

            # Visibility - convert meters to statute miles
            if props.get('visibility', {}).get('value'):
                vis_m = props['visibility']['value']
                vis_sm = int(vis_m / 1609.34)
                if vis_sm >= 10:
                    metar_parts.append("10SM")
                else:
                    metar_parts.append(f"{vis_sm}SM")

            # Cloud layers
            if props.get('cloudLayers'):
                for layer in props['cloudLayers']:
                    if layer.get('base', {}).get('value'):
                        # Convert meters to feet and then to hundreds of feet
                        base_ft = int(layer['base']['value'] * 3.28084 / 100)
                        coverage = layer.get('amount', 'UNK')[:3].upper()
                        metar_parts.append(f"{coverage}{base_ft:03d}")

            # Temperature/Dewpoint
            if props.get('temperature', {}).get('value') is not None and props.get('dewpoint', {}).get('value') is not None:
                temp_c = int(props['temperature']['value'])
                dew_c = int(props['dewpoint']['value'])
                temp_str = f"M{abs(temp_c):02d}" if temp_c < 0 else f"{temp_c:02d}"
                dew_str = f"M{abs(dew_c):02d}" if dew_c < 0 else f"{dew_c:02d}"
                metar_parts.append(f"{temp_str}/{dew_str}")

            # Altimeter setting
            if props.get('barometricPressure', {}).get('value'):
                # Convert Pa to inHg
                pressure_pa = props['barometricPressure']['value']
                pressure_inhg = pressure_pa / 3386.39
                alt_setting = int(pressure_inhg * 100)
                metar_parts.append(f"A{alt_setting:04d}")

            # Remarks
            metar_parts.append("RMK AO2")

            return " ".join(metar_parts)

        except Exception as e:
            logging.error(f"Error constructing METAR from NOAA data: {str(e)}")
            return None

    def _fetch_metar(self, station_id):
        """Fetch METAR data from NOAA Weather API."""
        try:
            url = self.metar_url.format(station_id.upper())
            response = requests.get(url, timeout=self.fetch_timeout,
                                    headers={'User-Agent': self.user_agent})
            if response.status_code != 200:
                logging.error(f"METAR request failed for {station_id}: {response.status_code}")
                return None

            # Parse JSON response from NOAA API
            data = response.json()
            props = data.get('properties', {})

            # Try to get raw METAR first
            if props.get('rawMessage'):
                metar_text = props['rawMessage'].strip()
                if metar_text:
                    logging.info(f"Using raw METAR for {station_id}")
                    return metar_text

            # If no raw METAR, construct one from structured data
            logging.info(f"Constructing METAR from structured data for {station_id}")
            metar_text = self._construct_metar_from_noaa(station_id, props)
            if metar_text:
                logging.info(f"Constructed METAR for {station_id}: {metar_text}")
                return metar_text
            else:
                logging.warning(f"Could not construct METAR for {station_id}")
                return None

        except Exception as e:
            logging.error(f"Error fetching METAR for {station_id}: {str(e)}")
            return None

    def _format_duration(self, seconds):
        """Convert seconds into human readable duration."""
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} min"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hr"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"{days} days"
        elif seconds < 2592000:
            weeks = int(seconds / 604800)
            return f"{weeks} weeks"
        else:
            months = int(seconds / 2592000)
            return f"{months} months"

    def _update_sensor_status(self, station_id, field, is_valid):
        if field not in self.sensor_status[station_id]:
            return
        current_status = self.sensor_status[station_id][field]
        now = datetime.now(self.alaska_tz)
        
        if is_valid and current_status["status"] == "offline":
            # Sensor coming back online
            if current_status.get("offline_since"):
                try:
                    offline_since = datetime.strptime(current_status["offline_since"], config.TIME_FORMAT).replace(tzinfo=self.alaska_tz)
                    duration_sec = (now - offline_since).total_seconds()
                    logging.info(f"{station_id} {field} sensor restored after {self._format_duration(duration_sec)}")
                except Exception as e:
                    logging.error(f"Error calculating duration for {station_id} {field}: {str(e)}")
            
            current_status["status"] = "online"
            current_status["offline_since"] = None
            current_status["outage_duration"] = None
        elif not is_valid and current_status["status"] == "online":
            # Sensor going offline
            current_status["status"] = "offline"
            current_status["offline_since"] = now.strftime(config.TIME_FORMAT)
            current_status["outage_duration"] = "just now"
        elif not is_valid and current_status["status"] == "offline":
            # Update duration for offline sensor
            try:
                offline_since = datetime.strptime(current_status["offline_since"], config.TIME_FORMAT).replace(tzinfo=self.alaska_tz)
                duration_sec = (now - offline_since).total_seconds()
                current_status["outage_duration"] = self._format_duration(duration_sec)
            except Exception as e:
                logging.error(f"Error updating duration for {station_id} {field}: {str(e)}")
        
        try:
            status_file = f"{self.base_dir}/{station_id}/sensor_status.json"
            # Add last_updated timestamp
            status_data = self.sensor_status[station_id].copy()
            status_data['last_updated'] = datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
            write_json(status_file, status_data, indent=4)
        except Exception as e:
            logging.error(f"Error writing sensor status for {station_id}: {str(e)}")

    def _parse_metar(self, metar_text, station_id):
        """Parse METAR text into structured data."""
        try:
            parts = metar_text.split()
            if len(parts) < 2:
                return None

            # Basic info
            # METAR strings can be in either form:
            # - "PASN 052030Z ..." (time token at index 1)
            # - "METAR PASN 052030Z ..." (time token at index 2)
            time_token = parts[1]
            if parts[0] in ("METAR", "SPECI") and len(parts) >= 3:
                time_token = parts[2]

            time = self._convert_to_alaska_time(time_token[:-1])  # Remove trailing 'Z'
            
            # Check if data is stale
            is_stale = False
            metar_time_utc = None
            try:
                metar_time_str = time_token[:-1]  # Remove trailing 'Z'
                metar_day = int(metar_time_str[0:2])
                metar_hour = int(metar_time_str[2:4])
                metar_minute = int(metar_time_str[4:6])

                # Get current time in UTC with timezone info
                now = datetime.now(pytz.UTC)

                # Build a candidate METAR timestamp in the current month/year first.
                metar_time = datetime(now.year, now.month, metar_day, metar_hour, metar_minute, tzinfo=pytz.UTC)

                # If the candidate time is in the future, it's almost certainly from the previous month.
                if metar_time > (now + timedelta(minutes=10)):
                    prev_month = now.month - 1
                    prev_year = now.year
                    if prev_month == 0:
                        prev_month = 12
                        prev_year -= 1
                    metar_time = datetime(prev_year, prev_month, metar_day, metar_hour, metar_minute, tzinfo=pytz.UTC)

                time_diff = now - metar_time
                is_stale = time_diff.total_seconds() > 7200  # More than 2 hours old
                metar_time_utc = metar_time
            except Exception as e:
                logging.error(f"Error checking stale data: {str(e)}")
                is_stale = True

            # Wind
            wind_speed = wind_direction = wind_gusts = "N/A"
            wind_valid = False
            for part in parts:
                if part.endswith('KT'):
                    try:
                        if part.startswith('VRB'):
                            wind_direction = "Variable"
                            wind_speed = f"{part[3:-2]}kt"
                        else:
                            direction = int(part[:3])
                            speed = part[3:-2]
                            if 'G' in speed:
                                speed, gusts = speed.split('G')
                                wind_gusts = f"{gusts}kt"
                            wind_direction = f"{direction}° ({self._degrees_to_cardinal(direction)})"
                            wind_speed = f"{speed}kt"
                        wind_valid = True
                    except ValueError:
                        pass
                    break
            self._update_sensor_status(station_id, "wind", wind_valid)
            
            # Visibility
            visibility = "N/A"
            vis_valid = False
            for i, part in enumerate(parts):
                if part.endswith('SM'):
                    # Check if this is a combined value like "1 1/2SM"
                    # In some METARs, the whole number and fraction are separate tokens
                    if i > 0 and "/" in part[:-2] and parts[i-1].isdigit():
                        # Format is like "1 1/2SM" split across two tokens
                        whole = parts[i-1]
                        vis_raw = f"{whole} {part[:-2]}"
                        try:
                            whole_num = int(whole)
                            fraction = part[:-2]
                            num, denom = fraction.split("/")
                            visibility = str(whole_num + float(num) / float(denom))
                        except (ValueError, ZeroDivisionError):
                            logging.error(f"Error parsing complex visibility: {vis_raw}")
                            visibility = "1.5"  # Default to 1.5 for "1 1/2" as fallback
                    else:
                        # Regular single token visibility
                        vis_raw = part[:-2]
                        
                        # Handle special cases
                        if vis_raw == "P6":
                            visibility = "6.0"
                        elif vis_raw == "M1/4":
                            # Less than 1/4 mile
                            visibility = "0.25"
                        elif "/" in vis_raw:
                            # Handle fractional values like 1/4, 1/2, etc.
                            try:
                                # Check if it's a mixed number like "1 1/4" in a single token
                                if " " in vis_raw:
                                    whole, fraction = vis_raw.split()
                                    num, denom = fraction.split("/")
                                    visibility = str(float(whole) + float(num) / float(denom))
                                else:
                                    # Simple fraction like 1/4
                                    num, denom = vis_raw.split("/")
                                    visibility = str(float(num) / float(denom))
                            except (ValueError, ZeroDivisionError):
                                logging.error(f"Error parsing fractional visibility: {vis_raw}")
                                visibility = vis_raw  # Keep original on error
                        else:
                            # Regular numeric value
                            visibility = vis_raw
                    
                    # Keep original visibility string for display, but add converted value for clarity
                    numeric_vis = visibility
                    try:
                        numeric_vis = f"{float(visibility):.2f}".rstrip('0').rstrip('.')
                    except ValueError:
                        pass
                    
                    # Format with both original and numeric value if they differ
                    # For multi-token visibility, reconstruct the original
                    if "1 1/2" in vis_raw or (i > 0 and "/" in part[:-2] and parts[i-1].isdigit()):
                        visibility = f"{numeric_vis} miles (1 1/2SM)"
                    elif vis_raw != numeric_vis:
                        visibility = f"{numeric_vis} miles ({vis_raw}SM)"
                    else:
                        visibility = f"{numeric_vis} miles"
                        
                    vis_valid = True
                    break
            self._update_sensor_status(station_id, "visibility", vis_valid)
            
            # Weather conditions
            conditions = []
            for part in parts:
                if part in self.WEATHER_CODES:
                    conditions.append(self.WEATHER_CODES[part])
                elif part.startswith('-') and part[1:] in self.WEATHER_CODES:
                    conditions.append(f"Light {self.WEATHER_CODES[part[1:]]}")
                elif part.startswith('+') and part[1:] in self.WEATHER_CODES:
                    conditions.append(f"Heavy {self.WEATHER_CODES[part[1:]]}")
            
            # Cloud coverage
            clouds = "N/A"
            clouds_valid = False
            cloud_layers = []
            ceiling_ft = None

            for part in parts:
                # Handle VV/// and VV### (vertical visibility)
                if part.startswith('VV'):
                    if part == 'VV///':
                        cloud_layers.append({
                            'type': 'VV',
                            'height_ft': None,
                            'is_ceiling': True
                        })
                        clouds_valid = True
                        continue
                    if len(part) == 5 and part[2:].isdigit():
                        vv_height = int(part[2:]) * 100
                        cloud_layers.append({
                            'type': 'VV',
                            'height_ft': vv_height,
                            'is_ceiling': True
                        })
                        clouds_valid = True
                        if ceiling_ft is None or vv_height < ceiling_ft:
                            ceiling_ft = vv_height
                        continue

                # Handle standard layers (SKC/CLR/FEW/SCT/BKN/OVC)
                if len(part) >= 3 and part[:3] in self.CLOUD_TYPES:
                    type_code = part[:3]
                    height_ft = None
                    if len(part) >= 6 and part[3:6].isdigit():
                        height_ft = int(part[3:6]) * 100

                    is_ceiling_layer = type_code in ('BKN', 'OVC')
                    cloud_layers.append({
                        'type': type_code,
                        'description': self.CLOUD_TYPES[type_code],
                        'height_ft': height_ft,
                        'is_ceiling': is_ceiling_layer
                    })
                    clouds_valid = True

                    if is_ceiling_layer and height_ft is not None:
                        if ceiling_ft is None or height_ft < ceiling_ft:
                            ceiling_ft = height_ft

            # Backwards-compatible summary string for existing consumers
            if ceiling_ft is not None:
                # Choose the lowest ceiling layer for display
                ceiling_layer = None
                for layer in cloud_layers:
                    if not layer.get('is_ceiling'):
                        continue
                    if layer.get('height_ft') == ceiling_ft:
                        ceiling_layer = layer
                        break

                if ceiling_layer and ceiling_layer.get('type') == 'VV':
                    clouds = f"Vertical visibility at {ceiling_ft} feet"
                elif ceiling_layer and ceiling_layer.get('description'):
                    clouds = f"{ceiling_layer['description']} at {ceiling_ft} feet"
                else:
                    clouds = f"Ceiling at {ceiling_ft} feet"
            else:
                # If no ceiling layer, show something meaningful if we have layers
                if cloud_layers:
                    first_layer = cloud_layers[0]
                    if first_layer.get('type') == 'VV':
                        clouds = "Vertical visibility"
                    elif first_layer.get('description') and first_layer.get('height_ft') is not None:
                        clouds = f"{first_layer['description']} at {first_layer['height_ft']} feet"
                    elif first_layer.get('description'):
                        clouds = f"{first_layer['description']}"

            self._update_sensor_status(station_id, "clouds", clouds_valid)

            # Temperature and dewpoint
            temp = dewpoint = None
            temp_valid = False
            
            # Find temperature/dewpoint field that matches the format M06/M08 or 06/08
            temp_pattern_found = False
            for part in parts:
                if '/' in part and len(part) >= 3:
                    # Check if this looks like a temperature reading (M## or ## format)
                    temp_str, dew_str = part.split('/')
                    
                    # Validate temp/dewpoint format - both parts should be 'M' followed by digits or just digits
                    if ((temp_str.startswith('M') and temp_str[1:].isdigit()) or temp_str.isdigit()) and \
                       ((dew_str.startswith('M') and dew_str[1:].isdigit()) or dew_str.isdigit()):
                        try:
                            temp = -int(temp_str[1:]) if temp_str.startswith('M') else int(temp_str)
                            dewpoint = -int(dew_str[1:]) if dew_str.startswith('M') else int(dew_str)
                            
                            # Convert to Fahrenheit
                            temp = int(temp * 1.8 + 32)
                            dewpoint = int(dewpoint * 1.8 + 32)
                            temp_valid = True
                            temp_pattern_found = True
                            logging.info(f"Found temperature data in METAR: {part} -> {temp}°F/{dewpoint}°F")
                            break
                        except (ValueError, IndexError) as e:
                            logging.error(f"Error parsing temperature/dewpoint '{part}': {str(e)}")
                            
            # If we didn't find a standard temp pattern, look in the remarks section for encoded temperature
            if not temp_pattern_found and "T" in parts:
                t_index = parts.index("T")
                if t_index < len(parts) - 1 and len(parts[t_index + 1]) == 8 and parts[t_index + 1].isdigit():
                    # Format is T01830145 - first 4 digits are temp in tenths of C with sign
                    try:
                        temp_code = parts[t_index + 1][:4]
                        sign = 1 if temp_code[0] == '0' else -1
                        temp_c = sign * int(temp_code[1:4]) / 10
                        temp = int(temp_c * 1.8 + 32)
                        
                        dew_code = parts[t_index + 1][4:8]
                        sign = 1 if dew_code[0] == '0' else -1
                        dew_c = sign * int(dew_code[1:4]) / 10
                        dewpoint = int(dew_c * 1.8 + 32)
                        
                        temp_valid = True
                        logging.info(f"Found encoded temperature in remarks: T{temp_code}{dew_code} -> {temp}°F/{dewpoint}°F")
                    except (ValueError, IndexError) as e:
                        logging.error(f"Error parsing encoded temperature: {str(e)}")
                
            self._update_sensor_status(station_id, "temperature", temp_valid)
            
            # Pressure
            pressure = "N/A"
            pressure_valid = False
            for part in parts:
                if part.startswith('A'):
                    try:
                        pressure_value = float(part[1:]) / 100
                        pressure = f"{pressure_value:.2f} inHg"
                        pressure_valid = True
                        break
                    except ValueError:
                        pass
            self._update_sensor_status(station_id, "pressure", pressure_valid)

            # Warnings and maintenance
            warnings = []
            if '$' in metar_text:
                warnings.append("Station needs maintenance")
                maintenance_needed = True
            else:
                maintenance_needed = False

            # Flight category calculation
            # Extract numeric visibility value
            vis_value = 0
            if visibility != "N/A":
                try:
                    # Get just the numeric part (before "miles")
                    vis_parts = visibility.split()[0]
                    vis_value = float(vis_parts)
                except (ValueError, IndexError):
                    logging.error(f"Error parsing visibility for flight category: {visibility}")
                    vis_value = 0
            ceiling_value = 99999
            if "at" in clouds:
                try:
                    ceiling_value = int(clouds.split()[clouds.split().index('at') + 1])
                except (ValueError, IndexError):
                    ceiling_value = 99999

            flight_category = self._determine_flight_category(vis_value, ceiling_value)
            category_since = self._check_category_change(station_id, flight_category)

            # Add stale data warning if needed
            if is_stale:
                warnings.append("Data is more than 2 hours old")
                
            # Analyze runway favorability and crosswind conditions for supported airports
            runway_analysis = None
            if station_id in self.runway_configs and wind_valid:
                runway_analysis = self._analyze_runway_winds(station_id, wind_direction, wind_speed)
                
                # Add warnings to remarks if needed
                if runway_analysis:
                    if runway_analysis.get("crosswind_warning"):
                        warnings.append("Caution: Strong crosswinds may affect landing")
                    
                    # Add the maintenance warning for airports flagged in airports.json
                    if runway_analysis.get("maintenance_warning"):
                        warnings.append("Airport has reported maintenance issues - wind data may be unreliable")

            return {
                "station": station_id,
                "time": time,
                "observed_at_utc": metar_time_utc.isoformat() if metar_time_utc else None,
                "remarks": warnings,
                "raw_metar": metar_text,
                "wind_speed": wind_speed,
                "wind_direction": wind_direction,
                "wind_gusts": wind_gusts,
                "visibility": visibility,
                "weather_conditions": conditions,
                "clouds": clouds,
                "cloud_layers": cloud_layers,
                "ceiling_ft": ceiling_ft,
                "temperature": temp,
                "dewpoint": dewpoint,
                "pressure": pressure,
                "flight_category": flight_category,
                "flight_category_since": category_since,
                "maintenance_needed": maintenance_needed,
                "is_stale": is_stale,
                "runway_analysis": runway_analysis
            }

        except Exception as e:
            logging.error(f"Error parsing METAR for {station_id}: {str(e)}")
            return None

    def _degrees_to_cardinal(self, degrees):
        """Convert wind direction in degrees to cardinal direction."""
        directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                     "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        val = int((degrees/22.5) + .5)
        return directions[val % 16]
        
    def _analyze_runway_winds(self, station, wind_direction_str, wind_speed_str):
        """Analyze wind conditions for runway favorability and crosswind component."""
        if wind_direction_str == "N/A" or wind_speed_str == "N/A":
            return None
            
        # Runway headings for each airport come from airports.json
        runway_configs = self.runway_configs
        
        # Check if this airport is supported
        if station not in runway_configs:
            return None
            
        # Get runway configuration for this airport
        airport_config = runway_configs[station]
        runway_headings = {k: v for k, v in airport_config.items() 
                          if k not in ['maintenance_issues']}
        has_maintenance_issues = airport_config.get('maintenance_issues', False)
        
        # Extract wind direction and speed
        try:
            # Handle variable winds
            if wind_direction_str == "Variable":
                return {
                    "favored_runway": "Variable winds - neither runway favored",
                    "crosswind_warning": False,
                    "details": "Variable winds, check local conditions"
                }
            
            # Parse direction and speed
            wind_direction = int(wind_direction_str.split('°')[0])
            # Remove 'kt' and convert to float
            wind_speed = float(wind_speed_str.replace('kt', ''))
            
            # Calculate wind components for each runway
            results = {}
            for runway, heading in runway_headings.items():
                # Normalize heading to 0-359
                heading = heading % 360
                
                # Calculate angle between wind and runway
                angle_diff = abs(wind_direction - heading)
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
                    
                # Calculate headwind/tailwind component (positive = headwind)
                headwind = wind_speed * cos(radians(angle_diff))
                
                # Calculate crosswind component (absolute value)
                crosswind = abs(wind_speed * sin(radians(angle_diff)))
                
                results[runway] = {
                    "headwind": round(headwind, 1),
                    "crosswind": round(crosswind, 1)
                }
            
            # Get runway numbers for this airport
            runway_numbers = list(runway_headings.keys())
            if len(runway_numbers) < 2:
                raise ValueError(f"Airport {station} has insufficient runway configuration")
                
            # Determine favored runway (higher headwind is better). On a
            # tie the runway listed first wins. Works for any number of
            # runway ends, not only two.
            favored = runway_numbers[0]
            for runway in runway_numbers[1:]:
                if results[runway]["headwind"] > results[favored]["headwind"]:
                    favored = runway
                
            # Check for strong crosswinds (over 25 kts is concerning)
            crosswind_warning = False
            if results[favored]["crosswind"] > 25:
                crosswind_warning = True
                
            # Add the maintenance warning for airports flagged in airports.json
            maintenance_warning = ""
            if has_maintenance_issues:
                maintenance_warning = " (NOTE: Airport has reported maintenance issues - data may be unreliable)"
                
            return {
                "favored_runway": f"Runway {favored}",
                "crosswind_warning": crosswind_warning,
                "maintenance_warning": has_maintenance_issues,
                "details": f"Runway {favored} - Headwind: {results[favored]['headwind']}kt, Crosswind: {results[favored]['crosswind']}kt" + 
                           (f" (Caution: Strong crosswinds > 25kt)" if crosswind_warning else "") + 
                           maintenance_warning,
                "components": results
            }
        except Exception as e:
            logging.error(f"Error analyzing runway winds: {str(e)}")
            return {
                "favored_runway": "Unable to determine",
                "crosswind_warning": False,
                "maintenance_warning": has_maintenance_issues,
                "details": f"Error in wind analysis: {str(e)}" + 
                           (" (Airport has reported maintenance issues)" if has_maintenance_issues else "")
            }

    def _determine_flight_category(self, visibility, ceiling):
        """Determine flight category based on visibility and ceiling."""
        if visibility < 1 or ceiling < 500:
            return "LIFR"  # Low Instrument Flight Rules
        elif visibility < 3 or ceiling < 1000:
            return "IFR"   # Instrument Flight Rules
        elif visibility < 5 or ceiling < 3000:
            return "MVFR"  # Marginal Visual Flight Rules
        else:
            return "VFR"   # Visual Flight Rules

    def _check_category_change(self, station, new_category):
        """Check if flight category has changed and update timestamp if it has."""
        if station not in self.last_categories:
            self.last_categories[station] = {
                "category": new_category,
                "since": datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
            }
        elif self.last_categories[station]["category"] != new_category:
            self.last_categories[station] = {
                "category": new_category,
                "since": datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
            }
        return self.last_categories[station]["since"]

    def run(self):
        """Main service loop."""
        consecutive_errors = 0
        while True:
            try:
                for station in self.weather_locations:
                    retry_count = 0
                    max_retries = 3
                    weather_data = None

                    while retry_count < max_retries:
                        try:
                            metar_text = self._fetch_metar(station)
                            if metar_text:
                                weather_data = self._parse_metar(metar_text, station)
                                if weather_data:
                                    self._update_weather_json(station, weather_data)
                                    try:
                                        self._record_metar_observation(station, weather_data)
                                        self._write_metar_history_json(station)
                                    except Exception as e:
                                        logging.error(f"Error updating METAR observation history for {station}: {str(e)}")
                                    logging.info(f"Processed weather data for {station}")
                                    break
                            retry_count += 1
                            if retry_count < max_retries:
                                logging.warning(f"Retrying {station} (attempt {retry_count + 1}/{max_retries})")
                                time.sleep(5)
                        except Exception as e:
                            retry_count += 1
                            logging.error(f"Error processing {station} (attempt {retry_count}): {str(e)}")
                            if retry_count < max_retries:
                                time.sleep(5)

                    if not weather_data and retry_count >= max_retries:
                        try:
                            location = self.weather_locations[station][0]
                            if os.path.exists(location):
                                with open(location, 'r') as f:
                                    weather_data = json.load(f)
                                    weather_data['time'] = datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
                                    weather_data['remarks'] = weather_data.get('remarks', []) + ["Using stale data"]
                                    self._update_weather_json(station, weather_data)
                                    logging.info(f"Used last known good data for {station}")
                        except Exception as e:
                            logging.error(f"Failed to use stale data for {station}: {str(e)}")

                consecutive_errors = 0
                time.sleep(900)  # Wait 15 minutes before next update

            except Exception as e:
                consecutive_errors += 1
                logging.error(f"Error in main loop (consecutive errors: {consecutive_errors}): {str(e)}")
                if consecutive_errors > 5:
                    time.sleep(300)  # Wait 5 minutes after 5 consecutive errors
                else:
                    time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    service = WeatherService()
    service.run()
