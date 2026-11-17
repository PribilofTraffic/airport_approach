#!/usr/bin/env python3
"""
Airport configuration shared by the approach services.

Everything that is specific to one place lives in airports.json: which
airports to follow, their runways and approach minimums, whether a TAF is
issued for them, and the local time zone. The services read it through this
module, so following different airports means editing that one file.

Settings (environment):
    AIRPORTS_FILE  path of the config file (default /app/config/airports.json)
    TZ             time zone used when the file does not name one
"""

import json
import os

AIRPORTS_FILE = os.getenv('AIRPORTS_FILE', '/app/config/airports.json')

with open(AIRPORTS_FILE) as f:
    _config = json.load(f)

AIRPORTS = _config['airports']

# Airport ids in the order they are listed, e.g. ['PASN', 'PAPB'].
STATIONS = [a['id'] for a in AIRPORTS]

# Local time zone (an IANA name such as America/Anchorage).
TIMEZONE = _config.get('timezone') or os.getenv('TZ') or 'America/Anchorage'

# The label printed after every local time. It is fixed text: the services
# read their own time stamps back and expect the same label all year.
TIME_LABEL = _config.get('time_label', 'AKST')
_label = TIME_LABEL.replace('%', '%%')
TIME_FORMAT = f"%Y-%m-%d %I:%M %p {_label}"   # 2026-11-16 07:36 PM AKST
CLOCK_FORMAT = f"%I:%M %p {_label}"           # 07:36 PM AKST

# Approach minimums: approach id -> {name, visibility, ceiling, category, type},
# and which airport each approach belongs to.
APPROACH_MINIMUMS = {}
APPROACH_STATION = {}
for _airport in AIRPORTS:
    for _approach_id, _info in _airport.get('approaches', {}).items():
        APPROACH_MINIMUMS[_approach_id] = _info
        APPROACH_STATION[_approach_id] = _airport['id']

# Runway headings for the wind analysis: airport id -> {runway: heading,
# 'maintenance_issues': bool}. Airports without runways listed are skipped.
RUNWAY_CONFIGS = {
    a['id']: dict(a['runways'], maintenance_issues=bool(a.get('maintenance_issues', False)))
    for a in AIRPORTS if a.get('runways')
}

# TAF: airport id -> {'active': bool, 'name': str, 'status': note shown when
# no TAF is collected for the airport}.
TAF_STATIONS = {
    a['id']: {'active': bool(a.get('taf', False)),
              'name': a.get('name', a['id']),
              'status': a.get('taf_note', 'No TAF is issued for this airport')}
    for a in AIRPORTS
}
