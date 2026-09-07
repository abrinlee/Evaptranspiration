-- Schema for the ET dashboard. Load with: mysql -u root -p < schema.sql
CREATE DATABASE IF NOT EXISTS weather CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE weather;

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `weather_daily` (
  `date` date NOT NULL,
  `station` varchar(4) NOT NULL DEFAULT 'KDFW',
  `tmax_f` decimal(5,2) DEFAULT NULL,
  `tmin_f` decimal(5,2) DEFAULT NULL,
  `tavg_f` decimal(5,2) DEFAULT NULL,
  `rh_mean` decimal(5,2) DEFAULT NULL,
  `dewpoint_f` decimal(5,2) DEFAULT NULL,
  `wind_mph` decimal(5,2) DEFAULT NULL,
  `wind_mph_max` decimal(5,2) DEFAULT NULL,
  `wind_dir_deg` decimal(5,2) DEFAULT NULL,
  `rain_in` decimal(6,3) DEFAULT NULL COMMENT 'Best available (NOAA preferred, ASOS fallback)',
  `rain_in_asos` decimal(6,3) DEFAULT NULL COMMENT 'ASOS reported precipitation',
  `noaa_prcp_in` decimal(6,3) DEFAULT NULL COMMENT 'NOAA GHCND official precipitation',
  `alti_inhg` decimal(6,3) DEFAULT NULL,
  `solar_irradiance` decimal(6,3) DEFAULT NULL COMMENT 'GHI or Ra in MJ/m²/day',
  `solar_source` enum('NASA_POWER','RA_FALLBACK') DEFAULT 'RA_FALLBACK',
  `ra_mj_m2` decimal(6,3) DEFAULT NULL COMMENT 'Extraterrestrial radiation (always calculated)',
  `et_est_in` decimal(6,3) DEFAULT NULL COMMENT 'Penman-Monteith ET₀ in inches/day',
  `created_at` timestamp NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`date`),
  KEY `idx_date_desc` (`date` DESC),
  KEY `idx_station_date` (`station`,`date`),
  KEY `idx_solar_source` (`solar_source`),
  KEY `idx_updated` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Daily weather aggregates from ASOS + NOAA + NASA POWER';
/*!40101 SET character_set_client = @saved_cs_client */;
