"""
Shared configuration loader for the pipeline scripts.

Search order:
  1. $WEATHER_DASHBOARD_CONFIG
  2. /etc/weather-dashboard/config.ini
  3. ~/.config/weather-dashboard/config.ini

See config.example.ini in the repo root for the expected sections and keys.
"""

import configparser
import os
from pathlib import Path

SEARCH_PATHS = [
    os.environ.get("WEATHER_DASHBOARD_CONFIG"),
    "/etc/weather-dashboard/config.ini",
    str(Path.home() / ".config" / "weather-dashboard" / "config.ini"),
]


def load_config() -> configparser.ConfigParser:
    for p in SEARCH_PATHS:
        if p and os.path.isfile(p):
            cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
            cfg.read(p)
            cfg.path = p  # handy for error messages
            return cfg
    raise FileNotFoundError(
        "No config file found. Copy config.example.ini to /etc/weather-dashboard/config.ini "
        "or set WEATHER_DASHBOARD_CONFIG."
    )


def db_config(cfg: configparser.ConfigParser, writer: bool = False) -> dict:
    """mysql.connector kwargs. writer=True selects the read/write user."""
    db = cfg["database"]
    return {
        "host": db.get("host", "localhost"),
        "database": db.get("name", "weather"),
        "user": db.get("writer_user") if writer else db.get("user"),
        "password": db.get("writer_password") if writer else db.get("password"),
    }
