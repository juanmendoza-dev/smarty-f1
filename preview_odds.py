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

from snapshot import build_markets


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--polymarket-slug", default="f1-dutch-grand-prix-winner-2026-08-23")
    ap.add_argument("--kalshi-series", default="KXF1RACE")
    ap.add_argument("--kalshi-event-ticker", default="KXF1RACE-DUTGP26")
    ap.add_argument("--race-date", default="2026-08-23")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="f1-odds-preview-") as cache_dir:
        markets, _ = build_markets(
            args.polymarket_slug, args.race_date, args.kalshi_series, args.kalshi_event_ticker,
            cache_dir, force_refresh=True,
        )

    pm_by_code = markets["polymarket"]["by_code"]
    kx_by_code = markets["kalshi"]["by_code"]
    mean = markets["market_mean"]

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
