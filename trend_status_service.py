#!/usr/bin/env python3
"""
Trend and duration service.

Answers "which way is the weather heading?" for each airport:
- looks at the last 6 hours of observations,
- says whether visibility, ceiling and wind are improving, worsening or
  stable, and by how much,
- says how long visibility and ceiling have been above or below the
  airport's lowest approach minimums,
- writes the result to trend_status.json in the airport's data folder.

It reads the observations that approach_service.py already stores in
metar_history.db (opened read-only), so it keeps no history file of its own
and has a full 6 hours to work with from the first cycle.

Settings (environment):
    WEATHER_BASE_DIR  data folder (default /app/data)
    TREND_HOURS       how far back to look (default 6)
    TREND_INTERVAL    seconds between updates (default 900)
Airports and their approach minimums come from airports.json (config.py).
"""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import config
from fileio import write_json

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('trend_status_service')

BASE_DIR = os.getenv('WEATHER_BASE_DIR', '/app/data')
TREND_HOURS = float(os.getenv('TREND_HOURS', '6'))
TREND_INTERVAL = int(os.getenv('TREND_INTERVAL', '900'))


def get_minimums(station):
    """The lowest approach minimums for an airport, from airports.json.

    "Lowest" is the approach with the lowest ceiling (then the lowest
    visibility): the last one to remain usable as the weather comes down.
    Returns None for an airport with no approaches listed.
    """
    approaches = [(info['ceiling'], info['visibility'], approach_id, info)
                  for approach_id, info in config.APPROACH_MINIMUMS.items()
                  if config.APPROACH_STATION[approach_id] == station]
    if not approaches:
        return None
    ceiling, visibility, approach_id, info = min(approaches, key=lambda a: a[:2])
    return {'visibility': visibility, 'ceiling': ceiling,
            'approach': approach_id, 'approach_name': info.get('name')}


def load_recent_history(station_dir, hours=TREND_HOURS):
    """Observations from the last `hours` hours, oldest first.

    Each one is {'time': datetime (UTC), 'visibility': miles or None,
    'ceiling': feet or None, 'wind': knots or None}.
    """
    db_path = os.path.join(station_dir, 'metar_history.db')
    if not os.path.exists(db_path):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT observed_at_utc, visibility_sm, ceiling_ft, wind_speed_kt "
            "FROM metar_observations WHERE observed_at_utc >= ? "
            "ORDER BY observed_at_utc", (cutoff,)).fetchall()
    finally:
        conn.close()
    history = []
    for observed_at, visibility, ceiling, wind in rows:
        try:
            when = datetime.fromisoformat(observed_at)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        history.append({'time': when, 'visibility': visibility,
                        'ceiling': ceiling, 'wind': wind})
    return history


def analyze_trend(values):
    """Change between the first and last known value in the window."""
    valid_values = [v for v in values if v is not None]

    if len(valid_values) < 2:
        return {'trend': 'unknown', 'change': None}

    delta = valid_values[-1] - valid_values[0]
    if abs(delta) < 0.01:
        return {'trend': 'stable', 'change': delta}

    # Whether "upward" is good or bad depends on the measurement; the caller
    # turns it into improving / worsening.
    return {'trend': 'upward' if delta > 0 else 'downward', 'change': delta}


def name_trend(raw, higher_is_better):
    """Turn upward/downward into improving/worsening."""
    trend = raw.copy()
    if trend['trend'] == 'upward':
        trend['trend'] = 'improving' if higher_is_better else 'worsening'
    elif trend['trend'] == 'downward':
        trend['trend'] = 'worsening' if higher_is_better else 'improving'
    return trend


def duration_above_below(values, threshold, above=True, times=None):
    """Seconds the value has been continuously at/above (or below) the
    threshold, counting back from the newest observation. 0 if the newest
    observation is on the other side. Limited to the window looked at."""
    if not values or times is None:
        return 0

    known = [(t, v) for t, v in zip(times, values) if v is not None]
    if not known:
        return 0

    compare = (lambda v: v >= threshold) if above else (lambda v: v < threshold)

    idx = len(known) - 1
    while idx >= 0 and compare(known[idx][1]):
        idx -= 1
    if idx == len(known) - 1:
        return 0  # the newest observation is on the other side

    start_time = known[idx + 1][0]
    return (datetime.now(timezone.utc) - start_time).total_seconds()


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


def process_station(station_dir, station):
    try:
        # 1. The current observation gives the time stamp shown with the trend
        weather_path = os.path.join(station_dir, 'weather.json')
        if not os.path.exists(weather_path):
            logger.warning(f"Weather data not found for {station}")
            return
        with open(weather_path) as f:
            obs = json.load(f)

        # 2. The last few hours of observations
        history = load_recent_history(station_dir)
        if not history:
            logger.warning(f"No observations in the last {TREND_HOURS:g} hours for {station}")

        # 3. Series
        times = [o['time'] for o in history]
        vis_values = [o['visibility'] for o in history]
        ceil_values = [o['ceiling'] for o in history]
        wind_values = [o['wind'] for o in history]
        # No ceiling reported while visibility is (clear, few or scattered
        # cloud) means there is no ceiling to be below: count it as above.
        ceil_for_duration = [
            float('inf') if c is None and v is not None else c
            for c, v in zip(ceil_values, vis_values)]

        # 4. Trends
        vis_trend = name_trend(analyze_trend(vis_values), higher_is_better=True)
        ceil_trend = name_trend(analyze_trend(ceil_values), higher_is_better=True)
        wind_trend = name_trend(analyze_trend(wind_values), higher_is_better=False)

        # 5. Durations above/below the lowest approach minimums
        mins = get_minimums(station)
        if mins:
            vis_above = duration_above_below(vis_values, mins['visibility'], above=True, times=times)
            vis_below = duration_above_below(vis_values, mins['visibility'], above=False, times=times)
            ceil_above = duration_above_below(ceil_for_duration, mins['ceiling'], above=True, times=times)
            ceil_below = duration_above_below(ceil_for_duration, mins['ceiling'], above=False, times=times)
        else:
            vis_above = vis_below = ceil_above = ceil_below = 0

        hours_text = f"{TREND_HOURS:g}hr"
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
                'visibility': f"Visibility {vis_trend['trend']}" + (f" ({vis_trend['change']:.2f} mi change in {hours_text})" if vis_trend['change'] is not None else ""),
                'ceiling': f"Ceiling {ceil_trend['trend']}" + (f" ({ceil_trend['change']:.0f} ft change in {hours_text})" if ceil_trend['change'] is not None else ""),
                'wind': f"Wind {wind_trend['trend']}" + (f" ({wind_trend['change']:.1f} kt change in {hours_text})" if wind_trend['change'] is not None else ""),
                'vis_above': f"Above min {format_duration(vis_above)}", 'vis_below': f"Below min {format_duration(vis_below)}",
                'ceil_above': f"Above min {format_duration(ceil_above)}", 'ceil_below': f"Below min {format_duration(ceil_below)}"
            },
            'minimums': mins,
            'observations_used': len(history),
            'window_hours': TREND_HOURS
        }

        write_json(os.path.join(station_dir, 'trend_status.json'), trend_status, indent=2)
        logger.info(f"Updated trend data for {station} from {len(history)} observations")
    except Exception as e:
        logger.error(f"Error processing station {station}: {e}")


def main():
    logger.info(f"Starting trend status service with data directory: {BASE_DIR}")

    while True:
        waiting = False
        try:
            for station in config.STATIONS:
                station_dir = os.path.join(BASE_DIR, station)
                if os.path.exists(os.path.join(station_dir, 'weather.json')):
                    process_station(station_dir, station)
                else:
                    waiting = True
                    logger.info(f"No weather.json yet for {station}; trying again in a minute")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")

        # On a first start the observation service needs a moment to write
        # its first file; do not wait a whole interval for it.
        time.sleep(60 if waiting else TREND_INTERVAL)


if __name__ == '__main__':
    main()
