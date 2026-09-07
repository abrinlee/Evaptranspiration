# Evapotranspiration Dashboard - Project Context

**Version:** 1.5.0
**Last Updated:** 2026-09-07
**Purpose:** Interactive web dashboard for visualizing weather and evapotranspiration data

## Table of Contents
1. [Project Overview](#project-overview)
2. [Evapotranspiration Calculation](#evapotranspiration-calculation)
3. [Quick Start](#quick-start)
4. [Project Structure](#project-structure)
5. [Database Configuration](#database-configuration)
6. [How It Works](#how-it-works)
7. [Deployment](#deployment)
8. [Maintenance Tasks](#maintenance-tasks)
9. [Modifying the Dashboard](#modifying-the-dashboard)
10. [Troubleshooting](#troubleshooting)
11. [Security Considerations](#security-considerations)

---

## Project Overview

This project provides a real-time dashboard for monitoring weather conditions and calculating evapotranspiration (ET) rates. It's designed for agricultural or environmental monitoring purposes, showing:

- **Evapotranspiration**: 7, 14, and 28-day ET trends
- **Water Balance**: Cumulative precipitation minus ET (shows surplus/deficit)
- **Weather Metrics**: Rainfall, temperature, solar radiation, wind, humidity, pressure

### Key Features
- **Interactive Charts**: Zoom (mouse wheel), pan (Ctrl+drag), reset (double-click)
- **Annotations**: Click data points to add custom notes (stored locally)
- **Export Options**: Download data as CSV or print charts
- **Auto-refresh**: Updates every 5 minutes
- **Responsive**: Works on desktop and mobile devices

---

## Evapotranspiration Calculation

### Method: FAO-56 Penman-Monteith Reference ET₀

The dashboard displays evapotranspiration values calculated using the **FAO-56 Penman-Monteith equation**, which is the international standard for reference crop evapotranspiration (ET₀).

### The Equation

The ET₀ is calculated in the data ingestion pipeline (`pipeline/Weather_ASOS.py`) using:

```
         0.408·Δ·(Rn - G) + γ·(900/(T+273))·u₂·(es - ea)
ET₀ = ───────────────────────────────────────────────────
                  Δ + γ·(1 + 0.34·u₂)
```

**Result:** ET₀ in mm/day, then converted to inches/day

### Variable Definitions

| Variable | Description | Source | Units |
|----------|-------------|--------|-------|
| **Δ** | Slope of saturation vapor pressure curve | Calculated | kPa/°C |
| **Rn** | Net radiation at crop surface | Calculated from solar data and cloudiness | MJ/m²/day |
| **G** | Soil heat flux density | Assumed 0 for daily calculations | MJ/m²/day |
| **γ** | Psychrometric constant | Calculated from elevation-derived pressure | kPa/°C |
| **T** | Mean daily air temperature | (Tmax + Tmin) / 2 | °C |
| **u₂** | Wind speed at 2m height | Converted from station height | m/s |
| **es** | Saturation vapor pressure | Mean of es(Tmax) and es(Tmin) | kPa |
| **ea** | Actual vapor pressure | Calculated from dewpoint | kPa |

### Input Data Requirements

The ET calculation requires these daily measurements from `weather_daily` table:

1. **Temperature** (`tmax_f`, `tmin_f`): Maximum and minimum daily temperatures (°F)
2. **Dewpoint** (`dewpoint_f`): Mean daily dewpoint (°F); actual vapor pressure comes from this, not from RH
3. **Wind Speed** (`wind_mph`): Daily average wind speed (mph)
4. **Elevation** (config `elevation_m`): atmospheric pressure is derived from elevation (FAO-56 eq 7). `alti_inhg` is stored for display only; it is an altimeter setting, not station pressure
5. **Solar Radiation** (`solar_irradiance`): Global horizontal irradiance in MJ/m²/day
   - **Primary source**: NASA POWER satellite data
   - **Fallback**: Extraterrestrial radiation (Ra) calculated from latitude and day of year
6. **Extraterrestrial Radiation** (`ra_mj_m2`): Always calculated for cloud adjustment

### Detailed Calculation Steps

The Python function performs these steps:

#### 1. Temperature Conversions
```python
tmax_c = (tmax_f - 32) * 5/9
tmin_c = (tmin_f - 32) * 5/9
tmean_c = (tmax_c + tmin_c) / 2
```

#### 2. Saturation Vapor Pressure (es) and Slope (Δ)
```python
# FAO-56 eq 12: es is the mean of es at Tmax and Tmin, NOT es at Tmean
es_tmax = 0.6108 · exp(17.27·Tmax / (Tmax + 237.3))
es_tmin = 0.6108 · exp(17.27·Tmin / (Tmin + 237.3))
es = (es_tmax + es_tmin) / 2                    # kPa
Δ = 4098·[0.6108·exp(17.27·Tmean/(Tmean+237.3))] / (Tmean + 237.3)²   # kPa/°C
```

#### 3. Actual Vapor Pressure (ea)
```python
# FAO-56 eq 14: directly from mean dewpoint
ea = 0.6108 · exp(17.27·Tdew / (Tdew + 237.3))  # kPa
```

#### 4. Psychrometric Constant (γ)
```python
# FAO-56 eq 7: pressure from elevation
P = 101.3 · ((293 - 0.0065·z) / 293)^5.26       # kPa, z = elevation_m
γ = 0.665 × 10⁻³ · P                            # kPa/°C
```

#### 5. Net Radiation (Rn)

**Shortwave radiation (net incoming solar):**
```python
α = 0.23                                        # Albedo for reference crop (grass)
Rns = (1 - α) · Rs                              # Net shortwave radiation
```

**Longwave radiation (outgoing thermal):**
```python
# FAO-56 eq 37: clear-sky radiation from elevation
Rso = (0.75 + 2×10⁻⁵·z) · Ra
f_cloud = clamp(Rs / Rso, 0.3, 1.0)

# FAO-56 eq 39: mean of T⁴ at Tmax and Tmin, not T⁴ at Tmean
σT⁴ = (Tmax_K⁴ + Tmin_K⁴) / 2
Rnl = σ·σT⁴·(0.34 - 0.14·√ea)·(1.35·f_cloud - 0.35)

where σ = 4.903 × 10⁻⁹ MJ/(K⁴·m²·day)
```

**Total net radiation:**
```python
Rn = Rns - Rnl                                  # MJ/m²/day
```

#### 6. Wind Speed Adjustment
```python
# Convert wind speed from mph to m/s at measurement height
u_ms = wind_mph · 0.44704

# Adjust to standard 2m height (assuming 10m measurement height)
u₂ = u_ms · (4.87 / ln(67.8·10 - 5.42))

# Minimum wind speed threshold
u₂ = max(u₂, 0.5)                              # m/s
```

#### 7. Final ET₀ Calculation
```python
# Numerator components
num1 = 0.408 · Δ · (Rn - G)                    # Radiation term
num2 = γ · (900/(T+273)) · u₂ · (es - ea)      # Aerodynamic term

# Denominator
den = Δ + γ · (1 + 0.34·u₂)

# ET in mm/day
ET₀_mm = (num1 + num2) / den

# Convert to inches/day
ET₀_in = ET₀_mm · 0.0393701

# Clamp at zero and round
ET₀_in = max(0.0, round(ET₀_in, 3))
```

### Solar Radiation Sources

The calculation uses a two-tier approach for solar radiation data:

1. **Primary**: NASA POWER satellite-derived Global Horizontal Irradiance (GHI)
   - Updated daily from NASA's POWER API
   - More accurate representation of actual solar energy

2. **Fallback**: Extraterrestrial Radiation (Ra)
   - Calculated from the site latitude and day of year
   - Used when NASA data is unavailable (typically the most recent ~7-9 days)
   - The fallback value stored is 0.75·Ra (a clear-sky estimate), so provisional days
     run ~13% high on average and more on cloudy days; they are overwritten by the
     nightly run once NASA publishes

**Database tracking:**
- `solar_irradiance`: Contains GHI or Ra depending on availability
- `solar_source`: Enum field ('NASA_POWER' or 'RA_FALLBACK') tracking which was used
- `ra_mj_m2`: Always calculated, used for cloud adjustment

### Location-Specific Parameters

**Station:** set in config (`station`, `ghcnd_station`); default KDFW
**Site:** latitude/longitude/elevation in config are the place being irrigated, not the airport
**Reference Crop:** Grass (FAO-56 standard)
**Measurement Height:** 10m (wind), standard for airport ASOS stations

### Validation and Quality Control

The calculation includes several validation steps:

1. **Missing Data**: Returns NaN if any input is missing
2. **Wind Speed Floor**: Minimum 0.5 m/s (FAO-56 recommendation)
3. **Cloud Factor Bounds**: Limited to 0.3-1.0 range for physical validity
4. **Minimum ET**: Clamped at 0.0 inches/day
5. **Rounding**: Final value rounded to 0.001 inches
6. **Physics checks**: γ, Δ, ea≤es, Rs/Ra, Rn, u₂ and ET₀ are checked against physical bounds and violations are printed

### Reference

This implementation follows the methodology documented in:

**FAO Irrigation and Drainage Paper No. 56**
*"Crop Evapotranspiration - Guidelines for Computing Crop Water Requirements"*
Food and Agriculture Organization of the United Nations, 1998

---

## Quick Start

### Prerequisites
- Web server (Apache/Nginx) with PHP support
- MariaDB/MySQL database
- Database already populated with weather data (see data pipeline below)

### Deploy to Server
```bash
cd /home/abrinlee/Projects/Evaptranspiration
sudo ./deploy.sh
```

This copies the dashboard to `/var/www/bookstack/public/dashboard.html` where it's accessible via your web server.

### Access Dashboard
Navigate to: `http://your-server-address/dashboard.html`

---

## Project Structure

See the README for the file layout. In short: `dashboard.html` and `api/` are deployed;
`pipeline/` holds the nightly collector, the ET₀ math, and the optional MQTT publisher;
`schema.sql` and `config.example.ini` are what a new install needs.

**Cron Schedule:** `pipeline/run_weather_collection.sh` runs daily at 3:05 AM

### File Descriptions

#### `dashboard.html` (54KB)
- **Purpose**: Complete single-page application containing HTML, CSS, and JavaScript
- **No external files needed**: All styling and logic embedded
- **Dependencies**: Loads Chart.js and plugins from CDN
- **Data source**: Calls `api/et-data.php` to fetch weather data

#### `api/et-data.php`
- **Purpose**: PHP API endpoint that queries MariaDB database
- **Accepts**: `?days=N` parameter (1-365)
- **Returns**: JSON array of weather data
- **Security**: Validates input to prevent SQL injection

#### `api/et-history.php`
- **Accepts**: `?start=YYYY-MM-DD&end=YYYY-MM-DD`; used by `historical_dashboard.html`
- **Returns**: same shape as et-data.php; prepared statement

#### `deploy.sh`
- **Purpose**: Automated deployment to web server
- **Requires**: sudo privileges
- **Action**: Copies dashboard.html and api/*.php to `WEB_ROOT` with correct ownership

---

## Database Configuration

### Connection Details

All credentials and site settings live in one INI file, never in code. See
`config.example.ini` and the README for the search order. The PHP API uses the
read-only `[database] user`; the pipeline uses `writer_user`.

### Database Schema

**Database:** `weather`
**Table:** `weather_daily`

**Required Columns:**
| Column | Type | Description | Units |
|--------|------|-------------|-------|
| `date` | DATE | Measurement date | YYYY-MM-DD |
| `et_est_in` | DECIMAL(6,3) | Penman-Monteith ET₀ | inches/day |
| `rain_in` | DECIMAL(6,3) | Daily rainfall | inches |
| `tmax_f` | DECIMAL(6,3) | Maximum temperature | °F |
| `tmin_f` | DECIMAL(6,3) | Minimum temperature | °F |
| `tavg_f` | DECIMAL(6,3) | Average temperature | °F |
| `rh_mean` | DECIMAL(6,3) | Mean relative humidity | % |
| `wind_mph` | DECIMAL(6,3) | Wind speed | mph |
| `alti_inhg` | DECIMAL(6,3) | Atmospheric pressure | inHg |
| `solar_irradiance` | DECIMAL(6,3) | GHI or Ra | MJ/m²/day |
| `ra_mj_m2` | DECIMAL(6,3) | Extraterrestrial radiation | MJ/m²/day |
| `solar_source` | ENUM | 'NASA_POWER' or 'RA_FALLBACK' | - |

**SQL Query Example:**
```sql
SELECT date, et_est_in, rain_in, tmax_f, tmin_f, tavg_f,
       rh_mean, wind_mph, alti_inhg, solar_irradiance, ra_mj_m2
FROM weather_daily
WHERE date >= DATE_SUB(CURDATE(), INTERVAL 28 DAY)
ORDER BY date ASC
```

### Data Updates

**NOTE:** This dashboard ONLY reads data. The weather data ingestion is handled by:

**Script:** `pipeline/Weather_ASOS.py`
**Schedule:** Runs daily at 3:05 AM via cron
**Process:**
1. Fetches last 30 days of ASOS hourly data from KDFW (or KADS fallback)
2. Aggregates to daily values (min, max, mean)
3. Gets official NOAA GHCND precipitation data
4. Fetches NASA POWER solar irradiance (GHI)
5. Calculates extraterrestrial radiation (Ra) as fallback
6. Computes Penman-Monteith ET₀
7. Writes/updates `weather.weather_daily` table

**Ensure this cron job continues running for fresh data!**

---

## How It Works

### Data Flow

```
┌──────────────┐
│ ASOS Weather │ ← Hourly data from KDFW airport
└──────┬───────┘
       │
       ↓ Daily @ 3:05 AM
┌──────────────────┐
│ Weather_ASOS.py  │ ← Calculates ET₀, aggregates data
└──────┬───────────┘
       │
       ↓ INSERT/UPDATE
┌──────────────┐
│   MariaDB    │ ← weather.weather_daily table
│  (storage)   │
└──────┬───────┘
       │
       ↓ AJAX Request (every 5 min)
┌──────────────┐
│ et-data.php  │ ← Returns JSON
│    (API)     │
└──────┬───────┘
       │
       ↓ Parse & Calculate Balance
┌──────────────┐
│dashboard.html│ ← JavaScript processes data
│  (frontend)  │
└──────┬───────┘
       │
       ↓ Render
┌──────────────┐
│  Chart.js    │ ← Interactive visualizations
│(visualization)│
└──────────────┘
       │
       ↓
┌──────────────┐
│     User     │
└──────────────┘
```

### Chart Types and Data

#### Row 1: Evapotranspiration Charts (3 cards)
- **7-Day ET**, **14-Day ET**, **28-Day ET**
- **Data**: `et_est_in` from database (Penman-Monteith calculated)
- **Stats**: Total ET, Average ET, Max ET
- **Units**: inches

#### Row 2: Water Balance Charts (3 cards)
- **7-Day Balance**, **14-Day Balance**, **28-Day Balance**
- **Calculation**: Cumulative sum of `(rain_in - et_est_in)` for each day
- **Stats**: Final Balance, P/ET Total, Low Point
- **Visual**: Green gradient (surplus), Red gradient (deficit)
- **Interpretation**:
  - Positive balance = More rain than ET (water surplus)
  - Negative balance = More ET than rain (irrigation may be needed)

#### Row 3: Primary Weather Metrics - 28 Days (3 cards)
- **Daily Rainfall** (bar chart): `rain_in`
- **Temperature** (line chart): `tmax_f`, `tmin_f`, `tavg_f`
- **Solar Radiation** (line chart): `ra_mj_m2` (extraterrestrial radiation)

#### Row 4: Additional Weather Metrics - 28 Days (3 cards)
- **Wind Speed**: `wind_mph`
- **Relative Humidity**: `rh_mean`
- **Atmospheric Pressure**: `alti_inhg`

### Annotation System

- **Storage**: Browser's localStorage (key: `chartAnnotations`)
- **Access**: Click any data point on any chart
- **Functionality**: Add, edit, delete notes
- **Persistence**: Saved per-browser, not shared across devices
- **Format**: Date + time + user text
- **Use Cases**: Mark frost events, irrigation dates, unusual weather, etc.

---

## Deployment

### Using the Deploy Script

```bash
cd /home/abrinlee/Projects/Evaptranspiration
sudo ./deploy.sh
```

**What it does:**
1. Copies `dashboard.html` and `api/et-data.php`, `api/et-history.php` to `WEB_ROOT` (default `/var/www/bookstack/public`)
2. Sets ownership to `www-data:www-data` and mode `644`
3. Does not deploy `historical_dashboard.html` (open it locally)

**Requirements:**
- Root/sudo access
- BookStack installation at `/var/www/bookstack/`
- Apache/Nginx configured to serve `/var/www/bookstack/public/`

### Manual Deployment

If deploying to a different location:

```bash
# Copy dashboard
cp dashboard.html /your/web/directory/

# Copy API (must maintain relative path: api/et-data.php)
mkdir -p /your/web/directory/api/
cp api/et-data.php /your/web/directory/api/

# Set permissions
sudo chown www-data:www-data /your/web/directory/dashboard.html
sudo chown www-data:www-data /your/web/directory/api/et-data.php
sudo chmod 644 /your/web/directory/dashboard.html
sudo chmod 644 /your/web/directory/api/et-data.php
```

**⚠️ IMPORTANT:** The API must be at `api/et-data.php` relative to `dashboard.html`, or you'll need to update the API path in dashboard.html (line 703).

---

## Maintenance Tasks

### Regular Maintenance

1. **Monitor Database Growth**
   ```bash
   mysql -u root -p -e "SELECT COUNT(*) as row_count FROM weather.weather_daily;"
   ```

2. **Check Data Freshness**
   ```bash
   mysql -u root -p -e "SELECT MAX(date) as latest_data FROM weather.weather_daily;"
   ```
   Should be yesterday's date (script runs at 3:05 AM)

3. **Verify API is Responding**
   ```bash
   curl http://localhost/api/et-data.php?days=7
   ```

4. **Check Data Pipeline Cron Job**
   ```bash
   crontab -l | grep Weather_ASOS
   # Should show: 5 3 * * * .../pipeline/run_weather_collection.sh
   ```

5. **Check PHP Error Logs**
   ```bash
   sudo tail -f /var/log/apache2/error.log
   # or for nginx:
   sudo tail -f /var/log/nginx/error.log
   ```

6. **Monitor ET Calculation Script Logs**
   ```bash
   # Check if script runs successfully
   grep "Weather_ASOS" /var/log/syslog | tail -20
   ```

### Updating Database Credentials

If a database password changes, edit `/etc/weather-dashboard/config.ini`. No redeploy is needed.

### Updating Refresh Interval

Default is 5 minutes (300000ms). To change, edit `dashboard.html` line 1553:

```javascript
// Change from 5 minutes to 10 minutes
setInterval(loadAllData, 600000);  // 600000ms = 10 minutes
```

### Database Maintenance

**Archiving Old Data** (if table gets too large):
```sql
-- Create archive table
CREATE TABLE weather_daily_archive LIKE weather_daily;

-- Move data older than 2 years
INSERT INTO weather_daily_archive
SELECT * FROM weather_daily
WHERE date < DATE_SUB(CURDATE(), INTERVAL 2 YEAR);

-- Delete archived data
DELETE FROM weather_daily
WHERE date < DATE_SUB(CURDATE(), INTERVAL 2 YEAR);
```

---

## Modifying the Dashboard

### Adding a New Chart

**Example: Adding a UV Index chart**

1. **Add database column** (if not exists):
```sql
ALTER TABLE weather_daily ADD COLUMN uv_index DECIMAL(6,3)
COMMENT 'UV index value';
```

2. **Update data pipeline** (`Weather_ASOS.py`) to populate the column

3. **Update API** (`et-data.php`) to return the field:
```php
$query = "SELECT date, et_est_in, rain_in, ..., uv_index
          FROM weather_daily ...";
// Add to response:
'uv_index' => floatval($row['uv_index'])
```

4. **Add HTML card** in `dashboard.html` (around line 520):
```html
<div class="card">
    <h2>UV Index - 28 Days</h2>
    <div class="chart-container">
        <canvas id="uvChart"></canvas>
    </div>
    <div id="uvStats" class="stats-container"></div>
    <div class="chart-actions">
        <button class="chart-action-btn" onclick="exportCSV('uvChart', 'uv-index')">
            📊 Export CSV
        </button>
        <button class="chart-action-btn" onclick="printChart('uvChart')">
            🖨️ Print
        </button>
    </div>
</div>
```

5. **Add chart creation function** (around line 1400):
```javascript
async function createUVChart() {
    const data = await fetchData(28);
    const dates = data.map(d => d.date);
    const uvValues = data.map(d => d.uv_index);

    createChart('uvChart', {
        type: 'line',
        data: {
            labels: dates,
            datasets: [{
                label: 'UV Index',
                data: uvValues,
                borderColor: '#9333EA',
                backgroundColor: 'rgba(147, 51, 234, 0.1)',
                borderWidth: 2
            }]
        },
        options: getBaseChartOptions('UV Index')
    });

    // Update stats
    const total = uvValues.reduce((a, b) => a + b, 0);
    const avg = total / uvValues.length;
    const max = Math.max(...uvValues);

    document.getElementById('uvStats').innerHTML = `
        <div class="stat-item"><span class="stat-label">Average:</span> ${avg.toFixed(1)}</div>
        <div class="stat-item"><span class="stat-label">Max:</span> ${max.toFixed(1)}</div>
    `;
}
```

6. **Call in loadAllData()** (around line 1524):
```javascript
async function loadAllData() {
    try {
        await Promise.all([
            createETChart(7),
            createETChart(14),
            createETChart(28),
            // ... other charts ...
            createUVChart(),  // Add this line
        ]);
        // ...
    }
}
```

### Changing Chart Colors

Colors are defined in each chart creation function. Current color palette:

```javascript
// Current colors used:
'#3B82F6'  // Blue - ET charts
'#10B981'  // Green - Positive balance
'#EF4444'  // Red - Negative balance
'#8B5CF6'  // Purple - Rainfall
'#F59E0B'  // Orange - Temperature max
'#06B6D4'  // Cyan - Temperature min
'#EC4899'  // Pink - Temperature avg
'#10B981'  // Green - Solar radiation
'#3B82F6'  // Blue - Wind speed
'#8B5CF6'  // Purple - Humidity
'#F59E0B'  // Orange - Pressure
```

### Changing Time Periods

To add a 60-day chart:

1. Add new card in HTML
2. Call `createETChart(60)` or `createBalanceChart(60)`
3. API already supports 1-365 days (no changes needed)

---

## Troubleshooting

### Dashboard Shows "Loading..." Forever

**Possible Causes:**
1. API not accessible
2. Database connection failed
3. JavaScript error
4. No data in database

**Debugging Steps:**
```bash
# 1. Check API directly
curl http://localhost/api/et-data.php?days=7

# 2. Check database connection
mysql -u root -p weather -e "SELECT * FROM weather_daily ORDER BY date DESC LIMIT 5;"

# 3. Check if data exists for recent dates
mysql -u root -p weather -e "SELECT COUNT(*) FROM weather_daily WHERE date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY);"

# 4. Check browser console (F12) for JavaScript errors

# 5. Check web server error logs
sudo tail -50 /var/log/apache2/error.log
```

### "Access Denied" Database Error

**Problem:** Database credentials are incorrect or user lacks permissions

**Solution:**
```bash
# Verify credentials work
mysql -u root -p -e "USE weather; SHOW TABLES;"

# Grant permissions if needed
mysql -u root -p -e "GRANT SELECT ON weather.* TO 'root'@'localhost';"
mysql -u root -p -e "FLUSH PRIVILEGES;"
```

### Charts Not Displaying

**Possible Causes:**
1. CDN blocked (firewall/no internet)
2. JavaScript disabled
3. Old browser version

**Solutions:**
```bash
# Check if CDN is accessible
curl -I https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js

# Verify Chart.js loaded: Open browser console, type:
Chart

# Should show: ƒ Chart2(item, userConfig) { ... }
```

If CDN is blocked, download and host libraries locally (see Dependencies section).

### API Returns Empty Data

**Problem:** No data in date range

**Debug:**
```sql
-- Check data exists
SELECT MIN(date), MAX(date), COUNT(*) FROM weather.weather_daily;

-- Check recent data
SELECT * FROM weather.weather_daily ORDER BY date DESC LIMIT 10;

-- Check for NULL ET values
SELECT COUNT(*) FROM weather.weather_daily WHERE et_est_in IS NULL;
```

**If no recent data:**
```bash
# Check if Weather_ASOS.py is running
ps aux | grep Weather_ASOS

# Manually run the data pipeline
cd pipeline && ~/myenv/bin/python Weather_ASOS.py

# Check for errors in output
```

### Deploy Script Fails

**Common Issues:**
```bash
# Permission denied
sudo ./deploy.sh

# Target directory doesn't exist
sudo mkdir -p /var/www/bookstack/public/

# Wrong ownership
sudo chown -R www-data:www-data /var/www/bookstack/public/

# Script not executable
chmod +x deploy.sh
```

### ET Values Seem Wrong

**Diagnostic Steps:**

1. **Check input data quality:**
```sql
SELECT date, tmax_f, tmin_f, rh_mean, wind_mph, alti_inhg,
       solar_irradiance, solar_source, et_est_in
FROM weather_daily
ORDER BY date DESC
LIMIT 10;
```

2. **Look for missing inputs:**
```sql
SELECT date,
       CASE WHEN tmax_f IS NULL THEN 'TMAX' END,
       CASE WHEN tmin_f IS NULL THEN 'TMIN' END,
       CASE WHEN rh_mean IS NULL THEN 'RH' END,
       CASE WHEN wind_mph IS NULL THEN 'WIND' END,
       CASE WHEN alti_inhg IS NULL THEN 'PRESSURE' END,
       CASE WHEN solar_irradiance IS NULL THEN 'SOLAR' END
FROM weather_daily
WHERE tmax_f IS NULL OR tmin_f IS NULL OR rh_mean IS NULL
   OR wind_mph IS NULL OR alti_inhg IS NULL OR solar_irradiance IS NULL
ORDER BY date DESC LIMIT 20;
```

3. **Typical ET ranges for Dallas area:**
   - Winter: 0.05 - 0.15 inches/day
   - Spring/Fall: 0.15 - 0.25 inches/day
   - Summer: 0.25 - 0.40 inches/day
   - Values outside these ranges may indicate data issues

4. **Check solar data source:**
```sql
SELECT solar_source, COUNT(*)
FROM weather_daily
GROUP BY solar_source;
```
Too many RA_FALLBACK entries may reduce accuracy.

---

## Security Considerations

### Current Security Issues

⚠️ **IMPORTANT:** This project has several security concerns for production use:

1. **Database users**: the config file separates a read-only user (API) from a read/write user (pipeline). Create them as shown in the README; until then both entries may point at the same account.

2. **No Authentication**: Dashboard is publicly accessible
   - **Risk**: Anyone can view data
   - **Fix**: Add Apache/Nginx basic auth or implement login system

3. **Credentials**: resolved. Nothing in the repo contains a password or token; everything reads `/etc/weather-dashboard/config.ini` (gitignored).

4. **CORS Wide Open**: `Access-Control-Allow-Origin: *`
   - **Risk**: Any website can call your API
   - **Fix**: Restrict to specific domain:
   ```php
   header("Access-Control-Allow-Origin: https://your-domain.com");
   ```

5. **No HTTPS**: Assumes HTTP
   - **Risk**: Data transmitted in clear text
   - **Fix**: Configure SSL/TLS certificate (Let's Encrypt)

6. **No Rate Limiting**: API can be called unlimited times
   - **Risk**: Potential DoS or database overload
   - **Fix**: Implement rate limiting in PHP or web server

### Recommended Security Improvements

#### 1. Dedicated database users and config file

Done; see README "Setup" steps 1 and 2.

#### 3. Add Rate Limiting

```php
// At top of et-data.php
session_start();
$_SESSION['api_calls'] = ($_SESSION['api_calls'] ?? 0) + 1;
$_SESSION['last_reset'] = $_SESSION['last_reset'] ?? time();

// Reset counter every hour
if (time() - $_SESSION['last_reset'] > 3600) {
    $_SESSION['api_calls'] = 1;
    $_SESSION['last_reset'] = time();
}

if ($_SESSION['api_calls'] > 200) {  // 200 calls per hour
    http_response_code(429);
    die(json_encode(['error' => 'Rate limit exceeded']));
}
```

---

## Dependencies

### External Libraries (CDN)

All dependencies are loaded from `cdn.jsdelivr.net`:

| Library | Version | Purpose | Documentation |
|---------|---------|---------|---------------|
| Chart.js | 4.4.0 | Core charting library | https://www.chartjs.org/ |
| chartjs-plugin-annotation | 3.0.1 | Annotation markers on charts | https://www.chartjs.org/chartjs-plugin-annotation/ |
| Hammer.js | 2.0.8 | Touch gesture support (pan/zoom) | https://hammerjs.github.io/ |
| chartjs-plugin-zoom | 2.0.1 | Mouse wheel zoom functionality | https://www.chartjs.org/chartjs-plugin-zoom/ |

### Hosting Libraries Locally (Fallback)

If CDN is unavailable or blocked, download libraries and host locally:

```bash
# Create assets directory
sudo mkdir -p /var/www/bookstack/public/assets/js/
cd /var/www/bookstack/public/assets/js/

# Download libraries
sudo wget https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js
sudo wget https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js
sudo wget https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js
sudo wget https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js

# Set permissions
sudo chown www-data:www-data *.js
sudo chmod 644 *.js
```

Then update `dashboard.html` script src paths (lines 530-533):
```html
<script src="assets/js/chart.umd.min.js"></script>
<script src="assets/js/chartjs-plugin-annotation.min.js"></script>
<script src="assets/js/hammer.min.js"></script>
<script src="assets/js/chartjs-plugin-zoom.min.js"></script>
```

### Backend Dependencies

- **PHP**: 7.4 or higher (mysqli extension required)
- **MariaDB/MySQL**: 10.3 or higher
- **Web Server**: Apache 2.4+ or Nginx 1.18+

### Data Pipeline Dependencies

Located in `pipeline/Weather_ASOS.py`:

```bash
# Python packages required:
pip3 install pandas numpy requests pytz
```

**Python Version:** 3.8 or higher

---

## Additional Notes

### Browser Compatibility

**Tested and working on:**
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

**NOT compatible with:** Internet Explorer (any version)

**Minimum Requirements:**
- ES6/ES2015 JavaScript support
- CSS Grid support
- Fetch API support
- LocalStorage API

### Performance Notes

- Dashboard loads 7, 14, and 28 days of data on initial page load
- Each time period makes a separate API call (7 total API calls on load)
- Auto-refresh every 5 minutes adds minimal server load (~0.5 KB per request)
- Chart.js renders efficiently even with 365 days of data
- Browser memory usage: ~50-100 MB (mostly Chart.js canvases)

### Mobile Experience

- **Responsive design** adapts to small screens (grid becomes single column below 768px)
- **Touch gestures** supported via Hammer.js
- **Pan/zoom** work with finger swipes and pinch gestures
- All charts stack vertically on mobile for easy scrolling
- Chart height reduced on mobile for better viewability

### Known Limitations

1. **Annotations are local**: Not shared between browsers/devices (stored in localStorage)
2. **No user accounts**: Cannot track who added annotations
3. **No historical data editing**: Only displays what's in the database
4. **Single location**: Hardcoded for KDFW (Dallas-Fort Worth)
5. **English units only**: All data displayed in imperial units (°F, inches, mph, inHg)

### Future Enhancement Ideas

1. **Multi-location support**: Select different weather stations
2. **Metric units toggle**: Switch between imperial and metric
3. **Date range picker**: Custom time period selection
4. **Crop coefficient calculator**: Convert ET₀ to crop-specific ETc
5. **Irrigation scheduler**: Recommend watering based on water balance
6. **Shared annotations**: Store notes in database instead of localStorage
7. **Email alerts**: Notify when balance drops below threshold
8. **Data export**: Download historical data in various formats
9. **Comparison mode**: Compare current year vs previous year
10. **Frost predictions**: Alert for low temperature forecasts

---

## Support and Contact

For questions about maintaining this dashboard:

1. Review this context document
2. Check browser console (F12) for JavaScript errors
3. Test API endpoint directly: `curl http://localhost/api/et-data.php?days=7`
4. Verify database contains recent data (see Troubleshooting section)
5. Check web server error logs
6. Verify data pipeline cron job is running

### File Locations

| Component | Path |
|-----------|------|
| **Dashboard Source** | `/home/abrinlee/Projects/Evaptranspiration/dashboard.html` |
| **Dashboard Deployed** | `/var/www/bookstack/public/dashboard.html` |
| **API Source** | `/home/abrinlee/Projects/Evaptranspiration/api/` |
| **Data Pipeline** | `/home/abrinlee/Projects/Evaptranspiration/pipeline/Weather_ASOS.py` |
| **Config (secrets)** | `/etc/weather-dashboard/config.ini` |
| **Database** | MariaDB: `weather.weather_daily` |
| **This Documentation** | `/home/abrinlee/Projects/Evaptranspiration/context.md` |

### Quick Reference Commands

```bash
# Deploy latest changes
cd /home/abrinlee/Projects/Evaptranspiration && sudo ./deploy.sh

# Check latest data
mysql -u root -p weather -e "SELECT * FROM weather_daily ORDER BY date DESC LIMIT 5;"

# Test API
curl http://localhost/api/et-data.php?days=7 | jq

# Run data pipeline manually
~/myenv/bin/python pipeline/Weather_ASOS.py

# Check cron job
crontab -l | grep Weather

# View web server logs
sudo tail -f /var/log/apache2/error.log
```

---

**Document Version:** 1.1
**Created:** 2025-12-13
**Dashboard Version Documented:** 1.5.0
**Last Reviewed:** 2026-09-07

*This documentation should be updated whenever significant changes are made to the dashboard, database schema, or data pipeline.*
