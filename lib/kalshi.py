"""Kalshi client -- regulated US market. See 01-data-pipeline.md sec7.

Market-data reads need no auth (sec7.3); this module never touches the RSA
signing path, which is trading-only and out of scope for Lane A.

Driver identity comes from the market TICKER suffix (KXF1RACE-DUTGP26-ANT -> ANT),
never from no_sub_title, which carries full names that don't match any other
source's format (sec8.2's "Andrea Kimi Antonelli" vs "Kimi Antonelli" trap).
"""

import urllib.parse

from . import httpcache

BASE = "https://external-api.kalshi.com/trade-api/v2"


class StaleMarketError(Exception):
    pass


def discover_event_ticker(series_ticker, cache_dir, force_refresh=False):
    url = f"{BASE}/events?" + urllib.parse.urlencode({"series_ticker": series_ticker, "status": "open"})
    body, meta = httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)
    events = body.get("events", [])
    return events, meta


def get_markets(event_ticker, cache_dir, force_refresh=False):
    url = f"{BASE}/markets?" + urllib.parse.urlencode({"event_ticker": event_ticker, "limit": 100})
    return httpcache.cached_get_json(url, cache_dir, force_refresh=force_refresh)


def resolve_markets(series_ticker, event_ticker, expected_race_date, cache_dir, force_refresh=False,
                     k=1):
    """Fetch + validate one race's Kalshi markets.

    expected_race_date: "YYYY-MM-DD" -- must match the date component of every
    active market's expected_expiration_time. close_time is a long-stop and is
    deliberately NOT checked against the race date (sec7.5).

    force_refresh: bypass the disk cache -- see polymarket.resolve_event's
    docstring, same reasoning applies here.

    k: the market's outcome-of-K shape (1 for winner/fastest-lap, 3 for podium,
    10 for points -- 04-outcome-expansion-algo.md sec6.4/sec8.2). Governs only
    the degenerate-price check below.
    """
    events, events_meta = discover_event_ticker(series_ticker, cache_dir, force_refresh=force_refresh)
    tickers = {e["event_ticker"] for e in events}
    if event_ticker not in tickers:
        raise StaleMarketError(
            f"Kalshi event {event_ticker!r} not found among open events for series "
            f"{series_ticker!r}: {sorted(tickers)}"
        )

    body, markets_meta = get_markets(event_ticker, cache_dir, force_refresh=force_refresh)
    markets = body["markets"]
    if not markets:
        raise StaleMarketError(f"Kalshi event {event_ticker} returned no markets")

    parsed = []
    for m in markets:
        ticker = m["ticker"]
        suffix = ticker.rsplit("-", 1)[-1]
        status = m["status"]
        expiration = m.get("expected_expiration_time", "")

        if status != "active":
            raise StaleMarketError(f"Kalshi market {ticker} status={status!r}, expected 'active'")
        if not expiration.startswith(expected_race_date):
            raise StaleMarketError(
                f"Kalshi market {ticker} expected_expiration_time={expiration!r} "
                f"does not match race date {expected_race_date}"
            )

        yes_bid = float(m["yes_bid_dollars"])
        yes_ask = float(m["yes_ask_dollars"])
        last_price = float(m["last_price_dollars"])
        mid = (yes_bid + yes_ask) / 2.0

        parsed.append({
            "ticker": ticker,
            "code": suffix,
            "no_sub_title": m["no_sub_title"],
            "status": status,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "last_price": last_price,
            "mid": mid,
            "volume": m.get("volume_fp"),
            "open_interest": m.get("open_interest_fp"),
            "expected_expiration_time": expiration,
        })

    # 04-outcome-expansion-algo.md sec6.4: degenerate-price check for K-of-N
    # markets (podium/points), same logic as polymarket.resolve_event. This
    # module never had this check for k=1 -- Kalshi's winner market relies on
    # the status/expiration checks above alone (01-data-pipeline.md sec7.5:
    # ticker-based resolution is "much safer than Polymarket" and doesn't need
    # it) -- so it's deliberately skipped at k=1 to leave that already-verified
    # path's behavior untouched.
    if k > 1:
        threshold = 0.99
        near_certain = sum(1 for m in parsed if m["mid"] >= threshold)
        if near_certain >= k:
            raise StaleMarketError(
                f"Kalshi event {event_ticker} has {near_certain} outcome(s) priced >={threshold} "
                f"against k={k} -- looks settled, not live"
            )

    return parsed, events_meta, markets_meta


def normalize(markets, k=1):
    """Proportional de-vig (01-data-pipeline.md sec8.4, generalized in
    04-outcome-expansion-algo.md sec6.4/sec8.2 for K-of-N markets whose raw
    mids sum to ~K rather than ~1). Prefer mid over last_price for illiquid legs.
    """
    overround = sum(m["mid"] for m in markets)
    normalized = {m["code"]: m["mid"] * k / overround for m in markets}
    return overround, normalized
