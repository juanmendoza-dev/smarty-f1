"""Disk-backed HTTP GET cache, keyed by exact URL.

Every response is cached to data/cache/<sha256(url)>.json as {"meta": ..., "body": ...}.
Repeat calls for the same URL read from disk instead of hitting the network again,
per 01-data-pipeline.md's mandatory caching policy for Jolpica (and applied here to
every source, since it costs nothing and gives every snapshot a network-call audit
trail for free).
"""

import collections
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "f1-prediction-model/0.1 (personal research project)"

# 01-data-pipeline.md sec4.3: Jolpica documents TWO limits, 4 requests/second
# burst and 500/hour sustained, and says both are "subject to change, and will
# decrease in the future". They need different mechanisms and honouring only
# the burst cap is what a first pass at this got wrong: spacing requests 0.25s
# apart satisfies 4/s forever while still hitting 500 in about two minutes.
# The 2015 backfill duly died at round 7 with a 429.
#
# So: a minimum gap between fetches for the burst limit, and a sliding one-hour
# window for the sustained one. The hourly cap is set below the documented 500
# because the limit is shared with anything else touching the API and there is
# no X-RateLimit header to see how close we are (sec4.4) -- budget
# conservatively rather than reactively.
#
# Throttling here rather than in the callers means every source gets it for
# free and no caller can forget. Only real network fetches count; cache hits
# are neither delayed nor charged against the window, so a warm re-run stays
# as fast as it ever was and the test suite is unaffected.
MIN_FETCH_INTERVAL = 0.25
MAX_FETCHES_PER_HOUR = 450
WINDOW_SECONDS = 3600
RETRY_ON_429 = 3

_throttle_lock = threading.Lock()
_last_fetch = [0.0]
_fetch_times = collections.deque()


def _throttle(verbose=True):
    with _throttle_lock:
        now = time.monotonic()

        while _fetch_times and now - _fetch_times[0] >= WINDOW_SECONDS:
            _fetch_times.popleft()

        if len(_fetch_times) >= MAX_FETCHES_PER_HOUR:
            sleep_for = WINDOW_SECONDS - (now - _fetch_times[0]) + 1
            if verbose:
                print(f"  [rate limit] {len(_fetch_times)} fetches in the last hour; "
                      f"pausing {sleep_for / 60:.1f} min", flush=True)
            time.sleep(sleep_for)
            now = time.monotonic()
            while _fetch_times and now - _fetch_times[0] >= WINDOW_SECONDS:
                _fetch_times.popleft()

        wait = MIN_FETCH_INTERVAL - (now - _last_fetch[0])
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()

        _last_fetch[0] = now
        _fetch_times.append(now)


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

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(RETRY_ON_429 + 1):
        _throttle()
        timestamp = datetime.now(timezone.utc).isoformat()
        retry_after = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
            retry_after = e.headers.get("Retry-After")

        # A 429 means the throttle's estimate of the budget was wrong -- the
        # limits are documented as subject to decrease, and the quota is shared
        # with anything else hitting the API. Back off and retry rather than
        # dropping the race: a skipped race is a hole in the training set, and
        # holes that correlate with *when* you ran are the worst kind.
        if status == 429 and attempt < RETRY_ON_429:
            backoff = float(retry_after) if retry_after and retry_after.isdigit() else 60 * (attempt + 1)
            print(f"  [429] {url}\n        backing off {backoff:.0f}s "
                  f"(attempt {attempt + 1}/{RETRY_ON_429})", flush=True)
            time.sleep(backoff)
            continue
        break

    # Check the status BEFORE parsing. An error response is usually not JSON at
    # all -- a 429 from Jolpica has an empty body -- so parsing first turned
    # every rate-limit into JSONDecodeError("Expecting value: line 1 column 1"),
    # which says nothing about what actually went wrong and cost a whole
    # debugging pass to see through.
    if status != 200:
        raise HttpError(f"GET {url} returned {status}: {raw[:300]!r}")

    body = json.loads(raw)
    meta = {"url": url, "status": status, "timestamp": timestamp, "cached": False}

    with open(path, "w") as f:
        json.dump({"meta": meta, "body": body}, f)

    return body, meta
