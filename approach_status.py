#!/usr/bin/env python3
# Approach Status Service for Pribilof Traffic - Tracks approaches status against current weather
from typing import Dict, Any, List
import json
import os
import time
import logging
import pytz
from datetime import datetime, timedelta

class ApproachStatus:
    def __init__(self):
        """Initialize the approach status service."""
        self.alaska_tz = pytz.timezone("America/Anchorage")
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        
        # Ensure station directories exist
        for station in ['PASN', 'PAPB']:
            os.makedirs(os.path.join(self.base_dir, station), exist_ok=True)
        
        # Define approach minimums for both airports
        # Format: {approach_name: {'visibility': value_in_miles, 'ceiling': value_in_feet, 'category': 'A' or 'ALL'}}
        self.approach_minimums = {
            # PASN (St. Paul Island) approaches
            'PASN_RNAV_36': {
                'name': 'RNAV (GPS) RWY 36',
                'visibility': 1.0,
                'ceiling': 793,
                'category': 'A',
                'type': 'LNAV'
            },
            'PASN_RNAV_18': {
                'name': 'RNAV (GPS) RWY 18',
                'visibility': 1.0,
                'ceiling': 674,
                'category': 'A',
                'type': 'LNAV'
            },
            'PASN_LOC_18': {
                'name': 'LOC/DME BC RWY 18',
                'visibility': 1.0,
                'ceiling': 514,
                'category': 'A',
                'type': 'S-18'
            },
            'PASN_ILS_36': {
                'name': 'ILS RWY 36',
                'visibility': 0.75,
                'ceiling': 200,
                'category': 'A',
                'type': 'ILS'
            },
            
            # PAPB (St. George Island) approaches
            'PAPB_RNAV_11': {
                'name': 'RNAV (GPS) RWY 11',
                'visibility': 1.0,
                'ceiling': 840,  # 840-1, derived from approach plate
                'category': 'A',
                'type': 'LNAV'
            },
            'PAPB_ILS_11': {
                'name': 'ILS RWY 11',
                'visibility': 1.0,
                'ceiling': 428,  # 428-1, derived from approach plate
                'category': 'A',
                'type': 'ILS'
            },
            'PAPB_LOC_11': {
                'name': 'LOC RWY 11',
                'visibility': 1.0,
                'ceiling': 840,  # 840-1, derived from approach plate
                'category': 'A',
                'type': 'LOC'
            },
            'PAPB_RNAV_29': {
                'name': 'RNAV (GPS) RWY 29',
                'visibility': 1.0,
                'ceiling': 980,  # 980-1, derived from approach plate
                'category': 'A',
                'type': 'LNAV'
            }
        }
        
        # Initialize status tracking
        self.approach_status = {}
        self.category_status = {}
        
        # Load any existing status data
        self._load_status()
    
    def _load_status(self):
        """Load existing status data if available."""
        try:
            for station in ['PASN', 'PAPB']:
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
        except Exception as e:
            logging.error(f"Error loading approach status data: {str(e)}")
            # Initialize new data if loading fails
            self._initialize_new_status()
    
    def _initialize_new_status(self):
        """Initialize new status tracking data."""
        self.approach_status = {}
        for station in ['PASN', 'PAPB']:
            self.approach_status[station] = {}
            for approach_id in self.approach_minimums:
                if approach_id.startswith(station):
                    self.approach_status[station][approach_id] = {
                        'name': self.approach_minimums[approach_id]['name'],
                        'status': 'UNKNOWN',
                        'below_minimums_since': None,
                        'above_minimums_since': None,
                        'duration_below': None,
                        'last_updated': datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
                    }
        
        self.category_status = {}
        for station in ['PASN', 'PAPB']:
            self.category_status[station] = {
                'current_category': 'UNKNOWN',
                'since': datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST"),
                'durations': {
                    'VFR': 0,
                    'MVFR': 0,
                    'IFR': 0,
                    'LIFR': 0
                },
                'last_updated': datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
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
                with open(approach_status_file, 'w') as f:
                    json.dump(self.approach_status[station], f, indent=4)
                
                if station in self.category_status:
                    category_status_file = os.path.join(station_dir, 'category_status.json')
                    with open(category_status_file, 'w') as f:
                        json.dump(self.category_status[station], f, indent=4)
            
            logging.info("Saved approach status data for all stations")
        except Exception as e:
            logging.error(f"Error saving approach status data: {str(e)}")
    
    def _parse_alaska_time(self, time_str):
        """Parse Alaska time string to datetime object."""
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %I:%M %p AKST").replace(tzinfo=self.alaska_tz)
        except Exception as e:
            logging.error(f"Error parsing time {time_str}: {str(e)}")
            return datetime.now(self.alaska_tz)
    
    def _update_category_duration(self, station, weather_data):
        """Update flight category duration tracking with monthly breakdown."""
        now = datetime.now(self.alaska_tz)
        current_month_key = now.strftime("%Y-%m")  # Format: YYYY-MM
        
        if station not in self.category_status:
            self.category_status[station] = {
                'current_category': 'UNKNOWN',
                'since': now.strftime("%Y-%m-%d %I:%M %p AKST"),
                'durations': {
                    'VFR': 0,
                    'MVFR': 0,
                    'IFR': 0,
                    'LIFR': 0
                },
                'monthly': {},
                'last_updated': now.strftime("%Y-%m-%d %I:%M %p AKST"),
                'tracking_start': now.strftime("%Y-%m-%d %I:%M %p AKST")
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
        
        # Update duration for the previous category (both total and monthly)
        if previous_category != 'UNKNOWN':
            # Update total durations
            self.category_status[station]['durations'][previous_category] += time_elapsed
            
            # Update monthly durations
            self.category_status[station]['monthly'][current_month_key][previous_category] += time_elapsed
        
        # If category changed, update the 'since' time
        if current_category != previous_category:
            self.category_status[station]['current_category'] = current_category
            self.category_status[station]['since'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
        
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
            'last_updated': now.strftime("%Y-%m-%d %I:%M %p AKST")
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
                visibility = float(visibility_str.split()[0])
                
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
            if not approach_id.startswith(station):
                continue
                
            # Initialize if not exists
            if approach_id not in self.approach_status[station]:
                self.approach_status[station][approach_id] = {
                    'name': approach_info['name'],
                    'status': 'UNKNOWN',
                    'below_minimums_since': None,
                    'above_minimums_since': None,
                    'duration_below': None,
                    'last_updated': now.strftime("%Y-%m-%d %I:%M %p AKST"),
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
                    self.approach_status[station][approach_id]['below_minimums_since'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
                    self.approach_status[station][approach_id]['above_minimums_since'] = None
                else:
                    self.approach_status[station][approach_id]['above_minimums_since'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
                    self.approach_status[station][approach_id]['below_minimums_since'] = None
            elif previous_status == 'UNKNOWN':
                if new_status == 'BELOW_MINIMUMS':
                    self.approach_status[station][approach_id]['below_minimums_since'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
                else:
                    self.approach_status[station][approach_id]['above_minimums_since'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
            
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
            self.approach_status[station][approach_id]['last_updated'] = now.strftime("%Y-%m-%d %I:%M %p AKST")
            
            # Set data reliability indicator
            self.approach_status[station][approach_id]['data_reliability'] = 'RELIABLE' if not unreliable else 'UNRELIABLE'
            
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
                
                # Check which specific sensors are offline
                offline_sensors = []
                if sensor_status and sensor_status.get('visibility', {}).get('status') == 'offline':
                    offline_sensors.append("visibility")
                if sensor_status and sensor_status.get('clouds', {}).get('status') == 'offline':
                    offline_sensors.append("ceiling")
                
                if offline_sensors:
                    reason.append(f"{', '.join(offline_sensors)} sensor(s) offline")
                
                self.approach_status[station][approach_id]['note'] = f"Warning: Some weather sensors at {station} are currently unreliable or offline ({', '.join(reason)}). Approach status data may be inaccurate."
    
    def update(self):
        """Main method to update approach status based on current weather data."""
        try:
            # Process both PASN and PAPB stations
            for station in ['PASN', 'PAPB']:
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
                            
                        # Check if critical sensors are offline or data is stale
                        if sensor_status.get('visibility', {}).get('status') == 'offline' or \
                           sensor_status.get('clouds', {}).get('status') == 'offline':
                            sensors_operational = False
                            logging.warning(f"Critical sensors offline for {station}, approach status may be unreliable")
                            
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