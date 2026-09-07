#!/bin/bash
# Nightly weather collection wrapper. Cron entry (3:05 AM):
#   5 3 * * * /path/to/Evaptranspiration/pipeline/run_weather_collection.sh
#
# Set PYTHON to the interpreter that has requirements.txt installed.

PYTHON="${PYTHON:-$HOME/myenv/bin/python}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/weather_collection.log"

cd "$DIR" || exit 1

# Collect ASOS/NOAA/NASA data, compute ET0, upsert into MariaDB
"$PYTHON" Weather_ASOS.py

# Optional: publish the last 30 days to MQTT for Home Assistant (needs [mqtt] in config)
"$PYTHON" publish_weather_to_mqtt_db.py

echo "Weather data collection and MQTT publish completed at $(date)" >> "$LOG"
