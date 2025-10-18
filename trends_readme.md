# Trend Analysis Service Setup Guide for PAPB Approach

## Overview
This guide explains how to set up the Trend Analysis service for PAPB airport approach data. The service analyzes 6-hour trend data for visibility, ceiling, and wind conditions and provides status information for approach conditions.

## Required Files and Locations

1. **Primary output file**: 
   ```
   /app/data/PAPB/trend_status.json
   ```

2. **Source data required**:
   ```
   /app/data/PAPB/weather_history.jsonl    # Historical weather data in jsonlines format
   /app/data/PAPB/approach_status.json     # Current approach minimums and status
   ```

## File Format Specifications

### trend_status.json
```json
{
  "station": "PAPB",
  "timestamp": "2025-04-28 11:35 PM AKST",
  "visibility_trend": {"trend": "stable", "change": 0.0},
  "ceiling_trend": {"trend": "improving", "change": 200},
  "wind_speed_trend": {"trend": "worsening", "change": 5.0},
  "duration_visibility_above_min": "5h 23m",
  "duration_visibility_below_min": "30m",
  "duration_ceiling_above_min": "3h 15m", 
  "duration_ceiling_below_min": "1h 45m",
  "summary": {
    "visibility": "Visibility stable (0.00 mi change in 6hr)",
    "ceiling": "Ceiling improving (+200 ft change in 6hr)",
    "wind": "Wind worsening (+5.0 kt change in 6hr)",
    "vis_above": "Above min for 5h 23m",
    "vis_below": "Below min for 30m",
    "ceil_above": "Above min for 3h 15m",
    "ceil_below": "Below min for 1h 45m"
  },
  "minimums": {
    "visibility": 2.5,
    "ceiling": 840
  }
}
```

### weather_history.jsonl
Each line is a separate JSON object with a timestamp and weather measurements:
```json
{"timestamp": "2025-04-28T09:30:00Z", "visibility": 10.0, "ceiling": 1200, "wind_speed": 12}
{"timestamp": "2025-04-28T10:00:00Z", "visibility": 8.5, "ceiling": 1100, "wind_speed": 14}
```

## Service Requirements

1. **Run Frequency**: Update the trend analysis every 5 minutes
2. **Time Window**: Analyze last 6 hours of data
3. **Trend Calculation**:
   - `improving`: Value is better now than 6 hours ago (visibility/ceiling higher, wind lower)
   - `worsening`: Value is worse now than 6 hours ago (visibility/ceiling lower, wind higher)
   - `stable`: Change is minimal or zero
   - `unknown`: Not enough data points to calculate trend

4. **Duration Calculation**:
   - Track how long conditions have been above/below approach minimums
   - Format as "Xh Ym" (e.g., "5h 23m")
   - Use "just now" for very recent transitions
   
5. **Current Minimums**:
   - PAPB_RNAV_29 visibility: 2.5 miles (recently updated from 1.0)
   - PAPB_RNAV_29 ceiling: 840 feet

## Implementation Steps

1. **Set up the data collection service**:
   ```python
   # Example Python pseudo-code
   def collect_weather_data():
       # Fetch current weather from METAR or API
       weather = fetch_current_weather("PAPB")
       
       # Append to history file with timestamp
       with open("/app/data/PAPB/weather_history.jsonl", "a") as f:
           json.dump({
               "timestamp": datetime.utcnow().isoformat(),
               "visibility": weather.visibility,
               "ceiling": weather.ceiling,
               "wind_speed": weather.wind_speed
           }, f)
           f.write("\n")
   ```

2. **Implement the trend analysis service**:
   ```python
   def analyze_trends():
       # Read weather history
       history = []
       with open("/app/data/PAPB/weather_history.jsonl", "r") as f:
           for line in f:
               if line.strip():
                   history.append(json.loads(line))
       
       # Filter to last 6 hours
       six_hours_ago = datetime.utcnow() - timedelta(hours=6)
       recent_history = [h for h in history if datetime.fromisoformat(h["timestamp"]) >= six_hours_ago]
       
       # Get approach minimums
       with open("/app/data/PAPB/approach_status.json", "r") as f:
           approach_data = json.load(f)
           minimums = {
               "visibility": approach_data["PAPB_RNAV_29"]["current_conditions"]["required_visibility"],
               "ceiling": approach_data["PAPB_RNAV_29"]["current_conditions"]["required_ceiling"]
           }
       
       # Calculate trends and durations
       result = calculate_trends_and_durations(recent_history, minimums)
       
       # Write output
       with open("/app/data/PAPB/trend_status.json", "w") as f:
           json.dump(result, f, indent=2)
   ```

3. **Set up scheduled execution**:
   ```bash
   # Run the trend analysis every 5 minutes via cron
   */5 * * * * /app/scripts/run_trend_analysis.py
   ```

## Troubleshooting

- **Issue**: Empty or missing trend_status.json
  **Solution**: Check if weather_history.jsonl exists and has recent data

- **Issue**: "unknown" trends for all metrics
  **Solution**: Ensure you have at least 3 hours of weather history data

- **Issue**: Incorrect minimums
  **Solution**: Verify approach_status.json has correct minimums (visibility: 2.5, ceiling: 840)

- **Issue**: Web access not working
  **Solution**: Ensure file permissions allow web server to read trend_status.json

## Verification

Access the API endpoint to verify it returns valid JSON:
```
curl http://your-server/api/approach/PAPB/trend_status.json
```

Expected HTTP response code is 200 OK with a valid JSON body matching the format described above.