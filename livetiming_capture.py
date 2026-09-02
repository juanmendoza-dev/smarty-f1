#!/usr/bin/env python3
"""Capture the F1 live-timing feed to local disk. 03 sec6 / sec11 / sec13.

Lane B's B0 client. Connects to livetiming.formula1.com/signalrcore
unauthenticated (03 sec6.1), subscribes to the sec6.3 channel set, folds the
feed into immutable per-car ticks (sec7), and writes raw frames + parsed ticks
+ a run log under data/live/ (gitignored, sec11.2).

Read 03 sec4-5 before running this: the authorized scope is personal
research/development only -- no hosted deployment, no running outside a live
session window (sec6.5.2). A 401/403/429 is a Stop: the client exits and does
not retry (sec9.3). No evasion, ever (sec5).

  # environment: use the 3.12 venv -- requests + websockets live there
  .venv312/bin/python livetiming_capture.py --session-start 2026-09-04T11:30:00Z
  .venv312/bin/python livetiming_capture.py --replay data/live/raw/<slug>/<file>.jsonl

The first live run IS 03 sec13's acceptance run: capture one full practice
session, then check the sec13 items against the raw file. Nothing here is
verified against the real wire until that happens -- every sec6-7 claim is
UNVERIFIED in this project's idiom until then.
"""

import argparse
import sys

from lib.livetiming_client import LiveTimingClient, CHANNELS, parse_session_start


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session-start", default=None,
                    help="ISO 8601 UTC start of the session (03 sec6.5.2 window enforcement)")
    ap.add_argument("--tick-interval", type=float, default=1.0,
                    help="seconds between emitted ticks (default 1.0)")
    ap.add_argument("--capture-root", default="data/live",
                    help="where raw/ ticks/ logs/ go (default data/live, gitignored)")
    ap.add_argument("--replay", default=None,
                    help="path to a raw capture .jsonl -- replay it offline, no network")
    args = ap.parse_args(argv)

    client = LiveTimingClient(
        capture_root=args.capture_root,
        tick_interval=args.tick_interval,
        session_start=parse_session_start(args.session_start),
        channels=CHANNELS,
    )

    if args.replay:
        client.run_replay(args.replay)
        return 0

    try:
        client.run_live()
    except KeyboardInterrupt:
        print("\ninterrupted -- closing capture", flush=True)
        if client._capture:
            client._capture.close()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
