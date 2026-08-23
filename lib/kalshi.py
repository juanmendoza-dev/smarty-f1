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


def resolve_markets(series_ticker, event_ticker, expected_race_date, cache_dir, force_refresh=False):
    """Fetch + validate one race's Kalshi markets.

    expected_race_date: "YYYY-MM-DD" -- must match the date component of every
    active market's expected_expiration_time. close_time is a long-stop and is
    deliberately NOT checked against the race date (sec7.5).

    force_refresh: bypass the disk cache -- see polymarket.resolve_event's
    docstring, same reasoning applies here.
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

    return parsed, events_meta, markets_meta


def normalize(markets):
    """Proportional de-vig (sec8.4). Prefer mid over last_price for illiquid legs."""
    overround = sum(m["mid"] for m in markets)
    normalized = {m["code"]: m["mid"] / overround for m in markets}
    return overround, normalized
