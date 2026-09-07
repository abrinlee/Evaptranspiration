#!/usr/bin/env python3
"""
Publish 30-day weather data to MQTT for Home Assistant consumption.
Reads directly from MariaDB weather database instead of CSV files.
"""

import json
import pandas as pd
import mysql.connector
import paho.mqtt.client as mqtt
from datetime import date, timedelta

from config import load_config, db_config

_cfg = load_config()
_mqtt = _cfg["mqtt"]

# MQTT Configuration
MQTT_BROKER = _mqtt.get("broker", "localhost")
MQTT_PORT = _mqtt.getint("port", 1883)
MQTT_USERNAME = _mqtt.get("username", "") or None
MQTT_PASSWORD = _mqtt.get("password", "") or None
MQTT_TOPIC_PREFIX = _mqtt.get("topic_prefix", "weather/30day")

# Database Configuration (read-only user is enough here)
DB_CONFIG = db_config(_cfg)

# Connect to database and fetch 30 days of data
print("Connecting to MariaDB...")
conn = mysql.connector.connect(**DB_CONFIG)

# Fetch last 30 days of weather data
query = """
SELECT
    date,
    tmax_f AS TMAX_F,
    tmin_f AS TMIN_F,
    tavg_f AS TAVG_F,
    rh_mean AS RH_MEAN,
    wind_mph AS WIND_MPH,
    wind_mph_max AS WIND_MPH_MAX,
    rain_in AS RAIN_IN,
    et_est_in AS ET_EST_IN,
    alti_inhg AS ALTI_inHg,
    solar_irradiance AS SOLAR_IRRADIANCE_MJ_M2,
    solar_source
FROM weather_daily
WHERE date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
ORDER BY date ASC
"""

print("Fetching weather data from database...")
df = pd.read_sql(query, conn)
conn.close()

if df.empty:
    print("No weather data found in database!")
    exit(1)

print(f"Retrieved {len(df)} days of data from {df['date'].iloc[0]} to {df['date'].iloc[-1]}")

# Convert date column to string for JSON serialization
df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

# Connect to MQTT
print("Connecting to MQTT broker...")
client = mqtt.Client()
if MQTT_USERNAME:
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()  # Start network loop to process messages

# Publish a simplified dataset with only chart-necessary fields to reduce size
chart_data = df[['date', 'TMAX_F', 'TMIN_F', 'TAVG_F', 'RAIN_IN', 'ET_EST_IN',
                 'RH_MEAN', 'WIND_MPH', 'WIND_MPH_MAX', 'ALTI_inHg']].to_dict(orient='records')
dataset_payload = {"data": chart_data, "count": len(chart_data)}
result = client.publish(f"{MQTT_TOPIC_PREFIX}/full_dataset", json.dumps(dataset_payload), retain=True)
result.wait_for_publish()
print(f"✓ Published chart dataset ({len(chart_data)} days) to {MQTT_TOPIC_PREFIX}/full_dataset")

# Publish summary statistics
summary = {
    "total_rain_in": round(float(df['RAIN_IN'].sum()), 3),
    "avg_temp_f": round(float(df['TAVG_F'].mean()), 1),
    "max_temp_f": round(float(df['TMAX_F'].max()), 1),
    "min_temp_f": round(float(df['TMIN_F'].min()), 1),
    "avg_humidity": round(float(df['RH_MEAN'].mean()), 1),
    "total_et_in": round(float(df['ET_EST_IN'].sum()), 3),
    "net_water_balance_in": round(float(df['RAIN_IN'].sum() - df['ET_EST_IN'].sum()), 3),
    "days_in_period": len(df),
    "start_date": df['date'].iloc[0],
    "end_date": df['date'].iloc[-1],
}
result = client.publish(f"{MQTT_TOPIC_PREFIX}/summary", json.dumps(summary), retain=True)
result.wait_for_publish()
print(f"✓ Published summary to {MQTT_TOPIC_PREFIX}/summary")

# Publish latest day's data separately for quick access
latest_day = df.iloc[-1].to_dict()
# Convert any NaN values to None for JSON
latest_day = {k: (None if pd.isna(v) else float(v) if isinstance(v, (int, float)) else v)
              for k, v in latest_day.items()}
result = client.publish(f"{MQTT_TOPIC_PREFIX}/latest", json.dumps(latest_day), retain=True)
result.wait_for_publish()
print(f"✓ Published latest day ({latest_day['date']}) to {MQTT_TOPIC_PREFIX}/latest")

# Publish data quality info
nasa_days = (df['solar_source'] == 'NASA_POWER').sum()
ra_days = (df['solar_source'] == 'RA_FALLBACK').sum()
data_quality = {
    "total_days": len(df),
    "nasa_power_days": int(nasa_days),
    "ra_fallback_days": int(ra_days),
    "data_quality_pct": round((nasa_days / len(df)) * 100, 1) if len(df) > 0 else 0
}
result = client.publish(f"{MQTT_TOPIC_PREFIX}/data_quality", json.dumps(data_quality), retain=True)
result.wait_for_publish()
print(f"✓ Published data quality info ({nasa_days} NASA, {ra_days} Ra fallback)")

client.loop_stop()  # Stop the loop
client.disconnect()
print("\n✓ Done! All weather data published to MQTT from MariaDB")
