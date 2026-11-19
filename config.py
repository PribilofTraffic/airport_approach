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
from datetime import datetime
import os

AIRPORTS_FILE = os.getenv('AIRPORTS_FILE', '/app/config/airports.json')

with open(AIRPORTS_FILE) as f:
    _config = json.load(f)

AIRPORTS = _config['airports']

# Airport ids in the order they are listed, e.g. ['PASN', 'PAPB'].
STATIONS = [a['id'] for a in AIRPORTS]

# Local time zone (an IANA name such as America/Anchorage).
TIMEZONE = _config.get('timezone') or os.getenv('TZ') or 'America/Anchorage'

# The label printed after every local time ("AKST" or "AKDT" in Alaska).
# "auto" (the default) takes it from the time itself, so it changes with the
# clocks and would stop changing if daylight saving time were abolished: the
# rule lives in the time zone database, not here. Any other text is printed
# as it is, all year.
#
# Until 2026-11 this was the fixed text "AKST", which was wrong for eight
# months of every year: the clock digits followed daylight time and the label
# did not, so a page that trusted the label was an hour out all summer.
TIME_LABEL = _config.get('time_label') or 'auto'
AUTO_LABEL = TIME_LABEL.strip().lower() == 'auto'
_STAMP = "%Y-%m-%d %I:%M %p"    # 2026-11-16 07:36 PM
_CLOCK = "%I:%M %p"             # 07:36 PM


def label(when):
    """The zone label for one moment (an aware datetime)."""
    if not AUTO_LABEL:
        return TIME_LABEL
    return when.strftime('%Z') or 'local'


def stamp(when):
    """'2026-11-16 07:36 PM AKST' for an aware datetime."""
    return f"{when.strftime(_STAMP)} {label(when)}"


def clock(when):
    """'07:36 PM AKST' for an aware datetime."""
    return f"{when.strftime(_CLOCK)} {label(when)}"


def parse_stamp(text, zone):
    """Read a stamp() string back into an aware datetime in `zone` (pytz).

    Whatever label it carries is accepted, so files written before and after
    a clock change, or with an older fixed label, all read back. In the one
    hour a year that happens twice (fall back), the label says which of the
    two it was. Raises ValueError for text that is not a stamp.
    """
    if not isinstance(text, str):
        raise ValueError('not a time stamp')
    head, _, tail = text.strip().rpartition(' ')
    try:
        naive, mark = datetime.strptime(head, _STAMP), tail
    except ValueError:
        naive, mark = datetime.strptime(text.strip(), _STAMP), ''
    for is_dst in (True, False):
        candidate = zone.localize(naive, is_dst=is_dst)
        if candidate.tzname() == mark:
            return candidate
    return zone.localize(naive)


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
