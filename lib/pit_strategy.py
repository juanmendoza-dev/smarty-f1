"""Project a stop already in progress onto post-cycle track position. `docs/12`.

**This is a projection, not a prediction, and the distinction is the whole
scope.** `CarState.in_pit` is an *observation*: the car is in the pit lane, the
tick says so. From that, one measured constant (`delta`, `lib/pit_loss.py`) and
the gaps the tick already carries, this module computes where the car rejoins
relative to the field. Nothing here guesses *when* a car will stop -- 12 sec2.4
measured that stint age moves the per-lap stop hazard between 0.015 and 0.075 on
a 0.037 base and is not even monotone, so the timing half is unfunded by this
corpus and is out of scope (12 sec8).

**What it hands 09.** A corrected running order, for the duration of a cycle
only, in place of raw track position (12 sec4). It replaces 09 sec5.7's "do
nothing explicit and suppress the estimate" with "correct the order and keep
publishing".

**Two properties are structural here rather than checked and hoped for**, which
is why the insertion below counts cars instead of re-sorting the field:

  - `delta = 0` is the identity (12 sec7 assertion 1). A car moves back past `d`
    only if `g(d) < g(c) + remaining`; at `remaining = 0` that set is empty by
    construction, so the corrected order is the observed order field for field.
  - A projection never gains a place (assertion 2). `remaining` is clamped at
    zero and the shift is a count, so a car's projected index is never below its
    observed one. It can only ever go backwards.

  The comparison is also floored at `g(d) >= g(c)`, so a car *behind* `c` whose
  gap reads *ahead* of it -- an asof join against a stale sample, `03` sec8's
  world -- cannot drag `c` backwards on noise alone.

**What is refused rather than approximated** (12 sec5.3, all three measured or
recorded elsewhere, none of them a judgement call made here):

  - a stop under SC/VSC, because 12 sec2.1's four noisiest circuits are the
    caution-heavy ones and a compressed field makes `delta` a different quantity;
  - a non-numeric `gap_leader`, because `08` sec13.6 item 3 found 72% of the
    `LAP n` rows sit at Position 1 and its semantics are UNVERIFIED;
  - a degraded or stale tick (`03` sec8, sec9.4);
  - and a red flag discards the state machine outright, per `03` sec9.5.
"""

from .invariants import require
from .overtake_serve import parse_interval
from .pit_loss import delta_for

# 12 sec5.2's cycle, and 03 sec7.4's latch discipline over it. The rank is what
# enforces "never runs backwards within one cycle" (12 sec7 assertion 6): a
# transition that un-happens -- in_pit flickering back true after the car has
# left the lane -- is a parsing artifact, not a fact about the car.
RUNNING, ENTERING, IN_PIT, OUT_LAP = "RUNNING", "ENTERING", "IN_PIT", "OUT_LAP"
STATE_RANK = {RUNNING: 0, ENTERING: 1, IN_PIT: 2, OUT_LAP: 3}
IN_CYCLE = (ENTERING, IN_PIT, OUT_LAP)

# 03 sec9.5's session change. A red flag stops the race, the field forms up in
# the pit lane, and every pit state the machine is holding describes a race that
# no longer exists.
RED_FLAG_STATUS = 5

REFUSE_CAUTION = "caution"
REFUSE_DEGRADED = "degraded"
REFUSE_STALE = "stale"
REFUSE_GAP = "gap_not_numeric"
REFUSE_NO_POSITION = "no_position"


class PitCycle:
    """One car's trip through the pit lane, from entry to the first real gap."""

    __slots__ = ("code", "state", "t_start", "gap_entry", "position_entry",
                 "lap_entry", "closed_by")

    def __init__(self, code, t_start, gap_entry, position_entry, lap_entry):
        self.code = code
        self.state = ENTERING
        self.t_start = float(t_start)
        self.gap_entry = gap_entry
        self.position_entry = position_entry
        self.lap_entry = lap_entry
        self.closed_by = None

    def advance(self, state):
        """Move the cycle forward, never backwards. 12 sec7 assertion 6."""
        require(STATE_RANK[state] >= STATE_RANK[self.state],
                "12 sec7 assertion 6: pit state ran backwards for %s, %s -> %s"
                % (self.code, self.state, state))
        self.state = state

    def elapsed(self, t_now):
        return max(float(t_now) - self.t_start, 0.0)


class PitProjection:
    """Where one car in a cycle is projected to rejoin, and off what."""

    __slots__ = ("code", "state", "delta_s", "remaining_s", "gap_now",
                 "projected_gap", "observed_index", "n_ahead", "projected_index",
                 "flagged")

    def __init__(self, code, state, delta_s, remaining_s, gap_now, projected_gap,
                 observed_index, n_ahead, flagged):
        self.code = code
        self.state = state
        self.delta_s = delta_s
        self.remaining_s = remaining_s
        self.gap_now = gap_now
        self.projected_gap = projected_gap
        self.observed_index = observed_index
        # How many cars that are NOT themselves mid-cycle the car rejoins
        # behind. It is the insertion point, not the final index: another car in
        # the same cycle can land ahead of it and push it back one more, which
        # is why `projected_index` is read off the built order rather than
        # predicted here.
        self.n_ahead = n_ahead
        self.projected_index = None
        self.flagged = flagged

    @property
    def places_lost(self):
        return self.projected_index - self.observed_index

    def as_json(self):
        return {"code": self.code, "state": self.state, "delta_s": self.delta_s,
                "remaining_s": self.remaining_s, "gap_now": self.gap_now,
                "projected_gap": self.projected_gap,
                "observed_index": self.observed_index,
                "projected_index": self.projected_index,
                "places_lost": self.places_lost, "flagged": self.flagged}


class Correction:
    """The corrected order for one tick, and everything that explains it."""

    __slots__ = ("order", "projections", "refusals", "in_cycle")

    def __init__(self, order, projections, refusals, in_cycle):
        self.order = order
        self.projections = projections      # [PitProjection]
        self.refusals = refusals            # {code -> reason}
        self.in_cycle = in_cycle            # {code -> state}

    @property
    def changed(self):
        return any(p.places_lost for p in self.projections)

    def projected_codes(self):
        return {p.code for p in self.projections}


def gap_seconds(car):
    """A car's numeric gap to the leader, or None.

    Never coerced. The leader's own `GapToLeader` is the feed's lap-count form
    or empty rather than a number -- `08` sec13.6 item 3 is exactly that row --
    so P1 is read as 0.0 from its *position*, which is an observation, and every
    other non-numeric form is dropped (12 sec5.3).
    """
    if car is None:
        return None
    if car.position == 1:
        return 0.0
    return parse_interval(car.gap_leader)


class PitProjector:
    """Folds ticks into per-car pit cycles and emits a corrected order.

    Offline only, like everything else in this chain: it consumes `03` sec7
    ticks and opens no socket. `03` sec4.4's live gate is untouched (12 sec8).
    """

    def __init__(self, season, circuit_id, table=None):
        self.pit_loss = delta_for(season, circuit_id, table=table)
        self.cycles = {}                # code -> PitCycle, open cycles only
        self.completed = {}             # code -> count of closed cycles
        self.red_flags = 0

    # -- fast path ---------------------------------------------------------

    def fold(self, tick):
        """Advance every car's pit state by one tick. No projection here."""
        if tick.track_status == RED_FLAG_STATUS:
            # 03 sec9.5: discard and re-derive from the first tick after the
            # restart. Not "pause" -- the state describes a race that stopped.
            self.cycles.clear()
            self.red_flags += 1
            return
        for code, car in tick.cars.items():
            cyc = self.cycles.get(code)
            if cyc is None:
                if car.in_pit:
                    self.cycles[code] = PitCycle(
                        code, tick.t_local, gap_seconds(car), car.position,
                        tick.lap_current)
                continue
            if cyc.state == ENTERING and car.in_pit:
                cyc.advance(IN_PIT)
            elif cyc.state in (ENTERING, IN_PIT) and not car.in_pit:
                cyc.advance(OUT_LAP)
            # A latched cycle never returns to IN_PIT: `in_pit` going true again
            # after the car has left the lane is 03 sec7.4's parsing artifact.
            if cyc.state == OUT_LAP and self._observation_has_landed(car):
                # 12 sec7 assertion 3: the projection is provisional. The moment
                # a real gap arrives the observed value replaces it *on this
                # tick* -- no blending, no carry-over -- which is exactly the
                # cycle closing rather than being faded out.
                cyc.closed_by = "pit_out" if car.pit_out else "gap"
                self.completed[code] = self.completed.get(code, 0) + 1
                del self.cycles[code]

    @staticmethod
    def _observation_has_landed(car):
        return (not car.in_pit) and gap_seconds(car) is not None

    # -- projection --------------------------------------------------------

    def refusal_for_tick(self, tick):
        """12 sec5.3's tick-wide refusals, or None if the tick is projectable."""
        if tick.stale:
            return REFUSE_STALE
        if tick.degraded:
            return REFUSE_DEGRADED
        if tick.track_status != 1:
            return REFUSE_CAUTION
        return None

    def project(self, tick, observed_order):
        """The corrected running order for this tick. 12 sec4 component 4.

        `observed_order` is 09's own running order -- retired cars already
        removed -- and the correction is a permutation of it, never a different
        set of cars: `p_win` has to sum to 1 over the field the tick carries
        (09 sec11 assertion 1).
        """
        in_cycle = {c: cyc.state for c, cyc in self.cycles.items()
                    if c in observed_order}
        if not in_cycle:
            return Correction(list(observed_order), [], {}, {})

        tick_refusal = self.refusal_for_tick(tick)
        if tick_refusal:
            return Correction(list(observed_order), [],
                              {c: tick_refusal for c in in_cycle}, in_cycle)

        gaps = {code: gap_seconds(tick.car(code)) for code in observed_order}
        index_of = {code: i for i, code in enumerate(observed_order)}

        refusals = {}
        projections = []
        for code, state in in_cycle.items():
            idx = index_of[code]
            car = tick.car(code)
            if car is None or car.position is None:
                refusals[code] = REFUSE_NO_POSITION
                continue
            g_now = gaps.get(code)
            if g_now is None:
                refusals[code] = REFUSE_GAP
                continue
            cyc = self.cycles[code]
            remaining = max(self.pit_loss.delta_s - cyc.elapsed(tick.t_local), 0.0)
            projected_gap = g_now + remaining
            shift = 0
            for other in observed_order[idx + 1:]:
                g_other = gaps.get(other)
                if g_other is None or other in in_cycle:
                    continue
                if g_now <= g_other < projected_gap:
                    shift += 1
            require(shift >= 0,
                    "12 sec7 assertion 2: %s projected forward by %d places"
                    % (code, -shift))
            ahead_stationary = sum(1 for other in observed_order[:idx]
                                   if other not in in_cycle)
            projections.append(PitProjection(
                code, state, self.pit_loss.delta_s, remaining, g_now,
                projected_gap, idx, ahead_stationary + shift,
                self.pit_loss.flagged))

        order = _reinsert(observed_order, projections)
        require(sorted(order) == sorted(observed_order),
                "12 sec4: the corrected order must be a permutation of the "
                "observed one, not a different field")
        for p in projections:
            p.projected_index = order.index(p.code)
            # 12 sec7 assertion 2, checked on the order that was actually built
            # rather than on the arithmetic that was supposed to build it.
            require(p.projected_index >= p.observed_index,
                    "12 sec7 assertion 2: %s was projected from P%d to P%d -- a "
                    "car in the pit lane cannot gain a place"
                    % (p.code, p.observed_index + 1, p.projected_index + 1))
        return Correction(order, projections, refusals, in_cycle)


def _reinsert(observed_order, projections):
    """Rebuild the order, cutting each projected car back into the cars that
    are not themselves mid-cycle.

    **The subtlety, which cost a bug the double-stack test caught.** A car that
    drops behind `k` others does not land at `observed_index + k`: every car it
    passed moves *up* one, so the target has to be expressed against the cars
    that are standing still, not against the original indices. Positions are
    therefore counted among the stationary cars alone, and a projected car with
    `n_ahead = k` sorts in ahead of stationary car `k`.

    Two cars cut in at the same point keep the order they arrived in. A double
    stack -- 12 sec2.4's elevated 0-4 lap stint bucket -- enters within a second
    or two, so the difference between their projected gaps is far inside
    `delta`'s own MAD (12 sec2.1: 3.7 s pooled); ordering them on it would be
    inventing a distinction the measurement cannot support.
    """
    if not projections:
        return list(observed_order)
    moved = {p.code: p for p in projections}
    keyed = []
    stationary_index = 0
    for i, code in enumerate(observed_order):
        p = moved.get(code)
        if p is None:
            keyed.append(((stationary_index, 1, 0), code))
            stationary_index += 1
        else:
            keyed.append(((p.n_ahead, 0, p.observed_index), code))
    keyed.sort(key=lambda kv: kv[0])
    return [code for _, code in keyed]
