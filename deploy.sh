#!/bin/bash
# Deploy the dashboard and its API to the web root. Run with sudo.
#
#   sudo ./deploy.sh                # uses WEB_ROOT below
#   sudo WEB_ROOT=/var/www/html ./deploy.sh
#
# historical_dashboard.html is not deployed by this script (it is heavy). It must be
# served from the same host as api/ because it fetches /api/ by absolute path; copy it
# into WEB_ROOT by hand if you want it (see README, "Optional pieces").
# The API reads credentials from /etc/weather-dashboard/config.ini (see README).

set -e
WEB_ROOT="${WEB_ROOT:-/var/www/bookstack/public}"
WEB_USER="${WEB_USER:-www-data}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying to $WEB_ROOT ..."
install -o "$WEB_USER" -g "$WEB_USER" -m 644 "$SRC/dashboard.html" "$WEB_ROOT/dashboard.html"
install -d -o "$WEB_USER" -g "$WEB_USER" -m 755 "$WEB_ROOT/api"
install -o "$WEB_USER" -g "$WEB_USER" -m 644 "$SRC/api/et-data.php"    "$WEB_ROOT/api/et-data.php"
install -o "$WEB_USER" -g "$WEB_USER" -m 644 "$SRC/api/et-history.php" "$WEB_ROOT/api/et-history.php"

echo "Deployment complete:"
ls -lh "$WEB_ROOT/dashboard.html" "$WEB_ROOT/api/"*.php
