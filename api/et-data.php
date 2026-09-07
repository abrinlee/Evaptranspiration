<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');

// Database configuration comes from config.ini (see config.example.ini).
// Search order: $WEATHER_DASHBOARD_CONFIG, then /etc/weather-dashboard/config.ini.
$cfg_path = getenv('WEATHER_DASHBOARD_CONFIG') ?: '/etc/weather-dashboard/config.ini';
$cfg = @parse_ini_file($cfg_path, true, INI_SCANNER_RAW);
if (!$cfg || empty($cfg['database'])) {
    http_response_code(500);
    echo json_encode(['success' => false, 'error' => "Config file not readable: $cfg_path"]);
    exit;
}
$db_host = $cfg['database']['host'] ?? 'localhost';
$db_user = $cfg['database']['user'];
$db_pass = $cfg['database']['password'];
$db_name = $cfg['database']['name'] ?? 'weather';

try {
    // Get days parameter (default to 7)
    $days = isset($_GET['days']) ? intval($_GET['days']) : 7;

    // Validate days parameter
    if ($days <= 0 || $days > 365) {
        throw new Exception("Invalid days parameter. Must be between 1 and 365.");
    }

    // Connect to database
    $conn = new mysqli($db_host, $db_user, $db_pass, $db_name);

    if ($conn->connect_error) {
        throw new Exception("Connection failed: " . $conn->connect_error);
    }

    // Fetch all weather data for specified number of days
    $query = "SELECT date, et_est_in, rain_in,
                     tmax_f, tmin_f, tavg_f,
                     rh_mean, wind_mph, alti_inhg,
                     solar_irradiance, ra_mj_m2
              FROM weather_daily
              WHERE date >= DATE_SUB(CURDATE(), INTERVAL $days DAY)
              ORDER BY date ASC";

    $result = $conn->query($query);

    if (!$result) {
        throw new Exception("Query failed: " . $conn->error);
    }

    $data = [];

    while($row = $result->fetch_assoc()) {
        $et = floatval($row['et_est_in']);
        $precip = floatval($row['rain_in']);
        $balance = $precip - $et;

        $data[] = [
            'date' => $row['date'],
            'et' => $et,
            'precipitation' => $precip,
            'balance' => $balance,
            'temp_max' => floatval($row['tmax_f']),
            'temp_min' => floatval($row['tmin_f']),
            'temp_avg' => floatval($row['tavg_f']),
            'humidity' => floatval($row['rh_mean']),
            'wind_speed' => floatval($row['wind_mph']),
            'pressure' => floatval($row['alti_inhg']),
            'solar_radiation' => floatval($row['solar_irradiance']),
            'extraterrestrial_radiation' => floatval($row['ra_mj_m2'])
        ];
    }

    $conn->close();

    // Return success response
    echo json_encode([
        'success' => true,
        'data' => $data,
        'count' => count($data)
    ]);

} catch (Exception $e) {
    // Return error response
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'error' => $e->getMessage()
    ]);
}
?>
