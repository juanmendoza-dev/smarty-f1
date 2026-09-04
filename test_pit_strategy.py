#!/usr/bin/env python3
"""Tests for the pit-strategy model. `docs/12` sec7's seven assertions, sec5.3.

Fixtures are hand-written and synthetic, never a truncated real capture: `03`
sec11.2 forbids committing F1 live-timing data to this repo, test fixture
included, and the repo is public. The one test that reads real data reads the
*fitted* delta artifact under `data/live/` -- gitignored -- and skips when it
is absent.

Every one of 12 sec7's assertions gets a test that can FAIL, not one that
merely runs the path: `lib/winprob_replay.degrade_every`'s docstring records
what this project thinks of a test that cannot fail. So the positive controls
come first -- a projection that actually moves a car -- and the assertions are
checked against a model that is demonstrably doing something.

Run: .venv312/bin/python test_pit_strategy.py
"""

import json
import os
import sys
from types import MappingProxyType

from lib import pit_loss as plm
from lib import pit_strategy as pit
from lib import winprob as wp
from lib import winprob_background as bgmod
from lib import winprob_priors as wpp
from lib.invariants import InvariantError
from lib.livetiming_tick import CarState, Tick

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


def raises(fn):
    try:
        fn()
    except InvariantError:
        return True
    except Exception:                               # noqa: BLE001
        return False
    return False


# ------------------------------------------------------------------ fixtures

CODES = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
# One second per place, so a delta of n seconds drops a car exactly n places.
# That makes every expected index in this file arithmetic a reader can check by
# eye rather than a number the model produced and the test then blessed.
GAPS = {"AAA": 0.0, "BBB": 1.0, "CCC": 2.0, "DDD": 3.0, "EEE": 4.0, "FFF": 5.0}

TEST_TABLE = {(2026, "testcircuit"): (3.0, 0.5, 40),
              (2026, "zerocircuit"): (0.0, 0.0, 40),
              (2026, "thincircuit"): (30.0, 1.0, 4)}


def make_tick(order=CODES, lap=5, total=40, track_status=1, in_pit=(),
              pit_out=(), retired=(), t=0.0, stale=False, degraded=frozenset(),
              gaps=None, gap_override=None):
    g = dict(GAPS if gaps is None else gaps)
    cars = {}
    for i, code in enumerate(order):
        raw = (gap_override or {}).get(code)
        if raw is None:
            # P1's GapToLeader is the feed's lap-count form, not a number:
            # `lib/pit_strategy.gap_seconds` reads the leader as 0.0 from its
            # position instead, and 08 sec13.6 item 3 is why it is never coerced.
            raw = "LAP %d" % lap if i == 0 else "%.3f" % g[code]
        cars[code] = CarState(
            code=code, racing_number=str(i + 1), position=i + 1,
            gap_leader=raw, gap_ahead="1.000", in_pit=code in in_pit,
            pit_out=code in pit_out, retired=code in retired, stopped=False,
            laps=lap, speed=280, throttle=100, brake=0, x=1, y=2)
    return Tick(session_key="test/R1", t_feed="%.3f" % t, t_local=t,
                t_wall="2026-09-04T12:00:00Z", track_status=track_status,
                lap_current=lap, lap_total=total, degraded=frozenset(degraded),
                gap_after_reconnect=False, stale=stale,
                cars=MappingProxyType(cars))


def projector(circuit="testcircuit"):
    return pit.PitProjector(2026, circuit, table=TEST_TABLE)


def run(proj, ticks):
    """Fold every tick, project on the last one."""
    for tk in ticks:
        proj.fold(tk)
    return proj.project(ticks[-1], list(CODES))


def background(pit_removed):
    return bgmod.BackgroundRate({}, {b: 0.06 for b in bgmod.BAND_NAMES}, 0.0,
                                meta={"pit_swaps_removed": pit_removed})


def make_layer(pit_removed=True, projector_=None, order=CODES):
    prior = wp.RacePrior(
        prior_id="test", p_algo={c: 1.0 / len(order) for c in order},
        strengths={c: 1.0 for c in order}, f_dnf={c: 0.0 for c in order},
        hazard=wpp.TwoSegmentHazard.flat(), reconciled=True)
    return wp.WinProbLayer(prior, background(pit_removed), n_paths=4000,
                           pit_projector=projector_)


# ------------------------------------------------- positive control, first
def test_projection_moves_a_car():
    print("12 sec5.2 -- the projection actually reorders the field")
    proj = projector()
    # BBB (gap 1.0) enters the pit lane. delta = 3.0 s, nothing elapsed, so it
    # projects to gap 4.0 -- behind CCC (2.0), DDD (3.0), ahead of EEE (4.0).
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0)])
    check("a car in the pit lane is projected", len(c.projections) == 1)
    p = c.projections[0]
    check("projected gap is its own plus the whole delta (1.0 + 3.0 = %.1f)"
          % p.projected_gap, abs(p.projected_gap - 4.0) < 1e-9)
    check("it drops exactly the two cars inside that window (order %s)" % c.order,
          c.order == ["AAA", "CCC", "DDD", "BBB", "EEE", "FFF"])
    check("places_lost is reported as 2", p.places_lost == 2)
    check("Correction.changed is True", c.changed)

    # Half the cycle spent: 1.5 s still owed, so it drops only past CCC.
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0),
                   make_tick(in_pit=["BBB"], t=1.5)])
    check("the projection shrinks as the cycle is spent (order %s)" % c.order,
          c.order == ["AAA", "CCC", "BBB", "DDD", "EEE", "FFF"])

    # Spent past delta: the remaining loss is clamped at zero, never negative.
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0),
                   make_tick(in_pit=["BBB"], t=30.0)])
    check("time beyond delta clamps at zero rather than going negative",
          c.projections[0].remaining_s == 0.0 and c.order == list(CODES))

    # Two cars stopping at once -- 12 sec2.4's double stack.
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB", "CCC"], t=0.0)])
    # BBB (1.0) rejoins at 4.0 and CCC (2.0) at 5.0, against DDD 3.0 and
    # EEE 4.0 standing still -- so BBB cuts in behind DDD and CCC behind EEE.
    # Counting each one's drop against the ORIGINAL indices puts BBB ahead of
    # DDD, which is the bug this case exists to catch.
    check("a double stack cuts both in against the cars standing still "
          "(order %s)" % c.order,
          c.order == ["AAA", "DDD", "BBB", "EEE", "CCC", "FFF"])


# ------------------------------------------------------ sec7 assertion 1
def test_delta_zero_is_the_identity():
    print("12 sec7 assertion 1 -- delta = 0 is the identity, field for field")
    for in_pit in (["BBB"], ["AAA"], ["FFF"], ["BBB", "EEE"], list(CODES)):
        proj = projector("zerocircuit")
        c = run(proj, [make_tick(in_pit=in_pit, t=0.0),
                       make_tick(in_pit=in_pit, t=7.0)])
        check("delta=0 leaves the order untouched with %s in the pit lane"
              % ",".join(in_pit), c.order == list(CODES), "got %s" % c.order)
        check("  ... and reports no movement", not c.changed)

    # The identity has to survive gaps that disagree with track position -- an
    # asof join against a stale sample is 03 sec8's world, not a hypothetical.
    inverted = {"AAA": 0.0, "BBB": 9.0, "CCC": 2.0, "DDD": 3.0,
                "EEE": 4.0, "FFF": 5.0}
    proj = projector("zerocircuit")
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0, gaps=inverted)])
    check("delta=0 is the identity even when a gap contradicts the order",
          c.order == list(CODES), "got %s" % c.order)


# ------------------------------------------------------ sec7 assertion 2
def test_never_gains_a_place():
    print("12 sec7 assertion 2 -- a projection never gains a place for free")
    worst = 0
    for circuit in ("testcircuit", "zerocircuit", "thincircuit"):
        for elapsed in (0.0, 0.5, 3.0, 12.5, 60.0):
            for code in CODES:
                proj = projector(circuit)
                c = run(proj, [make_tick(in_pit=[code], t=0.0),
                               make_tick(in_pit=[code], t=elapsed)])
                for p in c.projections:
                    worst = min(worst, p.places_lost)
                    if p.places_lost < 0:
                        check("%s/%s/%.1fs gained a place" % (circuit, code, elapsed),
                              False)
                        return
                obs = list(CODES).index(code)
                new = c.order.index(code)
                if new < obs:
                    check("%s/%s/%.1fs moved forward in the order"
                          % (circuit, code, elapsed), False)
                    return
    check("no projection over 3 circuits x 5 elapsed times x 6 cars moved a car "
          "forward (worst places_lost = %d)" % worst, worst >= 0)

    # The regression this assertion actually caught, on R7 of the real archive.
    # BBB is in the pit lane with an unreadable gap, so 12 sec5.3 refuses it and
    # it keeps its observed slot -- it is STATIONARY. EEE is projected past FFF.
    # Counting BBB as non-stationary in one place and stationary in the other
    # put EEE ahead of FFF, which had not moved at all.
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB", "EEE"], t=0.0,
                             gap_override={"BBB": "LAP 5"})])
    check("a refused car in the lane is treated as standing still",
          c.refusals.get("BBB") == pit.REFUSE_GAP and len(c.projections) == 1)
    check("the projected car still drops behind the car that never moved "
          "(order %s)" % c.order,
          c.order == ["AAA", "BBB", "CCC", "DDD", "FFF", "EEE"])

    # And a gap that reads AHEAD of the stopping car cannot drag it backwards
    # on noise: the comparison is floored at the car's own gap.
    inverted = dict(GAPS, EEE=0.5)
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0, gaps=inverted)])
    check("a car behind with an impossible gap is not counted as passing it",
          c.projections[0].places_lost == 2, "got %d" % c.projections[0].places_lost)


# ------------------------------------------------------ sec7 assertion 3
def test_projection_is_provisional():
    print("12 sec7 assertion 3 -- observation replaces the projection, no blending")
    proj = projector()
    t0 = make_tick(in_pit=["BBB"], t=0.0)
    t1 = make_tick(in_pit=["BBB"], t=1.0)
    proj.fold(t0)
    proj.fold(t1)
    check("mid-cycle the car is projected",
          len(proj.project(t1, list(CODES)).projections) == 1)

    # pit_out fires and a numeric gap arrives on the same tick.
    t2 = make_tick(in_pit=(), pit_out=["BBB"], t=2.0)
    proj.fold(t2)
    c = proj.project(t2, list(CODES))
    check("on the tick the real gap lands, nothing is projected",
          not c.projections and not c.in_cycle)
    check("the order is the observed one, not a blend", c.order == list(CODES))
    check("the cycle is recorded as closed by observation",
          proj.completed.get("BBB") == 1 and not proj.cycles)

    # And it does not come back on the next tick.
    t3 = make_tick(in_pit=(), t=3.0)
    proj.fold(t3)
    check("no carry-over on the following tick",
          not proj.project(t3, list(CODES)).projections)

    # A car that leaves the lane while its gap is still unreadable stays in
    # OUT_LAP: the projection is a bridge across the cycle, and the cycle is
    # not over until there is something to replace it with.
    proj = projector()
    proj.fold(make_tick(in_pit=["BBB"], t=0.0))
    t_out = make_tick(in_pit=(), t=1.0, gap_override={"BBB": "LAP 5"})
    proj.fold(t_out)
    check("pit exit with no readable gap holds the cycle open in OUT_LAP",
          proj.cycles.get("BBB") is not None
          and proj.cycles["BBB"].state == pit.OUT_LAP)

    # ... but not forever. A lapped car reads the `LAP n` form for the rest of
    # the race, so waiting for a numeric gap waits for something that never
    # arrives. Measured on R7 before this closed: 8 cycles still open at the
    # flag, the oldest 43 laps past its stop, every one of them counted as
    # mid-cycle by 12 sec4's suppression rule.
    proj.fold(make_tick(in_pit=(), t=2.9, gap_override={"BBB": "LAP 5"}))
    check("the cycle is still open while the projection still owes time",
          "BBB" in proj.cycles)
    proj.fold(make_tick(in_pit=(), t=3.1, gap_override={"BBB": "LAP 5"}))
    check("it closes once elapsed passes delta, where the projection is already "
          "the identity", "BBB" not in proj.cycles
          and proj.completed.get("BBB") == 1)
    for t in (60.0, 600.0):
        proj.fold(make_tick(in_pit=(), t=t, gap_override={"BBB": "LAP 5"}))
    check("and it does not reopen on later ticks", "BBB" not in proj.cycles)


# ------------------------------------------------------ sec7 assertion 4
def test_no_pit_cycle_double_count():
    print("12 sec7 assertion 4 -- scoring against an un-refit q fails LOUDLY")
    check("a layer with the projection and a q that still contains pit swaps "
          "refuses to be built",
          raises(lambda: make_layer(pit_removed=False, projector_=projector())))
    ok = True
    try:
        make_layer(pit_removed=True, projector_=projector())
    except Exception as e:                          # noqa: BLE001
        ok = False
        print("       %r" % e)
    check("the same layer against a refit q builds", ok)
    check("a layer WITHOUT the projection is unaffected by the flag",
          make_layer(pit_removed=False, projector_=None) is not None)

    # The flag is a property of the fit, not of the caller's intent.
    fitted = bgmod.fit_background([
        {"circuit_id": "monza", "observations": [(1, 0.5, 0)] * 50,
         "pit_swaps_removed": True}])
    check("fit_background records the exclusion on the rate it produces",
          fitted.pit_swaps_removed)
    check("a rate fitted across both kinds of race is refused",
          raises(lambda: bgmod.fit_background([
              {"circuit_id": "monza", "observations": [(1, 0.5, 0)] * 50,
               "pit_swaps_removed": True},
              {"circuit_id": "spa", "observations": [(1, 0.5, 0)] * 50,
               "pit_swaps_removed": False}])))
    check("a rate deserialised from a pre-12 fit.json reads as un-refit",
          not bgmod.BackgroundRate.from_dict(
              bgmod.BackgroundRate({}, {"P1-P3": 0.06}, 0.0).as_dict()
          ).pit_swaps_removed)


# ------------------------------------------------------ sec7 assertion 5
def test_refusals():
    print("12 sec5.3 / sec7 assertion 5 -- refused, never approximated")
    cases = [
        ("under a safety car", dict(track_status=2), pit.REFUSE_CAUTION),
        ("under a VSC", dict(track_status=6), pit.REFUSE_CAUTION),
        ("on a degraded tick", dict(degraded={"cardata"}), pit.REFUSE_DEGRADED),
        ("on a stale tick", dict(stale=True), pit.REFUSE_STALE),
    ]
    for label, kw, reason in cases:
        proj = projector()
        c = run(proj, [make_tick(in_pit=["BBB"], t=0.0, **kw)])
        check("no projection %s" % label,
              not c.projections and c.refusals.get("BBB") == reason,
              "got %r" % (c.refusals,))
        check("  ... and the order is left as observed", c.order == list(CODES))

    # The LAP n form: 08 sec13.6 item 3 says it is not a lapped car and its
    # semantics are UNVERIFIED, so it is dropped rather than coerced.
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0,
                             gap_override={"BBB": "LAP 12"})])
    check("no projection from a `LAP n` gap",
          not c.projections and c.refusals.get("BBB") == pit.REFUSE_GAP)
    proj = projector()
    c = run(proj, [make_tick(in_pit=["BBB"], t=0.0, gap_override={"BBB": ""})])
    check("no projection from an empty gap",
          not c.projections and c.refusals.get("BBB") == pit.REFUSE_GAP)
    check("gap_seconds never coerces a non-numeric form",
          pit.parse_interval("LAP 12") is None and pit.parse_interval("") is None)

    # A red flag discards the state machine outright (03 sec9.5).
    proj = projector()
    proj.fold(make_tick(in_pit=["BBB"], t=0.0))
    check("a cycle is open before the red flag", "BBB" in proj.cycles)
    proj.fold(make_tick(in_pit=["BBB"], t=1.0, track_status=pit.RED_FLAG_STATUS))
    check("the red flag discards every open pit cycle", not proj.cycles)
    check("it is re-derived from the first tick after the restart",
          (proj.fold(make_tick(in_pit=["BBB"], t=200.0)) or "BBB") in proj.cycles)

    # A thin circuit still projects -- flagged, on the pooled delta (12 sec5.1).
    proj = projector("thincircuit")
    check("a circuit under MIN_STOPS falls back to the pooled delta and is flagged",
          proj.pit_loss.flagged and proj.pit_loss.delta_s == plm.POOLED_DELTA_S)
    proj = projector("neverraced")
    check("an unmeasured circuit does the same", proj.pit_loss.flagged)


# ------------------------------------------------------ sec7 assertion 6
def test_latch_discipline():
    print("12 sec7 assertion 6 -- the state machine never runs backwards")
    # Times deliberately inside delta: a cycle whose elapsed time has run past
    # delta closes on its own (see test_projection_is_provisional), and this
    # test is about the latch rather than about the close.
    proj = projector()
    proj.fold(make_tick(in_pit=["BBB"], t=0.0))
    check("entry puts the car in ENTERING", proj.cycles["BBB"].state == pit.ENTERING)
    proj.fold(make_tick(in_pit=["BBB"], t=0.5))
    check("a second in-pit tick advances to IN_PIT",
          proj.cycles["BBB"].state == pit.IN_PIT)
    # in_pit flickers false, then true again: 03 sec7.4's parsing artifact.
    proj.fold(make_tick(in_pit=(), t=1.0, gap_override={"BBB": "LAP 5"}))
    check("leaving the lane advances to OUT_LAP",
          proj.cycles["BBB"].state == pit.OUT_LAP)
    proj.fold(make_tick(in_pit=["BBB"], t=1.5, gap_override={"BBB": "LAP 5"}))
    check("in_pit going true again does NOT drag the state back to IN_PIT",
          proj.cycles["BBB"].state == pit.OUT_LAP)
    check("the raw transition is refused if something asks for it directly",
          raises(lambda: proj.cycles["BBB"].advance(pit.IN_PIT)))
    check("and so is a jump back to RUNNING",
          raises(lambda: proj.cycles["BBB"].advance(pit.RUNNING)))


# ------------------------------------------------------ sec7 assertion 7
def test_09_assertions_still_hold():
    print("12 sec7 assertion 7 -- 09 sec11's assertions survive the model")
    proj = projector()
    layer = make_layer(projector_=proj)
    plain = make_layer(projector_=None, pit_removed=True)

    # sec11.2's t=0 identity. At lights-out no car is in the pit lane, so this
    # model must not disturb the baseline case at all.
    t0 = make_tick(lap=1, t=0.0)
    layer.fold(t0)
    plain.fold(t0)
    a, b = layer.estimate(), plain.estimate()
    check("at t=0 the corrected order IS the observed order",
          layer.pit_correction().order == layer.running_order())
    check("at t=0 the estimate is identical to the layer without the model",
          all(abs(a.p_win[c] - b.p_win[c]) < 1e-12 for c in CODES),
          "%r vs %r" % (dict(a.p_win), dict(b.p_win)))
    check("sec11.2's t=0 identity is untouched: p_win still sums to 1",
          abs(sum(a.p_win.values()) - 1.0) < 1e-6)

    # sec11.1, mid-cycle: the correction is a permutation, so the field the
    # estimate is over is the same field the tick carries.
    tk = make_tick(lap=5, t=100.0, in_pit=["BBB"])
    layer.fold(tk)
    est = layer.estimate()
    check("mid-cycle p_win still sums to 1",
          abs(sum(est.p_win.values()) - 1.0) < 1e-6)
    check("mid-cycle p_win still covers exactly the field",
          set(est.p_win) == set(CODES))
    check("wp.check_estimate passes on a corrected estimate", wp.check_estimate(est))
    check("the projection is reported in its own fields",
          len(est.pit_projected) == 1 and est.pit_order_changed)
    check("09 sec5.7's published pit_offset keeps its raw meaning "
          "(owner's call, sec9 item 4)", est.pit_offset == layer.pit_offset)

    # sec11.1's retirement zero, with a car retired and another mid-cycle.
    tk = make_tick(lap=6, t=200.0, in_pit=["CCC"], retired=["FFF"])
    layer.fold(tk)
    est = layer.estimate()
    check("a retired car is still exactly 0.0 with the model active",
          est.p_win["FFF"] == 0.0)
    check("and p_win still sums to 1", abs(sum(est.p_win.values()) - 1.0) < 1e-6)


# --------------------------------------------- the narrowed suppression rule
def test_narrowed_suppression():
    print("12 sec4 -- 09 sec5.7's rule narrows to the unprojectable")
    # Old rule: a top-three stop-count spread suppresses, cycle or no cycle.
    plain = make_layer(projector_=None, pit_removed=False)
    plain.fold(make_tick(lap=5, t=0.0, in_pit=["AAA"]))
    plain.fold(make_tick(lap=6, t=100.0))
    check("without the model, a completed top-3 stop suppresses the estimate",
          wp.REASON_PIT_OFFSET in plain.estimate().reasons)

    layer = make_layer(projector_=projector())
    layer.fold(make_tick(lap=5, t=0.0, in_pit=["AAA"]))
    layer.fold(make_tick(lap=6, t=100.0))
    est = layer.estimate()
    check("with the model, a top-3 offset with nobody in a cycle no longer does",
          wp.REASON_PIT_OFFSET not in est.reasons)
    check("  ... and the raw offset is still published for the record",
          layer.pit_offset_top3 > 0)

    # A top-three car mid-cycle that CANNOT be projected still suppresses.
    layer = make_layer(projector_=projector())
    layer.fold(make_tick(lap=5, t=0.0, in_pit=["BBB"],
                         gap_override={"BBB": "LAP 5"}))
    check("a top-3 cycle with an unreadable gap still suppresses",
          wp.REASON_PIT_OFFSET in layer.estimate().reasons)

    # ... and one that CAN be projected does not.
    layer = make_layer(projector_=projector())
    layer.fold(make_tick(lap=5, t=0.0, in_pit=["BBB"]))
    check("a top-3 cycle the model can project does not suppress",
          wp.REASON_PIT_OFFSET not in layer.estimate().reasons)


# --------------------------------------------------- the served delta table
def test_delta_table_matches_the_fit():
    print("12 sec5.1 -- the served table is the fitted one")
    check("the pooled fallback is 12 sec2.1's figure",
          plm.POOLED_DELTA_S == 22.8 and plm.POOLED_N == 286)
    check("every circuit under MIN_STOPS is flagged rather than served",
          all(plm.delta_for(s, c).flagged
              for (s, c), row in plm.DELTA_TABLE.items() if row[2] < plm.MIN_STOPS))
    check("China is the case that exists today (n=4)",
          plm.DELTA_TABLE[(2026, "shanghai")][2] == 4
          and plm.delta_for(2026, "shanghai").delta_s == plm.POOLED_DELTA_S)
    check("a negative delta is refused outright",
          raises(lambda: plm.PitLoss(-1.0, 0.0, 40, False, 2026, "x")))

    path = "data/live/winprob/pit_loss.json"
    if not os.path.exists(path):
        skip("served table matches pit_fit.py's artifact", "%s absent" % path)
        return
    with open(path) as fh:
        blob = json.load(fh)
    fitted = plm.table_from_fit(blob)
    drift = []
    for key, row in fitted.items():
        served = plm.DELTA_TABLE.get(key)
        if served is None or abs(served[0] - row[0]) > 0.05 or served[2] != row[2]:
            drift.append("%s: served %r fitted (%.2f, %.2f, %d)"
                         % (key, served, row[0], row[1], row[2]))
    check("the served table matches the fitted artifact", not drift,
          "; ".join(drift))
    check("the artifact's pooled figure matches the served fallback",
          abs(blob["pooled"]["delta"] - plm.POOLED_DELTA_S) < 0.05
          and blob["pooled"]["n"] == plm.POOLED_N,
          "artifact %r" % (blob["pooled"],))


# ------------------------------------------------------- the q refit window
def test_pit_exclusion_window():
    print("12 sec4 -- the pre-registered q exclusion window")
    # AAA ahead of BBB for six laps; BBB pits on lap 3 and takes the place back
    # on lap 4. The swap at 3->4 is a pit-cycle swap and must not reach q.
    order = {1: {1: "AAA", 2: "BBB"}, 2: {1: "AAA", 2: "BBB"},
             3: {1: "AAA", 2: "BBB"}, 4: {1: "BBB", 2: "AAA"},
             5: {1: "BBB", 2: "AAA"}, 6: {1: "BBB", 2: "AAA"}}
    base = bgmod.swap_observations(order, {}, 6)
    cut = bgmod.swap_observations(order, {}, 6, pit_laps_by_code={"BBB": {3}})
    check("the pit-cycle swap is in q when the window is not applied",
          sum(o[2] for o in base) == 1, "%r" % (base,))
    check("it is gone once the window is applied", sum(o[2] for o in cut) == 0)
    check("laps 2, 3 and 4 are all dropped for a stop on lap 3 "
          "(in-lap, out-lap, both endpoints)", len(base) - len(cut) == 3,
          "%d vs %d" % (len(base), len(cut)))
    check("laps outside the window survive", len(cut) == 2)


def main():
    for fn in (test_projection_moves_a_car, test_delta_zero_is_the_identity,
               test_never_gains_a_place, test_projection_is_provisional,
               test_no_pit_cycle_double_count, test_refusals,
               test_latch_discipline, test_09_assertions_still_hold,
               test_narrowed_suppression, test_delta_table_matches_the_fit,
               test_pit_exclusion_window):
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
