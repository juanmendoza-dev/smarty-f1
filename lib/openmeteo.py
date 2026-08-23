"""Open-Meteo client -- forecast + historical archive. See 01-data-pipeline.md sec5.

Timezone is always passed explicitly (sec5.4: defaulting to UTC silently reads the
wrong local hours). The archive endpoint has no precipitation_probability field --
only observed precipitation in mm -- so wet-race history must be defined on
observed precipitation > 0, never on a probability that doesn't exist there.
"""

import urllib.parse

from . import httpcache

FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"


def forecast(lat, lon, date, tz, cache_dir, force_refresh=False):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,relative_humidity_2m",
        "start_date": date,
        "end_date": date,
        "timezone": tz,
    }
    url = FORECAST_BASE + "?" + urllib.parse.urlencode(params)
    return httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)


def archive(lat, lon, start_date, end_date, tz, cache_dir):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation",
        "start_date": start_date,
        "end_date": end_date,
        "timezone": tz,
    }
    url = ARCHIVE_BASE + "?" + urllib.parse.urlencode(params)
    return httpcache.cached_get_json(url, cache_dir)
