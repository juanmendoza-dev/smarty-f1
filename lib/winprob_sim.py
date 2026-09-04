"""Monte Carlo forward simulation of the remaining race. 09 sec5, sec7.

This is 09 sec5.1's choice, and it is forced rather than stylistic. A Markov
chain over field orderings is intractable (20! states). A Markov chain over each
driver's position independently is tractable and wrong -- it does not preserve
the permutation constraint, so two drivers can hold P1 and the correlation that
carries all the information is discarded, which is the same shortcut `04` sec6.1
rejected for podium probabilities. Monte Carlo over orderings handles the
constraint for free and is already this project's idiom (`lib/simulate.py`);
this is that move one level up -- simulate the *evolution* of the order rather
than a single draw of it.

numpy is used deliberately and is authorized: 09 sec7.3 spec's it by name, and
`overtake_fit.py` already established that `05` sec7's no-numpy rule is scoped
to Phase A3's optimizer and does not govern Lane B modules. A pure-Python
forward simulation was costed in 09 sec7.3 at ~a minute per update against a
250 ms budget, so it is not a close call.

## The strength tilt -- a spec gap, closed here, recorded in 09 sec5.4

09 sec5.5 requires solving for reconciled strengths `w'` such that the simulator
reproduces `02`'s `p_algo` at lights-out. 09 sec5.4 as originally written
defined the background swap probability as `q(band, progress, circuit)` with no
strength term at all, and `WinProbState.strengths` was consumed nowhere in
sec5's propagation. With no path from `w'` into the estimate, the IPF update
`w' <- w' * p_algo / p_hat` has no lever, never converges, and sec11 assertion 2
is unreachable. Found before the simulator was written; the fix below is the
minimal one, and it is recorded as a dated in-place correction in 09 sec5.4
rather than left in code (this project's specs are the decision record).

For an adjacent pair with `a` ahead of `b`:

    q_pair = q(band, progress, circuit) * 2 * w_b / (w_a + w_b)

The tilt is exactly 1.0 when the two strengths are equal, so 09 sec2.3's
measured rate is recovered on a field of equals and stays the calibration
target; it is bounded in [0, 2q], so a strength ratio cannot drive the swap
probability to nonsense. TILT_EQUAL_STRENGTH_IS_ONE below asserts the reduction.
"""

import numpy as np

from .invariants import require

# 09 sec7.3: SE = sqrt(p(1-p)/N); Kalshi prices in whole cents so one tick is
# 1.0 point. N = 10,000 puts the worst-case SE at 0.50 points -- half a tick.
SERVE_N = 10_000
# Validation is offline and not under the 250 ms budget (09 sec7.2), and 09
# sec3 predicts 08's average contribution at ~0.4 points, which is below the
# serve-time SE. Scoring at the serve N would report a null produced by the
# estimator's own noise rather than by 08.
VALIDATE_N = 40_000
# 09 sec5.2: 08's horizon is exactly 10 s (08 sec5.2), so step 0 is exactly 10 s.
STEP0_HORIZON_S = 10.0
# A fallback when no lap time is observable; only ever scales the width of the
# partial first lap, never the number of laps.
DEFAULT_LAP_TIME_S = 90.0

_STREAM_RETIRE, _STREAM_EVEN, _STREAM_ODD, _STREAM_STEP0 = 0, 1, 2, 3
# Retirement is drawn once for the whole race, so its key must not move as
# the race progresses -- a sentinel lap index keeps it fixed (09 sec7.4).
_RETIRE_LAP_KEY = "race"


def _key(session_key, lap_index, stream):
    """A counter-based key, so the uniforms for a given simulated lap are
    byte-identical across every update within a race. 09 sec7.4: without this
    the layer emits ~0.5-point Monte Carlo jitter at 1 Hz and a consumer
    watching for a 1-point move sees a phantom every few seconds. The draws are
    regenerated from the counter, never cached -- 10,000 paths x 60 laps x 21
    pairs of float64 is ~100 MB.

    The same discipline is what makes 09 sec10's ablation readable: the 08-off
    arm draws the same uniforms, so the paired difference is not swamped by the
    ~0.5-point standard error of either arm on its own.
    """
    h = np.uint64(1469598103934665603)
    for part in (str(session_key), str(lap_index), str(stream)):
        for ch in part.encode("utf-8"):
            h = np.uint64((int(h) ^ ch) * 1099511628211 % (1 << 64))
    return np.array([h, np.uint64(stream + 1)], dtype=np.uint64)


def _uniforms(session_key, lap_index, stream, shape):
    """float32 rather than float64 -- the draws are compared against
    probabilities of order 1e-2, where float32's ~1e-7 resolution is six orders
    of magnitude finer than anything that could change a decision, and it halves
    both the generation cost and the memory traffic of the hot loop."""
    rng = np.random.Generator(np.random.Philox(key=_key(session_key, lap_index, stream)))
    return rng.random(shape, dtype=np.float32)


def tilt(w_ahead, w_behind):
    """09 sec5.4 as corrected -- see the module docstring."""
    total = w_ahead + w_behind
    return np.where(total > 0, 2.0 * w_behind / np.where(total > 0, total, 1.0), 1.0)


TILT_EQUAL_STRENGTH_IS_ONE = float(tilt(np.array([0.3]), np.array([0.3]))[0])
require(abs(TILT_EQUAL_STRENGTH_IS_ONE - 1.0) < 1e-12,
        "the strength tilt must reduce to 1.0 on equal strengths, or 09 sec2.3's "
        "measured background rate is no longer the calibration target")


def _compact(order, dead_at_slot, rows, slots):
    """Move retired cars to the tail, preserving the relative order of the rest.

    A retired car vacating a position is exactly how cars behind it gain a
    place, so "who is classified first" is "the first live car in the order"
    and nothing special is needed for retirement anywhere else.

    Done with one cumulative sum and a flat scatter rather than an argsort:
    argsort on (N, C) every lap is the most expensive thing that could be in
    this loop and buys nothing, because the ordering wanted here is
    stable-by-construction. A dead car's new slot is
    `n_alive + (slots before it) - (alive slots before it)`, which is the same
    cumsum read the other way round, so one pass does both halves.
    """
    n_paths, n_cars = order.shape
    alive = ~dead_at_slot
    ranks = np.cumsum(alive, axis=1, dtype=np.intp)
    n_alive = ranks[:, -1]
    new_slot = np.where(alive, ranks - 1, n_alive[:, None] + slots - ranks)
    out = np.empty(n_paths * n_cars, dtype=order.dtype)
    out[(new_slot + rows).ravel()] = order.ravel()
    return out.reshape(n_paths, n_cars), n_alive


def _swap_pass(order, w_arr, n_alive, slot_q, u, first_slot):
    """One checkerboard half-step: every non-overlapping adjacent pair starting
    at `first_slot` gets exactly one chance to invert.

    Even pairs then odd pairs, rather than all pairs at once, because
    simultaneous overlapping swaps are not well defined -- a car cannot trade
    places with both neighbours in the same instant. Each pair still gets one
    draw per lap, so the per-pair rate is unchanged from what was fitted.
    """
    n_slots = order.shape[1]
    stop = n_slots - 1
    if first_slot >= stop:
        return order
    # Strided slices rather than fancy indexing on an index array: `order[:,
    # first::2]` is a view, `order[:, ks]` is a gather that copies. Same pairs,
    # measurably less memory traffic, and this is the hot loop.
    a_sl = slice(first_slot, stop, 2)
    b_sl = slice(first_slot + 1, stop + 1, 2)
    a_idx = order[:, a_sl].copy()
    b_idx = order[:, b_sl].copy()
    # tilt(), inlined: strengths are floored above 0 when w_arr is built, so the
    # zero guard in the public tilt() cannot fire here and costs two extra
    # passes over the array if it is left in the hot loop.
    wa = w_arr[a_idx]
    wb = w_arr[b_idx]
    p = slot_q[a_sl][None, :] * (np.float32(2.0) * wb / (wa + wb))
    ks1 = np.arange(first_slot + 1, stop + 1, 2, dtype=np.intp)
    do = (u[:, a_sl] < p) & (ks1[None, :] < n_alive[:, None])
    order[:, a_sl] = np.where(do, b_idx, a_idx)
    order[:, b_sl] = np.where(do, a_idx, b_idx)
    return order


def forward_simulate(session_key, order_codes, strengths, hazards, background,
                     lap_current, lap_total, track_frac=0.0, m=1.0,
                     lap_time_s=DEFAULT_LAP_TIME_S, pursuits=(), n_paths=SERVE_N,
                     use_overtake_model=True, collect_orders=False):
    """P(classified first at the flag) for every code in `order_codes`.

    `order_codes` is the current classified order of cars still running; retired
    cars are the caller's business (they are exactly 0.0 and never enter here).
    `hazards` is {code: [per-lap retirement probability]} covering the current
    lap and every lap after it, from `winprob_priors.lap_hazards`.
    `pursuits` is 09 sec5.3's in-domain (pursuer, ahead, p_overtake) triples.

    `use_overtake_model=False` is 09 sec10 baseline 3, the ablation: step 0 is
    dropped and the same uniforms carry the rest of the race unchanged. That
    pairing is the whole point -- see `_key`.

    Returns (p_win, se_mc, info).
    """
    codes = list(order_codes)
    n_cars = len(codes)
    require(n_cars > 0, "forward_simulate: empty order")
    require(lap_total and lap_total > 0, "forward_simulate: lap_total must be positive")
    idx_of = {c: i for i, c in enumerate(codes)}

    if n_cars == 1:
        return ({codes[0]: 1.0}, {codes[0]: 0.0},
                {"n_paths": n_paths, "steps": 0, "laps_remaining": 0})

    w_arr = np.array([max(float(strengths.get(c, 0.0)), 1e-12) for c in codes],
                     dtype=np.float32)
    lap_current = max(int(lap_current or 1), 1)
    lap_idx0 = lap_current - 1                       # 0-based index of the current lap
    frac_left = max(0.0, 1.0 - float(track_frac))
    if lap_time_s and lap_time_s > 0:
        frac_left = max(0.0, frac_left - STEP0_HORIZON_S / float(lap_time_s))

    n_lap_steps = max(lap_total - lap_idx0, 0)
    haz = np.zeros((n_lap_steps, n_cars))
    for j, c in enumerate(codes):
        sched = hazards.get(c)
        if not sched:
            continue
        for l in range(min(n_lap_steps, len(sched))):
            haz[l, j] = float(sched[l])
    # The first step is a partial lap: the fraction of the current lap left
    # after t + 10 s (09 sec5.2 step 2). Both the background rate and the
    # retirement hazard are prorated by it; every later step is a whole lap.
    step_scale = np.ones(n_lap_steps)
    if n_lap_steps:
        step_scale[0] = frac_left

    order = np.tile(np.arange(n_cars, dtype=np.intp), (n_paths, 1))
    n_alive = np.full(n_paths, n_cars, dtype=np.intp)
    rows = (np.arange(n_paths, dtype=np.intp) * n_cars)[:, None]
    slots = np.arange(n_cars, dtype=np.intp)[None, :]

    # -- step 0: the next 10 seconds, where 08 enters exactly once (09 sec5.2).
    # It is deliberately NOT compounded up to lap scale: 1-(1-p)^9 assumes
    # independence across nine consecutive windows inside one pursuit, and 08
    # sec5.3 measured pursuit episodes at a median 46.4 s, so a window in which
    # the pass did not happen is strongly informative about the next one.
    step0_applied = []
    if use_overtake_model and pursuits:
        u0 = _uniforms(session_key, lap_idx0, _STREAM_STEP0, (n_paths, max(n_cars - 1, 1)))
        for pursuer, ahead, p_overtake in pursuits:
            i_p, i_a = idx_of.get(pursuer), idx_of.get(ahead)
            if i_p is None or i_a is None or i_p != i_a + 1:
                continue
            k = i_a
            do = u0[:, k] < np.float32(p_overtake)
            a_idx, b_idx = order[:, k].copy(), order[:, k + 1].copy()
            order[:, k] = np.where(do, b_idx, a_idx)
            order[:, k + 1] = np.where(do, a_idx, b_idx)
            step0_applied.append((pursuer, ahead, float(p_overtake)))

    n_steps = max(lap_total - lap_idx0, 0)

    # -- retirement, drawn once per (path, car) by inverse CDF rather than as a
    # Bernoulli coin on every simulated lap. Identical in distribution, ~60x
    # fewer random draws, and it strengthens 09 sec7.4's common random numbers:
    # this draw is keyed on the session alone, so it is byte-identical across
    # every update within a race and two consecutive estimates differ only
    # because the survival curve over the REMAINING laps differs.
    retire_step = np.full((n_paths, n_cars), n_steps + 1, dtype=np.intp)
    if n_steps > 0 and haz.size:
        surv = np.cumprod(1.0 - np.clip(haz, 0.0, 1.0) * step_scale[:, None], axis=0)
        u_ret = _uniforms(session_key, _RETIRE_LAP_KEY, _STREAM_RETIRE, (n_paths, n_cars))
        for c in range(n_cars):
            retire_step[:, c] = np.searchsorted(-surv[:, c], -u_ret[:, c].astype(np.float64),
                                                side="right")
    dies_at = set(int(v) for v in np.unique(retire_step) if v < n_steps)

    for step in range(n_steps):
        scale = float(step_scale[step])
        if scale <= 0.0:
            continue
        lap_index = lap_idx0 + step
        progress = (lap_index + 0.5) / float(lap_total)

        if step in dies_at:
            dead_at_slot = np.take(retire_step <= step, order + rows)
            order, n_alive = _compact(order, dead_at_slot, rows, slots)

        slot_q = np.array(background.slot_rates(n_cars, progress, m),
                          dtype=np.float32) * np.float32(scale)
        u_e = _uniforms(session_key, lap_index, _STREAM_EVEN, (n_paths, n_cars - 1))
        order = _swap_pass(order, w_arr, n_alive, slot_q, u_e, 0)
        u_o = _uniforms(session_key, lap_index, _STREAM_ODD, (n_paths, n_cars - 1))
        order = _swap_pass(order, w_arr, n_alive, slot_q, u_o, 1)

    winners = order[:, 0]
    counts = np.bincount(winners, minlength=n_cars).astype(float)
    p = counts / n_paths
    se = np.sqrt(np.maximum(p * (1.0 - p), 0.0) / n_paths)
    p_win = {codes[i]: float(p[i]) for i in range(n_cars)}
    se_mc = {codes[i]: float(se[i]) for i in range(n_cars)}
    info = {"n_paths": n_paths, "steps": n_steps, "laps_remaining": n_steps,
            "step0": step0_applied, "frac_left": frac_left,
            "mean_retired": float((retire_step < n_steps).sum(axis=1).mean())
                            if n_steps > 0 else 0.0}
    if collect_orders:
        info["orders"] = order
        info["codes"] = codes
    return p_win, se_mc, info
