"""Open-Meteo client -- forecast + historical archive. See 01-data-pipeline.md sec5.

Timezone is always passed explicitly (sec5.4: defaulting to UTC silently reads the
wrong local hours). The archive endpoint has no precipitation_probability field --
only observed precipitation in mm -- so wet-race history must be defined on
observed precipitation, never on a probability that doesn't exist there.

forecast() asks for no `models=` and gets the provider's own blend. It is kept
for anything that wants that single series; the snapshot path uses
forecast_ensemble() instead, per 06-weather-ensemble-signal.md sec3.
"""

import urllib.parse

from .invariants import require
from . import httpcache

FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_FIELDS = [
    "temperature_2m",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "relative_humidity_2m",
]

# 06 sec3: four independent operational centres, all verified global (sec3.1) --
# no per-venue model list is needed, and regional models (ICON-EU, HRRR) are
# ruled out because the calendar is not European.
ENSEMBLE_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless"]


def forecast(lat, lon, date, tz, cache_dir, force_refresh=False):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_FIELDS),
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


def forecast_ensemble(lat, lon, start_date, end_date, tz, cache_dir, models=None,
                      force_refresh=False):
    """Same forecast endpoint, but naming four models explicitly -> per-model series.

    Returns ({model: {field: [values]}}, units, meta). Every model carries its
    own copy of the shared `time` array so a caller can index one model's series
    without reaching back into the response. `units` is the response's
    hourly_units with the model suffix stripped -- sec5.4 says read the units
    rather than assume them, and they are identical across the four models.

    06 sec3.1's gotcha, verified live again 2026-09-04: with `models=` set the
    response suffixes every *value* field with the model name
    (`precipitation_probability_ecmwf_ifs025`) while `time` stays unsuffixed and
    shared. Code that reads the bare keys finds nothing -- this is not a
    "just add a query param" change.

    What is checked here is shape: every model present, every value array
    parallel to `time`. sec3.3's silent-null gap -- per-model keys full of nulls
    under an HTTP 200 -- is checked by the caller instead, because whether a null
    matters depends on whether it lands inside the race window, which this
    function does not know.
    """
    models = list(models or ENSEMBLE_MODELS)
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_FIELDS),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": tz,
        "models": ",".join(models),
    }
    url = FORECAST_BASE + "?" + urllib.parse.urlencode(params)
    body, meta = httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)

    hourly = body["hourly"]
    times = hourly["time"]
    series = {}
    for m in models:
        row = {"time": list(times)}
        for field in HOURLY_FIELDS:
            key = f"{field}_{m}"
            require(
                key in hourly,
                f"Open-Meteo returned no {key!r}: model {m} is missing from the "
                f"ensemble response (06 sec3.1 -- fields are suffixed per model)",
            )
            vals = hourly[key]
            require(
                len(vals) == len(times),
                f"Open-Meteo {key!r} has {len(vals)} values against {len(times)} "
                f"timestamps -- the hourly arrays are not parallel",
            )
            row[field] = list(vals)
        series[m] = row

    require(
        len(series) == len(models),
        f"expected {len(models)} models in the ensemble response, got {len(series)}",
    )

    units = {}
    for key, unit in (body.get("hourly_units") or {}).items():
        for m in models:
            if key.endswith(f"_{m}"):
                units[key[: -len(m) - 1]] = unit
                break
        else:
            units[key] = unit

    return series, units, meta
