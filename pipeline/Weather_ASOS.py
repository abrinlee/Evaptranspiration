#!/usr/bin/env python3
"""
Weather_ASOS.py – Daily weather data for KDFW (or KADS)
- ASOS hourly → daily aggregates
- NOAA GHCND daily precip (official)
- NASA POWER solar irradiance (GHI) → Penman-Monteith ET₀
- Falls back to extraterrestrial radiation (Ra) when solar data unavailable
- Writes to MariaDB weather database
- Optional CSV export with --export flag
- Supports historical backfill with --start/--end (auto-chunks in 30-day windows)
- Runs once per day (3:05 AM cron) → no need for heavy caching
- All site settings and credentials come from config.ini (see config.py)
"""

VERSION = "2.3"

import argparse
import math
import time
import numpy as np
import pandas as pd
import requests
import mysql.connector
from datetime import date, timedelta
from io import StringIO
import warnings

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------

from config import load_config, db_config

_cfg = load_config()
_site = _cfg["site"]

STATION = _site.get("station", "KDFW")
TZ = _site.get("timezone", "America/Chicago")
LATITUDE_DEG = _site.getfloat("latitude")        # location ET is computed for (yard, not airport)
LONGITUDE_DEG = _site.getfloat("longitude")
ELEVATION_M = _site.getfloat("elevation_m")
GHCND_STATION = _site.get("ghcnd_station", "GHCND:USW00003927")
DAYS_BACK = 365
CHUNK_SIZE = 30
CHUNK_DELAY_SEC = 15

NOAA_TOKEN = _cfg["noaa"].get("token")

FIELDS = ",".join([
    "tmpf", "dwpf", "sknt", "p01i", "drct", "alti",
])

# Database configuration (read/write user)
DB_CONFIG = db_config(_cfg, writer=True)

# ------------------------------------------------------------------
# Suppress pandas FutureWarning (optional)
# ------------------------------------------------------------------
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message="Downcasting object dtype arrays on .fillna"
)

# ------------------------------------------------------------------
# Helper: extraterrestrial radiation (Ra)
# ------------------------------------------------------------------
def extraterrestrial_radiation_MJm2(doy: int, lat_deg: float) -> float:
    """FAO-56 Ra [MJ m⁻² day⁻¹]"""
    lat_rad = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    cos_omega = -math.tan(lat_rad) * math.tan(delta)
    omega_s = (
        math.pi if cos_omega <= -1 else
        0.0 if cos_omega >= 1 else
        math.acos(cos_omega)
    )
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        omega_s * math.sin(lat_rad) * math.sin(delta) +
        math.cos(lat_rad) * math.cos(delta) * math.sin(omega_s)
    )
    return ra


# ------------------------------------------------------------------
# NOAA GHCND daily precipitation
# ------------------------------------------------------------------
def fetch_precip_noaa(token: str, ghcnd_station: str,
                      start_date: date, end_date: date) -> pd.DataFrame:
    """Return DataFrame with columns: date, NOAA_PRCP_IN (inches)"""
    url = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
    headers = {"token": token}
    params = {
        "datasetid": "GHCND",
        "stationid": ghcnd_station,
        "datatypeid": "PRCP",
        "startdate": start_date.isoformat(),
        "enddate": end_date.isoformat(),
        "limit": 1000,
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"[NOAA] HTTP {resp.status_code}")
        return pd.DataFrame(columns=["date", "NOAA_PRCP_IN"])

    data = resp.json()
    if "results" not in data or not data["results"]:
        return pd.DataFrame(columns=["date", "NOAA_PRCP_IN"])

    rows = []
    for r in data["results"]:
        d = r.get("date")
        v = r.get("value")
        if d and v is not None:
            inches = float(v) / 254.0          # tenths of mm → inches
            rows.append({"date": pd.to_datetime(d).date(), "NOAA_PRCP_IN": inches})

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.groupby("date", as_index=False)["NOAA_PRCP_IN"].sum()
    return df


# ------------------------------------------------------------------
# NASA POWER solar irradiance (no cache – runs once/day)
# ------------------------------------------------------------------
def fetch_solar_irradiance(start_date: date, end_date: date,
                           lat: float, lon: float) -> pd.DataFrame:
    """
    Fetch GHI from NASA POWER.
    If dates are too recent/future → return empty DF (will use Ra fallback).
    """
    # Clip end_date to 7 days ago (NASA POWER lag) instead of skipping entirely
    cutoff = date.today() - timedelta(days=7)
    if start_date > cutoff:
        print(f"[NASA] All dates too recent → using Ra fallback.")
        return pd.DataFrame(columns=["date", "SOLAR_IRRADIANCE_MJ_M2"])
    if end_date > cutoff:
        print(f"[NASA] Clipping end_date from {end_date} to {cutoff} (NASA lag)")
        end_date = cutoff

    params = {
        'parameters': 'ALLSKY_SFC_SW_DWN',
        'community': 'AG',
        'longitude': f"{lon:.6f}",
        'latitude': f"{lat:.6f}",
        'start': start_date.strftime("%Y%m%d"),
        'end': end_date.strftime("%Y%m%d"),
        'format': 'JSON',
    }
    url = 'https://power.larc.nasa.gov/api/temporal/daily/point'

    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 422:
            print("[NASA] 422 → likely future dates. Using Ra fallback.")
            return pd.DataFrame(columns=["date", "SOLAR_IRRADIANCE_MJ_M2"])
        resp.raise_for_status()
        data = resp.json()
        solar_vals = data['properties']['parameter']['ALLSKY_SFC_SW_DWN']

        rows = []
        for dstr, val in solar_vals.items():
            if val == -999:
                continue
            rows.append({
                "date": pd.to_datetime(dstr).date(),
                "SOLAR_IRRADIANCE_MJ_M2": float(val)
            })
        df = pd.DataFrame(rows).sort_values("date")
        print(f"[NASA] Fetched {len(df)} solar days.")
        return df
    except Exception as e:
        print(f"[NASA] Error {e} → using Ra fallback.")
        return pd.DataFrame(columns=["date", "SOLAR_IRRADIANCE_MJ_M2"])


# ------------------------------------------------------------------
# Penman-Monteith ET (inches/day)
# ------------------------------------------------------------------
def check_physics(es, ea, delta, gamma, rs, ra, rn, u2, et0_mm):
    """Validate intermediate PM values against physical bounds."""
    v = []
    if not 0.045 <= gamma <= 0.070:
        v.append(f"gamma={gamma:.4f} outside [0.045,0.070] -- pressure units must be kPa")
    if not 0.03 <= delta <= 0.50:
        v.append(f"delta={delta:.4f} outside [0.03,0.50]")
    if ea > es * 1.02:
        v.append(f"ea={ea:.3f} > es={es:.3f} (supersaturated)")
    if ra > 0 and rs / ra > 0.82:
        v.append(f"Rs/Ra={rs/ra:.3f} exceeds clear-sky limit 0.82")
    if rn > 0.77 * rs + 1e-9:
        v.append(f"Rn={rn:.2f} > 0.77*Rs (net longwave went negative)")
    if not 0.4 <= u2 <= 15:
        v.append(f"u2={u2:.2f} m/s outside [0.4,15]")
    if not 0 <= et0_mm <= 15:
        v.append(f"ETo={et0_mm:.2f} mm/d outside [0,15]")
    return v


def penman_monteith_et(tmax_f, tmin_f, tdew_f, wind_mph, rs_MJ, ra_MJ):
    """FAO-56 Penman-Monteith ET₀ (inches/day)"""
    if any(pd.isna(x) for x in [tmax_f, tmin_f, tdew_f, wind_mph, rs_MJ, ra_MJ]):
        return np.nan

    tmax_c = (tmax_f - 32) * 5/9
    tmin_c = (tmin_f - 32) * 5/9
    tmean_c = (tmax_c + tmin_c) / 2
    tdew_c = (tdew_f - 32) * 5/9

    # FAO-56 eq 12: es from Tmax/Tmin, not Tmean
    es_tmax = 0.6108 * math.exp(17.27 * tmax_c / (tmax_c + 237.3))
    es_tmin = 0.6108 * math.exp(17.27 * tmin_c / (tmin_c + 237.3))
    es = (es_tmax + es_tmin) / 2
    # FAO-56 eq 14: ea directly from dewpoint
    ea = 0.6108 * math.exp(17.27 * tdew_c / (tdew_c + 237.3))
    delta = 4098 * 0.6108 * math.exp(17.27 * tmean_c / (tmean_c + 237.3)) / (tmean_c + 237.3)**2

    # FAO-56 eq 7: atmospheric pressure from elevation
    p_kpa = 101.3 * ((293 - 0.0065 * ELEVATION_M) / 293) ** 5.26
    gamma = 0.665e-3 * p_kpa

    alpha = 0.23
    # FAO-56 eq 37: Rso from elevation, not self-referential through rs
    rso = (0.75 + 2e-5 * ELEVATION_M) * ra_MJ if ra_MJ > 0 else rs_MJ
    f_cloud = max(0.3, min(rs_MJ / rso, 1.0)) if rso > 0 else 1.0

    # FAO-56 eq 39: mean of T^4 at Tmax and Tmin, not T^4 at Tmean
    sigma_tk4 = ((tmax_c + 273.16)**4 + (tmin_c + 273.16)**4) / 2
    rnl = (4.903e-9 * sigma_tk4 *
           (0.34 - 0.14 * math.sqrt(ea)) *
           (1.35 * f_cloud - 0.35))
    rn = (1 - alpha) * rs_MJ - rnl
    g = 0.0

    u2 = wind_mph * 0.44704
    u2 = u2 * (4.87 / math.log(67.8 * 10 - 5.42))
    u2 = max(u2, 0.5)

    num1 = 0.408 * delta * (rn - g)
    num2 = gamma * (900 / (tmean_c + 273)) * u2 * (es - ea)
    den = delta + gamma * (1 + 0.34 * u2)

    et0_mm = (num1 + num2) / den
    et0_in = et0_mm * 0.0393701

    violations = check_physics(es, ea, delta, gamma, rs_MJ, ra_MJ, rn, u2, et0_mm)
    if violations:
        print(f"[PHYSICS] Tmax={tmax_f:.0f} Tmin={tmin_f:.0f} Td={tdew_f:.0f}:")
        for v in violations:
            print(f"  FAIL: {v}")

    return max(0.0, round(et0_in, 3))

def calc_rh(t_f, td_f):
    if pd.isna(t_f) or pd.isna(td_f):
        return np.nan
    t_c = (t_f - 32) * 5/9
    td_c = (td_f - 32) * 5/9
    es = 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))
    ea = 6.112 * math.exp(17.67 * td_c / (td_c + 243.5))
    return 100 * ea / es


# ------------------------------------------------------------------
# Database operations
# ------------------------------------------------------------------
def write_to_database(df, station_name):
    """Write daily weather data to MariaDB using UPSERT pattern"""
    upsert_sql = """
    INSERT INTO weather_daily (
        date, station, tmax_f, tmin_f, tavg_f, rh_mean, dewpoint_f,
        wind_mph, wind_mph_max, wind_dir_deg,
        rain_in, rain_in_asos, noaa_prcp_in, alti_inhg,
        solar_irradiance, solar_source, ra_mj_m2, et_est_in
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    ON DUPLICATE KEY UPDATE
        tmax_f = VALUES(tmax_f),
        tmin_f = VALUES(tmin_f),
        tavg_f = VALUES(tavg_f),
        rh_mean = VALUES(rh_mean),
        dewpoint_f = VALUES(dewpoint_f),
        wind_mph = VALUES(wind_mph),
        wind_mph_max = VALUES(wind_mph_max),
        wind_dir_deg = VALUES(wind_dir_deg),
        rain_in = VALUES(rain_in),
        rain_in_asos = VALUES(rain_in_asos),
        noaa_prcp_in = VALUES(noaa_prcp_in),
        alti_inhg = VALUES(alti_inhg),
        solar_irradiance = VALUES(solar_irradiance),
        solar_source = VALUES(solar_source),
        ra_mj_m2 = VALUES(ra_mj_m2),
        et_est_in = VALUES(et_est_in)
    """

    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        inserted = 0
        updated = 0

        for _, row in df.iterrows():
            solar_source = row.get('SOLAR_SOURCE', 'RA_FALLBACK')

            values = (
                row['date'],
                station_name,
                float(row['TMAX_F']) if pd.notna(row['TMAX_F']) else None,
                float(row['TMIN_F']) if pd.notna(row['TMIN_F']) else None,
                float(row['TAVG_F']) if pd.notna(row['TAVG_F']) else None,
                float(row['RH_MEAN']) if pd.notna(row['RH_MEAN']) else None,
                float(row['DAVG_F']) if pd.notna(row['DAVG_F']) else None,
                float(row['WIND_MPH']) if pd.notna(row['WIND_MPH']) else None,
                float(row['WIND_MPH_MAX']) if pd.notna(row['WIND_MPH_MAX']) else None,
                float(row['WIND_DIR_DEG']) if pd.notna(row['WIND_DIR_DEG']) else None,
                float(row['RAIN_IN']) if pd.notna(row['RAIN_IN']) else None,
                float(row['RAIN_IN_ASOS']) if pd.notna(row['RAIN_IN_ASOS']) else None,
                float(row['NOAA_PRCP_IN']) if pd.notna(row['NOAA_PRCP_IN']) else None,
                float(row['ALTI_inHg']) if pd.notna(row['ALTI_inHg']) else None,
                float(row['SOLAR_IRRADIANCE_MJ_M2']) if pd.notna(row['SOLAR_IRRADIANCE_MJ_M2']) else None,
                solar_source,
                float(row['RA_MJ_M2']) if pd.notna(row['RA_MJ_M2']) else None,
                float(row['ET_EST_IN']) if pd.notna(row['ET_EST_IN']) else None,
            )

            cursor.execute(upsert_sql, values)

            if cursor.rowcount == 1:
                inserted += 1
            elif cursor.rowcount == 2:
                updated += 1

        conn.commit()
        cursor.close()

        print(f"\n[DB] ✓ Database updated: {inserted} new, {updated} updated")
        return True

    except Exception as e:
        print(f"\n[DB] ✗ Database error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


# ------------------------------------------------------------------
# Process a single date range chunk (collect only, no DB write)
# ------------------------------------------------------------------
def process_chunk(start_date, end_date, station):
    """Fetch and aggregate weather data for a single date range. Returns DataFrame."""
    print(f"\n[ASOS] Fetching {station} from {start_date} to {end_date}")

    asos_end_date = end_date + timedelta(days=1)
    asos_params = {
        "station": station,
        "data": FIELDS,
        "year1": start_date.year, "month1": start_date.month, "day1": start_date.day,
        "year2": asos_end_date.year, "month2": asos_end_date.month, "day2": asos_end_date.day,
        "tz": TZ,
        "format": "onlycomma",
        "latlon": "no",
        "direct": "no",
        "report_type": "1,2",
    }
    resp = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
                        params=asos_params, timeout=120)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text.strip()), comment="#")

    for c in ["tmpf","dwpf","sknt","p01i","alti","drct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["valid"] = pd.to_datetime(df["valid"], errors="coerce")
    df = df.dropna(subset=["valid"])

    df["wind_mph"] = df["sknt"] * 1.15078
    df["date"] = df["valid"].dt.date

    def calc_daily_precip(group):
        sorted_group = group.sort_values('valid')
        vals = sorted_group['p01i'].fillna(0.0).values
        if len(vals) == 0:
            return 0.0
        total = 0.0
        prev = vals[0]
        for v in vals[1:]:
            if v < prev:
                total += prev
            prev = v
        total += prev
        return total

    asos_precip = df.groupby("date").apply(calc_daily_precip, include_groups=False).reset_index()
    asos_precip.columns = ["date", "RAIN_IN_ASOS"]

    daily = df.groupby("date").agg(
        TMAX_F=("tmpf","max"), TMIN_F=("tmpf","min"), TAVG_F=("tmpf","mean"),
        DAVG_F=("dwpf","mean"), WIND_MPH=("wind_mph","mean"),
        WIND_MPH_MAX=("wind_mph","max"),
        ALTI_inHg=("alti","mean"), WIND_DIR_DEG=("drct","mean")
    ).reset_index()

    daily = pd.merge(daily, asos_precip, on="date", how="left")
    daily["RH_MEAN"] = daily.apply(lambda r: calc_rh(r["TAVG_F"], r["DAVG_F"]), axis=1)

    # Solar + ET
    solar_df = fetch_solar_irradiance(start_date, end_date, LATITUDE_DEG, LONGITUDE_DEG)
    daily = pd.merge(daily, solar_df, on="date", how="left")

    daily["date_dt"] = pd.to_datetime(daily["date"])
    daily["DOY"] = daily["date_dt"].dt.dayofyear
    daily["RA_MJ_M2"] = daily["DOY"].apply(
        lambda d: extraterrestrial_radiation_MJm2(d, LATITUDE_DEG)
    )
    daily["SOLAR_SOURCE"] = daily["SOLAR_IRRADIANCE_MJ_M2"].apply(
        lambda v: "NASA_POWER" if pd.notna(v) else "RA_FALLBACK"
    )
    daily["SOLAR_IRRADIANCE_MJ_M2"] = daily["SOLAR_IRRADIANCE_MJ_M2"].fillna(daily["RA_MJ_M2"] * 0.75)

    daily["ET_EST_IN"] = daily.apply(
        lambda r: penman_monteith_et(
            r["TMAX_F"], r["TMIN_F"], r["DAVG_F"], r["WIND_MPH"],
            r["SOLAR_IRRADIANCE_MJ_M2"], r["RA_MJ_M2"]
        ),
        axis=1
    )

    # NOAA precip + final merge
    ghcnd_station = GHCND_STATION
    noaa_df = fetch_precip_noaa(NOAA_TOKEN, ghcnd_station, start_date, end_date)

    merged = pd.merge(daily, noaa_df, on="date", how="left")
    merged["RAIN_IN"] = merged["NOAA_PRCP_IN"].fillna(merged["RAIN_IN_ASOS"])

    final_cols = [
        "date","TMAX_F","TMIN_F","TAVG_F","RH_MEAN","DAVG_F","WIND_MPH","WIND_MPH_MAX",
        "RAIN_IN","RAIN_IN_ASOS","NOAA_PRCP_IN",
        "SOLAR_IRRADIANCE_MJ_M2","SOLAR_SOURCE","ET_EST_IN","ALTI_inHg","WIND_DIR_DEG","RA_MJ_M2"
    ]

    return merged[final_cols].sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------------
# Continuity report
# ------------------------------------------------------------------
def check_continuity(df, expected_start, expected_end):
    """Check for gaps and data quality issues. Returns (is_clean, report_lines)."""
    report = []
    is_clean = True

    expected_dates = set()
    d = expected_start
    while d <= expected_end:
        expected_dates.add(d)
        d += timedelta(days=1)

    actual_dates = set(df["date"].tolist())
    missing_dates = sorted(expected_dates - actual_dates)

    report.append(f"  Expected days: {len(expected_dates)}")
    report.append(f"  Received days: {len(actual_dates)}")

    if missing_dates:
        is_clean = False
        report.append(f"  MISSING DATES: {len(missing_dates)}")
        # Group consecutive missing dates into ranges for readability
        ranges = []
        range_start = missing_dates[0]
        prev = missing_dates[0]
        for d in missing_dates[1:]:
            if d == prev + timedelta(days=1):
                prev = d
            else:
                ranges.append((range_start, prev))
                range_start = d
                prev = d
        ranges.append((range_start, prev))
        for rs, re in ranges:
            if rs == re:
                report.append(f"    - {rs}")
            else:
                report.append(f"    - {rs} to {re} ({(re - rs).days + 1} days)")
    else:
        report.append(f"  Missing dates: none")

    # Check for null values in critical columns
    critical_cols = ["TMAX_F", "TMIN_F", "RAIN_IN", "ET_EST_IN"]
    null_issues = []
    for col in critical_cols:
        if col in df.columns:
            n = df[col].isna().sum()
            if n > 0:
                null_issues.append(f"{col}: {n} nulls")
    if null_issues:
        is_clean = False
        report.append(f"  NULL VALUES:")
        for issue in null_issues:
            report.append(f"    - {issue}")
    else:
        report.append(f"  Null values in critical fields: none")

    # Physics checks
    physics_violations = 0
    et_vals = df["ET_EST_IN"].dropna()
    rs_ra_ratios = (df["SOLAR_IRRADIANCE_MJ_M2"] / df["RA_MJ_M2"]).dropna()
    fallback_count = (df["SOLAR_SOURCE"] == "RA_FALLBACK").sum() if "SOLAR_SOURCE" in df.columns else 0

    if len(et_vals) > 0:
        et_max = et_vals.max()
        et_min = et_vals.min()
        if et_max > 0.591:  # 15 mm/d in inches
            physics_violations += 1
            report.append(f"  PHYSICS: ET max {et_max:.3f} in/d exceeds 15 mm/d global limit")
        if et_min < 0:
            physics_violations += 1
            report.append(f"  PHYSICS: ET min {et_min:.3f} in/d is negative")

    if len(rs_ra_ratios) > 0:
        over_clear = (rs_ra_ratios > 0.82).sum()
        if over_clear > 0:
            physics_violations += 1
            report.append(f"  PHYSICS: Rs/Ra > 0.82 on {over_clear} days (exceeds clear-sky limit)")

    if fallback_count > 0:
        report.append(f"  RA_FALLBACK days: {fallback_count}")

    if physics_violations > 0:
        is_clean = False
        report.append(f"  Physics violations: {physics_violations}")
    else:
        report.append(f"  Physics checks: passed")

    return is_clean, report


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Weather ASOS data processor')
    parser.add_argument('--export', action='store_true',
                        help='Export data to CSV (for debugging/verification)')
    parser.add_argument('--start', type=str, default=None,
                        help='Backfill start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default=None,
                        help='Backfill end date (YYYY-MM-DD)')
    parser.add_argument('--yes', action='store_true',
                        help='Auto-commit backfill without prompting')
    parser.add_argument('--version', action='store_true',
                        help='Print version and exit')
    args = parser.parse_args()

    if args.version:
        print(f"Weather_ASOS.py v{VERSION}")
        raise SystemExit(0)

    backfill_mode = args.start and args.end

    if backfill_mode:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    elif args.start or args.end:
        print("Error: --start and --end must both be provided for backfill.")
        raise SystemExit(1)
    else:
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=DAYS_BACK - 1)

    total_days = (end_date - start_date).days + 1

    # ------------------------------------------------------------------
    # Daily mode (no --start/--end): collect and write directly
    # ------------------------------------------------------------------
    if not backfill_mode:
        out = process_chunk(start_date, end_date, STATION)
        write_to_database(out, STATION)

        if args.export:
            outfile = f"{STATION.lower()}_30day_summary_{end_date}.csv"
            out.to_csv(outfile, index=False, float_format="%.3f")
            print(f"\n[CSV] Exported: {outfile}")

        print(f"\n{'='*60}")
        print(f"Summary for {STATION} ({start_date} to {end_date}):")
        print(f"{'='*60}")
        print(out[["date","TMAX_F","RAIN_IN","SOLAR_IRRADIANCE_MJ_M2","ET_EST_IN"]].tail(10).to_string(index=False))
        print(f"\nComplete - {len(out)} days processed")

    # ------------------------------------------------------------------
    # Backfill mode: collect all chunks, review, then commit
    # ------------------------------------------------------------------
    else:
        print(f"\n[BACKFILL] {total_days} days requested ({start_date} to {end_date})")
        print(f"[BACKFILL] Chunking into {CHUNK_SIZE}-day windows")

        all_chunks = []
        failed_chunks = []
        chunk_start = start_date
        chunk_num = 0

        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_SIZE - 1), end_date)
            chunk_num += 1
            print(f"\n--- Chunk {chunk_num}: {chunk_start} to {chunk_end} ---")

            try:
                chunk_df = process_chunk(chunk_start, chunk_end, STATION)
                all_chunks.append(chunk_df)
                print(f"[OK] {len(chunk_df)} days collected")
            except Exception as e:
                failed_chunks.append((chunk_start, chunk_end, str(e)))
                print(f"[FAIL] Chunk {chunk_num} error: {e}")

            chunk_start = chunk_end + timedelta(days=1)
            if chunk_start <= end_date:
                print(f"[WAIT] Pausing {CHUNK_DELAY_SEC}s before next chunk...")
                time.sleep(CHUNK_DELAY_SEC)

        if not all_chunks:
            print("\n[BACKFILL] No data collected. Exiting.")
            raise SystemExit(1)

        combined = pd.concat(all_chunks, ignore_index=True)
        combined = combined.sort_values("date").reset_index(drop=True)

        # Save to CSV for review
        csv_file = f"{STATION.lower()}_backfill_{start_date}_to_{end_date}.csv"
        combined.to_csv(csv_file, index=False, float_format="%.3f")

        # Continuity report
        is_clean, report_lines = check_continuity(combined, start_date, end_date)

        print(f"\n{'='*60}")
        print(f"BACKFILL CONTINUITY REPORT")
        print(f"{'='*60}")
        print(f"  Range: {start_date} to {end_date}")
        for line in report_lines:
            print(line)
        if failed_chunks:
            print(f"  FAILED CHUNKS: {len(failed_chunks)}")
            for cs, ce, err in failed_chunks:
                print(f"    - {cs} to {ce}: {err}")
        else:
            print(f"  Failed chunks: none")
        print(f"\n  CSV saved: {csv_file}")
        if is_clean and not failed_chunks:
            print(f"  Status: CLEAN")
        else:
            print(f"  Status: ISSUES FOUND (review CSV before committing)")
        print(f"{'='*60}")

        # Prompt user (or auto-commit with --yes if clean)
        if args.yes:
            if is_clean and not failed_chunks:
                print(f"\n[--yes] Data is clean. Auto-committing {len(combined)} days to database...")
                write_to_database(combined, STATION)
                print(f"\n[BACKFILL] Done - {len(combined)} days committed to database")
            else:
                print(f"\n[--yes] Issues found. Refusing to auto-commit. Review {csv_file}")
                raise SystemExit(1)
        else:
            answer = input(f"\nCommit {len(combined)} days to database? (y/n): ").strip().lower()
            if answer == 'y':
                write_to_database(combined, STATION)
                print(f"\n[BACKFILL] Done - {len(combined)} days committed to database")
            else:
                print(f"\n[BACKFILL] Aborted. Data saved in {csv_file} for review.")
