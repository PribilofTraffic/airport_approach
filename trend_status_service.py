#!/usr/bin/env python3
"""
Trend & Duration Add-on Service for Pribilof Traffic
- Non-intrusive: does not modify weather.json or main services
- Appends each new weather observation to weather_history.jsonl (per station)
- Computes 6-hour trends and above/below minimums durations
- Outputs result to trend_status.json (per station), auto-served by data-api
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone
import zoneinfo

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('trend_status_service')

def parse_float(value):
    try:
        return float(str(value).split()[0])
    except Exception:
        return None

def get_minimums(station):
    # These should match your approach_status.py minimums for the lowest approach per station
    minimums = {
        'PASN': {'visibility': 1.0, 'ceiling': 400},  # Example: 1 mile, 400 ft
        'PAPB': {'visibility': 2.5, 'ceiling': 840},  # Updated: 2.5 miles, 840 ft
    }
    return minimums.get(station, {'visibility': 1.0, 'ceiling': 400})


def append_to_history(station_dir, obs):
    history_path = os.path.join(station_dir, 'weather_history.jsonl')
    with open(history_path, 'a') as f:
        f.write(json.dumps(obs) + '\n')

def load_recent_history(station_dir, hours=6):
    history_path = os.path.join(station_dir, 'weather_history.jsonl')
    if not os.path.exists(history_path):
        return []
    alaska_tz = zoneinfo.ZoneInfo('America/Anchorage')
    cutoff = datetime.now(alaska_tz) - timedelta(hours=hours)
    result = []
    with open(history_path, 'r') as f:
        for line in f:
            try:
                obs = json.loads(line)
                obs_time = datetime.strptime(obs['time'], '%Y-%m-%d %I:%M %p AKST')
                # Add timezone info to parsed datetime for proper comparison
                obs_time = obs_time.replace(tzinfo=alaska_tz)
                if obs_time >= cutoff:
                    result.append(obs)
            except Exception as e:
                logger.error(f"Error parsing history entry: {e}")
                continue
    return result

def analyze_trend(values):
    # Filter out None values
    valid_values = [v for v in values if v is not None]
    
    if len(valid_values) < 2:
        return {'trend': 'unknown', 'change': None}
    
    delta = valid_values[-1] - valid_values[0]
    if abs(delta) < 0.01:
        return {'trend': 'stable', 'change': delta}
    
    # For aviation, "improving" means:
    # - Visibility increasing
    # - Ceiling increasing
    # - Wind decreasing
    # So we need to interpret upward/downward differently based on the parameter
    return {'trend': 'upward' if delta > 0 else 'downward', 'change': delta}

def duration_above_below(values, threshold, above=True, times=None):
    # Returns how long (in seconds) the value has been continuously above/below threshold
    if not values:
        return 0
    
    # Filter out None values
    valid_indices = [i for i, v in enumerate(values) if v is not None]
    if not valid_indices:
        return 0
        
    valid_values = [values[i] for i in valid_indices]
    
    # Start with the last valid value
    idx = len(valid_values) - 1
    compare = (lambda v: v > threshold) if above else (lambda v: v < threshold)
    
    while idx >= 0 and compare(valid_values[idx]):
        idx -= 1
        
    if times is None or idx == len(valid_values) - 1:
        return 0
        
    alaska_tz = zoneinfo.ZoneInfo('America/Anchorage')
    
    # Map back to original time indices
    end_time_idx = valid_indices[-1]
    start_time_idx = valid_indices[idx + 1]
    
    end_time = datetime.strptime(times[end_time_idx], '%Y-%m-%d %I:%M %p AKST')
    start_time = datetime.strptime(times[start_time_idx], '%Y-%m-%d %I:%M %p AKST')
    
    return (end_time - start_time).total_seconds()

def format_duration(seconds):
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        return f"{days}d {hours}h"

def extract_ceiling_height(cloud_text):
    """Extract ceiling height from cloud description regardless of cloud type"""
    # Check for common cloud type phrases
    for phrase in ["Overcast at", "Broken clouds at", "Broken at", "BKN", "OVC"]:
        if phrase in cloud_text:
            # Extract the number before 'feet'
            try:
                parts = cloud_text.split(phrase)[1].split('feet')[0].strip()
                # Extract digits from the text
                digits = ''.join(filter(str.isdigit, parts))
                if digits:
                    return int(digits)
            except Exception:
                pass
    return None

def process_station(station_dir, station):
    try:
        # 1. Read current weather.json
        weather_path = os.path.join(station_dir, 'weather.json')
        if not os.path.exists(weather_path):
            logger.warning(f"Weather data not found for {station}")
            return
            
        with open(weather_path) as f:
            obs = json.load(f)
            
        # 2. Append to history
        append_to_history(station_dir, obs)
        
        # 3. Load last 6 hours
        history = load_recent_history(station_dir, hours=6)
        
        # If no history available, use current observation as a starting point
        # This ensures we have at least something to display, even without trend data
        if not history:
            logger.warning(f"No historical data available for {station}, using current observation only")
            history = [obs]  # Use current observation as starting point
            
        # 4. Extract series
        vis_values = [parse_float(o.get('visibility', 0)) for o in history]
        ceil_values = []
        for o in history:
            clouds = o.get('clouds', '')
            ceiling = extract_ceiling_height(clouds)
            ceil_values.append(ceiling)
            
        wind_values = [parse_float(o.get('wind_speed', '0kt').replace('kt','')) for o in history]
        times = [o['time'] for o in history]
        
        # 5. Analyze trends
        vis_trend_raw = analyze_trend([v for v in vis_values if v is not None])
        ceil_trend_raw = analyze_trend([v for v in ceil_values if v is not None])
        wind_trend_raw = analyze_trend([v for v in wind_values if v is not None])
        
        # Convert upward/downward to improving/worsening based on parameter
        vis_trend = vis_trend_raw.copy()
        if vis_trend['trend'] == 'upward':
            vis_trend['trend'] = 'improving'
        elif vis_trend['trend'] == 'downward':
            vis_trend['trend'] = 'worsening'
        
        ceil_trend = ceil_trend_raw.copy()
        if ceil_trend['trend'] == 'upward':
            ceil_trend['trend'] = 'improving'
        elif ceil_trend['trend'] == 'downward':
            ceil_trend['trend'] = 'worsening'
        
        wind_trend = wind_trend_raw.copy()
        if wind_trend['trend'] == 'upward':
            wind_trend['trend'] = 'worsening'  # Higher wind is worse
        elif wind_trend['trend'] == 'downward':
            wind_trend['trend'] = 'improving'  # Lower wind is better
        
        # 6. Durations above/below minimums
        mins = get_minimums(station)
        vis_above = duration_above_below(vis_values, mins['visibility'], above=True, times=times)
        vis_below = duration_above_below(vis_values, mins['visibility'], above=False, times=times)
        ceil_above = duration_above_below(ceil_values, mins['ceiling'], above=True, times=times)
        ceil_below = duration_above_below(ceil_values, mins['ceiling'], above=False, times=times)
        
        # 7. Output
        trend_status = {
            'station': station,
            'timestamp': obs.get('time'),
            'visibility_trend': vis_trend,
            'ceiling_trend': ceil_trend,
            'wind_speed_trend': wind_trend,
            'duration_visibility_above_min': format_duration(vis_above),
            'duration_visibility_below_min': format_duration(vis_below),
            'duration_ceiling_above_min': format_duration(ceil_above),
            'duration_ceiling_below_min': format_duration(ceil_below),
            'summary': {
                'visibility': f"Visibility {vis_trend['trend']}" + (f" ({vis_trend['change']:.2f} mi change in 6hr)" if vis_trend['change'] is not None else ""),
                'ceiling': f"Ceiling {ceil_trend['trend']}" + (f" ({ceil_trend['change']:.0f} ft change in 6hr)" if ceil_trend['change'] is not None else ""),
                'wind': f"Wind {wind_trend['trend']}" + (f" ({wind_trend['change']:.1f} kt change in 6hr)" if wind_trend['change'] is not None else ""),
                'vis_above': f"Above min {format_duration(vis_above)}", 'vis_below': f"Below min {format_duration(vis_below)}",
                'ceil_above': f"Above min {format_duration(ceil_above)}", 'ceil_below': f"Below min {format_duration(ceil_below)}"
            },
            'minimums': mins
        }
        
        # 8. Write trend_status.json
        out_path = os.path.join(station_dir, 'trend_status.json')
        with open(out_path, 'w') as f:
            json.dump(trend_status, f, indent=2)
            
        logger.info(f"Updated trend data for {station}")
    except Exception as e:
        logger.error(f"Error processing station {station}: {e}")

def main():
    # Use environment variable for data directory, with fallback
    base_dir = os.getenv('WEATHER_BASE_DIR', '/home/docker/airport_approach/data')
    logger.info(f"Starting trend status service with data directory: {base_dir}")
    
    # Run continuously
    while True:
        try:
            logger.info("Processing trend data for stations")
            for station in ['PASN', 'PAPB']:
                station_dir = os.path.join(base_dir, station)
                if os.path.exists(os.path.join(station_dir, 'weather.json')):
                    process_station(station_dir, station)
                else:
                    logger.warning(f"Station directory or weather.json not found for {station}")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        
        # Wait before next update - check less frequently since weather updates ~3 times per hour
        time.sleep(900)  # Update every 15 minutes

if __name__ == '__main__':
    main()
