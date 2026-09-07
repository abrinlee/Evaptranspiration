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
    $start = isset($_GET['start']) ? $_GET['start'] : null;
    $end   = isset($_GET['end'])   ? $_GET['end']   : null;

    if (!$start || !$end) {
        throw new Exception("Both 'start' and 'end' date parameters are required (YYYY-MM-DD).");
    }

    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $start) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $end)) {
        throw new Exception("Date parameters must be in YYYY-MM-DD format.");
    }

    $conn = new mysqli($db_host, $db_user, $db_pass, $db_name);
    if ($conn->connect_error) {
        throw new Exception("Connection failed: " . $conn->connect_error);
    }

    $stmt = $conn->prepare(
        "SELECT date, et_est_in, rain_in,
                tmax_f, tmin_f, tavg_f,
                rh_mean, wind_mph, alti_inhg,
                solar_irradiance, ra_mj_m2
         FROM weather_daily
         WHERE date >= ? AND date <= ?
         ORDER BY date ASC"
    );
    $stmt->bind_param('ss', $start, $end);
    $stmt->execute();
    $result = $stmt->get_result();

    $data = [];
    while ($row = $result->fetch_assoc()) {
        $et = floatval($row['et_est_in']);
        $precip = floatval($row['rain_in']);
        $data[] = [
            'date'          => $row['date'],
            'et'            => $et,
            'precipitation' => $precip,
            'balance'       => $precip - $et,
            'temp_max'      => floatval($row['tmax_f']),
            'temp_min'      => floatval($row['tmin_f']),
            'temp_avg'      => floatval($row['tavg_f']),
            'humidity'      => floatval($row['rh_mean']),
            'wind_speed'    => floatval($row['wind_mph']),
            'pressure'      => floatval($row['alti_inhg']),
            'solar_radiation' => floatval($row['solar_irradiance']),
            'extraterrestrial_radiation' => floatval($row['ra_mj_m2'])
        ];
    }

    $stmt->close();
    $conn->close();

    echo json_encode([
        'success' => true,
        'data'    => $data,
        'count'   => count($data)
    ]);

} catch (Exception $e) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'error'   => $e->getMessage()
    ]);
}
?>
