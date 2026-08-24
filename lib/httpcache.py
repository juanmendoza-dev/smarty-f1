"""Disk-backed HTTP GET cache, keyed by exact URL.

Every response is cached to data/cache/<sha256(url)>.json as {"meta": ..., "body": ...}.
Repeat calls for the same URL read from disk instead of hitting the network again,
per 01-data-pipeline.md's mandatory caching policy for Jolpica (and applied here to
every source, since it costs nothing and gives every snapshot a network-call audit
trail for free).
"""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "f1-prediction-model/0.1 (personal research project)"

# 01-data-pipeline.md sec4.3: Jolpica allows 4 requests/second burst, and the
# documented limits are "subject to change, and will decrease in the future".
# A single snapshot needs ~30 requests and never came close, but the A3
# backfill fires ~25 per race back-to-back across hundreds of races
# (05-trained-model.md sec5.3), which would blow the burst cap immediately.
#
# Throttling here rather than in the callers means every source gets it for
# free and no caller can forget. Only real network fetches are spaced; cache
# hits are not delayed, so a warm re-run stays as fast as it was.
MIN_FETCH_INTERVAL = 0.25

_throttle_lock = threading.Lock()
_last_fetch = [0.0]


def _throttle():
    with _throttle_lock:
        wait = MIN_FETCH_INTERVAL - (time.monotonic() - _last_fetch[0])
        if wait > 0:
            time.sleep(wait)
        _last_fetch[0] = time.monotonic()


class HttpError(Exception):
    pass


def _cache_path(cache_dir, url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, key + ".json")


def cached_get_json(url, cache_dir, timeout=15, force_refresh=False):
    """GET url, parse the response as JSON, cache raw body + metadata to disk.

    Returns (body, meta) where meta = {"url", "status", "timestamp", "cached"}.
    Raises HttpError on a non-200 status (network errors surface as their
    original urllib exceptions).
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir, url)

    if not force_refresh and os.path.exists(path):
        with open(path) as f:
            entry = json.load(f)
        meta = dict(entry["meta"])
        meta["cached"] = True
        return entry["body"], meta

    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()

    body = json.loads(raw)
    meta = {"url": url, "status": status, "timestamp": timestamp, "cached": False}

    if status == 200:
        with open(path, "w") as f:
            json.dump({"meta": meta, "body": body}, f)

    if status != 200:
        raise HttpError(f"GET {url} returned {status}: {raw[:300]!r}")

    return body, meta
