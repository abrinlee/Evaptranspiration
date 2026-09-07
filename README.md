# Evapotranspiration Dashboard

A self-hosted landscape water-balance system. A nightly Python job pulls airport
weather observations, official daily rainfall, and satellite solar radiation, computes
FAO-56 Penman-Monteith reference evapotranspiration (ET₀), and stores one row per day
in MariaDB. A single-page dashboard shows ET, rainfall, and a cumulative
rain-minus-ET water balance with a twice-weekly irrigation simulation.

Every input is a free public data source, so this runs for any location covered by:

| Source | What it provides | Coverage |
|---|---|---|
| [IEM ASOS archive](https://mesonet.agron.iastate.edu/request/download.phtml) | hourly temperature, dewpoint, wind, pressure, precip | US airports, many international METAR networks, 1990s onward |
| [NOAA GHCND](https://www.ncdc.noaa.gov/cdo-web/) | official daily precipitation | global, free token |
| [NASA POWER](https://power.larc.nasa.gov/) | daily global horizontal irradiance | global, 1984 onward, ~1 week lag |

## Layout

```
dashboard.html              main dashboard (7/14/28-day ET, balances, weather, 365-day irrigation sim)
historical_dashboard.html   year-over-year comparison, meant to be opened locally
api/et-data.php             JSON: last N days
api/et-history.php          JSON: arbitrary date range
pipeline/Weather_ASOS.py    nightly collector + ET₀ calculation (also does historical backfill)
pipeline/publish_weather_to_mqtt_db.py   optional Home Assistant/MQTT publisher
pipeline/run_weather_collection.sh       cron wrapper
pipeline/config.py          config loader shared by the pipeline scripts
schema.sql                  MariaDB table definition
config.example.ini          template for the one config file everything reads
deploy.sh                   copies dashboard + API to the web root
context.md                  long-form reference: the ET math, troubleshooting, maintenance
```

## Setup

1. **Database**

   ```bash
   mysql -u root -p < schema.sql
   ```

   Create two users, one read-only for the web API and one read/write for the pipeline:

   ```sql
   CREATE USER 'weather_ro'@'localhost' IDENTIFIED BY 'choose-a-password';
   GRANT SELECT ON weather.* TO 'weather_ro'@'localhost';
   CREATE USER 'weather_rw'@'localhost' IDENTIFIED BY 'choose-another-password';
   GRANT SELECT, INSERT, UPDATE ON weather.* TO 'weather_rw'@'localhost';
   FLUSH PRIVILEGES;
   ```

2. **Config file.** Copy `config.example.ini` to `/etc/weather-dashboard/config.ini`,
   fill in the database users, a [NOAA token](https://www.ncdc.noaa.gov/cdo-web/token),
   and your site (ASOS station, GHCND station, latitude/longitude/elevation of the
   place you are irrigating, timezone). Make it readable by both the web server user
   and the user that runs cron:

   ```bash
   sudo mkdir -p /etc/weather-dashboard
   sudo cp config.example.ini /etc/weather-dashboard/config.ini   # then edit it
   sudo chown $USER:www-data /etc/weather-dashboard/config.ini
   sudo chmod 640 /etc/weather-dashboard/config.ini
   ```

   The pipeline also looks in `$WEATHER_DASHBOARD_CONFIG` and
   `~/.config/weather-dashboard/config.ini`; the PHP API looks in
   `$WEATHER_DASHBOARD_CONFIG` then `/etc/weather-dashboard/config.ini`.

3. **Python environment**

   ```bash
   python3 -m venv ~/myenv && ~/myenv/bin/pip install -r requirements.txt
   ```

4. **Backfill history.** Backfill runs in 30-day chunks with a pause between them,
   writes a CSV for review, prints a continuity report, and asks before committing.
   A full year takes a few minutes.

   ```bash
   cd pipeline
   ~/myenv/bin/python Weather_ASOS.py --start 2024-01-01 --end 2024-12-31
   ```

5. **Nightly cron.** The wrapper refreshes the trailing 365 days every night, which
   is how the provisional last week gets replaced once NASA data arrives.

   ```
   5 3 * * * /path/to/Evaptranspiration/pipeline/run_weather_collection.sh
   ```

   Set `PYTHON=/path/to/python` in the environment if your interpreter is not `~/myenv/bin/python`.

6. **Web.** Any PHP-capable web server. `deploy.sh` copies `dashboard.html` and `api/`
   to `WEB_ROOT` (default `/var/www/bookstack/public`):

   ```bash
   sudo ./deploy.sh
   sudo WEB_ROOT=/var/www/html ./deploy.sh
   ```

   The dashboard fetches `/api/et-data.php` by absolute path, so the API must live at
   the web root's `api/` directory.

## How the numbers are made

`pipeline/Weather_ASOS.py` aggregates hourly ASOS reports to daily max/min/mean
temperature, mean dewpoint, mean wind, and ASOS precipitation, then prefers the
official GHCND daily precipitation when available. Solar radiation comes from NASA
POWER; for the most recent days, before NASA has published, it falls back to
0.75 × extraterrestrial radiation (clear sky) and marks the row `RA_FALLBACK`.
Those rows are rewritten with real data on later nightly runs.

ET₀ follows FAO-56: saturation vapor pressure from Tmax and Tmin, actual vapor
pressure from dewpoint, pressure from elevation, net longwave from the mean of the
Tmax⁴ and Tmin⁴ terms with a cloudiness factor from Rs/Rso, and wind adjusted from
10 m to 2 m. Details, variable definitions, and a troubleshooting guide are in
`context.md`.

The dashboard's water balance is simply cumulative rain minus ET₀. It has no crop
coefficient and no soil-capacity cap, so it is a reference-crop balance, not a soil
moisture model; the irrigation simulation (Mon/Thu, trigger at −1.25 in, 1 in per run)
reflects one city's watering rules and one sprinkler system.

## Notes

- `historical_dashboard.html` fetches every year since 1999 on load. It is not
  deployed by `deploy.sh`; open it locally against the same API.
- The API has no authentication and sends `Access-Control-Allow-Origin: *`. Put it
  behind your web server's auth if the host is reachable from outside.
- Chart.js and its plugins load from jsDelivr. `context.md` describes hosting them
  locally if you need to.

## License

GPL-3.0. See `LICENSE`.
