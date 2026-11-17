# CHANGELOG

## November 2026

### November 16, 2026
- Airports, runways, approach minimums, time zone and time label moved out of the code into `airports.json`, so the service can follow any US airport
- TAF collection restored; decoder now reads gusts, fractional visibility, all weather codes, vertical visibility and TEMPO lines; `taf.json` gains `raw_text` and `wind.gust`
- Trend service repaired: it reads the observation history in `metar_history.db` instead of keeping its own file, takes its minimums from `airports.json` (the lowest approach) and writes `trend_status.json` again
- Every JSON file is written atomically (temp file, then rename)
- `run_services.py` restarts the container if one of the services stops
- Health check, User-Agent, pinned requirements, MIT licence, new README
- Favoured runway is chosen among all runway ends, not only the first two

## May 2025

### May 1, 2025
- Fixed trend_status.json output format for frontend compatibility
- Fixed approach_status.json dependency
- Updated trend terminology to use "improving"/"worsening"/"stable" format (was using "upward"/"downward"/"stable")
- Changed duration format to follow "Xh Ym" pattern for better readability
- Added fallback mechanism when no historical data is available
- Added minimums section to trend_status.json output
- Updated README with latest changes and format examples

## April 2025

### April 20, 2025
- Updated PAPB_RNAV_29 approach visibility minimums from 1.0 miles to 2.5 miles
- Fixed trend_status_service to work without pytz dependency (now using zoneinfo)
- Fixed format_duration function for improved human-readable output
- Set up automatic trend data collection every 5 minutes
- Fixed ceiling height extraction function to handle more cloud layer formats

### April 15, 2025
- Added robust handling of None values in trend analysis
- Updated trend analysis to handle missing or incomplete data points
- Added error handling for malformed weather history data
- Fixed duration calculation to properly include times with timezone info