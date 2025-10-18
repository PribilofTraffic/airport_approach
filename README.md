# Approach Service

Centralized approach plate and weather data collection service for Pribilof Islands airports (PASN and PAPB). This service fetches, processes, and serves METAR and TAF data as well as approach plate status information for St. Paul Island Airport (PASN) and St. George Island Airport (PAPB).

## Features

- Real-time METAR data collection and processing
- TAF (Terminal Aerodrome Forecast) decoding and formatting
- Approach plate status monitoring against current weather conditions
- Runway favorability analysis based on wind conditions
- Crosswind component calculation for each runway
- Flight category duration tracking (VFR, MVFR, IFR, LIFR)
- Approach minimums status tracking
- Centralized data storage in JSON format
- Automatic time zone conversion (UTC to AKST)
- Robust error handling and logging
- Rate-limited API requests

## Supported Stations

- PASN (St. Paul Island Airport)
  - Active for both METAR and TAF
  - Full weather condition decoding
  - Cloud layer interpretation
  - Visibility processing
  - Approach plate status monitoring
  - Flight category duration tracking
  
- PAPB (St. George Island Airport)
  - Operational with intermittent reliability
  - Full weather condition monitoring and decoding
  - Approach plate status monitoring
  - Note: Station experiences periodic outages - status monitoring may be temporarily unavailable

## Data Storage

## Recent Changes

### May 2025 Updates
- Fixed trend_status.json output format and frontend compatibility
- Updated trend terminology to use "improving"/"worsening"/"stable" format
- Changed duration format to follow "Xh Ym" pattern for better readability
- Added fallback mechanism when no historical data is available
- Improved approach_status.json integration with trend analysis
- Fixed dependency between services to ensure proper operation

### April 2025 Updates
- Updated PAPB_RNAV_29 approach visibility minimums from 1.0 miles to 2.5 miles
- Fixed trend_status_service to work without pytz dependency
- Added minimums information to trend_status.json output
- Set up automatic trend data collection every 5 minutes
- Added robust handling of None values in trend analysis
- Updated trend analysis to handle missing or incomplete data points

All data is stored in a centralized location following the pattern:
```
/home/docker/airport_approach/data/{station}/
  ├── weather.json           # Combined METAR data with runway wind analysis
  ├── taf.json               # Decoded TAF data
  ├── sensor_status.json     # Sensor status information
  ├── approach_status.json   # Status of approaches including runway favorability
  ├── category_status.json   # Flight category durations and current status
  ├── trend_status.json      # 6-hour trend and above/below minimums analysis (add-on)
  └── weather_history.jsonl  # Historical weather observations for trending
```

Note: When deploying in a Docker container, these files are mapped to `/app/data/{station}/` as defined in the Docker run command.

## Trend & Duration Analysis (API Add-on)

A dedicated trend and duration analysis service runs inside the Docker container and outputs `trend_status.json` for each station. This file is auto-served by the data-api and is safe to use for website integration.

**Note:**
- Weather observations for both PASN and PAPB are typically updated only once or twice per hour (not live/minute-by-minute).
- Trend and duration calculations are based on the available data points in the last 6 hours. Results reflect the actual reporting frequency and may be less granular during periods of infrequent updates.
- **RECENT CHANGE (April 2025)**: The minimums for PAPB_RNAV_29 approach have been updated from 1.0 miles to 2.5 miles visibility.

**Purpose:**
- Tracks trends (improving/worsening/stable) in visibility, ceiling, and wind over the last 6 hours
- Reports net change for each metric
- Calculates how long visibility/ceiling have been above or below minimums
- Provides human-readable summary strings for easy display
- Includes current minimum values in output for reference
- Works gracefully even with limited or no historical data

**API Endpoint Example:**
```
/api/approach/PASN/trend_status.json
/api/approach/PAPB/trend_status.json
```

**Sample Output:**
```json
{
  "station": "PASN",
  "timestamp": "2025-04-27 10:53 PM AKST",
  "visibility_trend": {"trend": "improving", "change": 0.5},
  "ceiling_trend": {"trend": "stable", "change": 0},
  "wind_speed_trend": {"trend": "worsening", "change": 3.0},
  "duration_visibility_above_min": "3h 45m",
  "duration_visibility_below_min": "just now",
  "duration_ceiling_above_min": "5h 20m",
  "duration_ceiling_below_min": "just now",
  "summary": {
    "visibility": "Visibility improving (+0.50 mi change in 6hr)",
    "ceiling": "Ceiling stable (0 ft change in 6hr)",
    "wind": "Wind worsening (+3.0 kt change in 6hr)",
    "vis_above": "Above min 3h 45m",
    "vis_below": "Below min just now",
    "ceil_above": "Above min 5h 20m",
    "ceil_below": "Below min just now"
  },
  "minimums": {
    "visibility": 1.0,
    "ceiling": 400
  }
}

// For PAPB station with updated minimums:
{
  "station": "PAPB",
  "timestamp": "2025-04-27 11:35 PM AKST",
  "visibility_trend": {"trend": "improving", "change": 2.0},
  "ceiling_trend": {"trend": "improving", "change": 300},
  "wind_speed_trend": {"trend": "stable", "change": 0.0},
  "duration_visibility_above_min": "2h 15m",
  "duration_visibility_below_min": "1h 45m",
  "duration_ceiling_above_min": "4h 30m",
  "duration_ceiling_below_min": "just now",
  "summary": {
    "visibility": "Visibility improving (+2.00 mi change in 6hr)",
    "ceiling": "Ceiling improving (+300 ft change in 6hr)",
    "wind": "Wind stable (0.0 kt change in 6hr)",
    "vis_above": "Above min 2h 15m",
    "vis_below": "Below min 1h 45m",
    "ceil_above": "Above min 4h 30m",
    "ceil_below": "Below min just now"
  },
  "minimums": {
    "visibility": 2.5,
    "ceiling": 840
  }
}
```

**How to Use:**
- Fetch `/api/approach/{station}/trend_status.json` in your website or API client.
- Display the summary fields or detailed trends as needed.
- No changes to your main weather/approach logic are required.

## Docker Deployment

Build the container:
```bash
docker build -t approach-service .
```

Run the service:
```bash
docker run -d --name approach-service \
  -v /path/to/data:/app/data \
  approach-service
```

## Service Components

1. `approach_service.py`: METAR data collection and processing, runway wind analysis 
2. `taf_decoder.py`: TAF data fetching and decoding
3. `approach_status.py`: Approach plate status against current weather, including runway favorability
4. `run_services.py`: Service orchestration and process management

## Approach Status

The service tracks the following for each approach:
- Whether current conditions are above or below minimums
- How long an approach has been below minimums
- How long an approach has been above minimums
- When the status last changed
- Current vs. required visibility and ceiling (with +/- indicators showing if values are above/below minimums)
- Data reliability indication based on sensor status
- Detailed notes about any sensor or data issues
- Wind favorability analysis:
  - Whether the approach is on the runway favored by current winds
  - Crosswind component for the approach's runway
  - Crosswind intensity classification (LIGHT, MODERATE, HIGH)
  - Warnings for strong crosswinds (exceeding 25 knots)

The service also tracks flight category durations:
- VFR (Visual Flight Rules)
- MVFR (Marginal Visual Flight Rules)
- IFR (Instrument Flight Rules)
- LIFR (Low Instrument Flight Rules)

### Reliability Handling

The service has special handling for various operational issues:
- Offline weather sensors (visibility, ceiling, etc.)
- Stale data due to internet outages
- Automated warnings when data is unreliable
- Detailed diagnostic information about what's not working

## Data Update Frequency

- METAR processing: Every 15 minutes (checking for new data)
- METAR source data: Updated approximately every 30 minutes
- TAF: Every 5 minutes
- Sensor status: Every 1 minute
- Approach status: Every 1 minute
- Stale data detection: After 90 minutes (1.5 hours) without updates

## Error Handling

- Automatic retry on API failures
- Graceful handling of missing or malformed data
- Detailed logging for troubleshooting
- Proper process cleanup on shutdown

## Dependencies

- Python 3.9+
- requests
- pytz
- math (for trigonometric calculations in wind analysis)
- See requirements.txt for complete list

## Logging

All logs are sent to stdout/stderr for Docker log collection. Log format includes:
- Timestamp
- Log level
- Component name
- Detailed message

## Development

Maintained by the Aleutian Weather Service team. For issues or contributions, please contact the team lead.
