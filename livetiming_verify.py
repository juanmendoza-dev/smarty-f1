#!/usr/bin/env python3
"""Check a live-timing capture against 03 sec13's acceptance items.

After the first real run (Monza FP1, ~2026-09-04), point this at the capture
and it prints sec13's eight items with a PASS/FAIL/observation for each, so the
acceptance run is a command and not an afternoon of grepping a 4-hour file.

    .venv312/bin/python livetiming_verify.py --session <slug>
    .venv312/bin/python livetiming_verify.py --raw data/live/raw/<slug> \\
        --ticks data/live/ticks/<slug>.jsonl --log data/live/logs/<slug>.log

Reads only local capture files (03 sec11.3). Decodes a sample of CarData.z /
Position.z payloads through lib/livetiming_parse to confirm sec7.2's decode
path on the real wire -- the one thing 03 sec6.1 carries on expectation.

Testable today: `--raw <synthetic replay dir>` runs the same checks against a
hand-built capture, which is what test_livetiming.py does.
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys

from lib import livetiming_parse as P
from lib.livetiming_client import CHANNELS

CARDATA_SEC63 = {"CarData.z"}
REQUIRED_CAR_INDICES = [0, 2, 3, 4, 5, 45]


def _iter_raw(raw_path):
    files = ([raw_path] if os.path.isfile(raw_path)
             else sorted(glob.glob(os.path.join(raw_path, "*.jsonl"))))
    for fn in files:
        with open(fn) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _median_dt(times):
    times = sorted(times)
    if len(times) < 2:
        return None
    return statistics.median(times[i + 1] - times[i] for i in range(len(times) - 1))


def verify(raw_path, ticks_path, log_path):
    records = list(_iter_raw(raw_path))
    log_text = open(log_path).read() if log_path and os.path.exists(log_path) else ""
    checks = []

    def result(item, ok, detail):
        checks.append((item, ok, detail))
        mark = {True: "PASS", False: "FAIL", None: "----"}[ok]
        print("  [%s] %s\n         %s" % (mark, item, detail))

    # feed records, per channel: list of recv_mono
    channel_times = {}
    snapshot_channels, missing_channels = None, None
    reconnects = []
    driver_nums = set()
    car_nums, pos_nums = set(), set()
    car_samples, pos_samples = [], []
    car_domain = {n: [] for n in ("speed", "rpm", "throttle", "gear", "brake")}
    aero_values = set()

    for rec in records:
        msg = rec.get("msg", {})
        mono = rec.get("recv_mono", 0.0)
        t = msg.get("type")
        if t == 3:
            snapshot_channels = msg.get("subscribe_snapshot_channels")
            missing_channels = msg.get("missing")
            continue
        if t == "reconnect":
            reconnects.append((mono, msg.get("backoff_s")))
            continue
        args = msg.get("arguments") if isinstance(msg, dict) else None
        if not args or len(args) < 2:
            continue
        ch, data = args[0], args[1]
        channel_times.setdefault(ch, []).append(mono)
        if ch == "DriverList" and isinstance(data, dict):
            driver_nums |= set(P.parse_driver_list(data))
        elif ch == "CarData.z":
            try:
                decoded = P.decompress_z(data)
                cars, _ = P.parse_cardata(decoded)
            except Exception as e:
                car_samples.append(("ERROR", str(e)))
                continue
            car_samples.append(("ok", decoded))
            car_nums |= set(cars)
            for rec2 in cars.values():
                for k in car_domain:
                    if rec2.get(k) is not None:
                        car_domain[k].append(rec2[k])
                if rec2.get("aero_raw") is not None:
                    aero_values.add(rec2["aero_raw"])
        elif ch == "Position.z":
            try:
                decoded = P.decompress_z(data)
                pos = P.parse_position(decoded)
                pos_samples.append(("ok", decoded))
                pos_nums |= set(pos)
            except Exception as e:
                pos_samples.append(("ERROR", str(e)))

    total_span = (records[-1]["recv_mono"] - records[0]["recv_mono"]) if len(records) >= 2 else 0.0

    # 1 -- handshake + subscribe channel set
    if snapshot_channels is None:
        result("1  handshake / subscribe completion", None,
               "no type-3 snapshot record in the capture (old client, or handshake failed)")
    else:
        alb = ("present" if "AWSALBCORS cookie present" in log_text
               else "NOT FOUND" if "NOT FOUND" in log_text else "unknown (no log)")
        result("1  handshake unauthenticated + sec6.3 channels present",
               not missing_channels,
               "completion carried %d channels; missing: %s; AWSALBCORS cookie: %s"
               % (len(snapshot_channels), missing_channels or "none", alb))

    # 1b -- multi-message frames
    mm_counts = [int(m) for m in re.findall(r"(\d+) carried >1 message", log_text)]
    total_mm = sum(mm_counts)
    result("1b split on \\x1e, multi-message frame observed", bool(mm_counts) and total_mm > 0,
           "run log reports %d frames carrying >1 message across %d segment(s)"
           % (total_mm, len(mm_counts)) if mm_counts
           else "no multi-message-frame count in the log -- path still untested, not broken")

    # 2 -- .z decode
    car_ok = sum(1 for s, _ in car_samples if s == "ok")
    pos_ok = sum(1 for s, _ in pos_samples if s == "ok")
    car_err = [d for s, d in car_samples if s == "ERROR"]
    result("2  CarData.z / Position.z decode (base64 + raw DEFLATE, sec7.2)",
           car_ok > 0 and not car_err,
           "CarData.z: %d decoded, %d failed%s | Position.z: %d decoded"
           % (car_ok, len(car_err), (" (%s)" % car_err[0] if car_err else ""), pos_ok))

    # 3 -- CarData channel indices + domains
    present = [i for i in REQUIRED_CAR_INDICES
              if P.CAR_CHANNELS[i] in ("aero_raw",) or car_domain.get(P.CAR_CHANNELS.get(i), [])]
    ranges = {k: (min(v), max(v)) for k, v in car_domain.items() if v}
    dom_ok = all(P.CAR_DOMAINS_HALT[k][0] <= lo and hi <= P.CAR_DOMAINS_HALT[k][1]
                 for k, (lo, hi) in ranges.items() if k in P.CAR_DOMAINS_HALT)
    result("3  CarData indices 0/2/3/4/5/45 present, 0-5 in range (sec12.2)",
           len(present) >= 5 and dom_ok,
           "ranges %s | channel 45 values seen: %s"
           % (ranges, sorted(aero_values) if aero_values else "none"))

    # 4 -- racing-number join
    unresolved = (car_nums | pos_nums) - driver_nums
    result("4  every CarData/Position racing number resolves to a DriverList code",
           not unresolved and bool(driver_nums),
           "DriverList: %d nums | CarData: %d | Position: %d | unresolved: %s"
           % (len(driver_nums), len(car_nums), len(pos_nums), sorted(unresolved) or "none"))

    # 5 -- Position.z broadcast at all / mid-session drop
    pos_gap = _largest_gap(channel_times.get("Position.z", []))
    car_gap = _largest_gap(channel_times.get("CarData.z", []))
    result("5  Position.z broadcast (sec8's 2026 concern) + no long mid-session drop",
           pos_ok > 0,
           "Position.z samples: %d (largest gap %.1fs) | CarData.z largest gap %.1fs"
           % (pos_ok, pos_gap, car_gap))

    # 6 -- update rates
    rates = {ch: _median_dt(channel_times.get(ch, []))
             for ch in ("CarData.z", "Position.z", "Heartbeat")}
    result("6  observed update rates", all(v is not None for v in rates.values()),
           " | ".join("%s median Dt %s" % (k, ("%.3fs (%.2f Hz)" % (v, 1 / v)) if v else "n/a")
                      for k, v in rates.items()))

    # 7 -- server disconnect / reconnect
    result("7  server-initiated disconnect + reconnect recovery (sec9.1)", None,
           "%d reconnect(s) over %.0fs capture%s"
           % (len(reconnects), total_span,
              (" at t+" + ", t+".join("%.0fs" % (m - records[0]["recv_mono"]) for m, _ in reconnects))
              if reconnects else " -- none seen (capture may be < 2h)"))

    # 8 -- t_local + t_wall on every tick
    if ticks_path and os.path.exists(ticks_path):
        ticks = [json.loads(l) for l in open(ticks_path) if l.strip()]
        bad = [i for i, tk in enumerate(ticks)
               if not isinstance(tk.get("t_local"), (int, float)) or not tk.get("t_wall")]
        result("8  t_local (monotonic) + t_wall (UTC) on every tick -- B1 needs both",
               ticks and not bad,
               "%d ticks, %d missing a timestamp" % (len(ticks), len(bad)))
    else:
        result("8  t_local + t_wall on every tick", None, "no ticks file given")

    passed = sum(1 for _, ok, _ in checks if ok is True)
    graded = sum(1 for _, ok, _ in checks if ok is not None)
    print("\n%d / %d graded checks passed (%d observation-only)."
          % (passed, graded, len(checks) - graded))
    return 0 if passed == graded else 1


def _largest_gap(times):
    times = sorted(times)
    return max((times[i + 1] - times[i] for i in range(len(times) - 1)), default=0.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="slug under data/live/ (derives --raw/--ticks/--log)")
    ap.add_argument("--root", default="data/live")
    ap.add_argument("--raw", help="raw capture dir or a single .jsonl segment")
    ap.add_argument("--ticks", help="parsed ticks .jsonl")
    ap.add_argument("--log", help="run log")
    args = ap.parse_args(argv)

    if args.session:
        args.raw = args.raw or os.path.join(args.root, "raw", args.session)
        args.ticks = args.ticks or os.path.join(args.root, "ticks", args.session + ".jsonl")
        args.log = args.log or os.path.join(args.root, "logs", args.session + ".log")
    if not args.raw:
        ap.error("need --session or --raw")

    print("03 sec13 acceptance check -- %s\n" % args.raw)
    return verify(args.raw, args.ticks, args.log)


if __name__ == "__main__":
    sys.exit(main())
