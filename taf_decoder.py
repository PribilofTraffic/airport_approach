#!/usr/bin/env python3
# TAF Decoder Service for Pribilof Traffic
from datetime import datetime, timedelta
import requests
import json
import os
import time
import logging
import pytz

class TafDecoderService:
    def __init__(self):
        self.taf_url = "https://aviationweather.gov/api/data/taf?ids="
        self.alaska_tz = pytz.timezone("America/Anchorage")
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        self.max_retries = 3
        self.retry_delay = 60
        self.stations = {
            'PASN': {'active': True, 'name': 'St. Paul Island'},
            'PAPB': {'active': False, 'name': 'St. George Island', 'status': 'Station under repair'}
        }

    def _normalize_time(self, day: str, hour: str, minute: str = "00") -> datetime:
        """Normalize DDHHMM time (handles 24:00) to a UTC datetime."""
        try:
            day = int(day)
            hour = int(hour)
            minute = int(minute)
            now = datetime.now(pytz.utc)
            
            # Handle month/year based on current date
            if day > now.day:
                month = now.month - 1 if now.month > 1 else 12
                year = now.year if now.month > 1 else now.year - 1
            else:
                month = now.month
                year = now.year
                
            # Create base datetime
            dt = datetime(year, month, day, tzinfo=pytz.utc)
            
            # Handle special case for hour 24
            if hour == 24:
                dt = dt + timedelta(days=1)
                hour = 0
            
            # Add hours and minutes directly to the datetime
            # This properly handles the case where hour=0 (00:00Z)
            dt = dt.replace(hour=hour, minute=minute)
            
            return dt
            
        except Exception as e:
            logging.error(f"Error normalizing time {day}{hour:02d}{minute:02d}: {str(e)}")
            return datetime.now(pytz.utc)

    def _convert_to_alaska_time(self, time_str: str) -> str:
        """Convert UTC time string (DDHHMM) to Alaska time."""
        try:
            day = time_str[:2]
            hour = time_str[2:4]
            minute = time_str[4:6] if len(time_str) > 4 else "00"
            dt = self._normalize_time(day, hour, minute)
            ak_time = dt.astimezone(self.alaska_tz)
            return ak_time.strftime("%Y-%m-%d %I:%M %p AKST")
        except Exception as e:
            logging.error(f"Error converting time {time_str}: {str(e)}")
            return datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")

    def decode_taf(self, taf_string: str, station: str) -> dict:
        """Decode TAF string into structured data."""
        try:
            # Log the raw TAF string for debugging
            logging.debug(f"Processing TAF for {station}: {taf_string}")
            
            if not taf_string or not taf_string.strip():
                logging.warning(f"No TAF data available for {station}")
                return None

            lines = taf_string.strip().split('\n')
            if not lines or not lines[0].strip():
                logging.warning(f"Empty TAF data for {station}")
                return None

            header = lines[0].strip().split()
            if len(header) < 3:
                logging.warning(f"Invalid TAF header for {station}: {lines[0]}")
                return None

            # Parse issuance time
            issue_time_utc = header[1].rstrip('Z')
            if not issue_time_utc.isdigit() or len(issue_time_utc) < 6:
                logging.warning(f"Invalid issue time format for {station}: {issue_time_utc}")
                return None

            issue_dt_utc = self._normalize_time(issue_time_utc[:2], issue_time_utc[2:4], issue_time_utc[4:6])
            issue_dt_akst = issue_dt_utc.astimezone(self.alaska_tz)

            # Parse period (e.g., 2324/2424)
            period = header[2]
            if len(period) < 9 or period[4] != '/':
                logging.warning(f"Invalid period format for {station}: {period}")
                return None

            start_utc = self._normalize_time(period[:2], period[2:4])
            end_utc = self._normalize_time(period[5:7], period[7:9])
            start_akst = start_utc.astimezone(self.alaska_tz)
            end_akst = end_utc.astimezone(self.alaska_tz)

            forecast_periods = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                parts = line.split()
                if len(parts) < 4:
                    continue

                if line.startswith(station):
                    time_period = "Initial Period"
                    start_idx = 3
                    start_time_utc = start_utc
                elif line.startswith('FM'):
                    if len(parts[0]) < 8:
                        continue
                    utc_time = parts[0][2:]  # Remove 'FM'
                    start_time_utc = self._normalize_time(utc_time[:2], utc_time[2:4], utc_time[4:6])
                    start_idx = 1
                    time_period = f"From {start_time_utc.astimezone(self.alaska_tz).strftime('%I:%M %p')} AKST"
                elif line.startswith('TEMPO'):
                    tempo_period = parts[1]
                    if len(tempo_period) < 9 or tempo_period[4] != '/':
                        continue
                    start_time = self._normalize_time(tempo_period[:2], tempo_period[2:4])
                    end_time = self._normalize_time(tempo_period[5:7], tempo_period[7:9])
                    start_idx = 2
                    time_period = f"Temporarily between {start_time.astimezone(self.alaska_tz).strftime('%I:%M %p')} and {end_time.astimezone(self.alaska_tz).strftime('%I:%M %p')} AKST"
                else:
                    continue

                try:
                    wind = parts[start_idx]
                    if len(wind) < 5:
                        continue
                    wind_dir = wind[:3]
                    wind_speed = int(wind[3:5])
                    
                    vis = parts[start_idx + 1]
                    if vis == "P6SM":
                        vis_value = "6+"
                    elif ' ' in vis:
                        whole, frac = vis.replace("SM", "").split()
                        num, denom = frac.split('/')
                        vis_value = str(float(whole) + float(num)/float(denom))
                    else:
                        vis_value = vis.replace("SM", "")
                    
                    weather = []
                    cloud_idx = start_idx + 2
                    while len(parts) > cloud_idx and parts[cloud_idx] in ["BR", "VCSH", "-SN", "SN", "+SN", "-SHSN"]:
                        condition = {
                            "BR": "Mist", "VCSH": "Showers in Vicinity", "-SN": "Light snow", "SN": "Snow", "+SN": "Heavy snow", "-SHSN": "Light snow showers"
                        }.get(parts[cloud_idx])
                        if condition:
                            weather.append(condition)
                        cloud_idx += 1
                    
                    clouds = []
                    while cloud_idx < len(parts) and parts[cloud_idx][:3] in ["SKC", "CLR", "FEW", "SCT", "BKN", "OVC"]:
                        cloud = parts[cloud_idx]
                        cloud_type = cloud[:3]
                        cloud_height = int(cloud[3:]) * 100 if len(cloud) > 3 else None
                        clouds.append({
                            "type": cloud_type,
                            "height": cloud_height,
                            "text": f"{cloud_type} at {cloud_height} feet" if cloud_height else cloud_type
                        })
                        cloud_idx += 1
                except (ValueError, IndexError) as e:
                    logging.warning(f"Error parsing TAF line for {station}: {str(e)}")
                    continue
                
                period_data = {
                    "period": time_period,
                    "time_utc": start_time_utc.strftime("%H:%MZ"),
                    "time_akst": start_time_utc.astimezone(self.alaska_tz).strftime("%I:%M %p AKST"),
                    "wind": {
                        "direction": wind_dir,
                        "speed": wind_speed,
                        "text": f"{wind_dir}° at {wind_speed} knots"
                    },
                    "visibility": {
                        "distance": vis_value,
                        "text": f"{vis_value} statute miles"
                    },
                    "weather": weather if weather else None,
                    "clouds": clouds if clouds else None
                }
                forecast_periods.append(period_data)
            
            if not forecast_periods:
                logging.warning(f"No valid forecast periods found in TAF for {station}")
                return None

            return {
                "station": station,
                "issued_utc": issue_dt_utc.strftime("%Y-%m-%d %H:%MZ"),
                "issued_akst": issue_dt_akst.strftime("%Y-%m-%d %I:%M %p AKST"),
                "valid_from_utc": start_utc.strftime("%Y-%m-%d %H:%MZ"),
                "valid_to_utc": end_utc.strftime("%Y-%m-%d %H:%MZ"),
                "valid_from_akst": start_akst.strftime("%Y-%m-%d %I:%M %p AKST"),
                "valid_to_akst": end_akst.strftime("%Y-%m-%d %I:%M %p AKST"),
                "forecast_periods": forecast_periods
            }
            
        except Exception as e:
            logging.error(f"Error decoding TAF for {station}: {str(e)}")
            return None

    def fetch_and_process_taf(self, station: str) -> dict:
        """Fetch and process TAF data for a station."""
        if not self.stations[station]['active']:
            return {
                "station": station,
                "status": "inactive",
                "message": self.stations[station]['status'],
                "timestamp": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
            }
            
        try:
            response = requests.get(f"{self.taf_url}{station.lower()}", timeout=10)
            if response.status_code != 200:
                raise Exception(f"TAF request failed: {response.status_code}")
                
            taf_text = response.text.strip()
            if not taf_text:
                raise Exception("Empty TAF response")
                
            taf_data = self.decode_taf(taf_text, station)
            if not taf_data:
                raise Exception("Failed to decode TAF")
                
            return taf_data
            
        except Exception as e:
            logging.error(f"Error processing TAF for {station}: {str(e)}")
            return {
                "station": station,
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
            }

    def save_taf_data(self, station: str, data: dict) -> None:
        """Save TAF data to JSON file."""
        try:
            output_file = f"{self.base_dir}/{station}/taf.json"
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logging.error(f"Error saving TAF data for {station}: {str(e)}")

    def run(self):
        """Main service loop."""
        while True:
            for station in self.stations:
                try:
                    taf_data = self.fetch_and_process_taf(station)
                    self.save_taf_data(station, taf_data)
                    logging.info(f"Updated TAF data for {station}")
                except Exception as e:
                    logging.error(f"Error in TAF service for {station}: {str(e)}")
            
            time.sleep(300)  # Update every 5 minutes

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    service = TafDecoderService()
    service.run()
