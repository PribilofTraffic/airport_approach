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
  - Currently under repair
  - Basic status monitoring
  - Will support approach plate monitoring once METAR equipment is fixed

## Data Storage

All data is stored in a centralized location following the pattern:
```
/app/data/{station}/
  ├── weather.json           # Combined METAR data with runway wind analysis
  ├── taf.json               # Decoded TAF data
  ├── sensor_status.json     # Sensor status information
  ├── approach_status.json   # Status of approaches including runway favorability
  └── category_status.json   # Flight category durations and current status
```

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
