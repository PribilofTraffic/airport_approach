#!/usr/bin/env python3
# TAF Decoder Service for Pribilof Traffic
from datetime import datetime, timedelta
import requests
import json
import os
import time
import logging
import pytz

from fileio import write_json

class TafDecoderService:
    def __init__(self):
        self.taf_url = "https://aviationweather.gov/api/data/taf?ids="
        self.alaska_tz = pytz.timezone("America/Anchorage")
        self.base_dir = os.getenv('WEATHER_BASE_DIR', '/app/data')
        # How the service identifies itself to aviationweather.gov, how long
        # it waits for an answer, how often it tries, and how often it asks.
        # A TAF is issued about every six hours (amendments in between), so
        # asking every half hour is plenty.
        self.user_agent = os.getenv('USER_AGENT', 'approach-service-collector/1.1')
        self.fetch_timeout = int(os.getenv('FETCH_TIMEOUT', '10'))
        self.max_retries = int(os.getenv('TAF_RETRIES', '3'))
        self.retry_delay = int(os.getenv('TAF_RETRY_DELAY', '20'))
        self.update_interval = int(os.getenv('TAF_INTERVAL', '1800'))
        self.stations = {
            'PASN': {'active': True, 'name': 'St. Paul Island'},
            'PAPB': {'active': False, 'name': 'St. George Island', 'status': 'Station under repair'}
        }

    # Weather groups, e.g. "-SNRA" = light snow and rain, "FZFG" = freezing fog
    WX_DESCRIPTORS = {'MI': 'shallow', 'PR': 'partial', 'BC': 'patches of',
                      'DR': 'drifting', 'BL': 'blowing', 'FZ': 'freezing'}
    WX_PHENOMENA = {'DZ': 'drizzle', 'RA': 'rain', 'SN': 'snow', 'SG': 'snow grains',
                    'IC': 'ice crystals', 'PL': 'ice pellets', 'GR': 'hail',
                    'GS': 'small hail', 'UP': 'unknown precipitation', 'BR': 'mist',
                    'FG': 'fog', 'FU': 'smoke', 'VA': 'volcanic ash', 'DU': 'dust',
                    'SA': 'sand', 'HZ': 'haze', 'PY': 'spray', 'PO': 'dust whirls',
                    'SQ': 'squalls', 'FC': 'funnel cloud', 'SS': 'sandstorm',
                    'DS': 'duststorm'}

    def _decode_weather(self, token: str):
        """Turn one TAF weather group into words, or return None if the
        word is not a weather group (so the caller moves on to clouds)."""
        rest = token
        intensity = ''
        vicinity = False
        if rest.startswith('+'):
            intensity, rest = 'heavy ', rest[1:]
        elif rest.startswith('-'):
            intensity, rest = 'light ', rest[1:]
        elif rest.startswith('VC'):
            vicinity, rest = True, rest[2:]
        if not rest or len(rest) % 2 or not rest.isalpha():
            return None
        codes = [rest[i:i + 2] for i in range(0, len(rest), 2)]
        known = set(self.WX_DESCRIPTORS) | set(self.WX_PHENOMENA) | {'SH', 'TS'}
        if any(code not in known for code in codes):
            return None
        prefix = ' '.join(self.WX_DESCRIPTORS[c] for c in codes if c in self.WX_DESCRIPTORS)
        what = ' and '.join(self.WX_PHENOMENA[c] for c in codes if c in self.WX_PHENOMENA)
        text = ' '.join(x for x in (prefix, what) if x)
        if 'SH' in codes:
            text = f"{text} showers" if text else "showers"
        if 'TS' in codes:
            text = f"thunderstorm with {text}" if text else "thunderstorm"
        text = intensity + text
        if vicinity:
            text += " in Vicinity"
        return text[0].upper() + text[1:]

    @staticmethod
    def _format_miles(value: float) -> str:
        """1.5 -> "1.5", 0.75 -> "0.75", 2.0 -> "2"."""
        return f"{value:g}"

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

            # aviationweather.gov now starts the text with "TAF" (and "AMD"
            # or "COR" for an amended or corrected forecast). The rest of
            # this decoder expects the text to start with the station id.
            taf_string = taf_string.strip()
            words = taf_string.split(None, 3)
            while words and words[0] in ('TAF', 'AMD', 'COR'):
                taf_string = taf_string[len(words[0]):].lstrip(' ')
                words = taf_string.split(None, 3)

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
                if len(parts) < 3:
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
                    wind_gust = None
                    if wind.endswith('KT'):
                        wind_dir = wind[:3]
                        wind_speed = int(wind[3:5])
                        # Gust, when the group carries one (14025G35KT)
                        if 'G' in wind[5:]:
                            gust_digits = ''.join(c for c in wind.split('G', 1)[1] if c.isdigit())
                            wind_gust = int(gust_digits) if gust_digits else None
                        wind_text = f"{wind_dir}° at {wind_speed} knots" + (
                            f", gusting {wind_gust}" if wind_gust else "")
                    else:
                        # A TEMPO line often gives no wind: it only lists
                        # what changes. Step back so this word is read as
                        # the visibility.
                        wind_dir = wind_speed = None
                        wind_text = "No change forecast"
                        start_idx -= 1

                    # Visibility. "1 1/2SM" arrives as two words ("1", "1/2SM")
                    # and "3/4SM" as a bare fraction.
                    vis = parts[start_idx + 1]
                    cloud_idx = start_idx + 2
                    if (vis.isdigit() and len(parts) > cloud_idx
                            and '/' in parts[cloud_idx] and parts[cloud_idx].endswith('SM')):
                        num, denom = parts[cloud_idx].replace("SM", "").split('/')
                        vis_value = self._format_miles(float(vis) + float(num) / float(denom))
                        cloud_idx += 1
                    elif vis == "P6SM":
                        vis_value = "6+"
                    elif '/' in vis:
                        num, denom = vis.replace("SM", "").split('/')
                        vis_value = self._format_miles(float(num) / float(denom))
                    else:
                        vis_value = vis.replace("SM", "")
                    
                    weather = []
                    while len(parts) > cloud_idx:
                        condition = self._decode_weather(parts[cloud_idx])
                        if not condition:
                            break
                        weather.append(condition)
                        cloud_idx += 1
                    
                    clouds = []
                    while cloud_idx < len(parts) and (
                            parts[cloud_idx][:3] in ["SKC", "CLR", "FEW", "SCT", "BKN", "OVC"]
                            or parts[cloud_idx][:2] == "VV"):
                        cloud = parts[cloud_idx]
                        if cloud[:2] == "VV":
                            # Sky hidden (fog, snow): vertical visibility instead of a cloud base
                            height = int(cloud[2:]) * 100 if cloud[2:].isdigit() else None
                            clouds.append({
                                "type": "VV",
                                "height": height,
                                "text": f"Vertical visibility {height} feet" if height is not None else "Sky obscured"
                            })
                            cloud_idx += 1
                            continue
                        cloud_type = cloud[:3]
                        height_digits = cloud[3:6]
                        cloud_height = int(height_digits) * 100 if height_digits.isdigit() else None
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
                        "gust": wind_gust,
                        "text": wind_text
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
            taf_text = self._fetch_taf_text(station)
            if not taf_text:
                raise Exception("Empty TAF response")
                
            taf_data = self.decode_taf(taf_text, station)
            if not taf_data:
                raise Exception("Failed to decode TAF")

            # The TAF exactly as issued, for pages that show the raw text.
            taf_data["raw_text"] = taf_text
            return taf_data
            
        except Exception as e:
            logging.error(f"Error processing TAF for {station}: {str(e)}")
            return {
                "station": station,
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(self.alaska_tz).strftime("%Y-%m-%d %I:%M %p AKST")
            }

    def _fetch_taf_text(self, station: str) -> str:
        """GET the raw TAF, retrying network errors and server errors."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(
                    f"{self.taf_url}{station.lower()}",
                    timeout=self.fetch_timeout,
                    headers={'User-Agent': self.user_agent})
                if response.status_code == 200:
                    return response.text.strip()
                last_error = Exception(f"TAF request failed: {response.status_code}")
                if response.status_code < 500:
                    break  # a 4xx: asking again will not change it
            except requests.RequestException as e:
                last_error = e
            if attempt < self.max_retries:
                time.sleep(self.retry_delay)
        raise last_error

    def _current_taf_still_valid(self, station: str) -> bool:
        """True if taf.json already holds a decoded TAF that has not expired."""
        try:
            with open(f"{self.base_dir}/{station}/taf.json") as f:
                current = json.load(f)
            valid_to = datetime.strptime(current["valid_to_utc"], "%Y-%m-%d %H:%MZ")
            return valid_to.replace(tzinfo=pytz.utc) > datetime.now(pytz.utc)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def save_taf_data(self, station: str, data: dict) -> None:
        """Save TAF data to JSON file."""
        try:
            output_file = f"{self.base_dir}/{station}/taf.json"
            if data.get("status") == "error" and self._current_taf_still_valid(station):
                # One failed fetch should not wipe a forecast that is still
                # in force. The error replaces it only once it has expired.
                logging.warning(f"Keeping the current TAF for {station}; it is still valid")
                return
            write_json(output_file, data, indent=2)
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
            
            time.sleep(self.update_interval)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    service = TafDecoderService()
    service.run()
