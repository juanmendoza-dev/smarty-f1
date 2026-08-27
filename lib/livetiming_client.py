"""Connection lifecycle for the F1 live-timing feed. 03 sec6.5 / sec9 / sec11.

Owns everything the transport (lib/signalr.py) and the tick assembler
(lib/livetiming_tick.py) deliberately do not: the reconnect/backoff state
machine (03 sec9.3), the ~2h server-close handling (sec9.1), heartbeat liveness
(sec9.2), the reconnect seam marker (sec9.4), the staleness clock (sec9.4),
session-change detection (sec9.5), and the local capture files (sec11).

The disconnect taxonomy is sec9.3's table, implemented as the exception
hierarchy in lib/signalr.py. Stop-class events (Refused, RateLimited, Gated,
Exhausted) exit the process non-zero with a message naming the section. The
client never retries past the ceiling, never falls back to another endpoint,
and never varies a header between attempts (sec5 / sec12.14).
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone

from lib import signalr
from lib.invariants import require
from lib.livetiming_tick import TickAssembler, STALENESS_LIMIT_S

# 03 sec6.3 -- subscribe to exactly this set and no more. Every entry is parsed
# into tick state or needed to interpret one that is.
CHANNELS = [
    "Heartbeat", "SessionInfo", "SessionData", "SessionStatus", "DriverList",
    "TimingData", "TimingDataF1", "CarData.z", "Position.z", "TrackStatus",
    "LapCount", "ExtrapolatedClock",
]

HEARTBEAT_DEAD_S = 30.0      # 03 sec9.2: no Heartbeat for 30s -> connection dead
PING_INTERVAL_S = 10.0
BACKOFF_START_S = 5.0        # 03 sec9.3: exponential from 5s, double, cap 60s
BACKOFF_CAP_S = 60.0
BACKOFF_CEILING = 8          # ~5 minutes of trying, then Stop (Exhausted)
CONNECT_LEAD_S = 60 * 60     # sec6.5.2: connect no earlier than T-60min
DISCONNECT_LAG_S = 30 * 60   # sec6.5.2: and no later than T+30min

DATA_ROOT = "data/live"
SESSION_END_STATES = {"Finished", "Finalised", "Ends"}


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _backoff_delays():
    """03 sec9.3: 5s, doubling, capped at 60s, +/-20% jitter, ceiling of 8
    consecutive failures. A successful connection resets the sequence."""
    for i in range(BACKOFF_CEILING):
        base = min(BACKOFF_START_S * (2 ** i), BACKOFF_CAP_S)
        yield base * (1.0 + random.uniform(-0.2, 0.2))


class StopClass(SystemExit):
    """A 03 sec9.3 Stop-class disconnect. Exits non-zero; never retried."""
    def __init__(self, section, detail):
        super().__init__("03 %s: %s -- stopping, no retry" % (section, detail))


class Capture:
    """The three local files per session, 03 sec11.1. A new segment (new file
    trio suffix) per reconnect, following FastF1's 2-hour-split convention.

    03 sec11.2: nothing written here is ever committed to git. data/live/ is
    gitignored with a comment saying why, and test_livetiming.py asserts it.
    """

    def __init__(self, root, session_key):
        self.slug = _slugify(session_key)
        self.raw_dir = os.path.join(root, "raw", self.slug)
        self.tick_dir = os.path.join(root, "ticks")
        self.log_dir = os.path.join(root, "logs")
        for d in (self.raw_dir, self.tick_dir, self.log_dir):
            os.makedirs(d, exist_ok=True)
        self._segment = 0
        self._raw = None
        self._ticks = open(os.path.join(self.tick_dir, self.slug + ".jsonl"), "a")
        self._log = open(os.path.join(self.log_dir, self.slug + ".log"), "a")
        self.new_segment()

    def new_segment(self):
        if self._raw is not None:
            self._raw.close()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._raw = open(os.path.join(self.raw_dir, "%s-seg%02d.jsonl" % (stamp, self._segment)), "a")
        self._segment += 1

    def raw(self, recv_wall, recv_mono, msg):
        """One message as received, before any semantic parsing (03 sec11.1:
        'written first, always, even when parsing fails')."""
        self._raw.write(json.dumps({"recv_wall": recv_wall, "recv_mono": recv_mono,
                                    "msg": msg}) + "\n")
        self._raw.flush()

    def tick(self, t):
        self._ticks.write(json.dumps({
            "session_key": t.session_key, "t_feed": t.t_feed, "t_local": t.t_local,
            "t_wall": t.t_wall, "track_status": t.track_status,
            "lap_current": t.lap_current, "lap_total": t.lap_total,
            "degraded": sorted(t.degraded), "gap_after_reconnect": t.gap_after_reconnect,
            "stale": t.stale,
            "cars": {code: vars(cs) for code, cs in t.cars.items()},
        }) + "\n")
        self._ticks.flush()

    def log(self, line):
        stamped = "%s  %s" % (_utc_now_iso(), line)
        self._log.write(stamped + "\n")
        self._log.flush()
        print(stamped, flush=True)

    def close(self):
        for f in (self._raw, self._ticks, self._log):
            try:
                f.close()
            except Exception:
                pass


def _slugify(session_key):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_key or "unknown"))


class LiveTimingClient:
    def __init__(self, capture_root=DATA_ROOT, tick_interval=1.0,
                 session_start=None, channels=None):
        self.capture_root = capture_root
        self.tick_interval = tick_interval
        self.session_start = session_start  # datetime or None
        self.channels = channels or CHANNELS
        self._capture = None
        self._assembler = TickAssembler()
        self._last_heartbeat = None
        self._last_ping = 0.0
        self._last_msg_mono = None
        self._next_tick_at = None
        self._pending_reconnect_marker = False
        self._multi_frame_seen = 0

    # -- live -------------------------------------------------------------

    def run_live(self):
        """03 sec9.3 state machine. Loops connect->consume->reconnect until a
        Stop class or a clean session end."""
        self._enforce_window()
        delays = _backoff_delays()
        while True:
            client = signalr.SignalRClient()
            try:
                self._log("connect: negotiate + subscribe, %d channels" % len(self.channels))
                snapshot = client.connect(self.channels)
                self._log("negotiate: AWSALBCORS cookie %s (03 sec6.2 / sec13 item 1)"
                          % ("present" if client.alb_cookie_seen else "NOT FOUND -- ALB behaviour changed"))
                self._on_snapshot(snapshot)
                delays = _backoff_delays()  # success resets backoff (sec9.3)
                ended = self._consume(client, source="live")
                self._multi_frame_seen += client.multi_message_frames
                self._log("segment closed: %d WS frames, %d carried >1 message (sec13 item 1b)"
                          % (client.frames_received, client.multi_message_frames))
                if ended:
                    self._log("session reached an end state -- disconnecting cleanly")
                    return
                self._log("socket closed (routine, sec9.1) -- reconnecting")
            except (signalr.Refused,) as e:
                raise StopClass("sec9.3", "refusal: %s" % e)
            except signalr.RateLimited as e:
                raise StopClass("sec9.3", "rate limited: %s" % e)
            except signalr.Gated as e:
                raise StopClass("sec6.4", "gated: %s" % e)
            except signalr.TransientError as e:
                self._log("transient: %s" % e)
            finally:
                client.close()

            try:
                delay = next(delays)
            except StopIteration:
                raise StopClass("sec9.3", "backoff ceiling of %d reached (Exhausted)"
                                % BACKOFF_CEILING)
            self._log("backoff %.1fs" % delay)
            if self._capture:
                self._capture.raw(_utc_now_iso(), time.monotonic(),
                                  {"type": "reconnect", "backoff_s": round(delay, 1)})
            time.sleep(delay)
            self._pending_reconnect_marker = True
            if self._capture:
                self._capture.new_segment()

    def _consume(self, client, source):
        """Drain the feed, fold messages into the assembler, emit ticks on the
        cadence. Returns True on a clean session end, False on a socket close
        that should reconnect. Raises TransientError on a heartbeat timeout."""
        for item in client.feed(recv_timeout=1.0):
            now = time.monotonic()
            if item is None:
                self._maybe_ping(client, now)
                self._check_heartbeat(now)
                self._maybe_emit(now)
                continue
            channel, data, ts = item
            self._capture.raw(_utc_now_iso(), now, {"type": 1, "target": "feed",
                                                    "arguments": [channel, data, ts]})
            self._last_msg_mono = now
            if channel == "Heartbeat":
                self._last_heartbeat = now
            if channel in ("SessionStatus", "SessionData"):
                if self._is_session_end(channel, data):
                    self._maybe_emit(now, force=True)
                    return True
            if channel == "SessionInfo":
                if self._session_changed(data):
                    self._log("SessionInfo.Path changed -- clearing state, new capture (sec9.5)")
                    self._rotate_session(data)
                    return False
            if channel in self._assembler.TICK_CHANNELS:
                self._assembler.ingest(channel, data, ts)
            self._maybe_ping(client, now)
            self._check_heartbeat(now)
            self._maybe_emit(now)
        return False

    def _maybe_ping(self, client, now):
        if now - self._last_ping >= PING_INTERVAL_S:
            client.send_ping()
            self._last_ping = now

    def _check_heartbeat(self, now):
        if self._last_heartbeat is None:
            self._last_heartbeat = now  # grace: start the clock on first loop
            return
        if now - self._last_heartbeat > HEARTBEAT_DEAD_S:
            raise signalr.TransientError(
                "no Heartbeat for %.0fs (03 sec9.2: connection considered dead)"
                % (now - self._last_heartbeat))

    def _maybe_emit(self, now, force=False):
        if self._next_tick_at is None:
            self._next_tick_at = now + self.tick_interval
            return
        if not force and now < self._next_tick_at:
            return
        self._next_tick_at = now + self.tick_interval
        self._emit(t_local=now, t_wall=_utc_now_iso())

    def _emit(self, t_local, t_wall):
        # 03 sec9.4 staleness: if the newest data folded in is older than 5s,
        # the tick is stale and no consumer may predict from it.
        stale = (self._last_msg_mono is not None
                 and t_local - self._last_msg_mono > STALENESS_LIMIT_S)
        marker = self._pending_reconnect_marker
        self._pending_reconnect_marker = False
        try:
            tick = self._assembler.emit(
                t_feed=self._assembler_t_feed(), t_local=t_local, t_wall=t_wall,
                gap_after_reconnect=marker, stale=stale)
        except Exception:
            self._pending_reconnect_marker = marker  # don't lose the marker on a failed emit
            raise
        self._capture.tick(tick)

    def _assembler_t_feed(self):
        return _utc_now_iso()

    def _on_snapshot(self, snapshot):
        """03 sec7.4 / sec9.4: the subscribe completion is the complete current
        state. Replace, don't merge."""
        session_key = None
        si = snapshot.get("SessionInfo")
        if isinstance(si, dict):
            session_key = si.get("Path")
        if self._capture is None:
            self._capture = Capture(self.capture_root, session_key)
            self._log("capture opened for session %r" % session_key)
        # 03 sec13 item 1: record which channels the subscribe completion
        # carried, and flag any sec6.3 channel that is missing. Written into
        # the raw capture so livetiming_verify.py can check it after the fact.
        got = sorted(snapshot.keys())
        missing = [c for c in self.channels if c not in snapshot]
        self._capture.raw(_utc_now_iso(), time.monotonic(),
                          {"type": 3, "subscribe_snapshot_channels": got, "missing": missing})
        self._log("subscribe completion carried %d channels; missing from sec6.3 set: %s"
                  % (len(got), missing or "none"))
        self._assembler.apply_snapshot(
            {ch: snapshot[ch] for ch in snapshot if ch in self._assembler.TICK_CHANNELS})
        hb = snapshot.get("Heartbeat")
        self._last_heartbeat = time.monotonic() if hb is not None else self._last_heartbeat

    def _rotate_session(self, session_info):
        if self._capture:
            self._capture.close()
        self._capture = None
        self._assembler = TickAssembler()
        self._last_heartbeat = None
        self._next_tick_at = None

    def _session_changed(self, session_info):
        if not isinstance(session_info, dict):
            return False
        path = session_info.get("Path")
        return bool(path) and self._assembler.session_key not in (None, path)

    @staticmethod
    def _is_session_end(channel, data):
        if not isinstance(data, dict):
            return False
        if channel == "SessionStatus":
            return str(data.get("Status", "")) in SESSION_END_STATES
        series = data.get("StatusSeries")
        if isinstance(series, (list, dict)):
            vals = series.values() if isinstance(series, dict) else series
            return any(isinstance(v, dict) and str(v.get("SessionStatus", "")) in SESSION_END_STATES
                       for v in vals)
        return False

    def _enforce_window(self):
        """03 sec6.5.2: connect no earlier than T-60min, and this run should be
        over by T+30min. Advisory when no --session-start was given."""
        if self.session_start is None:
            self._log("WARNING: no --session-start; sec6.5.2 says connect only inside "
                      "a session window. Proceeding on the operator's judgement.")
            return
        now = datetime.now(timezone.utc)
        delta = (self.session_start - now).total_seconds()
        if delta > CONNECT_LEAD_S:
            raise StopClass("sec6.5.2", "session starts in %.0f min; connect no earlier "
                            "than 60 min before" % (delta / 60))
        if -delta > DISCONNECT_LAG_S + 3 * 3600:
            raise StopClass("sec6.5.2", "session was >3.5h ago; nothing to capture")

    def _log(self, line):
        if self._capture:
            self._capture.log(line)
        else:
            print("%s  %s" % (_utc_now_iso(), line), flush=True)

    # -- replay ---------------------------------------------------------

    def run_replay(self, path):
        """03 sec6.5.3: develop against recordings, not against F1. Feeds a raw
        capture back through the same assembler + emit path. No network."""
        require(os.path.exists(path), "replay file not found: %s" % path)
        first_mono = None
        session_key = None
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                msg = rec.get("msg", {})
                recv_mono = rec.get("recv_mono", lineno * 0.001)
                if first_mono is None:
                    first_mono = recv_mono
                    self._next_tick_at = None
                args = msg.get("arguments") if isinstance(msg, dict) else None
                if not args or len(args) < 2:
                    continue
                channel, data = args[0], args[1]
                ts = args[2] if len(args) > 2 else ""
                if session_key is None and channel == "SessionInfo" and isinstance(data, dict):
                    session_key = data.get("Path")
                    if self._capture is None:
                        self._capture = Capture(self.capture_root, session_key or "replay")
                if channel == "Heartbeat":
                    self._last_heartbeat = recv_mono
                self._last_msg_mono = recv_mono
                if channel in self._assembler.TICK_CHANNELS:
                    self._assembler.ingest(channel, data, ts)
                self._replay_emit(recv_mono)
        self._log("replay done: %s" % path)

    def _replay_emit(self, recv_mono):
        if self._next_tick_at is None:
            self._next_tick_at = recv_mono + self.tick_interval
            return
        if recv_mono < self._next_tick_at:
            return
        self._next_tick_at = recv_mono + self.tick_interval
        stale = (self._last_msg_mono is not None
                 and recv_mono - self._last_msg_mono > STALENESS_LIMIT_S)
        marker = self._pending_reconnect_marker
        self._pending_reconnect_marker = False
        tick = self._assembler.emit(t_feed="", t_local=recv_mono,
                                    t_wall="(replay)", gap_after_reconnect=marker, stale=stale)
        if self._capture:
            self._capture.tick(tick)


def parse_session_start(s):
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
