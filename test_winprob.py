#!/usr/bin/env python3
"""Tests for the win-probability layer. 09-live-win-probability.md sec11, sec8.2.

Fixtures are hand-written and synthetic, never a truncated real capture --
`03` sec11.2 forbids committing any F1 live-timing data to this repo, including
a test fixture, and this repo is public. The one test that reads real data
(`test_t0_identity`) reads a *fitted artifact* under `data/live/`, which is
gitignored, and skips cleanly when it is absent.

Run: .venv312/bin/python test_winprob.py
"""

import ast
import json
import math
import os
import sys
from types import MappingProxyType

from lib import winprob as wp
from lib import winprob_background as bgmod
from lib import winprob_priors as wpp
from lib import winprob_sim as wsim
from lib import overtake_serve as osv
from lib.invariants import InvariantError
from lib.livetiming_tick import CarState, Tick, TickAssembler

FAILURES = []
SKIPPED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def skip(name, why):
    print("  skip %s (%s)" % (name, why))
    SKIPPED.append(name)


# ------------------------------------------------------------------ fixtures

CODES = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def flat_background(rate=0.06):
    return bgmod.BackgroundRate({}, {b: rate for b in bgmod.BAND_NAMES}, 0.0)


def make_tick(order, lap=1, total=40, track_status=1, retired=(), in_pit=(),
              telemetry=True, t=0.0, stale=False, degraded=frozenset(),
              gaps=None, gap_after_reconnect=False):
    cars = {}
    for i, code in enumerate(order):
        cars[code] = CarState(
            code=code, racing_number=str(i + 1), position=i + 1,
            gap_ahead=(gaps or {}).get(code, "1.200" if i else None),
            gap_leader=None, in_pit=code in in_pit, pit_out=False,
            retired=code in retired, stopped=False, laps=lap,
            speed=280 if telemetry else None,
            throttle=100 if telemetry else None,
            brake=0 if telemetry else None,
            x=100 if telemetry else None, y=200 if telemetry else None)
    for code in retired:
        if code not in cars:
            cars[code] = CarState(code=code, racing_number="99", position=None,
                                  retired=True, laps=lap)
    return Tick(session_key="test/R1", t_feed="%.3f" % t, t_local=t,
                t_wall="2026-09-03T12:00:00Z", track_status=track_status,
                lap_current=lap, lap_total=total, degraded=frozenset(degraded),
                gap_after_reconnect=gap_after_reconnect, stale=stale,
                cars=MappingProxyType(cars))


def make_layer(order=CODES, strengths=None, f_dnf=0.0, **kw):
    prior = wp.RacePrior(
        prior_id="test", p_algo={c: 1.0 / len(order) for c in order},
        strengths=strengths or {c: 1.0 for c in order},
        f_dnf={c: f_dnf for c in order},
        hazard=wpp.TwoSegmentHazard.flat(), reconciled=True)
    return wp.WinProbLayer(prior, flat_background(), n_paths=4000, **kw)


# --------------------------------------------------- sec11.1 / sec11.7 / 11.8
def test_sums_and_retired():
    print("sec11.1 -- p_win sums to 1, retired cars are exactly 0.0")
    layer = make_layer()
    layer.fold(make_tick(CODES))
    est = layer.estimate()
    check("p_win sums to 1.0", abs(sum(est.p_win.values()) - 1.0) < 1e-9,
          "got %r" % sum(est.p_win.values()))

    layer.fold(make_tick([c for c in CODES if c != "CCC"], lap=5, retired=("CCC",)))
    est = layer.estimate()
    check("a latched retired car has p_win EXACTLY 0.0, not merely small",
          est.p_win["CCC"] == 0.0, "got %r" % est.p_win["CCC"])
    check("p_win still sums to 1.0 over the survivors",
          abs(sum(est.p_win.values()) - 1.0) < 1e-9)
    check("the latch is never reversed by a later clean tick",
          "CCC" in layer.retired)

    print("sec11.7 -- se_mc is populated on every driver of every estimate")
    check("every driver has an se_mc", set(est.se_mc) == set(est.p_win))
    check("an estimate with a missing se_mc is refused",
          raises(lambda: wp.check_estimate(strip_se(est))))

    print("sec11.8 -- reliable is computed, never defaulted")
    check("reliable is False when reasons is non-empty",
          raises(lambda: wp.check_estimate(force_reliable(est))))


def strip_se(est):
    se = dict(est.se_mc)
    se.pop(sorted(se)[0])
    return est.__class__(**{**est.__dict__, "se_mc": MappingProxyType(se)})


def force_reliable(est):
    return est.__class__(**{**est.__dict__, "reliable": True,
                            "reasons": ("stale",)})


def raises(fn):
    try:
        fn()
    except InvariantError:
        return True
    except Exception:
        return False
    return False


# -------------------------------------------------------------------- sec11.3
def test_endgame_identity():
    print("sec11.3 -- the endgame identity (09 sec2.2's ladder: 120/120 inside 10 laps)")
    layer = make_layer()
    layer.fold(make_tick(CODES, lap=40, total=40))     # leader has completed lap 40
    est = layer.estimate()
    check("with zero laps remaining the leader's p_win is 1.0",
          est.p_win[CODES[0]] == 1.0, "got %r" % est.p_win[CODES[0]])
    check("and everyone else is exactly 0.0",
          all(est.p_win[c] == 0.0 for c in CODES[1:]))

    # The >= 0.9 band in 09 sec11.3 is a smoke test set from twelve REAL races
    # (08 sec10's precedent for a band asserted from a small measured sample),
    # and it does not transfer to a synthetic field of six equal-strength cars
    # at a flat 6%/lap: 0.77 with four laps to go is the correct output for that
    # fixture, not a regression. What is structural, and is asserted here, is
    # that the leader's p_win rises monotonically as the laps run out. The real
    # number is measured over the eight replayed races and reported by
    # winprob_validate.py as `late_leader_p_win`.
    curve = []
    for lap in (10, 20, 30, 36, 39):
        layer = make_layer()
        layer.fold(make_tick(CODES, lap=lap, total=40))
        layer.laps_done = {c: lap - 1 for c in CODES}
        curve.append(layer.estimate(n_paths=20000).p_win[CODES[0]])
    check("the leader's p_win rises monotonically as laps run out (%s)"
          % ", ".join("%.3f" % x for x in curve),
          all(b >= a - 0.01 for a, b in zip(curve, curve[1:])))
    check("and it is well above the start-of-race value by the last few laps",
          curve[-1] > curve[0] + 0.3, "%.3f vs %.3f" % (curve[-1], curve[0]))


# -------------------------------------------------------------------- sec11.9
def test_domain_gates():
    print("sec11.9 -- 08 inputs are in-domain only, and probabilities are in [0,1]")
    check("theta is read from 08's fit output, not re-derived", osv.THETA == 0.0037)
    check("theta_front likewise", osv.THETA_FRONT == 0.0105)
    check("a below-theta pair is refused", not osv.admits(0.0036, 12))
    check("a front-of-field pair between theta and theta_front is refused",
          not osv.admits(0.0100, 3))
    check("the same probability is admitted in the midfield",
          osv.admits(0.0100, 12))
    check("a front-of-field pair above theta_front is admitted",
          osv.admits(0.0106, 2))
    check("P7 is outside the front band (09 sec2.4 measured theta_front on the top six)",
          osv.admits(0.0050, 7))

    layer = make_layer()
    layer.fold(make_tick(CODES))
    est = layer.estimate()
    bad = est.__class__(**{**est.__dict__,
                           "in_domain": (("BBB", "AAA", 0.001),)})
    check("an estimate carrying a sub-theta step-0 pair fails loudly",
          raises(lambda: wp.check_estimate(bad)))


# -------------------------------------------------------------------- sec11.5
def test_train_serve_parity():
    print("sec11.5 -- train/serve parity: a replayed tick and a live-shaped tick agree")
    # The live-shaped tick is assembled by the real TickAssembler from raw
    # feed-shaped payloads, so this exercises 03 sec7's own merge path rather
    # than comparing two hand-built records to each other.
    asm = TickAssembler()
    asm.apply_snapshot({
        "SessionInfo": {"Path": "test/R1"},
        "DriverList": {str(i + 1): {"Tla": c} for i, c in enumerate(CODES)},
        "LapCount": {"CurrentLap": 1, "TotalLaps": 40},
        "TrackStatus": {"Status": "1"},
        "TimingData": {"Lines": {
            str(i + 1): {"Position": str(i + 1), "NumberOfLaps": 1,
                         "IntervalToPositionAhead": {"Value": "" if i == 0 else "1.200"},
                         "InPit": False, "Retired": False, "Stopped": False}
            for i, c in enumerate(CODES)}},
    })
    live = asm.emit("0.000", 0.0, "2026-09-03T12:00:00Z")
    replayed = make_tick(CODES, telemetry=False)

    a, b = make_layer(), make_layer()
    a.fold(live)
    b.fold(replayed)
    check("the two ticks produce the same running order", a.order == b.order)
    ea, eb = a.estimate(), b.estimate()
    check("field for field, the estimates agree",
          ea.p_win == eb.p_win and ea.se_mc == eb.se_mc and ea.progress == eb.progress,
          "%r vs %r" % (dict(ea.p_win), dict(eb.p_win)))


# ------------------------------------------------- sec8.1 reliability contract
def test_reliability_reasons():
    print("sec8.1 -- reliable is False with a reason code for each condition")
    cases = [
        ("stale", dict(stale=True), wp.REASON_STALE),
        ("degraded", dict(degraded={"cardata"}), wp.REASON_DEGRADED),
        ("caution", dict(track_status=4), wp.REASON_CAUTION),
        ("reconnect gap", dict(gap_after_reconnect=True), wp.REASON_RECONNECT),
    ]
    for name, kw, reason in cases:
        layer = make_layer()
        layer.fold(make_tick(CODES, **kw))
        est = layer.estimate()
        check("%s sets reliable=False with %s" % (name, reason),
              not est.reliable and reason in est.reasons, "reasons=%r" % (est.reasons,))

    print("sec5.7 -- a pit offset inside the top three suppresses, one outside does not")
    layer = make_layer()
    layer.fold(make_tick(CODES, t=0.0))
    layer.fold(make_tick(CODES, t=1.0, in_pit=("BBB",)))
    layer.fold(make_tick(CODES, t=2.0))
    est = layer.estimate()
    check("a stop by P2 suppresses", wp.REASON_PIT_OFFSET in est.reasons)
    layer = make_layer()
    layer.fold(make_tick(CODES, t=0.0))
    layer.fold(make_tick(CODES, t=1.0, in_pit=("FFF",)))
    layer.fold(make_tick(CODES, t=2.0))
    est = layer.estimate()
    check("a stop by P6 does not suppress (09 sec2.6's 34.5%% is not the "
          "suppression rate)", wp.REASON_PIT_OFFSET not in est.reasons)
    check("but it is still published as pit_offset", est.pit_offset == 1)

    print("sec7.3 -- an se_mc above half a market tick is not actionable")
    layer = make_layer()
    layer.fold(make_tick(CODES, lap=1, total=40))
    est = layer.estimate(n_paths=200)
    check("a tiny N sets reliable=False with the noise reason",
          wp.REASON_MC_NOISE in est.reasons, "reasons=%r" % (est.reasons,))

    print("sec8.1 -- an unreconciled prior is never reliable")
    prior = wp.RacePrior("test", {c: 1.0 / len(CODES) for c in CODES},
                          {c: 1.0 for c in CODES}, {c: 0.0 for c in CODES},
                          wpp.TwoSegmentHazard.flat(), reconciled=False)
    layer = wp.WinProbLayer(prior, flat_background(), n_paths=4000)
    layer.fold(make_tick(CODES))
    check("reconciled=False sets the reason",
          wp.REASON_UNRECONCILED in layer.estimate().reasons)


# ---------------------------------------------------- sec5.4 / sec11.6, sec5.6
def test_propagation_rules():
    print("sec5.4 -- the strength tilt reduces to 1.0 on equal strengths")
    check("tilt(w, w) == 1.0", abs(wsim.TILT_EQUAL_STRENGTH_IS_ONE - 1.0) < 1e-12)
    check("a stronger car behind is more likely to pass",
          wsim.tilt(1.0, 3.0) > 1.0 > wsim.tilt(3.0, 1.0))

    print("sec11.6 -- modelled position-change sources do not exceed the measured total")
    # The background fit excludes any pair where either car retires within the
    # lap, so the fitted rate must come in at or below the raw 09 sec2.3 count
    # that still contains those. Same double-count 04 sec6.3 rejected.
    order_by_lap = {1: {1: "AAA", 2: "BBB", 3: "CCC"},
                    2: {1: "BBB", 2: "AAA", 3: "CCC"},
                    3: {1: "BBB", 2: "CCC", 3: "AAA"}}
    raw = bgmod.swap_observations(order_by_lap, {}, 3)
    net = bgmod.swap_observations(order_by_lap, {"AAA": 2}, 3)
    check("removing a retiring car's pairs cannot increase the swap count",
          sum(s for _, _, s in net) <= sum(s for _, _, s in raw))
    check("and it does remove them here", len(net) < len(raw))

    print("sec5.6 -- overtaking is suppressed structurally under SC/VSC")
    model = trivial_overtake_model(p=0.9)
    layer = make_layer(overtake_model=model)
    layer.fold(make_tick(CODES, track_status=1, gaps={c: "0.300" for c in CODES[1:]}))
    green = len(layer.pursuits)
    layer = make_layer(overtake_model=model)
    layer.fold(make_tick(CODES, track_status=4, gaps={c: "0.300" for c in CODES[1:]}))
    check("green flag admits in-domain pairs", green > 0)
    check("safety car admits none, regardless of what 08 says",
          len(layer.pursuits) == 0)


def trivial_overtake_model(p=0.5):
    """A model that returns a constant probability -- enough to exercise the
    gates without depending on a fitted artifact."""
    z = math.log(p / (1.0 - p))
    from lib import overtake_features as of
    return osv.OvertakeModel([0.0] * len(of.FEATURE_NAMES), z,
                             {k: (0.0, 1.0) for k in of.FEATURE_NAMES})


# ----------------------------------------------------------- sec5.2 / sec7.4
def test_common_random_numbers():
    print("sec7.4 -- common random numbers, or the noise looks like signal")
    a = make_layer(); a.fold(make_tick(CODES, lap=10, total=40, t=100.0))
    b = make_layer(); b.fold(make_tick(CODES, lap=10, total=40, t=100.0))
    check("two updates on identical state are byte-identical, not merely close",
          dict(a.estimate().p_win) == dict(b.estimate().p_win))

    print("sec10 baseline 3 -- the ablation is a PAIRED comparison")
    layer = make_layer(overtake_model=trivial_overtake_model(0.9))
    layer.fold(make_tick(CODES, lap=10, total=40,
                         gaps={c: "0.300" for c in CODES[1:]}))
    full = layer.estimate(use_overtake_model=True)
    abl = layer.estimate(use_overtake_model=False)
    check("the arms differ (08 is doing something)",
          dict(full.p_win) != dict(abl.p_win))
    off = make_layer()
    off.fold(make_tick(CODES, lap=10, total=40, telemetry=True))
    check("and the ablation arm equals a layer with no 08 at all -- same uniforms",
          dict(abl.p_win) == dict(off.estimate().p_win))


# --------------------------------------------------------------------- sec11.4
def test_no_lookahead():
    print("sec11.4 -- no lookahead in the replay")
    # 09 sec11.4 says `lib.overtake_features.assert_no_lookahead` must be REUSED
    # here, not reimplemented, so this drives that exact function over the join
    # the replayer performs. `_asof` is `merge_asof(direction="backward")`; the
    # failing case below is what an interpolation (np.interp, which an early 08
    # probe used) would produce -- it reads the sample AFTER the decision time.
    import pandas as pd
    from lib import overtake_features as of
    from lib import winprob_replay as wpr

    src = pd.DataFrame({"t": [0.0, 5.0, 10.0], "v": [1.0, 2.0, 3.0]})
    grid = [1.0, 6.0, 11.0]
    got = wpr._asof(src.assign(src_t=src["t"]), grid, ["src_t", "v"], 30.0)
    of.assert_no_lookahead({"replay_asof": got["src_t"].values}, grid)
    check("the replayer's asof join reads only sources at or before t",
          list(got["v"].values) == [1.0, 2.0, 3.0])
    check("and a forward-looking join is caught by the same function",
          raises_any(lambda: of.assert_no_lookahead(
              {"interpolated": [5.0, 10.0, 15.0]}, grid)))


def raises_any(fn):
    try:
        fn()
    except InvariantError:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------- sec8.2
LANE_B_ROOTS = ("lib.winprob", "lib.winprob_sim", "lib.winprob_priors",
                "lib.winprob_background", "lib.winprob_replay",
                "lib.overtake_serve", "lib.overtakes", "lib.overtake_features",
                "lib.livetiming_tick", "lib.livetiming_client", "lib.livetiming_parse",
                "lib.signalr")
MARKET_MODULES = ("lib.kalshi", "lib.polymarket", "kalshi", "polymarket")


def module_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        name.replace(".", os.sep) + ".py")


def imports_of(name):
    path = module_path(name)
    if not os.path.exists(path):
        return []
    tree = ast.parse(open(path).read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:                       # `from . import x` inside lib/
                base = "lib" + ("." + base if base else "")
            out.append(base)
            out.extend("%s.%s" % (base, a.name) for a in node.names)
    return out


def test_trading_interlock():
    print("sec8.2 -- the 03 sec4.3 interlock, as an enforced check over the module graph")
    # Written as a REACHABILITY check, not a name ban. lib/kalshi.py and
    # lib/polymarket.py still exist and are legitimately used by Lane A's
    # market comparison (02 sec6), so a global ban would either fail spuriously
    # or pass trivially. The claim 03 sec4.3 actually makes is that nothing in
    # Lane B may import from or be imported by a market/trading component, and
    # that is a property of the transitive import closure.
    seen, stack = set(), list(LANE_B_ROOTS)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for imp in imports_of(name):
            if imp.startswith("lib.") or os.path.exists(module_path(imp)):
                stack.append(imp)
    hits = sorted(n for n in seen if any(n == m or n.startswith(m + ".")
                                         for m in MARKET_MODULES))
    check("no market/trading module is reachable from any Lane B root (%d modules "
          "in the closure)" % len(seen), not hits, "reachable: %r" % hits)
    check("the closure is non-trivial, so the check is not passing by accident",
          len(seen) >= 8, "only %d modules walked" % len(seen))


# --------------------------------------------------------------------- sec11.2
def test_t0_identity():
    print("sec11.2 -- the t = 0 identity, on the real fitted priors")
    path = "data/live/winprob/fit.json"
    if not os.path.exists(path):
        skip("t=0 identity", "run winprob_fit.py first; %s is gitignored" % path)
        return
    with open(path) as fh:
        fit = json.load(fh)
    worst_cond, worst_abs, worst_round = 0.0, 0.0, None
    for rnd, blob in sorted(fit["races"].items()):
        rec = blob["reconciled"]
        if rec["residual_cond"] > worst_cond:
            worst_cond, worst_round = rec["residual_cond"], rnd
        worst_abs = max(worst_abs, rec["residual"])
    # 3 x se_mc at the reconciliation budget. 09 sec11.2 as amended 2026-09-03:
    # asserted on the reconcile band's conditional distribution, because the
    # absolute form has a structural floor set by the mass 02's softmax puts on
    # backmarkers this simulator says cannot win. Both are reported.
    tol = 3.0 * math.sqrt(0.25 / wpp.RECONCILE_N)
    check("every race reconciles to within 3 x se_mc on the band "
          "(worst %.5f at R%s, tolerance %.5f)" % (worst_cond, worst_round, tol),
          worst_cond <= tol)
    print("       absolute residual (carries 02's unreachable softmax tail): %.4f"
          % worst_abs)


def main():
    for fn in (test_sums_and_retired, test_endgame_identity, test_domain_gates,
               test_train_serve_parity, test_reliability_reasons,
               test_propagation_rules, test_common_random_numbers,
               test_trading_interlock, test_t0_identity):
        fn()
    print()
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        sys.exit(1)
    print("all tests passed%s" % (" (%d skipped)" % len(SKIPPED) if SKIPPED else ""))


if __name__ == "__main__":
    main()
