#!/usr/bin/env python3
# Approach Status Service for Pribilof Traffic - Tracks approaches status against current weather
from typing import Dict, Any, List
import json
import os
import sqlite3
import time
import logging
import pytz
from datetime import datetime, timedelta

import config
from fileio import write_json

class ApproachStatus:
    def __init__(self):
        """Initialize the approach status service."""
        self.alaska_tz = pytz.timezone(config.TIMEZONE)
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        
        # Ensure station directories exist
        for station in config.STATIONS:
            os.makedirs(os.path.join(self.base_dir, station), exist_ok=True)
        
        # Approach minimums come from airports.json:
        # {approach_id: {'name', 'visibility' (miles), 'ceiling' (feet), 'category', 'type'}}
        self.approach_minimums = config.APPROACH_MINIMUMS
        self.approach_station = config.APPROACH_STATION
        
        # Initialize status tracking
        self.approach_status = {}
        self.category_status = {}
        
        # Load any existing status data
        self._load_status()
    
    def _load_status(self):
        """Load existing status data if available."""
        try:
            for station in config.STATIONS:
                station_dir = os.path.join(self.base_dir, station)
                
                approach_status_file = os.path.join(station_dir, 'approach_status.json')
                if os.path.exists(approach_status_file):
                    with open(approach_status_file, 'r') as f:
                        if station not in self.approach_status:
                            self.approach_status[station] = {}
                        self.approach_status[station] = json.load(f)
                
                category_status_file = os.path.join(station_dir, 'category_status.json')
                if os.path.exists(category_status_file):
                    with open(category_status_file, 'r') as f:
                        if station not in self.category_status:
                            self.category_status[station] = {}
                        self.category_status[station] = json.load(f)

                if station in self.category_status:
                    self._sanitize_category_status(station)
        except Exception as e:
            logging.error(f"Error loading approach status data: {str(e)}")
            # Initialize new data if loading fails
            self._initialize_new_status()
    
    def _initialize_new_status(self):
        """Initialize new status tracking data."""
        self.approach_status = {}
        for station in config.STATIONS:
            self.approach_status[station] = {}
            for approach_id in self.approach_minimums:
                if self.approach_station[approach_id] == station:
                    self.approach_status[station][approach_id] = {
                        'name': self.approach_minimums[approach_id]['name'],
                        'status': 'UNKNOWN',
                        'below_minimums_since': None,
                        'above_minimums_since': None,
                        'duration_below': None,
                        'last_updated': datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
                    }
        
        self.category_status = {}
        for station in config.STATIONS:
            self.category_status[station] = {
                'current_category': 'UNKNOWN',
                'since': datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT),
                'durations': {
                    'VFR': 0,
                    'MVFR': 0,
                    'IFR': 0,
                    'LIFR': 0
                },
                'last_updated': datetime.now(self.alaska_tz).strftime(config.TIME_FORMAT)
            }
    
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
    
    def _save_status(self):
        """Save current status to JSON files."""
        try:
            for station in self.approach_status:
                station_dir = os.path.join(self.base_dir, station)
                os.makedirs(station_dir, exist_ok=True)
                
                approach_status_file = os.path.join(station_dir, 'approach_status.json')
                write_json(approach_status_file, self.approach_status[station], indent=4)
                
                if station in self.category_status:
                    category_status_file = os.path.join(station_dir, 'category_status.json')
                    write_json(category_status_file, self.category_status[station], indent=4)
            
            logging.info("Saved approach status data for all stations")
        except Exception as e:
            logging.error(f"Error saving approach status data: {str(e)}")
    
    def _parse_alaska_time(self, time_str):
        """Parse Alaska time string to datetime object."""
        try:
            return datetime.strptime(time_str, config.TIME_FORMAT).replace(tzinfo=self.alaska_tz)
        except Exception as e:
            logging.error(f"Error parsing time {time_str}: {str(e)}")
            return datetime.now(self.alaska_tz)

    def _sanitize_category_status(self, station: str) -> None:
        now = datetime.now(self.alaska_tz)
        status = self.category_status.get(station)
        if not isinstance(status, dict):
            return

        if 'durations' not in status or not isinstance(status['durations'], dict):
            status['durations'] = {'VFR': 0, 'MVFR': 0, 'IFR': 0, 'LIFR': 0}

        if 'monthly' not in status or not isinstance(status['monthly'], dict):
            status['monthly'] = {}

        if 'tracking_start' not in status:
            status['tracking_start'] = now.strftime(config.TIME_FORMAT)

        tracking_start_dt = self._parse_alaska_time(status.get('tracking_start'))
        last_updated_dt = self._parse_alaska_time(status.get('last_updated'))

        # If timestamps are nonsensical (very far in the past/future), reset tracking.
        if tracking_start_dt > (now + timedelta(hours=1)) or tracking_start_dt < (now - timedelta(days=366)):
            self.category_status[station] = {
                'current_category': status.get('current_category', 'UNKNOWN'),
                'since': status.get('since', now.strftime(config.TIME_FORMAT)),
                'durations': {'VFR': 0, 'MVFR': 0, 'IFR': 0, 'LIFR': 0},
                'monthly': {},
                'last_updated': now.strftime(config.TIME_FORMAT),
                'tracking_start': now.strftime(config.TIME_FORMAT)
            }
            return

        if last_updated_dt > (now + timedelta(hours=1)) or last_updated_dt < (now - timedelta(days=30)):
            # Prevent runaway accumulation from a bad last_updated baseline.
            status['last_updated'] = now.strftime(config.TIME_FORMAT)

        # If durations are wildly larger than the possible tracking window, reset them.
        max_reasonable = (now - tracking_start_dt).total_seconds() + 86400
        try:
            total_seconds = sum(float(v) for v in status['durations'].values())
        except Exception:
            total_seconds = 0

        if max_reasonable < 0 or total_seconds > (max_reasonable * 5):
            status['durations'] = {'VFR': 0, 'MVFR': 0, 'IFR': 0, 'LIFR': 0}
            status['monthly'] = {}
            status['tracking_start'] = now.strftime(config.TIME_FORMAT)
            status['last_updated'] = now.strftime(config.TIME_FORMAT)

    def _get_category_db_path(self, station: str) -> str:
        return os.path.join(self.base_dir, station, 'category_history.db')

    def _ensure_category_db(self, station: str) -> None:
        db_path = self._get_category_db_path(station)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS category_periods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station TEXT NOT NULL,
                    category TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    ceiling_ft INTEGER,
                    visibility_sm REAL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_category_periods_station_started ON category_periods(station, started_at);")
            conn.commit()
        finally:
            conn.close()

    def _extract_visibility_sm(self, weather_data: Dict[str, Any]):
        visibility_str = weather_data.get('visibility', 'N/A')
        if visibility_str == 'N/A':
            return None
        try:
            return float(str(visibility_str).split()[0])
        except Exception:
            return None

    def _extract_ceiling_ft(self, weather_data: Dict[str, Any]):
        clouds = weather_data.get('clouds', 'N/A')
        if clouds == 'N/A':
            return None
        if 'at' not in clouds:
            return None
        try:
            parts = clouds.split()
            at_index = parts.index('at')
            return int(parts[at_index + 1])
        except Exception:
            return None

    def _record_category_period_if_changed(self, station: str, previous_category: str, current_category: str, now: datetime, weather_data: Dict[str, Any]) -> None:
        if current_category in (None, 'UNKNOWN'):
            return

        self._ensure_category_db(station)
        db_path = self._get_category_db_path(station)
        started_at = now.isoformat()
        ceiling_ft = self._extract_ceiling_ft(weather_data)
        visibility_sm = self._extract_visibility_sm(weather_data)

        conn = sqlite3.connect(db_path)
        try:
            open_row = conn.execute(
                "SELECT id, category FROM category_periods WHERE station = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (station,)
            ).fetchone()

            # If there is no open period, create one even if the category hasn't changed.
            if open_row is None:
                conn.execute(
                    "INSERT INTO category_periods (station, category, started_at, ended_at, ceiling_ft, visibility_sm) VALUES (?, ?, ?, NULL, ?, ?)",
                    (station, current_category, started_at, ceiling_ft, visibility_sm)
                )
                conn.commit()
                return

            open_id, open_category = open_row
            if open_category == current_category:
                return

            conn.execute(
                "UPDATE category_periods SET ended_at = ? WHERE id = ?",
                (started_at, open_id)
            )
            conn.execute(
                "INSERT INTO category_periods (station, category, started_at, ended_at, ceiling_ft, visibility_sm) VALUES (?, ?, ?, NULL, ?, ?)",
                (station, current_category, started_at, ceiling_ft, visibility_sm)
            )
            conn.commit()
        finally:
            conn.close()

    def _write_category_history_json(self, station: str, now: datetime) -> None:
        self._ensure_category_db(station)
        db_path = self._get_category_db_path(station)
        out_path = os.path.join(self.base_dir, station, 'category_history.json')
        cutoff = (now - timedelta(hours=48)).isoformat()

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT category, started_at, ended_at, ceiling_ft, visibility_sm
                FROM category_periods
                WHERE station = ? AND (started_at >= ? OR (ended_at IS NOT NULL AND ended_at >= ?) OR ended_at IS NULL)
                ORDER BY started_at DESC
                """,
                (station, cutoff, cutoff)
            ).fetchall()
        finally:
            conn.close()

        periods = []
        for category, started_at, ended_at, ceiling_ft, visibility_sm in rows:
            try:
                started_dt = datetime.fromisoformat(started_at)
            except Exception:
                continue

            if ended_at:
                try:
                    ended_dt = datetime.fromisoformat(ended_at)
                except Exception:
                    ended_dt = now
            else:
                ended_dt = now

            duration_minutes = int(max(0, (ended_dt - started_dt).total_seconds()) / 60)

            started_str = started_dt.astimezone(self.alaska_tz).strftime(config.TIME_FORMAT)
            ended_str = None
            if ended_at:
                ended_str = ended_dt.astimezone(self.alaska_tz).strftime(config.TIME_FORMAT)

            periods.append({
                'category': category,
                'started': started_str,
                'ended': ended_str,
                'duration_minutes': duration_minutes,
                'ceiling_ft': ceiling_ft,
                'visibility_sm': visibility_sm,
            })

        write_json(out_path, periods, indent=2)
    
    def _update_category_duration(self, station, weather_data):
        """Update flight category duration tracking with monthly breakdown."""
        if station in self.category_status:
            self._sanitize_category_status(station)
        now = datetime.now(self.alaska_tz)
        current_month_key = now.strftime("%Y-%m")  # Format: YYYY-MM
        
        if station not in self.category_status:
            self.category_status[station] = {
                'current_category': 'UNKNOWN',
                'since': now.strftime(config.TIME_FORMAT),
                'durations': {
                    'VFR': 0,
                    'MVFR': 0,
                    'IFR': 0,
                    'LIFR': 0
                },
                'monthly': {},
                'last_updated': now.strftime(config.TIME_FORMAT),
                'tracking_start': now.strftime(config.TIME_FORMAT)
            }
        
        # Initialize current month if not exists
        if 'monthly' not in self.category_status[station]:
            self.category_status[station]['monthly'] = {}
            
        if current_month_key not in self.category_status[station]['monthly']:
            self.category_status[station]['monthly'][current_month_key] = {
                'VFR': 0,
                'MVFR': 0,
                'IFR': 0,
                'LIFR': 0
            }
        
        # Get the current flight category from weather data
        current_category = weather_data.get('flight_category', 'UNKNOWN')
        previous_category = self.category_status[station]['current_category']
        
        # Get the last update time
        last_updated = self._parse_alaska_time(self.category_status[station]['last_updated'])
        
        # Calculate time elapsed since last update
        time_elapsed = (now - last_updated).total_seconds()

        # Prevent runaway accumulation from bad baselines.
        if time_elapsed < 0 or time_elapsed > 86400:
            time_elapsed = 0
        
        # Update duration for the previous category (both total and monthly)
        if previous_category != 'UNKNOWN':
            # Update total durations
            self.category_status[station]['durations'][previous_category] += time_elapsed
            
            # Update monthly durations
            self.category_status[station]['monthly'][current_month_key][previous_category] += time_elapsed
        
        # If category changed, update the 'since' time
        if current_category != previous_category:
            self.category_status[station]['current_category'] = current_category
            self.category_status[station]['since'] = now.strftime(config.TIME_FORMAT)

        # Record discrete category periods and export last-48h history
        # (Creates an initial open period if one doesn't exist.)
        try:
            self._record_category_period_if_changed(station, previous_category, current_category, now, weather_data)
            self._write_category_history_json(station, now)
        except Exception as e:
            logging.error(f"Error updating category history for {station}: {str(e)}")
        
        # Format total durations for display
        formatted_durations = {}
        for cat, seconds in self.category_status[station]['durations'].items():
            formatted_durations[cat] = self._format_duration(seconds)
        
        # Format monthly durations for display
        monthly_formatted = {}
        for month, durations in self.category_status[station]['monthly'].items():
            monthly_formatted[month] = {}
            for cat, seconds in durations.items():
                monthly_formatted[month][cat] = self._format_duration(seconds)
        
        # Calculate monthly percentages
        monthly_percentages = {}
        for month, durations in self.category_status[station]['monthly'].items():
            total_seconds = sum(durations.values())
            if total_seconds > 0:
                monthly_percentages[month] = {}
                for cat, seconds in durations.items():
                    percentage = (seconds / total_seconds) * 100
                    monthly_percentages[month][cat] = f"{percentage:.1f}%"
        
        # Update the tracking data
        self.category_status[station].update({
            'current_category': current_category,
            'durations_formatted': formatted_durations,
            'monthly_formatted': monthly_formatted,
            'monthly_percentages': monthly_percentages,
            'last_updated': now.strftime(config.TIME_FORMAT)
        })
    
    def _update_approach_status(self, station, weather_data, unreliable=False, sensor_status=None):
        """Update approach status based on current weather conditions."""
        now = datetime.now(self.alaska_tz)
        
        # Extract visibility and ceiling from weather data
        try:
            visibility_str = weather_data.get('visibility', 'N/A')
            if visibility_str == 'N/A':
                visibility = 0
            else:
                # Our new format is "X.XX miles" or "X.XX miles (original)"
                try:
                    # Extract just the numeric part (before "miles")
                    vis_parts = visibility_str.split()[0]
                    visibility = float(vis_parts)
                except (ValueError, IndexError):
                    logging.error(f"Error parsing visibility: {visibility_str}")
                    visibility = 0
                
            clouds = weather_data.get('clouds', 'N/A')
            if clouds == 'N/A' or not 'at' in clouds:
                ceiling = 99999  # Unknown ceiling treated as very high
            else:
                try:
                    cloud_parts = clouds.split()
                    at_index = cloud_parts.index('at')
                    ceiling = int(cloud_parts[at_index + 1])
                except (ValueError, IndexError):
                    ceiling = 99999
        except Exception as e:
            logging.error(f"Error parsing weather data for {station}: {str(e)}")
            return
        
        # Check each approach for this station
        if station not in self.approach_status:
            self.approach_status[station] = {}
        
        for approach_id, approach_info in self.approach_minimums.items():
            if self.approach_station[approach_id] != station:
                continue
                
            # Initialize if not exists
            if approach_id not in self.approach_status[station]:
                self.approach_status[station][approach_id] = {
                    'name': approach_info['name'],
                    'status': 'UNKNOWN',
                    'below_minimums_since': None,
                    'above_minimums_since': None,
                    'duration_below': None,
                    'last_updated': now.strftime(config.TIME_FORMAT),
                    'data_reliability': 'RELIABLE' if not unreliable else 'UNRELIABLE'
                }
            
            # Check if approach is above or below minimums
            min_visibility = approach_info['visibility']
            min_ceiling = approach_info['ceiling']
            
            if visibility < min_visibility or ceiling < min_ceiling:
                new_status = 'BELOW_MINIMUMS'
            else:
                new_status = 'ABOVE_MINIMUMS'
            
            previous_status = self.approach_status[station][approach_id]['status']
            last_updated = self._parse_alaska_time(self.approach_status[station][approach_id]['last_updated'])
            
            # If status changed, update the tracking information
            if new_status != previous_status and previous_status != 'UNKNOWN':
                if new_status == 'BELOW_MINIMUMS':
                    self.approach_status[station][approach_id]['below_minimums_since'] = now.strftime(config.TIME_FORMAT)
                    self.approach_status[station][approach_id]['above_minimums_since'] = None
                else:
                    self.approach_status[station][approach_id]['above_minimums_since'] = now.strftime(config.TIME_FORMAT)
                    self.approach_status[station][approach_id]['below_minimums_since'] = None
            elif previous_status == 'UNKNOWN':
                if new_status == 'BELOW_MINIMUMS':
                    self.approach_status[station][approach_id]['below_minimums_since'] = now.strftime(config.TIME_FORMAT)
                else:
                    self.approach_status[station][approach_id]['above_minimums_since'] = now.strftime(config.TIME_FORMAT)
            
            # Calculate duration below and above minimums if applicable
            if new_status == 'BELOW_MINIMUMS' and self.approach_status[station][approach_id]['below_minimums_since']:
                below_since = self._parse_alaska_time(self.approach_status[station][approach_id]['below_minimums_since'])
                duration_seconds = (now - below_since).total_seconds()
                self.approach_status[station][approach_id]['duration_below'] = self._format_duration(duration_seconds)
                self.approach_status[station][approach_id]['duration_above'] = None
            elif new_status == 'ABOVE_MINIMUMS' and self.approach_status[station][approach_id]['above_minimums_since']:
                above_since = self._parse_alaska_time(self.approach_status[station][approach_id]['above_minimums_since'])
                duration_seconds = (now - above_since).total_seconds()
                self.approach_status[station][approach_id]['duration_above'] = self._format_duration(duration_seconds)
                self.approach_status[station][approach_id]['duration_below'] = None
            else:
                self.approach_status[station][approach_id]['duration_below'] = None
                self.approach_status[station][approach_id]['duration_above'] = None
            
            # Update status and last updated time
            self.approach_status[station][approach_id]['status'] = new_status
            self.approach_status[station][approach_id]['last_updated'] = now.strftime(config.TIME_FORMAT)
            
            # Set data reliability indicator
            self.approach_status[station][approach_id]['data_reliability'] = 'RELIABLE' if not unreliable else 'UNRELIABLE'
            
            # Add runway wind favorability data if available in weather data
            if 'runway_analysis' in weather_data and weather_data['runway_analysis']:
                runway_analysis = weather_data['runway_analysis']
                
                # Extract runway number from approach name for matching
                approach_runway = None
                if "RWY" in approach_info['name']:
                    # Extract runway number (e.g., "ILS RWY 36" -> "36")
                    approach_runway = approach_info['name'].split("RWY")[-1].strip()
                
                # Determine if this approach aligns with the favored runway
                favored_runway = runway_analysis.get('favored_runway', '').replace('Runway ', '')
                
                if approach_runway == favored_runway:
                    wind_favor = "YES"
                elif approach_runway:
                    wind_favor = "NO"
                else:
                    wind_favor = "UNKNOWN"
                
                # Get crosswind component for this runway if available
                crosswind_value = "N/A"
                crosswind_status = "NORMAL"
                if approach_runway and 'components' in runway_analysis:
                    if approach_runway in runway_analysis['components']:
                        crosswind = runway_analysis['components'][approach_runway]['crosswind']
                        crosswind_value = f"{crosswind} kt"
                        
                        # Determine crosswind status
                        if crosswind > 25:
                            crosswind_status = "HIGH"
                        elif crosswind > 15:
                            crosswind_status = "MODERATE"
                        else:
                            crosswind_status = "LIGHT"
                
                # Add wind information to approach status
                self.approach_status[station][approach_id]['wind_analysis'] = {
                    'favored': wind_favor,
                    'crosswind': crosswind_value,
                    'crosswind_status': crosswind_status,
                    'warning': runway_analysis.get('crosswind_warning', False),
                    'details': runway_analysis.get('details', 'No wind data available')
                }
            
            # Add current conditions for reference with + or - indicators showing the difference
            visibility_diff = round(visibility - min_visibility, 1)
            visibility_indicator = f"+{visibility_diff}" if visibility_diff >= 0 else f"{visibility_diff}"
            
            ceiling_diff = ceiling - min_ceiling
            ceiling_indicator = f"+{ceiling_diff}" if ceiling_diff >= 0 else f"{ceiling_diff}"
            
            self.approach_status[station][approach_id]['current_conditions'] = {
                'visibility': f"{visibility_str} ({visibility_indicator})",
                'ceiling': f"{ceiling} ({ceiling_indicator})",
                'required_visibility': f"{min_visibility} miles",
                'required_ceiling': f"{min_ceiling} feet"
            }
            
            # Add additional note if sensors are unreliable for any station
            if unreliable:
                # Add specific details about what's unreliable
                reason = []
                if weather_data.get('is_stale', False):
                    reason.append("data is stale (possibly due to internet outage)")
                
                # Check which critical sensors are offline (only visibility and clouds affect approach minimums)
                offline_sensors = []
                if sensor_status:
                    for sensor_name, display_name in [
                        ('visibility', 'visibility'), 
                        ('clouds', 'ceiling')
                    ]:
                        if sensor_status.get(sensor_name, {}).get('status') == 'offline':
                            offline_sensors.append(display_name)
                
                if offline_sensors:
                    reason.append(f"{', '.join(offline_sensors)} sensor(s) offline")
                
                self.approach_status[station][approach_id]['note'] = f"Warning: Some weather sensors at {station} are currently unreliable or offline ({', '.join(reason)}). Approach status data may be inaccurate."
    
    def update(self):
        """Main method to update approach status based on current weather data."""
        try:
            # Process every configured airport
            for station in config.STATIONS:
                weather_file = os.path.join(self.base_dir, station, 'weather.json')
                sensor_status_file = os.path.join(self.base_dir, station, 'sensor_status.json')
                
                if not os.path.exists(weather_file):
                    logging.warning(f"Weather file for {station} not found")
                    continue
                
                # Check sensor status
                sensors_operational = True
                if os.path.exists(sensor_status_file):
                    try:
                        with open(sensor_status_file, 'r') as f:
                            sensor_status = json.load(f)
                            
                        # Only check critical sensors for approach calculations (visibility and clouds)
                        # Temperature being offline doesn't impact approach minimums
                        offline_sensors_list = []
                        for sensor_name in ['visibility', 'clouds']:
                            if sensor_status.get(sensor_name, {}).get('status') == 'offline':
                                offline_sensors_list.append(sensor_name)
                                
                        if offline_sensors_list:
                            sensors_operational = False
                            logging.warning(f"Critical sensors offline for {station}: {', '.join(offline_sensors_list)}, approach status may be unreliable")
                            
                        # Also check if data is stale (which can happen if internet is down)
                        with open(weather_file, 'r') as f:
                            weather_data = json.load(f)
                            
                        if weather_data.get('is_stale', False):
                            sensors_operational = False
                            logging.warning(f"Weather data for {station} is stale, approach status may be unreliable")
                    except Exception as e:
                        logging.error(f"Error reading sensor status for {station}: {str(e)}")
                
                try:
                    with open(weather_file, 'r') as f:
                        weather_data = json.load(f)
                    
                    # Always update category duration tracking
                    self._update_category_duration(station, weather_data)
                    
                    # Always update all approaches with the same weather data
                    # Track whether this is unreliable data
                    unreliable = not sensors_operational
                    
                    # Update approach status for all approaches, marking as unreliable if needed
                    self._update_approach_status(station, weather_data, unreliable=unreliable, sensor_status=sensor_status)
                    
                    if unreliable:
                        logging.info(f"Updated approach status for {station} (unreliable due to sensor status)")
                    else:
                        logging.info(f"Updated approach status for {station}")
                        
                except Exception as e:
                    logging.error(f"Error processing weather data for {station}: {str(e)}")
            
            # Save updated status
            self._save_status()
            
        except Exception as e:
            logging.error(f"Error in approach status update: {str(e)}")
    
    def run(self):
        """Run the approach status service in a loop."""
        logging.info("Starting approach status service")
        
        # Initialize status if not already done
        if not self.approach_status or not self.category_status:
            self._initialize_new_status()
            self._save_status()
        
        # Main service loop
        while True:
            try:
                self.update()
                time.sleep(60)  # Update every minute
            except Exception as e:
                logging.error(f"Error in approach status service: {str(e)}")
                time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    service = ApproachStatus()
    service.run()