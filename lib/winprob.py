"""The live win-probability state estimator. 09-live-win-probability.md, Phase B4.

**This builds no new predictive model.** Every predictive input already exists:
Lane A's pre-race distribution (`02` sec5.4), `04` sec5's reliability model, and
`08`'s calibrated in-domain overtake probability. What is new is the
*propagation* -- carrying those forward over the remaining race distance under
the constraint that the field is a permutation (`lib/winprob_sim.py`).

Read 09 sec3 before this file. The three things that actually move P(win), in
order, are pit-cycle track position (09 sec2.1's 71% of lead changes),
retirement (sec2.5), and laps simply running out (sec2.2). `08` is **fourth**,
its average contribution to P(win) at the front measures ~0.4 points against a
1-point market tick, and 09 sec10's ablation exists to find out whether that
survives Monte Carlo noise at all. A reader who comes to this file expecting the
overtake model to be the engine is reading the wrong sport.

**Offline only.** `03` sec4.4 as amended on 2026-09-03 authorizes building and
validating this layer by replaying archived races. Running it against a live
feed stays gated on B1, which is unrun. Nothing in this module opens a socket;
it consumes `03` sec7's ticks and nothing else, exactly as the tick contract
requires, and where those ticks come from is the caller's business.
"""

import json
import math
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Optional

from . import overtake_serve as osv
from . import pit_strategy as pitmod
from . import winprob_sim as wsim
from .invariants import require
from .winprob_priors import lap_hazards

# 09 sec7.3: Kalshi prices in whole cents, so one market tick is 1.0 point and
# an estimate whose Monte Carlo standard error exceeds half a tick is not
# actionable at that N. This is the pre-registered decision rule, stated before
# the numbers existed, and `reliable` enforces it.
MARKET_TICK = 0.01
MAX_SE_MC = MARKET_TICK / 2.0

# 09 sec7.2's slow-path cadence. Kept here as the contract even though the
# offline harness drives estimates at lap boundaries rather than on a clock.
SLOW_PATH_HZ = 1.0
SLOW_PATH_BUDGET_S = 0.25

DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "live", "winprob")

REASON_STALE = "stale"
REASON_DEGRADED = "degraded"
REASON_CAUTION = "caution"
REASON_PIT_OFFSET = "pit_offset"
REASON_RECONNECT = "gap_after_reconnect"
REASON_UNRECONCILED = "prior_not_reconciled"
REASON_MC_NOISE = "se_mc_above_half_tick"


@dataclass(frozen=True)
class RacePrior:
    """What the layer is initialised with, per race. 09 sec6.

    `strengths` are the RECONCILED `w'` of 09 sec5.5, not `02`'s raw softmax
    weights, and `reconciled` says whether the IPF actually ran -- an
    unreconciled prior is usable but every estimate off it is `reliable = False`
    (09 sec8.1), because the DNF hazard has not been proven un-double-counted
    for it.
    """
    prior_id: str
    p_algo: dict
    strengths: dict
    f_dnf: dict
    hazard: object
    reconciled: bool = False
    residual: Optional[float] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WinProbEstimate:
    """09 sec8.1's output record. Immutable once emitted, `03` sec7.4's rule.

    `t_wall` is load-bearing rather than decorative, for the reason `03` sec7.1
    added it during B0's build: a monotonic clock has no epoch, so it cannot be
    subtracted from a market timestamp. Any feed-versus-market comparison needs
    the wall clock.
    """
    session_key: Optional[str]
    t_feed: str
    t_local: float
    t_wall: str
    lap_current: Optional[int]
    progress: float
    p_win: MappingProxyType
    se_mc: MappingProxyType
    prior_id: str
    model_id: str
    n_paths: int
    in_domain: tuple
    pit_offset: int
    pit_projected: tuple
    pit_refused: tuple
    pit_order_changed: bool
    pit_cycle_in_top3: bool
    degraded: frozenset
    stale: bool
    reliable: bool
    reasons: tuple

    def as_json(self):
        return {
            "session_key": self.session_key, "t_feed": self.t_feed,
            "t_local": self.t_local, "t_wall": self.t_wall,
            "lap_current": self.lap_current, "progress": self.progress,
            "p_win": dict(self.p_win), "se_mc": dict(self.se_mc),
            "prior_id": self.prior_id, "model_id": self.model_id,
            "n_paths": self.n_paths,
            "in_domain": [list(x) for x in self.in_domain],
            "pit_offset": self.pit_offset,
            "pit_projected": [dict(p) for p in self.pit_projected],
            "pit_refused": [list(x) for x in self.pit_refused],
            "pit_order_changed": self.pit_order_changed,
            "pit_cycle_in_top3": self.pit_cycle_in_top3,
            "degraded": sorted(self.degraded),
            "stale": self.stale, "reliable": self.reliable,
            "reasons": list(self.reasons),
        }


class WinProbLayer:
    """Folds ticks into `WinProbState` (fast path) and emits estimates (slow).

    09 sec7.2 splits the two because the tick stream is faster than the
    simulator: every tick updates order, retirement latches, lap counters, pit
    counts and the `08` feature buffers at microsecond cost, and only the
    re-simulation is rate-limited.
    """

    def __init__(self, prior, background, overtake_model=None, m=1.0,
                 n_paths=wsim.SERVE_N, use_overtake_model=True, use_platt=False,
                 model_id="08:none", pit_projector=None):
        # 12 sec7 assertion 4, and it fires HERE -- at construction, before a
        # single tick is folded -- rather than at scoring time. A layer running
        # `docs/12`'s projection against a background rate that still contains
        # pit-cycle swaps counts the pit cycle twice, and the number it produces
        # is plausible, which is this project's demonstrated failure mode
        # (03 sec12). Loudly, or not at all.
        require(pit_projector is None or background.pit_swaps_removed,
                "12 sec7 assertion 4: the pit-strategy projection is active but "
                "09 sec5.4's background rate still contains pit-cycle swaps. "
                "Refit with `winprob_fit.py --pit-refit`; scoring against this "
                "rate would double-count every pit cycle.")
        self.prior = prior
        self.background = background
        self.pit_projector = pit_projector
        self.overtake_model = overtake_model
        self.m = float(m)
        self.n_paths = int(n_paths)
        self.use_overtake_model = bool(use_overtake_model)
        self.use_platt = bool(use_platt)
        self.model_id = "%s theta=%.4f theta_front=%.4f cal=%s" % (
            model_id, osv.THETA, osv.THETA_FRONT, "platt" if use_platt else "raw")

        # -- WinProbState (09 sec4)
        self.session_key = None
        self.order = []
        self.retired = set()
        self.laps_done = {}
        self.lap_current = None
        self.lap_total = None
        self.track_status = 1
        self.stops_done = {}
        self.pursuits = []
        self.last_tick = None

        self._in_pit = {}
        self._pair_history = {}
        self._lap_started_at = None
        self._lap_time_s = wsim.DEFAULT_LAP_TIME_S
        self._track_frac = 0.0

    # -- fast path ---------------------------------------------------------

    def fold(self, tick):
        """One tick into retained state. No simulation. 09 sec7.2."""
        if tick.session_key is not None:
            self.session_key = tick.session_key
        self.track_status = tick.track_status
        if tick.lap_total is not None:
            self.lap_total = tick.lap_total

        if tick.lap_current is not None and tick.lap_current != self.lap_current:
            if self._lap_started_at is not None:
                seen = tick.t_local - self._lap_started_at
                if 20.0 < seen < 400.0:
                    self._lap_time_s = seen
            self._lap_started_at = tick.t_local
            self.lap_current = tick.lap_current
        if self._lap_started_at is None:
            self._lap_started_at = tick.t_local
        self._track_frac = min(max((tick.t_local - self._lap_started_at)
                                   / max(self._lap_time_s, 1e-6), 0.0), 1.0)

        placed = []
        for code, car in tick.cars.items():
            # 03 sec7.4 / 09 sec4: terminal states latch and are never reversed.
            # A car that un-retires is a parsing artifact, never a fact.
            if car.retired or car.stopped:
                self.retired.add(code)
            if car.laps is not None:
                self.laps_done[code] = car.laps
            was_in_pit = self._in_pit.get(code, False)
            if car.in_pit and not was_in_pit:
                self.stops_done[code] = self.stops_done.get(code, 0) + 1
            self._in_pit[code] = bool(car.in_pit)
            self.stops_done.setdefault(code, 0)
            if car.position is not None and code not in self.retired:
                placed.append((car.position, code))
        if placed:
            self.order = [c for _, c in sorted(placed)]

        # The pit state machine advances on the fast path with everything else:
        # it is a fold over `in_pit` / `pit_out`, and 09 sec7.2 rate-limits the
        # simulator, not the bookkeeping.
        if self.pit_projector is not None:
            self.pit_projector.fold(tick)

        self.pursuits = self._compute_pursuits(tick)
        self.last_tick = tick

    def _compute_pursuits(self, tick):
        """09 sec5.3: which adjacent pairs `08` is allowed to speak for."""
        out = []
        if self.overtake_model is None or not self.use_overtake_model:
            return out
        # Overtaking is forbidden under SC/VSC, so 08 is suppressed structurally
        # rather than trusted to have learned it -- 08 sec11 found `under_caution`
        # unidentified, flipping sign across all ten folds (09 sec5.6).
        if tick.track_status != 1:
            return out
        for i in range(1, len(self.order)):
            pursuer, ahead = self.order[i], self.order[i - 1]
            hist = self._pair_history.setdefault((pursuer, ahead), osv.PairHistory())
            interval = osv.parse_interval(
                tick.car(pursuer).gap_ahead if tick.car(pursuer) else None)
            if interval is not None:
                hist.update(tick.t_local, interval)
            row = osv.feature_row(tick, pursuer, ahead, hist, self._track_frac,
                                  self.lap_total)
            if row is None:
                continue
            p_raw = self.overtake_model.predict_row(row, use_platt=self.use_platt)
            if osv.admits(p_raw, row["position"]):
                out.append((pursuer, ahead, p_raw))
        return out

    # -- slow path ---------------------------------------------------------

    @property
    def progress(self):
        if not self.lap_total:
            return 0.0
        return min(max((self.lap_current or 1) / float(self.lap_total), 0.0), 1.0)

    @property
    def pit_offset(self):
        """09 sec5.7: the spread in completed stops across the top ten.
        Diagnostic information published to the consumer, not a correction."""
        top = [c for c in self.order if c not in self.retired][:10]
        if len(top) < 2:
            return 0
        counts = [self.stops_done.get(c, 0) for c in top]
        return max(counts) - min(counts)

    def _pit_offset_top3(self):
        top = [c for c in self.order if c not in self.retired][:3]
        if len(top) < 2:
            return 0
        counts = [self.stops_done.get(c, 0) for c in top]
        return max(counts) - min(counts)

    @staticmethod
    def _cycle_in_top3(correction, order):
        return any(c in correction.in_cycle for c in order[:3])

    def _pit_suppressed(self, correction, order):
        """09 sec5.7's suppression rule, narrowed by `docs/12` (12 sec4).

        **Before:** `reliable = False` while `pit_offset > 0` among the top
        three. Measured at 28.5% of checkpoints (09 sec10.4) -- the layer silent
        over more than a quarter of the race, and the number `docs/12` was
        funded to move.

        **After:** the estimate is suppressed only for a top-three car whose
        rejoin is *not projectable* -- a stop under caution, a degraded or stale
        tick, an unparseable gap (12 sec5.3). A cycle this model has corrected
        no longer silences the estimate, because the correction is the answer
        the suppression was standing in for.

        **What this does not fix, stated here rather than discovered later.**
        Most of the old 28.5% is not a car in the pit lane at all: it is a
        completed-stop spread that persists between cycles -- a leader who has
        yet to stop and will lose the place when he does. Projecting that needs
        stop *timing*, which 12 sec2.4 measured and rejected as out of scope. So
        this rule change un-suppresses checkpoints whose underlying error this
        model does not touch, and 12 sec6's outcome 3 is the empirical check on
        whether that was warranted.
        """
        if self.pit_projector is None:
            return self._pit_offset_top3() > 0
        return any(c in correction.refusals for c in order[:3])

    def running_order(self):
        """Raw track position, retired cars removed.

        Deliberately NOT the pit-corrected order: 09 sec10's position-only
        ladder is a baseline built on track position and nothing else, and
        handing it this model's correction would stop it being that baseline.
        The correction goes to the simulator, in `estimate()`.
        """
        return [c for c in self.order if c not in self.retired]

    def pit_correction(self, running=None):
        """`docs/12`'s corrected order for the current tick, or the identity."""
        running = self.running_order() if running is None else running
        if self.pit_projector is None or self.last_tick is None:
            return pitmod.Correction(list(running), [], {}, {})
        return self.pit_projector.project(self.last_tick, running)

    def estimate(self, n_paths=None, use_overtake_model=None, collect_orders=False):
        """Re-simulate and emit one `WinProbEstimate`. 09 sec8.1."""
        tick = self.last_tick
        require(tick is not None, "estimate() before any tick was folded")
        running = self.running_order()
        require(running, "estimate(): every car in the field is latched retired")

        correction = self.pit_correction(running)
        # The permutation the simulator sees. Every downstream identity -- the
        # p_win sum, the retirement zero, the t=0 baseline -- is over the same
        # set of cars, because 12 sec4's correction reorders the field and never
        # changes who is in it.
        sim_order = correction.order

        n_paths = int(n_paths or self.n_paths)
        use_om = self.use_overtake_model if use_overtake_model is None else use_overtake_model
        lap_total = self.lap_total or max(self.laps_done.values() or [1])
        lap_current = self.lap_current or 1
        # The flag. `LapCount.CurrentLap` stops at `lap_total` and stays there,
        # so "the leader is on the last lap" and "the leader has finished it"
        # look identical on that field alone; the leader's own completed-lap
        # count is what separates them. Without this the layer simulates one
        # more partial lap after the race has ended and 09 sec11.3's endgame
        # identity cannot hold -- the leader stays passable at the flag.
        leader_laps = self.laps_done.get(running[0])
        if leader_laps is not None and leader_laps >= lap_total:
            lap_current = lap_total + 1
        hz = lap_hazards({c: self.prior.f_dnf.get(c, 0.0) for c in running},
                         self.prior.hazard, max(lap_current - 1, 0), lap_total)

        p_win, se_mc, info = wsim.forward_simulate(
            self.session_key or "session", sim_order, self.prior.strengths, hz,
            self.background, lap_current, lap_total, track_frac=self._track_frac,
            m=self.m, lap_time_s=self._lap_time_s,
            pursuits=self.pursuits if use_om else (), n_paths=n_paths,
            use_overtake_model=use_om, collect_orders=collect_orders)

        # 09 sec11 assertion 1: a car latched retired or stopped has p_win
        # EXACTLY 0.0 -- not merely small, and not "the simulation happened
        # never to pick it".
        for code in self.retired:
            p_win[code] = 0.0
            se_mc[code] = 0.0
        total = sum(p_win.values())
        require(abs(total - 1.0) < 1e-6,
                "09 sec11.1: p_win sums to %.9f, not 1.0" % total)

        reasons = []
        if tick.stale:
            reasons.append(REASON_STALE)
        if tick.degraded:
            reasons.append(REASON_DEGRADED)
        if tick.track_status != 1:
            reasons.append(REASON_CAUTION)
        if self._pit_suppressed(correction, sim_order):
            reasons.append(REASON_PIT_OFFSET)
        if tick.gap_after_reconnect:
            reasons.append(REASON_RECONNECT)
        if not self.prior.reconciled:
            reasons.append(REASON_UNRECONCILED)
        if se_mc and max(se_mc.values()) > MAX_SE_MC:
            reasons.append(REASON_MC_NOISE)

        est = WinProbEstimate(
            session_key=self.session_key, t_feed=tick.t_feed, t_local=tick.t_local,
            t_wall=tick.t_wall, lap_current=self.lap_current, progress=self.progress,
            p_win=MappingProxyType(dict(p_win)), se_mc=MappingProxyType(dict(se_mc)),
            prior_id=self.prior.prior_id, model_id=self.model_id, n_paths=n_paths,
            in_domain=tuple(tuple(x) for x in (self.pursuits if use_om else ())),
            # 12 sec9 item 4, owner's call 2026-09-04: the published diagnostic
            # keeps its 09 sec5.7 meaning -- the raw spread in completed stops --
            # because it is published and something downstream may read it. The
            # projection is reported alongside it in its own fields rather than
            # by quietly changing what an existing one means.
            pit_offset=self.pit_offset,
            pit_projected=tuple(p.as_json() for p in correction.projections),
            pit_refused=tuple(sorted(correction.refusals.items())),
            pit_order_changed=correction.changed,
            pit_cycle_in_top3=self._cycle_in_top3(correction, sim_order),
            degraded=tick.degraded, stale=tick.stale,
            reliable=not reasons, reasons=tuple(reasons))
        check_estimate(est)
        return (est, info) if collect_orders else est


def check_estimate(est):
    """09 sec11 assertions 1, 7, 8 and the [0,1] half of 9, on every emission.

    Via `lib.invariants.require`, never a bare `assert` -- `05`/`08` convention
    and `03` sec12's reasoning: these guard *data*, and a plausible wrong number
    is the failure mode this project has repeatedly been bitten by. Under
    `python -O` a bare assert disappears and takes the guard with it.
    """
    total = sum(est.p_win.values())
    require(abs(total - 1.0) < 1e-6,
            "09 sec11.1: p_win sums to %.9f, not 1.0" % total)
    for code, p in est.p_win.items():
        require(0.0 <= p <= 1.0, "09 sec11.9: p_win/%s = %r outside [0,1]" % (code, p))
        require(code in est.se_mc,
                "09 sec11.7: no se_mc for %s -- an estimate without it is not emitted" % code)
    computed = bool(est.reasons)
    require(est.reliable == (not computed),
            "09 sec11.8: reliable=%r is inconsistent with reasons=%r"
            % (est.reliable, est.reasons))
    for pursuer, ahead, p_raw in est.in_domain:
        require(p_raw >= osv.THETA,
                "09 sec11.9: %s/%s fed to step 0 at p_raw=%.6f, below theta=%.4f"
                % (pursuer, ahead, p_raw, osv.THETA))
    return True


def write_jsonl(estimates, path):
    """09 sec8.2: a local append-only JSONL log under `data/live/winprob/`,
    gitignored, `03` sec11.2's rule unchanged and for its unchanged reason --
    this is derived F1 timing data and the repo is public."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        for est in estimates:
            fh.write(json.dumps(est.as_json(), sort_keys=True) + "\n")
    return path
