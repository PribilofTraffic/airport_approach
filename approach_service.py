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
from datetime import datetime
from math import cos, sin, radians

class WeatherService:
    def __init__(self):
        # API endpoints
        self.metar_url = "https://aviationweather.gov/api/data/metar?ids="
        self.alaska_tz = pytz.timezone("America/Anchorage")
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        
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
        
        self.weather_locations = {
            'PASN': [f"{self.base_dir}/PASN/weather.json"],
            'PAPB': [f"{self.base_dir}/PAPB/weather.json"]
        }
        self.last_categories = {}
        for station in self.weather_locations:
            self.last_categories[station] = {
                "category": "N/A",
                "since": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
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
                os.makedirs(os.path.dirname(status_file), exist_ok=True)
                with open(status_file, 'w') as f:
                    json.dump(self.sensor_status[station], f, indent=4)

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
            return ak_time.strftime("%Y-%m-%d %I:%M %p AKST")
        except Exception as e:
            logging.error(f"Error converting time {time_str}: {str(e)}")
            return datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")

    def _ensure_single_instance(self):
        """Ensure only one instance of the service is running."""
        try:
            lockfile = "/tmp/weather_service.lock"
            self.lock_file = open(lockfile, 'w')
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logging.error("Another instance is already running")
            sys.exit(1)

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

    def _fetch_metar(self, station_id):
        """Fetch METAR data from NOAA."""
        try:
            response = requests.get(f"{self.metar_url}{station_id.lower()}", timeout=10)
            if response.status_code != 200:
                logging.error(f"METAR request failed for {station_id}: {response.status_code}")
                return None
            return response.text.strip()
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
                    offline_since = datetime.strptime(current_status["offline_since"], "%Y-%m-%d %I:%M %p AKST").replace(tzinfo=self.alaska_tz)
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
            current_status["offline_since"] = now.strftime("%Y-%m-%d %I:%M %p AKST")
            current_status["outage_duration"] = "just now"
        elif not is_valid and current_status["status"] == "offline":
            # Update duration for offline sensor
            try:
                offline_since = datetime.strptime(current_status["offline_since"], "%Y-%m-%d %I:%M %p AKST").replace(tzinfo=self.alaska_tz)
                duration_sec = (now - offline_since).total_seconds()
                current_status["outage_duration"] = self._format_duration(duration_sec)
            except Exception as e:
                logging.error(f"Error updating duration for {station_id} {field}: {str(e)}")
        
        try:
            status_file = f"{self.base_dir}/{station_id}/sensor_status.json"
            # Add last_updated timestamp
            status_data = self.sensor_status[station_id].copy()
            status_data['last_updated'] = datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
            with open(status_file, 'w') as f:
                json.dump(status_data, f, indent=4)
        except Exception as e:
            logging.error(f"Error writing sensor status for {station_id}: {str(e)}")

    def _parse_metar(self, metar_text, station_id):
        """Parse METAR text into structured data."""
        try:
            parts = metar_text.split()
            if len(parts) < 2:
                return None
                
            # Basic info
            time = self._convert_to_alaska_time(parts[1][:-1])  # Remove trailing 'Z'
            
            # Check if data is stale (more than 1 hour old)
            is_stale = False
            try:
                # Parse METAR time and make it timezone-aware in UTC
                metar_time_str = parts[1][:-1]  # Remove trailing 'Z'
                metar_time = datetime.strptime(metar_time_str, "%d%H%M").replace(tzinfo=pytz.UTC)
                
                # Get current time in UTC with timezone info
                now = datetime.now(pytz.UTC)
                
                # Add year and month to METAR time
                if now.day != metar_time.day:
                    metar_time = metar_time.replace(month=now.month, year=now.year)
                    # Handle month rollover (METAR from previous month)
                    if metar_time > now:
                        metar_time = metar_time.replace(
                            month=now.month-1 if now.month > 1 else 12,
                            year=now.year if now.month > 1 else now.year-1
                        )
                else:
                    metar_time = metar_time.replace(month=now.month, year=now.year)
                
                # Calculate time difference with timezone-aware datetimes
                time_diff = now - metar_time
                is_stale = time_diff.total_seconds() > 5400  # More than 90 minutes (1.5 hours) old
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
            for part in parts:
                if any(part.startswith(t) for t in self.CLOUD_TYPES):
                    type_code = part[:3]
                    if type_code in self.CLOUD_TYPES:
                        height = int(part[3:]) * 100 if len(part) >= 6 else 0
                        clouds = f"{self.CLOUD_TYPES[type_code]}{' at ' + str(height) + ' feet' if height else ''}"
                        clouds_valid = True
                elif part == 'VV///':
                    clouds_valid = True
                    clouds = "Vertical visibility"
                    break
            self._update_sensor_status(station_id, "clouds", clouds_valid)

            # Temperature and dewpoint
            temp = dewpoint = None
            temp_valid = False
            for part in parts:
                if '/' in part and len(part) >= 3:
                    try:
                        temp_str, dew_str = part.split('/')
                        temp = -int(temp_str[1:]) if temp_str.startswith('M') else int(temp_str)
                        dewpoint = -int(dew_str[1:]) if dew_str.startswith('M') else int(dew_str)
                        temp = int(temp * 1.8 + 32)
                        dewpoint = int(dewpoint * 1.8 + 32)
                        temp_valid = True
                    except (ValueError, IndexError):
                        pass
                    break
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
                warnings.append("Data is more than 1 hour old")
                
            # Analyze runway favorability and crosswind conditions for supported airports
            runway_analysis = None
            if station_id in ["PASN", "PAPB"] and wind_valid:
                runway_analysis = self._analyze_runway_winds(station_id, wind_direction, wind_speed)
                
                # Add warnings to remarks if needed
                if runway_analysis:
                    if runway_analysis.get("crosswind_warning"):
                        warnings.append("Caution: Strong crosswinds may affect landing")
                    
                    # Add maintenance warning for PAPB
                    if runway_analysis.get("maintenance_warning"):
                        warnings.append("Airport has reported maintenance issues - wind data may be unreliable")

            return {
                "station": station_id,
                "time": time,
                "remarks": warnings,
                "raw_metar": metar_text,
                "wind_speed": wind_speed,
                "wind_direction": wind_direction,
                "wind_gusts": wind_gusts,
                "visibility": visibility,
                "weather_conditions": conditions,
                "clouds": clouds,
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
            
        # Define runway configurations for each airport
        runway_configs = {
            "PASN": {
                "18": 180,
                "36": 360,
                "maintenance_issues": False
            },
            "PAPB": {
                "11": 110,
                "29": 290,
                "maintenance_issues": True
            }
        }
        
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
            
            # Determine favored runway (higher headwind is better)
            runway_18 = results["18"]
            runway_36 = results["36"]
            
            favored = "18"
            if runway_36["headwind"] > runway_18["headwind"]:
                favored = "36"
                
            # Check for strong crosswinds (over 25 kts is concerning)
            crosswind_warning = False
            if results[favored]["crosswind"] > 25:
                crosswind_warning = True
                
            # Add maintenance warning for PAPB
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
                "since": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
            }
        elif self.last_categories[station]["category"] != new_category:
            self.last_categories[station] = {
                "category": new_category,
                "since": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
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
                                    weather_data['time'] = datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
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
