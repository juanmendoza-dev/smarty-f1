# 12 — Pit-Strategy Model (pit-loss projection)

Status: **specced 2026-09-04; not approved; not built.** No code for this model exists or is
authorized to exist (`welcome.md`: no implementation without an approved spec). The measurements
behind it are committed under `probes/12_pit_loss.py` and `probes/12b_pit_projection.py`, and §11
reproduces every number below.

This is the model `09` §5.7 names as "the most valuable thing this layer could gain" and `09` §13
item 2 puts to the owner as a funding decision. It exists because `09` §2.1 measured **pit stops
behind 71% of lead changes** and `09` §5.7 then declined to model them, on the stated ground that
doing so inside `09` would be scope creep.

Read `09-live-win-probability.md` (§2.1, §2.6, §5.4, §5.7, §10's results), `08-overtake-model.md`
§2.1, and `11-features-tested-and-rejected.md`'s pit-execution entry before this.

---

## 1. What this is, and the one thing it is not

**What it is.** A projection: given that a car is *in the pit lane right now* — `CarState.in_pit`
says so, and `03` §7.1 carries it — where will it rejoin, relative to the cars it is racing? The
answer is a track-position correction applied inside `09`'s state estimator, computed from one
measured per-circuit quantity (`δ`, the time a stop costs) and the gaps the tick already carries.

**What it is not: a model of *when* a car will stop.** That is a different and much harder model,
and §2.4 measures that this corpus does not support it. The distinction is the single most
important scoping decision in this document, and it is made on a measurement rather than on
convenience.

**Not a repeat of `11`'s null.** `11` records **pit-crew execution quality** — a race-aggregate,
*pre-race* feature asking whether a team's average stationary time predicts race outcome — as
tested and null. That is a different claim from this one in three ways: it was pre-race where this
is in-race, aggregate where this is per-stop, and about *crew speed* where this is about
*track position through a cycle*. `11` already says "reopen if" and points here. This document does
not reopen it and does not use crew speed as a feature.

---

## 2. Measurements, run before the spec was written

`08` §2 established the convention and the reason: documentary research on this project has a
demonstrated failure mode of confident wrong answers, so numbers come first. Two probe passes were
run — one before `09`'s B4 build, one during it, because building B4 raised questions the first
pass had not asked.

### 2.1 δ — what a stop costs, per circuit

In-lap plus out-lap against the driver's own green-lap median, over 12 archived 2026 rounds.

| | stops | median δ | robust spread | range across circuits |
|---|---|---|---|---|
| First pass (`12_pit_loss.py`) | 306 | **23.0 s** | IQR 20.6–26.3 | 19–30 s |
| **Tightened green filter (`12b`)** | **286** | **22.8 s** | **MAD 3.7 s** | **19.4–28.3 s** |

The tightening matters and is worth stating rather than presenting the second number alone. The
first pass admitted any lap under 1.6× the driver's baseline as a green in/out lap, which lets a
lap run behind a safety car into the sample; the second pass uses 1.45× and builds the baseline
from laps within 1.15× of the driver's own median. The pooled median barely moves (23.0 → 22.8),
which is itself the useful result: **δ is robust to the filter, so the pooled figure was not an
artifact of SC contamination.** What changes is the per-circuit spread.

Per-circuit, on the tightened filter:

| Circuit | n | median δ | MAD |
|---|---|---|---|
| Miami | 19 | 19.4 | 0.9 |
| Dutch (Zandvoort) | 37 | 19.6 | 1.9 |
| British (Silverstone) | 29 | 21.8 | 5.1 |
| Austrian (Red Bull Ring) | 36 | 22.0 | 1.9 |
| Monaco | 19 | 22.5 | 2.2 |
| Hungarian | 45 | 22.9 | 2.4 |
| Japanese (Suzuka) | 10 | 24.4 | 2.2 |
| Barcelona | 42 | 25.2 | 1.7 |
| Belgian (Spa) | 16 | 25.7 | 7.5 |
| Australian | 12 | 26.0 | 7.1 |
| Canadian | 17 | 28.2 | 7.8 |
| Chinese | 4 | 28.3 | 5.0 |
| **Pooled** | **286** | **22.8** | **3.7** |

**Four circuits are still noisy and it is not the filter's fault.** Canadian (MAD 7.8), Australian
(7.1), Belgian (7.5) and British (5.1) carry spreads two to four times the tight circuits'. Those
are the races with the most caution periods in the corpus, and a stop taken under a safety car has
a genuinely different δ — the field is compressed, so the time lost relative to *staying out* is
much smaller. **That is a real effect, not measurement noise, and §5.3 handles it by refusing to
project a stop taken under caution rather than by widening the error bar.** China's n = 4 is a
small-sample caveat of a different kind and is flagged as such.

**Sanity check against the general magnitude, stated honestly about its limits.** A total pit loss
of roughly 20–25 s is the standard figure for modern Formula 1 at a typical circuit, and the pooled
22.8 s sits inside it. A circuit-by-circuit comparison against published pit-loss tables is **not**
done here: this project has no citable free source for them in-repo, and quoting numbers from
memory is exactly the failure mode `08` §2 and `03`'s correction banner exist to stop. The check
above is an order-of-magnitude check and is labelled as one.

### 2.2 The pit phase moves the front less than the mechanism suggests

Eventual top-6 finishers, comparing their position on the lap before their first stop against their
final classification: **net mean ≈ 0, and |move| ≥ 2 in only 27% of cases** (`12_pit_loss.py`).

And of `09` §2.1's pit-attributable P1 changes, **only 38% had the beneficiary win the race.** The
other 62% are the cycle resolving itself — the leader who stopped comes back past.

`12b` measures the same thing from the other end: of all 48 lap-to-lap P1 changes, **19% revert
within 5 laps**, and the split is the opposite of the obvious guess —

| | changes | reverted within 5 laps |
|---|---|---|
| Pit-attributable | 32 | 4 (**12%**) |
| Not pit-attributable | 16 | 5 (**31%**) |

**Read these two together or misread both.** A pit-attributable lead change usually holds for
several laps (only 12% revert inside five) but usually does *not* hold to the flag (only 38%
convert). So the pit cycle produces a lead that is stable on the timescale a live layer updates on
and unstable on the timescale that decides the race. That is precisely the shape of error a live
win-probability layer makes, and precisely what this model is for.

### 2.3 The finding that reframes the whole thing: a per-lap swap rate over-disperses

This came out of B4's build, not the first probe pass, and it is the most consequential measurement
in this document.

`09` §5.4 feeds the simulator a per-lap adjacent-pair **swap rate** `q`, and the simulator makes
every swap permanent. Those are not the same thing, because swaps revert. Measured over all 12
races — net displacement at 5 laps (is the car that was behind actually ahead?) against
`1 − (1−q)⁵` from the same pairs' own one-lap rate:

| Band | net @ 5 laps / compounded rate |
|---|---|
| P1–P3 | 0.58 – 0.71 |
| P4–P6 | 0.53 – 0.75 |
| P7–P10 | 0.47 – 0.61 |
| P11–P15 | 0.58 – 0.74 |
| P16+ | 0.41 – 0.78 |
| **Pooled (13,056 pairs)** | **0.61** |

**A per-lap swap rate implies about 1.6× more net movement than the archive actually shows, in
every band and every quarter of every race.** It is the same error, one level down, as the one §2.5
catches in the undercut comparison: **"swapped at least once over n laps" is not "ahead after n
laps."**

> **Correction, 2026-09-04, before this document was first read by anyone.** This section originally
> said "**the simulator** disperses the field about 1.6× faster than the archive does", and `09`
> §10.2 said the same. **That was an inference, not a measurement, and it is wrong.** `09`'s
> simulator does not consume the raw rate above: it consumes a cell rate that has been shrunk toward
> its band, had retirement-driven changes removed and been scaled by `exp(c·(m−1))`, and it then
> multiplies that by an asymmetric strength tilt. Measured directly against the real
> `forward_simulate` (`probes/09b_dispersion.py`), the simulator's net displacement at five laps is
> **0.176 against the archive's 0.178 — a ratio of 0.99.** Those steps absorb the gap almost
> exactly.
>
> The table above stands as what it is: **a property of the raw rate, and a warning about feeding a
> swap rate to anything that treats swaps as permanent.** It is not a defect of `09` as built.
> `09` §10.2 carries the corrected version and `09` §16.6 item 7 records how the wrong one got
> written.

**Why this still belongs in *this* document.** Pit cycles are the largest single generator of
transient swaps — a car pits, drops several places, and takes them back over the following laps —
and `09` §5.4 deliberately leaves pit-cycle swaps *inside* `q` because `09` §5.7 does not model
them. That is a live hazard for anyone who reuses `q` in a context where the shrinkage and the
strength tilt are not there to absorb it, and §4 states what removing pit swaps from `q` is and is
not expected to buy.

### 2.4 Stop timing is not predictable from stint age, and this decides v1's scope

Per-lap hazard that *this* lap is a car's stop lap, bucketed by laps since its last stop:

| Stint age | laps at risk | stops | hazard |
|---|---|---|---|
| 0–4 | 2,798 | 93 | 0.0332 |
| 5–9 | 3,063 | 45 | **0.0147** |
| 10–14 | 2,654 | 74 | 0.0279 |
| 15–19 | 2,126 | 100 | 0.0470 |
| 20–24 | 1,525 | 101 | 0.0662 |
| 25–29 | 919 | 64 | 0.0696 |
| 30–34 | 465 | 26 | 0.0559 |
| 35–39 | 253 | 10 | 0.0395 |
| 40–44 | 163 | 6 | 0.0368 |
| 45–49 | 107 | 8 | **0.0748** |

Base rate 0.0374; the whole range across buckets with ≥100 laps at risk is **0.0147–0.0748**, a
factor of five on a 3.7% base, and it is **not monotone** — the 0–4 bucket is elevated (double
stacks, early stops under an early caution), then drops, then climbs.

**So stint age tells you roughly a factor of two about which lap a stop lands on.** A model that
must predict *when* a car will stop starts from that, and it would need tyre compound, stint plan,
fuel and a strategy prior none of which this project has. **v1 therefore does not predict stop
timing.** It projects a stop that has already started, which needs no prediction at all —
`CarState.in_pit` is an observation.

### 2.5 The undercut is a real but modest effect, once compared against the right background

`12_pit_loss.py` measured **23 successes in 154 clean undercut attempts (14.9%)**, mean span 4.7
laps. Its own reading of that number was wrong and is corrected here (`probes/README.md` carries
the correction in place).

- **The wrong background**: compound the ~6%/lap adjacent swap rate over 4.7 laps → 25%, making the
  undercut look *worse* than doing nothing. That quantity is "did this pair swap at least once",
  which §2.3 has just shown is not the same as "is the car behind ahead at the end."
- **The matched background**: adjacent pairs over the same span where **neither car stopped**, so
  strategy is excluded by construction:

| Span (laps) | pairs | behind car ahead at the end | rate |
|---|---|---|---|
| 2 | 10,582 | 645 | 0.061 |
| 4 | 8,995 | 839 | 0.093 |
| 5 | 8,261 | 865 | 0.105 |
| 8 | 6,308 | 821 | 0.130 |

At the undercut's mean span of 4.7 laps the matched background is **9.9%**, against the undercut's
**14.9%**.

**Read it as a ~1.5× lift and no further.** 23 successes across 154 attempts clustered inside 12
races is not a corpus that pins down a 5-point difference — the same power problem `09` §9.3 names,
and the attempts are not independent within a race. The honest statement is: *the undercut carries a
real advantage, of a size this corpus can see but not measure precisely.*

---

## 3. What `09`'s B4 replay measured about the gap this model fills

`09` §5.7 required the realised suppression fraction to be measured and reported. It was, over the
8 scoreable races and 520 checkpoints (`09` §10's results):

- **`reliable = False` on 33.3% of checkpoints**, and **28.5 points of that 33.3 is the pit-offset
  rule alone** — `09` §5.7's `pit_offset > 0` among the top three.
- The layer is therefore silent, by its own contract, on **more than a quarter of the race**. `09`
  §5.7 pre-registered that "if it lands anywhere near 34.5%, the layer is silent through most of the
  race and that is a headline result, not a tuning detail." At 28.5% it lands nearer 34.5% than to
  the "narrow enough" case §5.7 hoped for.
- **Not** the closing tenth, where the position-only ladder beats the full layer (log-loss 0.058 vs
  0.119). That is a real defect and it belongs to someone else: `09` §10.2 traces it to §5.4's front
  band handing the P1/P2 pair the pooled P1–P3 rate, and `09` §13 item 6 owns the fix. It is named
  here only so that it is not silently absorbed into this model's case — §6's outcome 3 disclaims it
  explicitly, and the funding argument below does not rest on it.

**This is the strongest available argument for funding this model, and it is an argument from
measurement rather than from mechanism.** It is also the honest counter-argument: `09` §10's layer
already succeeds on its pre-registered criteria *with* that 28.5% suppression, so this model buys
coverage and late-race sharpness, not a rescue.

---

## 4. The design in one page

**Scope: project a stop in progress. Nothing else.**

When a car is observed in the pit lane, the layer knows three things it can act on immediately: the
car's track position and gaps at pit entry, the circuit's `δ`, and which of its rivals have and have
not yet stopped. From those it can compute where the car rejoins, without predicting anything.

**Four components, in dependency order:**

1. **`δ_circuit`** — §2.1's per-circuit median, with §2.1's spread as the projection's error bar,
   and a hard refusal to project under caution (§5.3).
2. **A pit-state machine** on top of `03` §7.1's `in_pit` / `pit_out`, per car: `RUNNING →
   ENTERING → IN_PIT → OUT_LAP → RUNNING`, with the same latch discipline `03` §7.4 uses for
   terminal states — a transition that un-happens is a parsing artifact.
3. **A rejoin projection**: for a car in `IN_PIT` or `OUT_LAP`, its projected position is where its
   `gap_leader` plus `δ_circuit` (less whatever it has already spent) places it against the cars
   that are still circulating. This is arithmetic on quantities the tick already carries.
4. **A corrected order** handed to `09` §5's simulator in place of raw track position, for the
   duration of the cycle only.

**Where it slots into `09`.** It replaces §5.7's "do nothing explicit and suppress the estimate"
with "correct the order and keep publishing". Concretely, in `09`'s terms:

- `09` §5.7's `reliable = False while pit_offset > 0 among the top three` **narrows** to the cars
  whose rejoin is not yet projectable (a stop under caution, a degraded tick, a car whose gap is
  unparseable). §3's 28.5% is the number this is trying to move.
- `09` §5.4's background rate must have **pit-cycle swaps removed** the moment this model exists —
  otherwise the pit cycle is counted twice, which is the double-count `04` §6.3 rejected and `09`
  §5.4 already applies to retirement. This is not optional and it is the reason this model cannot
  be bolted on without refitting `q`.
- **What that refit is and is not expected to buy.** Pit cycles are a principal source of the
  transient swaps §2.3 measures, so removing them from `q` should move the raw net/compounded ratio
  toward 1. **It should not be expected to fix `09`'s late-race leader error**, and this is stated
  here rather than discovered later: that error is confined to the lead pair in the closing quarter
  (`09` §10.2, 9.9×), the closing quarter is not where the stops are, and `09`'s own diagnosis is
  that the repair there is a finer front band rather than anything to do with pit strategy. A
  pit-cycle refit of `q` and a P1-only cell are two independent fixes to two independent defects.

**What v1 explicitly does not do**: predict when a car will stop (§2.4), model tyre degradation or
stint plans, model the undercut as a *decision* (§2.5's effect is an outcome, not a policy), or
change anything in `02`.

---

## 5. The model

### 5.1 `δ_circuit` as a served constant

A per-circuit table, fitted offline from the archive exactly as §2.1 measures it, refit whenever the
archive grows, and read at serve time as a constant. It is **never** computed from the race in
progress — the same rule `08` §11.1 applies to `θ` and for the same reason: a live consumer sees one
tick at a time and cannot take a median over a race that has not finished.

Circuits with no measured `δ` fall back to the **pooled 22.8 s**, and the estimate they produce is
flagged. A circuit with fewer than 10 measured stops (China, n = 4) uses the pooled value too, and
that threshold is a stated judgement, not a measurement.

### 5.2 The rejoin projection

For a car `c` entering the pit on lap `L` with `gap_leader = g_c`:

```
projected_gap_after(c) = g_c + (δ_circuit − time_already_elapsed_in_cycle)
```

and `c`'s projected position is its rank among the still-circulating cars by projected gap. The
projection is **provisional and is replaced by observation** the moment `pit_out` fires and a real
gap arrives — it is a bridge across the cycle, not a prediction that competes with the feed.

Two properties that must hold and are assertions (§7): the projection is exactly the identity when
`δ = 0`, and a car's projected position is never ahead of where its own pre-stop gap would put it.

### 5.3 What is refused rather than approximated

- **A stop under SC/VSC is not projected.** §2.1 measured the four noisiest circuits' δ spreads and
  they are the caution-heavy races; a compressed field makes δ a different quantity. Under
  `track_status != 1` the model returns no projection, the car keeps `09` §5.7's existing treatment,
  and `09` §5.6 already marks the estimate unreliable for the caution anyway.
- **A stop whose `gap_leader` is non-numeric is not projected.** `08` §13.6 item 3 recorded that the
  `LAP n` form is not "a lapped car" — 72% of those rows are at Position 1 — and its semantics are
  still UNVERIFIED. It is dropped, never coerced.
- **A red-flag stop is not projected.** `03` §9.5's session-change handling governs; the model
  discards its pit state and re-derives from the first tick after the restart.

---

## 6. Validation, pre-registered

Same corpus, folds and discipline as `09` §9: the eight scoreable races R5–R12, race-forward fits,
checkpoints at lap boundaries, block-bootstrap over whole races, and per-race breakdown alongside
every pooled number. `09` §9.3's power warning applies unchanged and is not restated as if it were
weaker here.

**Three pre-registered outcomes, stated before the model exists:**

1. **Coverage.** The `pit_offset` suppression fraction falls from §3's measured **28.5%**. If it does
   not fall by at least half, the projection is not doing the job it was funded for.
2. **The rate prediction (§4).** Refitting `09` §5.4's `q` with pit-cycle swaps removed moves the
   raw net-at-5-laps / compounded ratio from **0.61** toward 1.0. This is the sharpest test in the
   document because it is a prediction about a quantity already measured, and it can fail cleanly.
   It is a prediction about the *rate*; `09`'s simulator already tracks the archive at 0.99 (§2.3's
   correction), so this buys a cleaner input rather than a visibly different estimate.
3. **Mid-race scoring, not late.** The layer's log-loss should improve **where the stops are** —
   `09` §10.1's curve puts the layer's largest margin over the position ladder around half distance
   (0.96 against 1.46), which is the pit window. The final two deciles, where the ladder currently
   beats the layer (0.061 and 0.058 against 0.281 and 0.119), are **not** this model's target: `09`
   §10.2 traces that to the lead-pair band cell, and `09` §13 item 6 owns it. **Claiming the last
   two deciles for this model would be claiming credit for someone else's fix.**

**And one pre-registered null**, in the spirit of `09` §1.3 and `05` §6.4.1: `09` §10's layer
already meets its success criteria with the pit cycle unmodelled. **If this model improves coverage
without improving pooled log-loss beyond the bootstrap width, the correct report is that pit-cycle
projection buys availability and not accuracy** — which would be a real and useful finding, and is
nameable now rather than argued about afterwards.

---

## 7. Required assertions

Via `lib.invariants.require`, never a bare `assert` — `05`/`08`/`09` convention, and `03` §12's
reasoning: these guard data, and a plausible wrong number is this project's demonstrated failure
mode.

1. **δ = 0 is the identity.** With `δ_circuit = 0` the projected order equals the observed order,
   field for field.
2. **A projection never gains a place for free.** A car's projected position is never ahead of the
   position its own pre-stop gap implies.
3. **The projection is provisional.** Once `pit_out` fires and a numeric gap arrives, the observed
   value replaces the projection on that tick — no blending, no carry-over.
4. **No pit-cycle double count.** With this model active, `09` §5.4's background rate must be the
   refit one; scoring against a `q` still containing pit swaps fails loudly rather than quietly
   producing a plausible number.
5. **Nothing is projected under caution, on a degraded tick, or from a non-numeric gap** (§5.3).
6. **Latch discipline.** The pit state machine never runs backwards within one cycle (`03` §7.4).
7. **`09` §11's assertions all still hold** with the model active — in particular the t = 0 identity
   (§11.2), which this model must not disturb because at lights-out no car is in the pit lane.

---

## 8. Out of scope

- **Predicting when a car will stop** (§2.4). The measurement does not support it.
- **Tyre degradation, compound, stint planning, fuel.** None are in the tick contract and none are
  measured here.
- **The undercut as a decision model** (§2.5). This model projects a stop that is happening; it does
  not advise one.
- **Pit-crew execution quality.** `11`'s null stands and is not reopened (§1).
- **Anything live.** `03` §4.4 as amended governs. This is an offline model against the archive, on
  the same terms `08` and `09` were built.
- **Any change to `02`'s locked weights or `T`**, or to `09`'s reconciliation.

---

## 9. Open items — the owner's call

1. **Fund this at all?** `09` §13 item 2's question, now with numbers on both sides: 28.5% of
   checkpoints suppressed and the ladder beating the layer in the last tenth argue for; `09` §10's
   layer already succeeding without it argues that this is an improvement, not a rescue.
2. **Is `δ` per circuit, or per circuit-and-season?** 286 stops over 12 races supports a per-circuit
   median. It does not support a per-circuit trend, and regulations change.
3. **Does a caution-time `δ` get measured separately** (§2.1's four noisy circuits), or does the
   model keep refusing to project under caution? The refusal is cheap and safe; the measurement is
   a fifth probe.
4. **Does this model also correct `09` §5.7's `pit_offset` field**, or only the order? The field is
   published as diagnostic information and something downstream may already read it.

---

## 10. What this changes in other docs

- **`09` §5.7** — "a real fix is an undercut/pit-loss model … it is §13 item 2 and it is the most
  valuable thing this layer could gain." That model is this document.
- **`09` §5.4** — the background rate must be refit with pit-cycle swaps removed if this is built
  (§4). Until then §5.4 is correct as written and §2.3's over-dispersion is a known, measured cost of
  it, recorded in `09` §10's results.
- **`09` §13 item 2** — becomes the owner's approve/decline rather than an open question.
- **`11`** — the pit-execution entry's "reopen if" pointer resolves here, and §1 states why this is a
  different claim rather than a reopening.
- **`probes/README.md`** — gains `12b_pit_projection.py`, and carries the corrected undercut
  background (§2.5) in place.

---

## 11. Reproducing §2's measurements

```bash
# environment: .venv312, run from the repo root (08 sec13.2)
.venv312/bin/python probes/12_pit_loss.py           # sec2.1 first pass, sec2.2, sec2.5's raw rate
.venv312/bin/python probes/12b_pit_projection.py    # sec2.1 tightened, sec2.3, sec2.4, sec2.5
```

**Environment**: `.venv312` — `fastf1` is not installed anywhere else (`08` §13.2). Cache at
`data/cache/fastf1/`, warm. `probes/README.md` carries the expected output for both.

§3's numbers come from `09`'s B4 validation run and are reproduced with:

```bash
.venv312/bin/python winprob_fit.py         # ~23 min
.venv312/bin/python winprob_validate.py    # ~12 min
```

| § | Quantity | Source | Method |
|---|---|---|---|
| 2.1 | δ = 22.8 s pooled, MAD 3.7, 286 stops | `session.laps` LapTime / PitInTime / PitOutTime | in-lap + out-lap against the driver's own green median; green = within 1.15× of that median, in/out laps capped at 1.45× |
| 2.2 | 19% of lead changes revert within 5 laps | per-lap `Position` | P1 identity per lap; reverted if the previous leader is back in P1 within 5 laps |
| 2.3 | net/compounded = 0.61 pooled | per-lap `Position` | per adjacent pair at lap L: did it swap by L+1, and is the behind car ahead at L+5 |
| 2.4 | stint-age hazard 0.0147–0.0748 | `PitInTime` | laps since last stop, bucketed by 5; hazard = stops / laps at risk |
| 2.5 | undercut 14.9% vs matched 9.9% | `Position` + `PitInTime` | matched background = adjacent pairs over the same span with **no** stop by either car |
