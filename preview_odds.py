#!/usr/bin/env python3
"""Peek at live Polymarket + Kalshi odds without persisting anything.

Calls snapshot.py's build_markets() directly -- same fetch + normalize logic
a real snapshot uses, not a reimplementation -- with force_refresh=True and a
temp cache dir that's deleted the moment this exits. Nothing lands in
data/cache or data/snapshots. Prints a table and discards everything; run it
again any time for a fresh read.
"""

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snapshot import RACE_CONFIG_FIELDS, build_markets, resolve_race_config
from lib import jolpica


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    # Same race-config surface as snapshot.py -- these used to be four defaults
    # pinned to the Dutch GP, which meant a bare run silently previewed last
    # month's odds rather than the race you were thinking about.
    ap.add_argument("--race", help="path to a races/*.json, e.g. races/2026-monza.json")
    ap.add_argument("--polymarket-slug")
    ap.add_argument("--kalshi-series")
    ap.add_argument("--kalshi-event-ticker")
    ap.add_argument("--polymarket-fallback-title")
    ap.add_argument("--season", type=int)
    ap.add_argument("--round", type=int)
    ap.add_argument("--race-date", help="defaults to Jolpica's date for the config's season/round")
    args = ap.parse_args()

    for field in RACE_CONFIG_FIELDS:
        if not hasattr(args, field):
            setattr(args, field, None)
    cfg = resolve_race_config(args)

    with tempfile.TemporaryDirectory(prefix="f1-odds-preview-") as cache_dir:
        race_date = args.race_date
        if race_date is None:
            # Derived, not configured: a hand-typed date that disagrees with the
            # schedule is exactly what the venues' staleness checks are for, and
            # deriving it means the two can't drift apart.
            race, _ = jolpica.race_info(cfg["season"], cfg["round"], cache_dir)
            race_date = race["date"]
        markets, _ = build_markets(
            cfg["polymarket_slug"], race_date, cfg["kalshi_series"], cfg["kalshi_event_ticker"],
            cache_dir, force_refresh=True,
            fallback_title_contains=cfg["polymarket_fallback_title"],
        )

    pm_by_code = markets["polymarket"]["by_code"]
    kx_by_code = markets["kalshi"]["by_code"]
    mean = markets["market_mean"]

    print(f"{cfg['polymarket_slug']}  ({race_date})")
    print(f"polymarket overround={markets['polymarket']['overround']:.4f}  "
          f"kalshi overround={markets['kalshi']['overround']:.4f}")
    print()
    print(f"{'code':5} {'poly':>7} {'kalshi':>7} {'mean':>7} {'spread':>7}")

    fmt = lambda v: f"{v * 100:6.1f}%" if v is not None else "   n/a "
    for code in sorted(mean, key=lambda c: -mean[c]):
        pm = pm_by_code.get(code, {}).get("normalized")
        kx = kx_by_code.get(code, {}).get("normalized")
        spread = abs(pm - kx) if (pm is not None and kx is not None) else None
        spread_fmt = f"{spread * 100:6.1f}" if spread is not None else "   n/a"
        print(f"{code:5} {fmt(pm)} {fmt(kx)} {fmt(mean[code])} {spread_fmt}")

    print("\nlive preview only -- nothing written to disk")


if __name__ == "__main__":
    main()
