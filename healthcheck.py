#!/usr/bin/env python3
"""
Container health check for the approach services.

Healthy means the loops are alive: for every station, weather.json and
approach_status.json were rewritten recently. Both are rewritten on every
cycle even when the weather source is down (the last reading is kept and
marked stale), so an outage at the source does not make the container
unhealthy; restarting it would not help.

Settings (environment): WEATHER_BASE_DIR, HEALTH_WEATHER_MAX_AGE (seconds,
default 2700 = three 15-minute cycles), HEALTH_STATUS_MAX_AGE (default 300).
Exit code 0 = healthy, 1 = unhealthy.
"""

import os
import sys
import time

BASE_DIR = os.getenv('WEATHER_BASE_DIR', '/app/data')
LIMITS = {
    'weather.json': int(os.getenv('HEALTH_WEATHER_MAX_AGE', '2700')),
    'approach_status.json': int(os.getenv('HEALTH_STATUS_MAX_AGE', '300')),
}
STATIONS = ('PASN', 'PAPB')

problems = []
for station in STATIONS:
    for name, max_age in LIMITS.items():
        path = os.path.join(BASE_DIR, station, name)
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError as e:
            problems.append(f"{station}/{name}: {e}")
            continue
        if age > max_age:
            problems.append(f"{station}/{name} is {int(age)}s old (limit {max_age}s)")

if problems:
    print("unhealthy: " + "; ".join(problems))
    sys.exit(1)
print(f"healthy: weather and approach status fresh for {len(STATIONS)} stations")
