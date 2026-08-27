"""SignalR Core transport for the F1 live-timing feed. 03 sec6.

Hand-rolled rather than via the `signalrcore` package, and 03 sec6.2 is why:
the AWSALBCORS cookie must be replayed on the WebSocket upgrade or the upgrade
lands on a different ALB target that never heard of the connectionToken, and
`signalrcore` owns its own negotiate/handshake with no seam to inject that. The
six handshake steps in sec6.1 are followed exactly here.

`requests` does the OPTIONS/negotiate (HTTP); `websockets` (sync client, which
supports `additional_headers` on the upgrade -- the selection criterion sec6.2
calls out) does the socket. No asyncio.

sec5 / sec6.2 no-evasion rule, enforced structurally: the header values below
are module constants. Nothing in this file picks a header value based on what
happened to a previous attempt. 03 sec12.14 is the assertion; the absence of
any such code path is the implementation.
"""

import json
import uuid

import requests
from websockets.sync.client import connect as ws_connect
from websockets.exceptions import WebSocketException

BASE = "https://livetiming.formula1.com/signalrcore"
WS_BASE = "wss://livetiming.formula1.com/signalrcore"
HUB = "Streaming"

# 03 sec6.2 -- exactly what the protocol needs to complete a handshake, set
# once as constants and never varied. `BestHTTP` is the User-Agent the primary
# source (slowlydev/f1-dash, matteocelani/f1-telemetry) sends.
HEADERS = {
    "User-Agent": "BestHTTP",
    "Accept-Encoding": "gzip,identity",
}

RS = "\x1e"  # SignalR Core record separator (U+001E)

# SignalR Core message types (03 sec6.1).
INVOCATION = 1
COMPLETION = 3
PING = 6


# -- disconnect taxonomy (03 sec9.3) --------------------------------------
#
# The single most important distinction in the spec: a routine drop vs. being
# refused. Retrying into a block is the escalation sec5 promised not to do, so
# the taxonomy is a type hierarchy the client's backoff logic switches on.

class SignalRError(Exception):
    """Base for every transport failure."""


class TransientError(SignalRError):
    """Routine close, network blip, 5xx, or the sec6.2 ALB upgrade fault.
    The client reconnects with backoff up to sec9.3's ceiling."""


class UpgradeFailed(TransientError):
    """WS upgrade failed after a successful negotiate -- 03 sec6.2's ALB
    sticky-session trap. Reads like an auth rejection, is not one. The fix is
    to re-run the whole handshake from step 1 for a fresh cookie + token."""


class Refused(SignalRError):
    """401 / 403 on negotiate, connect, or subscribe. 03 sec9.3: stop, one
    attempt, no retry. Not a transient fault -- a gate is an answer."""


class RateLimited(SignalRError):
    """429, or a Retry-After header. 03 sec9.3: stop, log the header verbatim,
    do not retry within the session."""


class Gated(SignalRError):
    """Negotiate succeeded but the subscribe snapshot came back empty or
    missing required channels. 03 sec6.4's detection signature: stop, loudly."""


# -- framing (pure, unit-tested) -----------------------------------------

def split_frames(buffer):
    """Split a receive buffer on the record separator. 03 sec6.1.

    A single WebSocket frame may carry several messages, so every received
    frame is concatenated onto any remainder and split here. Returns
    (complete_messages, remainder) where remainder is the trailing partial
    message (no separator yet) to prepend to the next frame.

    A parser that treats one WS frame as one message drops data under load;
    this function and the caller keeping the remainder are what prevent that.
    """
    parts = buffer.split(RS)
    return parts[:-1], parts[-1]


def parse_message(text):
    """One record-separator-delimited chunk -> a dict, or None for empty.

    The SignalR Core handshake response is `{}` (empty object) on success; an
    object with an `error` key is a failed handshake, not a message (sec6.1).
    """
    text = text.strip()
    if not text:
        return None
    return json.loads(text)


def is_feed_invocation(msg):
    """type 1 with target 'feed' -- a live channel update. arguments is a
    three-element tuple [channelName, data, timestamp] (03 sec6.1)."""
    return (isinstance(msg, dict) and msg.get("type") == INVOCATION
            and msg.get("target") == "feed")


# -- client -------------------------------------------------------------

class SignalRClient:
    """One connection to the feed. 03 sec6.5: one process, one socket, ever.

    Usage:
        c = SignalRClient()
        snapshot = c.connect(channels)      # steps 1-6, returns the type-3 result
        for channel, data, ts in c.feed():  # live type-1 'feed' invocations
            ...
        c.close()
    """

    def __init__(self, timeout=20):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._ws = None
        self._buffer = ""
        self._multi_message_frames = 0   # 03 sec13 item 1b instrumentation
        self._frames_received = 0
        self.alb_cookie_seen = None      # 03 sec13 item 1: was the ALB replay needed

    @property
    def multi_message_frames(self):
        return self._multi_message_frames

    @property
    def frames_received(self):
        return self._frames_received

    def negotiate(self):
        """Steps 1-2. Returns (connection_token, alb_cookie).

        Step 1's OPTIONS and step 2's POST both carry the AWSALBCORS cookie;
        step 1 is where the cookie is minted (03 sec6.1 / sec6.2).
        """
        try:
            opt = self._session.options(BASE + "/negotiate", timeout=self._timeout)
        except requests.RequestException as e:
            raise TransientError("negotiate OPTIONS failed: %s" % e)
        self._check_status(opt, "negotiate OPTIONS")
        alb = opt.cookies.get("AWSALBCORS") or self._session.cookies.get("AWSALBCORS")

        try:
            resp = self._session.post(
                BASE + "/negotiate",
                params={"negotiateVersion": "1"},
                headers={"Cookie": "AWSALBCORS=%s" % alb} if alb else {},
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise TransientError("negotiate POST failed: %s" % e)
        self._check_status(resp, "negotiate POST")
        self.alb_cookie_seen = bool(alb)

        try:
            body = resp.json()
        except ValueError:
            body = {}
        token = body.get("connectionToken")
        # 03 sec6.1: an empty body is a failure even on HTTP 200.
        if not token:
            raise TransientError("negotiate POST returned no connectionToken (body=%r)"
                                 % (str(body)[:200]))
        return token, alb

    def connect(self, channels, subscribe_invocation_id=None):
        """Steps 3-6. Opens the socket, handshakes, subscribes, and returns the
        step-6 `type: 3` completion `result` (the full current state)."""
        token, alb = self.negotiate()
        uri = "%s?id=%s" % (WS_BASE, token)
        extra = dict(HEADERS)
        if alb:
            extra["Cookie"] = "AWSALBCORS=%s" % alb
        try:
            self._ws = ws_connect(
                uri, additional_headers=extra,
                open_timeout=self._timeout, close_timeout=5,
                max_size=None, ping_interval=None,
            )
        except (WebSocketException, OSError, TimeoutError) as e:
            # 03 sec6.2: a failed upgrade AFTER a successful negotiate is the
            # ALB cookie fault, classified routine, fixed by re-running the
            # whole handshake -- NOT a refusal.
            raise UpgradeFailed("WS upgrade failed after negotiate: %s" % e)

        self._handshake()
        inv_id = subscribe_invocation_id or str(uuid.uuid4())
        self._send({"type": INVOCATION, "invocationId": inv_id,
                    "target": "Subscribe", "arguments": [list(channels)]})
        return self._await_completion(inv_id)

    def _handshake(self):
        """Step 4: `{"protocol":"json","version":1}` + RS, then read the reply.
        A reply containing `error` is a failed handshake, not a message."""
        self._send_raw(json.dumps({"protocol": "json", "version": 1}))
        for msg in self._read_messages(deadline_msgs=1):
            if msg is None:
                return
            if isinstance(msg, dict) and "error" in msg:
                raise Refused("SignalR handshake rejected: %s" % msg["error"])
            return
        raise TransientError("no handshake response")

    def _await_completion(self, inv_id):
        """Step 6: the subscribe snapshot arrives as a type-3 whose
        invocationId matches. Do not assume the next frame is it (03 sec6.1)."""
        for msg in self._read_messages(deadline_msgs=200):
            if isinstance(msg, dict) and msg.get("type") == COMPLETION \
                    and msg.get("invocationId") == inv_id:
                result = msg.get("result")
                if not isinstance(result, dict) or not result:
                    raise Gated("subscribe completion carried no state (result=%r)"
                                % (str(result)[:200]))
                return result
        raise Gated("no subscribe completion for invocationId %s" % inv_id)

    def feed(self, recv_timeout=1.0):
        """Yield (channel, data, timestamp) for every live type-1 'feed'
        invocation. Returns when the socket closes (caller reconnects).
        Yields nothing on a recv timeout -- the caller's loop uses that tick
        to check heartbeat age and send a keepalive ping (03 sec9.2)."""
        while True:
            try:
                got_data = False
                for msg in self._read_messages(recv_timeout=recv_timeout):
                    if is_feed_invocation(msg):
                        args = msg.get("arguments") or []
                        if len(args) >= 2:
                            got_data = True
                            yield (args[0], args[1], args[2] if len(args) > 2 else "")
                if not got_data:
                    yield None  # recv timeout -- caller does liveness bookkeeping
            except TimeoutError:
                yield None
            except (WebSocketException, OSError) as e:
                raise TransientError("socket closed during feed: %s" % e)

    def send_ping(self):
        """Application-level SignalR keepalive. Safe to call any time."""
        try:
            self._send({"type": PING})
        except (WebSocketException, OSError) as e:
            raise TransientError("ping failed: %s" % e)

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._session.close()

    # -- internals ----------------------------------------------------------

    def _send(self, obj):
        self._send_raw(json.dumps(obj))

    def _send_raw(self, text):
        self._ws.send(text + RS)

    def _read_messages(self, recv_timeout=None, deadline_msgs=None):
        """Recv one WS frame, split it on RS, parse each part. Any trailing
        partial is held in self._buffer for the next frame (03 sec6.1)."""
        try:
            frame = self._ws.recv(timeout=recv_timeout)
        except TimeoutError:
            raise
        self._frames_received += 1
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8")
        self._buffer += frame
        messages, self._buffer = split_frames(self._buffer)
        if len(messages) > 1:
            self._multi_message_frames += 1
        emitted = 0
        for raw in messages:
            msg = parse_message(raw)
            if msg is not None and isinstance(msg, dict) and msg.get("type") == PING:
                continue  # 03 sec6.1: discard keep-alives
            yield msg
            emitted += 1
            if deadline_msgs is not None and emitted >= deadline_msgs:
                return

    def _check_status(self, resp, what):
        code = resp.status_code
        if code in (401, 403):
            raise Refused("%s -> HTTP %d (03 sec9.3: refusal, stop)" % (what, code))
        if code == 429 or resp.headers.get("Retry-After"):
            raise RateLimited("%s -> HTTP %d, Retry-After=%r"
                              % (what, code, resp.headers.get("Retry-After")))
        if code >= 500:
            raise TransientError("%s -> HTTP %d" % (what, code))
        if code >= 400:
            raise TransientError("%s -> HTTP %d" % (what, code))
