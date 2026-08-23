"""Polymarket Gamma client. See 01-data-pipeline.md sec6.

Gamma only (never CLOB/Data -- sec6.2). No auth for reads (sec6.3).

The single most dangerous failure mode in the whole pipeline lives here (sec6.5):
name search returns a stale, closed, already-settled market from a prior season
with a plausible, confident, completely wrong price. Every event this module
returns has been through resolve_event()'s assertions; nothing calls the raw
Gamma response directly from snapshot.py.
"""

import json
import urllib.parse
from datetime import datetime, timezone

from . import httpcache
from .driver_map import polymarket_name_to_code, POLYMARKET_NON_DRIVER_OUTCOMES

GAMMA_BASE = "https://gamma-api.polymarket.com"


class StaleMarketError(Exception):
    pass


def _events_by_slug(slug, cache_dir, force_refresh=False):
    url = f"{GAMMA_BASE}/events?" + urllib.parse.urlencode({"slug": slug})
    return httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)


def _events_by_tag(tag_slug, cache_dir, force_refresh=False):
    url = f"{GAMMA_BASE}/events?" + urllib.parse.urlencode(
        {"tag_slug": tag_slug, "closed": "false", "limit": 100}
    )
    return httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)


def resolve_event(slug, expected_race_date, cache_dir, fallback_title_contains=None, tag_slug="f1",
                   force_refresh=False, k=1):
    """Resolve the live event for one race's market, refusing anything stale.

    expected_race_date: "YYYY-MM-DD", used only to sanity-check endDate is not in
    the past relative to the race.

    force_refresh: bypass the disk cache. Prices move; unlike Jolpica's grid/form/
    track-history, replaying a cached response here silently reports stale odds
    with no error (01-data-pipeline.md sec8.3's pre-lights-out re-snapshot depends
    on this actually being true).

    k: the market's outcome-of-K shape (1 for winner/fastest-lap, 3 for podium,
    10 for points -- 04-outcome-expansion-algo.md sec6.4/sec8.2). Governs only
    the degenerate-price check below; k=1 is byte-for-byte the original
    single-winner check.
    """
    body, meta = _events_by_slug(slug, cache_dir, force_refresh=force_refresh)
    event = body[0] if body else None

    if event is None and fallback_title_contains:
        body2, meta2 = _events_by_tag(tag_slug, cache_dir, force_refresh=force_refresh)
        candidates = [
            e for e in body2
            if fallback_title_contains.lower() in e.get("title", "").lower()
        ]
        if len(candidates) == 1:
            event, meta = candidates[0], meta2
        elif len(candidates) > 1:
            raise StaleMarketError(
                f"tag_slug fallback matched {len(candidates)} open events for "
                f"{fallback_title_contains!r}; need a slug, not a title match"
            )

    if event is None:
        raise StaleMarketError(f"no Polymarket event found for slug {slug!r}")

    if event.get("closed") is not False:
        raise StaleMarketError(f"Polymarket event {event.get('slug')} is closed=={event.get('closed')}")

    end_date = datetime.fromisoformat(event["endDate"].replace("Z", "+00:00"))
    race_date = datetime.fromisoformat(expected_race_date + "T00:00:00+00:00")
    if end_date < race_date:
        raise StaleMarketError(
            f"Polymarket event endDate {event['endDate']} is before the race date {expected_race_date}"
        )

    markets = parse_markets(event)
    driver_markets = [m for m in markets if m["code"] is not None]
    if not driver_markets:
        raise StaleMarketError(f"Polymarket event {event.get('slug')} has no driver markets")

    # 04-outcome-expansion-algo.md sec6.4: a live K-of-N market can plausibly
    # have up to K-1 near-certain legs without being settled (e.g. a dominant
    # favourite's podium spot effectively locked in); K or more means the
    # market already knows the full top-K, which is the stale/settled trap
    # sec3/01 sec6.5 warns about. At k=1 this is exactly the original
    # single-outcome-at-0.999 check -- same threshold, same trigger condition
    # ("any" == "count >= 1").
    threshold = 0.999 if k == 1 else 0.99
    near_certain = sum(1 for m in driver_markets if m["mid"] is not None and m["mid"] >= threshold)
    if near_certain >= k:
        raise StaleMarketError(
            f"Polymarket event {event.get('slug')} has {near_certain} outcome(s) priced >={threshold} "
            f"against k={k} -- looks settled, not live"
        )

    return event, meta, markets


def parse_markets(event):
    """Flatten event['markets'] into per-driver mid prices.

    Only active markets carry real prices; the negRisk group also contains
    inactive placeholder legs ("Other", "Driver A".."Driver E") which are kept
    as a distinct, codeless pseudo-entry per sec8.2 rather than silently dropped
    or run through the name->code table.
    """
    out = []
    for m in event["markets"]:
        name = m["groupItemTitle"]
        active = bool(m.get("active"))
        code = None if name in POLYMARKET_NON_DRIVER_OUTCOMES else polymarket_name_to_code(name)

        best_bid = m.get("bestBid")
        best_ask = m.get("bestAsk")
        raw_outcome_prices = json.loads(m["outcomePrices"]) if m.get("outcomePrices") else None
        last_trade = m.get("lastTradePrice")

        mid = None
        if active:
            if best_bid is not None and best_ask is not None:
                mid = (float(best_bid) + float(best_ask)) / 2.0
            elif raw_outcome_prices is not None:
                mid = float(raw_outcome_prices[0])
            elif last_trade is not None:
                mid = float(last_trade)

        out.append({
            "name": name,
            "code": code,
            "active": active,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "outcome_prices_raw": raw_outcome_prices,
            "last_trade_price": last_trade,
            "mid": mid,
            "volume": m.get("volumeNum"),
            "condition_id": m.get("conditionId"),
        })
    return out


def normalize(markets, k=1):
    """Proportional de-vig over active driver markets only (01-data-pipeline.md
    sec8.4, generalized in 04-outcome-expansion-algo.md sec6.4/sec8.2 for K-of-N
    markets like podium/points, whose raw mids sum to ~K rather than ~1).

    Returns (overround, {code: normalized_probability}), where overround is the
    raw sum (not divided by k) so it stays comparable to how sec8.4 already
    records/logs it. Raw values are left untouched in the market entries the
    caller already has.
    """
    active_driver = [m for m in markets if m["active"] and m["code"] is not None and m["mid"] is not None]
    overround = sum(m["mid"] for m in active_driver)
    normalized = {m["code"]: m["mid"] * k / overround for m in active_driver}
    return overround, normalized
