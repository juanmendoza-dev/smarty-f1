# 09 — Live Win Probability (Phase B4)

Status: **specced 2026-08-27; approved for build 2026-09-03; the offline layer is BUILT and
VALIDATED as of 2026-09-04 — §10.1 has the results, and the layer meets §10's pre-registered
success criteria.** Live use remains gated on B1 and is untouched by any of this. This is the consumer `08-overtake-model.md` was shaped for — it is `08` §12 item 5,
the top open decision for Lane B. It builds **no new predictive model**: it is a state estimator
that carries Lane A's pre-race distribution forward through a race, using `03` §7's tick stream
and `08`'s calibrated in-domain overtake probabilities as its inputs.

**What 2026-09-03 authorizes:** building and validating the layer **offline, by replaying the 12
archived races** (§9), including §10's baselines and the `08`-off ablation. This extends `03`
§4.4's 2026-08-26 amendment by the reasoning §1.2 sets out — dated in `03` §4.4 in place.
**Unchanged:** running this layer against a *live* feed stays gated on B1 (`03` §4.4, §1.2), which
is still unrun; Monza FP1 is the first opportunity. Nothing in the 2026-09-03 decision touches the
live gate.

Read `welcome.md`, `00-roadmap.md` (Lane B), `03-live-telemetry-overtakes.md`
(§4.4's amended gate, §7's tick contract), `08-overtake-model.md` (all of it,
especially §2.1, §3, §11.1, §13),
`02-winner-prediction-algo.md` §§9–10, and `05-trained-model.md` §6 first.

---

## 1. What this is, and what authorizes it

### 1.1 The chain, and where this layer sits in it

`08` §1 records the owner's rationale for this lane, as a standalone live-prediction feature:

> overtake probability → **live win probability**

`08` built the first link and stopped there deliberately (`08` §9: "the live win-probability model.
Named as the consumer, not specced. It needs its own doc."). This is that doc.

**This layer is a state estimator, not a model.** It answers one question continuously: *given the
field's observed positions, gaps, retirements, pit state and laps remaining right now, what is the
probability each driver is classified first at the flag?* Every predictive input it uses already
exists — Lane A's pre-race distribution (`02` §5.4), `04` §5's reliability model, and `08`'s
calibrated overtake probability. What is new here is the **propagation**: carrying those forward
over the remaining race distance under the constraint that the field is a permutation.

### 1.2 The gate, stated exactly

**Authorized by this spec, if approved:** building and validating the layer **offline, against the
archived races `08` already used**, by replaying them (§9). This needs no live connection, so B1's
delay measurement cannot bear on whether the estimator is correct — the same reasoning `03` §4.4's
2026-08-26 amendment applied to `08`.

**Still gated on B1:** running this layer against a *live* feed. `03` §4.4's bar is unchanged —
seconds, not minutes. B1 is still unrun; it runs at Monza FP1 (~2026-09-04) off the B0 client
built 2026-08-27 (`03` §13).

**One honest note on the authorization chain, because this project does not paper over these.**
`03` §4.4's amendment names `08-overtake-model.md` explicitly, and `08` §1 says in terms: "What
this spec does not authorize: … the win-probability layer itself." So approving this document is
not a matter of inheriting an existing authorization — it **extends** `03` §4.4's amendment by the
same reasoning to a second offline model. That is a one-line change to `03` §4.4 and to the
roadmap (§14), and it should be dated like every other decision in this project rather than
assumed.

**Zero budget.** No new data source, no new API, no paid service. Every input is already local:
the FastF1 archive cache (`08` §13.4), the committed Lane A training matrix, and the tick contract
`03` §7 defines.

### 1.3 The prior this initialises from has no measured edge — say it first

`05` §6.4.1 is a closed negative result: the trained A3 model lost to A1 on both Brier and
log-loss over 48 held-out races, and **A1 itself barely clears a grid-only floor** (0.6179 vs
0.6054). On the one live race this pipeline has snapshotted, A1 lost to the market mean (`02` §9).

So this layer starts from a prior that has never been shown to beat the crowd. **Its entire claim
to being informative is in the in-race divergence, not in the starting point.** The thing a state
estimator can plausibly track better than a static pre-race number is *mechanical* facts — a
retirement, a pit cycle, a closing pursuit, laps running out — updated continuously rather than
once. It cannot fix a bad prior, and this spec does not claim it will.

Stated as a pre-registration: **if §10's validation shows this layer beating Lane A's static
number but not the position-only ladder baseline, the honest finding is that the value is in
"where the cars actually are", which is free information the market already has.** That outcome is
nameable in advance and would be a real result, in the same way `05` §6.4.1 is.

---

## 2. Six measurements that shape the design, run 2026-08-27 before the spec was written

`08` §2 established the convention and the reason for it: documentary research on this project has
a demonstrated failure mode of confident wrong answers, so the numbers come first. Six quantities
were measured against the warm FastF1 archive — the same 12 completed 2026 rounds `08` §13.3 uses,
745 race-laps — plus one pass over `08`'s own training matrix. §15 has the reproduction commands.

### 2.1 P1 changes hands about four times a race — and almost never by an on-track pass

This is the finding the whole design turns on, and it **resolves the tension in `08` §2.1** rather
than contradicting it.

| | count | per race |
|---|---|---|
| Changes of the car classified P1, after lap 1, across 12 races | **48** | 4.0 |
| — attributable to a pit stop by either car (±2 laps) | **34** | 71% |
| — attributable to the leader's retirement | **1** | 2% |
| — **neither pit nor retirement** (upper bound on on-track lead passes) | **13** | 27% |

Per race: AUS 4, CHN 1, JPN 4, MIA 5, CAN 5, **MON 0**, BAR 5, AUT 5, GBR 2, BEL 4, HUN 7, NED 6.

`08` §2.1 measured **one on-track lead change across three races** using its five-filter labeller
(§5.1: single-place, post-lap-1, pit-padded, debounced, reversion-checked). The 13 above is a
*looser* count — it comes from end-of-lap `session.laps` positions with a ±2-lap pit window, so it
still admits in-lap/out-lap ordering artifacts that `08`'s labeller rejects. **Read the two numbers
as bounds, not as a disagreement:** on-track passes for the lead are somewhere between ~0.3 and
~1.1 per race, and pit cycles are the dominant mechanism by which the winner market's favourite
changes.

**Consequence, and it is the central design decision:** a live win-probability layer whose only
in-race input is `08`'s overtake probability would be modelling the *rarest* of the three
mechanisms that move P1. The layer must model **pit-cycle track position, retirement, and the
background per-lap reordering process** as first-class components, with `08` entering as the
short-horizon correction on top. §3 states what that buys and what it does not.

### 2.2 The leader-conversion ladder — the floor this layer has to beat

P(the car leading at lap L is the eventual winner), pooled over 12 races, bucketed by laps
remaining. Each bucket is 5 laps × 12 races = 60 lap-observations.

| Laps remaining | obs | leader won | rate |
|---|---|---|---|
| 0–4 | 60 | 60 | **1.000** |
| 5–9 | 60 | 60 | **1.000** |
| 10–14 | 60 | 56 | 0.933 |
| 15–19 | 60 | 51 | 0.850 |
| 20–24 | 60 | 40 | 0.667 |
| 25–29 | 60 | 46 | 0.767 |
| 30–34 | 60 | 41 | 0.683 |
| 35–39 | 60 | 32 | 0.533 |
| 40–44 | 59 | 25 | 0.424 |
| 45–49 | 55 | 19 | 0.345 |
| 50–54 | 50 | 18 | 0.360 |
| 55–59 | 36 | 11 | 0.306 |
| 60–64 | 65 | 37 | 0.569 |

Three things to read off it, one of them a warning:

- **The last ten laps are settled.** 120 of 120 lap-observations inside 10 laps to go had the
  leader win. A live layer that does not converge hard to ~1.0 for the leader in that window is
  broken, and this is the cheapest sanity assertion in the whole spec (§11).
- **The ladder is not monotone**, and the non-monotonicity at 20–24 (0.667) vs 25–29 (0.767), and
  at 60–64 (0.569) after 55–59 (0.306), is **a composition artifact, not a fact about racing**.
  Only the six longest races (Monaco 78, Dutch 72, Austrian 71, Hungarian 70, Canadian 68,
  Barcelona 66) reach the 60+ bucket at all, and those laps are the opening laps where the
  pole-sitter still leads. Bucketing by *laps remaining* mixes race lengths. **Any fitted version
  of this must condition on race progress as a fraction, not on absolute laps remaining.**
- **Effective n is 12, not 60 per bucket.** Sixty lap-observations from twelve races are massively
  autocorrelated — a race where the leader led wire-to-wire contributes ~50 concordant
  observations. This is the same power trap `05` §6.4 names, and §10 handles it explicitly.

### 2.3 The background per-lap adjacent-pair swap rate — measured, and flatter than expected

For every adjacent pair (P*k*, P*k+1*) at the end of a lap, how often has their order inverted by
the end of the next lap. Pooled over 12 races.

| Band | adjacent pairs observed | swaps | rate per lap |
|---|---|---|---|
| P1–P3 | 2,190 | 132 | **0.0603** |
| P4–P6 | 2,187 | 126 | **0.0576** |
| P7–P10 | 2,898 | 172 | **0.0594** |
| P11–P15 | 3,558 | 282 | **0.0793** |
| P16+ | 2,223 | 159 | **0.0715** |

**About 6% per lap at the front, about 7–8% in the midfield, and the gradient is shallow.** This is
the background process §5.4 specs, and it already contains pit cycles, retirements and on-track
passes mixed together — which is exactly what a background rate should be, provided the components
modelled explicitly on top of it are *removed* from it rather than double-counted (§5.4 states how).

The flatness is worth flagging as counter-intuitive and real: an intuition that the front of the
field is much more static than the midfield is not supported here. It is consistent with §2.1 —
the front reorders through pit cycles as readily as the midfield does.

### 2.4 `08`'s calibrated domain is thinnest exactly where this layer needs it most

This is the measurement that most constrains what this spec may claim, and it is not in `08`.
`08` §11.1's headline — 10/10 calibration bins within 2×, in-domain — is **pooled over all
in-domain rows regardless of where in the field the pair sits.** This layer leans hardest on
front-of-field pairs. Re-running `08`'s nested race-forward folds (R5–R12, 264,049 test rows, 986
overtakes) and bucketing the test rows by the pursuer's position, at the serve-time constant
θ = 0.0037:

| Pursuer position | test rows | overtakes | in-domain rows | in-domain overtakes | positives retained | observed rate in-domain |
|---|---|---|---|---|---|---|
| **P1–P3** | 26,636 | **47** | 3,358 | **32** | **68.1%** | 0.00953 |
| P4–P6 | 45,974 | 111 | 7,478 | 105 | 94.6% | 0.01404 |
| P7–P10 | 46,813 | 149 | 7,008 | 134 | 89.9% | 0.01912 |
| P11–P15 | 85,756 | 292 | 22,926 | 270 | 92.5% | 0.01178 |
| P16+ | 58,870 | 387 | 14,255 | 339 | 87.6% | 0.02378 |

Two facts, both bad for the front of the field:

1. **Thirty-two in-domain positives at P1–P3 across eight test races.** That is the entire evidential
   basis for the calibrated probability this layer would multiply when a car is racing for the lead.
2. **The domain gate is *worse* at the front, not better.** It retains 68.1% of front-of-field
   overtakes against 88–95% everywhere else — so `08` §11.1's "89.2% of overtakes retained" is
   carried by the midfield and does not hold where it matters here.

And the calibration itself, restricted to in-domain rows with the pursuer in P2–P6 (n = 10,836,
137 overtakes), in five quintile bins rather than ten because the count will not support ten:

| Bin | predicted | observed | ratio |
|---|---|---|---|
| q1 | 0.00430 | 0.00185 | **2.33** |
| q2 | 0.00590 | 0.00508 | 1.16 |
| q3 | 0.00866 | 0.00831 | 1.04 |
| q4 | 0.01421 | 0.00831 | 1.71 |
| q5 | 0.04739 | 0.03967 | 1.19 |

**Four of five bins within 2×, worst ratio 2.33 — this fails `08` §7's acceptance bar in the band
this layer cares about**, where the pooled in-domain figure passes at 1.71. The failing bin is the
*lowest* one, which is the same structural floor `08` §2.4 identified (overtakes with no tracked
pursuit episode land in the bottom bins by construction) — but it fails there nonetheless.

**Required consequence, and it was measured rather than asserted.** `08`'s probability may be
consumed at the front of the field only above the level where it is calibrated, so a **second
serve-time constant** is needed alongside θ. It is computed exactly the way `08` §11.1 computes θ
and for exactly the same reason — from the **train+calibration** predictions only, never the test
fold, because a live consumer sees one tick at a time and cannot take a percentile over a race in
progress.

**θ_front = the 60th percentile of the calibration folds' predictions, restricted to rows that are
already in-domain and have the pursuer inside the top six.** Measured per fold across R5–R12:
**mean 0.0105, range 0.0095–0.0116** — a much tighter range than θ's own 0.0023–0.0059, which is
itself a small piece of evidence that the front-of-field score distribution is stable. The layer
hard-codes **θ_front = 0.0105** and refits it whenever `08` is retrained.

What it costs and what it buys, on the same held-out test folds:

| | rows | overtakes | observed rate |
|---|---|---|---|
| Front-of-field, in-domain (`p_raw ≥ θ`, pursuer P2–P6) | 10,836 | 137 | 0.01264 |
| **After `p_raw ≥ θ_front`** | **4,448 (41.0%)** | **106 (77.4%)** | **0.02383** |

| Bin | predicted | observed | ratio |
|---|---|---|---|
| q1 | 0.01242 | 0.01012 | **1.23** |
| q2 | 0.01942 | 0.01484 | **1.31** |
| q3 | 0.05891 | 0.04650 | **1.27** |

**Three of three bins within 2×, worst ratio 1.31 — a PASS of `08` §7's bar**, against the 2.33
FAIL above it, at the cost of 22.6% of front-of-field overtakes and 59% of the rows. Three bins
rather than ten because 106 positives will not support ten and pretending otherwise is how a
calibration table becomes decoration.

Pairs below θ_front fall back to the background rate. This is the same shape of decision as `08`
§11.1's domain gate, applied one level down, and it was made because the measurement forced it.

### 2.5 Retirement hazard is mildly front-loaded, not constant

Fifty retirements across 12 races (4.2 per race), from FastF1's `Status` field, whose four values
across the season are exactly the four `04` §5.1 measured from Jolpica — `Finished` 120,
`Lapped` 87, `Retired` 50, `Did not start` 7. Retirement lap as a fraction of race distance:

| Race fraction | retirements | share |
|---|---|---|
| 0.00–0.25 | 17 | **34%** |
| 0.25–0.50 | 9 | 18% |
| 0.50–0.75 | 13 | 26% |
| 0.75–1.00 | 11 | 22% |

Median 0.440, mean 0.457. A **constant** hazard over race distance would put 25% in each quarter.
The measured shape is mildly front-loaded: the first quarter runs ~1.4× the flat rate and the last
quarter ~0.9×. So a constant-hazard model **overstates late-race retirement risk by roughly 10%
relative** — which matters precisely in the closing laps where §2.2's ladder says the leader
converts 100% of the time.

**With n = 50 the shape is weakly determined**, and a four-bin histogram is not a hazard function.
§5.5 specs a two-segment piecewise-constant hazard (first quarter elevated, remainder flat) as the
cheap correction, fitted race-forward like everything else, and requires the flat-hazard variant to
be reported alongside it so the choice is visible rather than assumed.

### 2.6 A third of race laps are inside a pit cycle

Laps on which at least one car pitted: **257 of 745 race-laps, 34.5%.** Per race it runs from 20%
(China) to 48% (Britain), with Barcelona 44%, Dutch 47%, Hungary 43%.

This kills the obvious safe design. §2.1 says pit cycles are the dominant mechanism moving P1, so
the naive protection — set `reliable = False` whenever a pit cycle is in progress — **would
silence the layer across a third to a half of exactly the window the market trades.** §5.7 states
what is done instead, and states the resulting limitation plainly rather than hiding it behind a
flag.

---

## 3. The design in one page, and what it does and does not buy

Everything below follows from §2. Stated compactly first so the rest of the document is reading
detail rather than discovering the shape.

**The layer maintains a distribution over finishing orders, not a vector of independent
probabilities.** It estimates P(win) by **Monte Carlo forward simulation** of the remaining race
from the currently observed state, under a per-lap reordering process, with retirement folded in.
P(win)_d is the fraction of simulated paths in which *d* is classified first.

**Two regimes, because `08` covers only one of them:**

- **Near field, next 10 seconds.** Adjacent pairs where `08` reports `p_raw ≥ θ = 0.0037` (subject
  to §2.4's extra front-of-field restriction) get their pass probability from `08` directly. This
  is the only place `08` enters, and it enters **once per update, on the observed state.**
- **Everything else — every other pair, and the entire remainder of the race.** `08` is silent by
  construction below θ ("no approach in progress", `08` §11.1), and its features are not
  simulatable forward: you would have to simulate intervals, speeds and throttle traces to re-apply
  it at lap 30. So the remaining distance runs on §2.3's **measured background per-lap swap rate**,
  conditioned on race progress, position band and circuit.

**What that buys, stated in the units that matter.** `08`'s contribution to P(win) is not a large
change in level; it is a change in **timing**. The layer re-runs every second, so each update folds
in the newest 10-second window: P(win) begins moving *before* the position change the market
reprices on. That is exactly the edge claim in `08` §1 ("we are ahead of it"), now stated as a
mechanism rather than a hope.

**What it does not buy, with the arithmetic done in advance.** All three figures below are
**observed** rates from §2.4's held-out folds, not predicted ones, and the band each comes from is
named because they differ:

| Front-of-field pursuit | observed 10 s pass rate | × 40-point P(win) gap ≈ effect |
|---|---|---|
| In-domain, pursuer P1–P3 (`p_raw ≥ θ`) | **0.95%** | **~0.4 pt** |
| In-domain and above θ_front, pursuer P2–P6 | **2.38%** | **~1.0 pt** |
| Strongest third of those | **4.65%** | **~1.9 pt** |

Kalshi's tick is 1 point and §7.3's Monte Carlo standard error is 0.5 points at the spec'd `N`. So
an *average* in-domain pursuit moves P(win) by less than one tick and barely more than the
estimator's own noise; only the pairs that clear θ_front are worth a tick or more, and only the
strongest third are worth about two. **The signal is real and it is small**, and §10's ablation
baseline exists to measure whether it survives Monte Carlo noise at all.

**The three things that actually move P(win), in order, per §2:** pit-cycle track position (§2.1's
71%), retirement (§2.5), and laps simply running out (§2.2). `08` is fourth. A spec that presented
`08` as the engine of this layer would be describing a different sport than the one measured.

---

## 4. The state the layer carries

One immutable estimate per update, derived from `03` §7.1's tick plus the layer's own retained
state. The tick is the only interface to the feed (`03` §7: "the model reads ticks and nothing
else"), and this layer adds no second channel.

```
WinProbState
  session_key      str    03 sec7.1, carried through unchanged
  prior_id         str    which Lane A snapshot/backfill row seeded this (sec6)
  order            list   FIA codes in current classified order, from CarState.position
  retired          set    codes with CarState.retired or .stopped latched true (03 sec7.4)
  laps_done        dict   code -> CarState.laps
  lap_current      int?    LapCount.CurrentLap
  lap_total        int?    LapCount.TotalLaps
  progress         float  lap_current / lap_total -- the conditioning variable, not laps_remaining (sec2.2)
  track_status     int    03 sec7.1's code: 1 clear, 2 yellow, 4 SC, 5 red, 6 VSC, 7 VSC ending
  stops_done       dict   code -> count of completed pit cycles (derived, sec5.7)
  pursuits         list   in-domain (pursuer, ahead, p_overtake) triples this tick (sec5.3)
  strengths        dict   code -> reconciled Plackett-Luce strength (sec6), frozen for the race
```

**`progress`, not `laps_remaining`, is the conditioning variable** everywhere in this spec. §2.2
measured why: bucketing by absolute laps remaining mixes a 44-lap Belgian GP with a 78-lap Monaco
and produces a non-monotone ladder that is an artifact of race length.

**Terminal states are latched and never reversed**, inheriting `03` §7.4 unchanged: a car that
un-retires is a parsing artifact, never a fact. A retired car's P(win) is exactly 0.0 and that is
an assertion (§11), not a consequence of the simulation happening to never pick it.

---

## 5. Propagation

### 5.1 Monte Carlo forward simulation, not a Markov chain — and why

The task's own framing offered both. The choice is forced, not stylistic.

**A Markov chain over full field orderings is intractable.** The state space is the set of
permutations of ~20 cars: 20! ≈ 2.4 × 10¹⁸ states. There is no transition matrix to build.

**A Markov chain over each driver's own position independently is tractable and wrong.** It does not
preserve the permutation constraint — two drivers can occupy P1, marginals do not sum to one, and
the correlation that carries all the information ("if VER passes NOR then NOR is second") is
discarded. `04` §6.1 rejected the same shortcut for podium probabilities and for the same reason.

**Monte Carlo over field orderings handles the constraint for free**, folds in retirement without
any special machinery, and is already this project's idiom: `04` §6.2 established the
exponential-race Plackett-Luce draw, and `lib/simulate.py` implements it. This layer is the same
move one level up — simulate the *evolution* of the order rather than a single draw of it.

The cost is sampling error, and unlike `04` it is not negligible here (§7).

### 5.2 The step structure — `08` enters exactly once, undiluted

Per simulated path, from decision time `t`:

1. **Step 0, the next 10 seconds.** For each adjacent pair carrying an in-domain `08` probability
   (§5.3), draw a swap with that probability. `08`'s horizon is exactly 10 s (`08` §5.2), so this
   step is exactly 10 s.
2. **Remainder of the current lap.** Background rate (§5.4), prorated by the fraction of the lap
   left after `t + 10s`.
3. **Every subsequent lap to the flag.** Background rate per lap, plus a retirement draw per
   surviving car (§5.5), until `lap_total`.

**Do not compound `08`'s 10-second probability up to lap scale.** `1 − (1 − p)⁹` assumes
independence across nine consecutive windows inside one pursuit, and `08` §5.3 measured pursuit
episodes at a **median 46.4 seconds** — a window in which the pass did *not* happen is strongly
informative about the next one, so the independence assumption is false in a direction that
inflates the probability. Letting `08` enter once, at its own horizon, and handing off to a rate
measured at lap scale avoids the error entirely.

**`08` is not re-applied after step 0**, and this is a scope statement, not an oversight. Its
feature vector (`08` §6) is built from intervals, speeds, throttle and track position; re-applying
it at simulated lap 30 would require simulating those channels, which is a different and much
larger model. `08`'s influence on the estimate is therefore bounded by one 10-second window per
update — see §3 for what that is worth, measured.

### 5.3 Which pairs `08` is allowed to speak for

A pair `(pursuer, ahead)` gets its step-0 swap probability from `08` if **all** of:

- the two cars are adjacent in the current `order`, and `ahead` is directly ahead of `pursuer`;
- the `08` feature vector can be computed from the current tick without any `None` (`03` §8's
  degraded modes make this a real branch, not a formality — no `Position.z` means no
  `track_frac`);
- `p_raw ≥ θ`, with **θ = 0.0037** hard-coded as a serve-time constant, per `08` §11.1. It is
  refit whenever `08` is retrained and is never a percentile computed over a race in progress;
- **and, if `pursuer` is inside the top six, `p_raw ≥ θ_front`, with θ_front = 0.0105** hard-coded
  as a second serve-time constant. §2.4 forces this: the lower in-domain range fails `08` §7's bar
  in that band (worst ratio 2.33), and above θ_front it passes (worst 1.31). Like θ, θ_front is
  computed by `overtake_fit.py` from train+calibration predictions only and refit whenever `08` is
  retrained — **never a percentile taken over a race in progress**, which is the defect `08` §11.1
  ruled out for θ and which applies here identically.

The consumer may take `p_raw` directly or the damped-Platt map of it (`08` §11.1: worst in-domain
ratio 1.71 → 1.28). **v1 takes `p_raw` directly** — it already passes pooled in-domain, the Platt
gain is small, and one fewer fitted object between the model and the estimate is worth more than
0.4 of a ratio point. The Platt map stays available behind a flag and §10 reports both.

Every pair not meeting all five conditions runs on the background rate. That includes the great
majority of pairs: §2.4 measured θ admitting ~20% of rows overall and only 12.6% of front-of-field
rows (3,358 of 26,636), and θ_front then keeps 41% of what survives at the front.

### 5.4 The background per-lap transition model

A pair-swap probability `q(band, progress, circuit)` for each adjacent pair on each simulated lap,
fitted from §2.3's counts.

> **Correction, 2026-09-03, made in place during the B4 build.** As originally written this section
> defined the swap probability as `q(band, progress, circuit)` and nothing else — **no strength
> term** — while §4 declares `WinProbState.strengths` and §5.5 requires solving for reconciled
> strengths `w'` such that the simulator reproduces `02`'s `p_algo` at lights-out. Traced through
> the step structure before any simulator code was written, `w'` had **no path into `p̂` at all**:
> the estimate was a function of starting order and retirement only, the IPF update
> `w' ← w' · p_algo / p̂` had nothing to pull on, and §11 assertion 2 — the backbone assertion of
> this whole layer — was unreachable. The spec was internally inconsistent, not merely incomplete.
>
> **The fix, which is the minimal one.** For an adjacent pair with `a` ahead of `b`:
>
> ```
> q_pair = q(band, progress, circuit) · 2·w_b / (w_a + w_b)
> ```
>
> The tilt is exactly 1.0 when the two strengths are equal, so §2.3's measured rate is recovered
> on a field of equals and remains the calibration target rather than being replaced by a fitted
> quantity; and it is bounded in `[0, 2q]`, so no strength ratio can drive a swap probability to
> nonsense. That reduction is itself an assertion in `lib/winprob_sim.py`
> (`TILT_EQUAL_STRENGTH_IS_ONE`), not a comment.
>
> Measured lever, on a 20-car 60-lap synthetic field at `q = 0.06`: the leader's simulated P(win)
> moves from **0.002 to 0.879** as his strength runs from 0.05× to 8× the field, against 0.291 at
> parity. IPF has room in both directions, and the direction it needs most — flattening a leader
> whose `p_algo` is below the process's natural conversion rate — is the easy one.
>
> Recorded here rather than only in the code because this project's specs are the decision record
> (§15's `Lapped` correction is the precedent): the number that changed is in the spec, so the
> correction belongs in the spec.


Requirements:

- **Conditioned on `progress`, not on laps remaining** (§2.2's composition artifact).
- **Conditioned on position band**, at §2.3's granularity — P1–P3 / P4–P6 / P7–P10 / P11–P15 /
  P16+. Finer bands are not supported: the front band already rests on 2,190 pair-observations and
  132 swaps.
- **Circuit enters as `02` §5.1's existing overtaking multiplier `m`**, not as a free per-circuit
  parameter. Twelve races cannot fit 12 circuit effects, and `02` §10 item 1 already flags that
  those multipliers are hand-set judgements awaiting exactly this kind of measurement — so
  reporting the fitted per-circuit residual against `m` is a **free by-product that pays a debt in
  another document**, and §13 lists it.
- **Fitted race-forward**, on races 1..n, applied to race n+1. Never pooled over the corpus it is
  scored on (`05` §6.1).

**The double-counting hazard, and how it is handled.** §2.3's 6%/lap already *contains* pit-cycle
swaps, retirements and on-track passes. Retirement is modelled separately (§5.5), so **retirement-
caused position changes must be removed from the background fit** — a driver who retires vacates
positions, and if that vacancy is in both the background rate and the hazard model the layer
double-counts attrition exactly the way `04` §6.3 rejected. The fit therefore excludes any
adjacent-pair observation where either car retires within the lap. Pit-cycle swaps stay *in* the
background rate, because §5.7 does not model pit strategy explicitly. `08`'s step-0 contribution
covers only the first 10 s and is not part of any lap-scale rate, so it needs no subtraction.

This is a required assertion (§11): the sum of modelled position-change sources must not exceed the
measured total, checked on the training folds.

### 5.5 DNF hazard, and the `02` §5.4 `T`-calibration interaction

This is the question the task named, and it has a real answer rather than a caveat.

**The conflict.** `04` §6.3 established, and rejected an earlier design over, a specific
double-count: `02` §5.4 anchored `T = 0.1168` to "the long-run rate at which pole converts to a
win" — a *realized historical* rate, which already includes every race where the pole-sitter
retired. So `w_d = exp(score_d / T)` **already has full-race DNF risk priced in implicitly.**
Layering `04` §5.2's explicit `p_dnf_d` on top of it counts the same risk twice, and `04` §6.3
measured the damage: NOR at `p_points = 100.0%` next to `p_dnf = 27.3%`, which cannot both be true.

**But this layer cannot simply omit DNF, the way `04` §6.3 could.** A static outcome model can
leave attrition implicit because it never has to update. A live one must handle two things a
constant cannot: a car that has *actually* retired must go to exactly 0, and the remaining hazard
must **shrink as laps run out** — which is the whole reason §2.2's ladder reaches 1.000 inside ten
laps. An implicit full-race hazard baked into `T` does not decay.

**The resolution: replace the implicit hazard with an explicit one, do not stack them.** Concretely,
per race, once, offline, before the race starts:

1. Take Lane A's per-driver strengths `w_d = exp((score_d − max score) / T)` (`04` §6.1's
   quantity, unchanged, from `02`'s own code path per `05` §4.2).
2. Define the explicit survival model: `S_d(a, b)` = P(car *d* survives from race fraction *a* to
   *b*), from §2.5's two-segment hazard with per-driver intensity scaled by `04` §5.2's `F_dnf_d`.
   A retired car cannot be classified first.
3. **Solve for reconciled strengths `w'_d` such that the simulator, run from lights-out over the
   full race distance with the explicit hazard active, reproduces `02`'s `p_algo` to within
   tolerance.** Iterative proportional fitting: `w'_d ← w'_d · p_algo_d / p̂_d`.

Step 3 is what makes the two hazards mutually exclusive rather than additive: the explicit hazard's
full-race effect is *absorbed into* `w'`, so at `progress = 0` nothing has changed, and as the race
runs the layer applies survival over the **remaining** fraction only. The hazard decays correctly
and the double-count `04` §6.3 identified never forms.

**Three implementation constraints on the reconciliation, all of them from a way it can go wrong:**

- **Do not run IPF against the working simulator's noisy `p̂`.** Twenty iterations of a ratio update
  against a Monte Carlo estimate chases sampling noise into `w'`. Reconcile **once per race,
  offline, at N ≥ 200,000** (`04` §6.2's budget, which is affordable because it happens once) and
  cache the result keyed on `prior_id`. Never at serve time.
- **Guard the tail.** `02` §9's reference field has 15 drivers sharing ~2.1%, and IPF is
  ill-conditioned against near-zero targets. Reconcile the top band only — drivers with
  `p_algo ≥ 0.01` — and hold the remainder proportional to their unreconciled strengths, then
  renormalize.
- **The t = 0 identity is an acceptance assertion, not a diagnostic** (§11). At lights-out, with no
  live information, this layer **must** reproduce `02`'s `p_algo` within Monte Carlo tolerance.
  That check is what proves the hazard is not double-counted, and it is exactly the shape of `04`
  §6.2's self-consistency assertion, which validates the podium machinery against `02` §9's locked
  numbers every time it runs.

> **Correction, 2026-09-03, made in place during the B4 build — the identity has a measured
> structural floor, and it is not the hazard.** Run as written, IPF against the *absolute* targets
> converges to a worst residual of **0.0184 on R5 and stops there**, far outside the 3 × `se_mc`
> tolerance §11 asks for. That is not a tuning failure and not a double-counted hazard. It
> decomposes exactly:
>
> - `02` §5.4's softmax leaves a flat tail — on R5, **17 backmarkers share 2.9%** of `p_algo`
>   (§9's own reference field says the same thing: "remaining 15 drivers share ~2.1%").
> - A forward simulation that respects track position says a car starting P18 essentially **cannot**
>   be classified first. The background process moves a car at most `2q` per pair per lap, which
>   over a race is a climb of order ten places at the extreme, so the tail's mass is unreachable at
>   *any* strength vector.
> - The band's simulated mass is therefore ~1.000 against a target of 0.971, and the excess lands on
>   the band in proportion. Predicted floor: `0.029 × 0.617 = 0.0179`. Measured: **0.0184**.
>
> Chasing that residual means inflating backmarker strengths until the simulator produces wins it
> does not believe in, which is worse than the discrepancy.
>
> **What is done instead.** The IPF ratio update is run on the band's **conditional** distribution —
> `p_algo_d / Σ_band p_algo` against `p̂_d / Σ_band p̂`. On R9 that takes the residual it can
> control from 0.0054 to **0.0001**. Two numbers are reported for every race and **neither is
> quoted without the other**: `residual_cond`, which the reconciliation controls and which §11
> assertion 2 is now asserted against, and `residual` (absolute), which carries the tail artifact
> and is reported so the gap stays visible rather than being defined away. The tail mass itself is
> reported per race as `tail_mass`.
>
> **This is a real disagreement with the prior, and it is worth stating as one.** The layer says a
> backmarker's win probability is ~0 where Lane A's softmax says 0.17%. Given §1.3 — the prior has
> no measured edge, and `02` §10 item 2 already records that `T`'s calibration does not describe the
> real field's shape — the layer's answer here is the more defensible of the two. It is recorded
> because it means the t = 0 identity is an identity **on the reconcile band**, not on all 22 cars,
> and a reader who does not know that would think something was broken.
>
> Two smaller fixes made at the same time, both measured: IPF damping now decays across the sweep
> and the **best** iterate is kept rather than the last (at a fixed 0.7 the update overshoots and
> oscillates between ~0.012 and ~0.023 indefinitely, so "the final sweep" was a coin flip over that
> band); and §5.4's circuit term is `exp(c·(m−1))` rather than `1 + c·(m−1)`, because the linear
> form went negative before it could fit what the data asks for — see §5.4's own correction and the
> Monaco number in §10.

**Reported alongside, always:** the same validation run with the flat-hazard variant instead of
§2.5's two-segment one. n = 50 does not settle a hazard shape (§2.5) and the spec should not
pretend a choice made on 50 events is closed.

### 5.6 Safety car, VSC and red flag

`03` §7.1 carries `track_status` and this layer must act on it, for three separate reasons:

- **Overtaking is forbidden under SC/VSC.** Step-0 `08` probabilities and the background rate are
  both suppressed to zero for the duration. Note honestly that `08` §11's fit found `under_caution`
  **unidentified — it flips sign across all ten folds**, which `08` §12 item 4 records as
  surprising and attributes to caution episodes being rare in a 12-race corpus. This layer does not
  rely on `08` having learned it; it suppresses structurally.
- **A safety car compresses gaps and materially changes the race.** A 20-second lead becomes zero.
  The layer does not model the compression's effect on subsequent pace, and the honest treatment is
  to widen rather than narrow: while `track_status != 1`, the estimate is emitted with
  `reliable = False` and a `caution` reason code (§8).
- **A red flag suspends the race.** `03` §9.5's session-change detection and §7.4's
  replace-on-snapshot rule govern; the layer discards its pursuit state, keeps `strengths` and
  `retired`, and re-derives everything else from the first tick after the restart.

### 5.7 Pit cycles — the limitation, stated rather than flagged away

§2.1 measured pit stops as the mechanism behind 71% of lead changes. §2.6 measured 34.5% of race
laps carrying at least one stop. So this is the single largest source of error in the layer and it
gets a section rather than a footnote.

**What v1 does:** nothing explicit. Track position is read from the tick as-is, and pit-cycle
reordering is carried inside §2.3's background rate, which contains it by construction.

**What that gets wrong, precisely:** during an offset cycle — one car has stopped, the car it is
racing has not — raw track position is not the running order. A leader who has yet to stop is
ahead of a car that has, and will lose the place at his own stop; the layer will read him as
leading and overstate his P(win). The error is largest exactly where the market is most active.

**What v1 does about it, and why not more:**

- The layer derives `stops_done` per car from `CarState.in_pit` / `pit_out` transitions and emits
  a **`pit_offset` field**: the spread in completed stops across the top ten. This is diagnostic
  information published to the consumer, not a correction.
- **`reliable = False` while `pit_offset > 0` among the top three** — narrow enough that §2.6's
  34.5% figure is not the suppression rate, because most of those laps involve stops outside the
  podium fight. §10 must **measure the realised suppression fraction over the 12 replayed races
  and report it**; if it lands anywhere near 34.5%, the layer is silent through most of the race
  and that is a headline result, not a tuning detail. **Measured 2026-09-04: 28.5% of 520
  checkpoints** (§10.4). That is nearer the headline than the narrow case this bullet hoped for,
  and it is the argument `docs/12` is built on.
- A real fix is an **undercut/pit-loss model** — expected time loss per stop per circuit, projected
  onto post-cycle track position. That is a genuine second model, it needs its own measurements,
  and building it inside this spec would be exactly the scope creep `welcome.md` warns against. It
  is §13 item 2 and it is the most valuable thing this layer could gain.

---

## 6. Initialising from Lane A, and diverging from it

**The prior is Lane A's pre-race winner distribution**, `p_algo` from `02` §5.4, obtained through
`02`'s own unchanged code path — `snapshot.build_*` and `score.compute_*`, never a
reimplementation. That is `05` §4.2's locked rule and the reason for it applies here with full
force: a re-typed `pos_score` with a different `K` produces plausible numbers that simply are not
the feature the scorer computes.

**For a replayed archived race, the prior must be reconstructed, not looked up.** Only one race has
a live snapshot (the 2026 Dutch GP, `data/snapshots/2026-12-race-20260823T031058Z.json`).
`backfill.py` already builds A1 feature rows for historical races under `05` §4.4's leakage rules;
§9 uses that path, and the leakage rule is inherited verbatim — the prior for race *n* may read
nothing timestamped after that race's qualifying session.

**How it diverges.** Three channels, in increasing order of how much they move the number:

1. **Observed order replaces grid-derived order.** From the first tick, the simulation starts from
   where the cars actually are, not from where `02` expected them to be. This is most of the
   divergence and it arrives immediately.
2. **Laps run out.** The remaining distance over which the background process can reorder the field
   shrinks; §2.2's ladder is the shape this produces, and reproducing that ladder is a validation
   check (§10), not an input.
3. **Retirement and pursuit state.** A retirement is a discrete jump to zero and a redistribution;
   `08`'s step-0 probabilities are a continuous nudge (§3's arithmetic: ~0.4 to ~2 points).

**Strengths are frozen for the race.** `w'` is reconciled once (§5.5) and does not update from
observed in-race pace. This is a deliberate v1 scope decision and the reason is `05` §6.4.1: A3
tried to improve on A1's hand-set weights with a fitted model over 48 races and **lost**. A
per-race online update of driver strength from a handful of in-race laps, with no validation
corpus, would be a much weaker version of the same bet. **Live pace updating is §13 item 1** and
should be specced with its own measurements or not at all.

The consequence is worth stating plainly: **a driver whose car is visibly fast today gets no credit
for it beyond his track position.** That is a real limitation and the market will have it priced.

---

## 7. Horizon, cadence, and the Monte Carlo budget

### 7.1 What the output's horizon is

The estimate is a **terminal-outcome probability** — P(classified first at the flag) — so it has no
horizon of its own. The 10-second horizon belongs to `08`'s contribution alone (§5.2), and the
per-lap background rate carries everything past it. This distinction must survive into the output
record and the writeup: it is easy and wrong to describe this as "a 10-second-ahead win
probability."

### 7.2 Two rates, because the tick stream is faster than the simulator

`03` §7 emits ticks as the feed delivers them, sub-second. Re-simulating on every tick is not
affordable (§7.3).

- **Fast path, every tick.** Fold the tick into `WinProbState` (§4): update order, latch
  retirements, update `laps_done`, `track_status`, `stops_done`, recompute `08` features for
  adjacent pairs. No simulation. Cost is microseconds.
- **Slow path, re-simulate.** On a **1 Hz cadence**, and immediately on any of: a change in
  `order`, a newly latched retirement or stop, a `track_status` transition, a pit entry or exit
  in the top ten, or a lap boundary. 1 Hz matches `08`'s own training cadence (`08` §5.3's
  `SAMPLE_HZ = 1.0`), which keeps the offline and live sampling rates identical — the train/serve
  skew guard `05` §4.2 exists for.
- **Budget: the slow path must complete in under 250 ms.** That is a quarter of the cadence and it
  leaves the fast path unblocked. It is a hard requirement on `N`, not an aspiration.

### 7.3 The budget arithmetic, done here rather than discovered later

`04` §6.2 measured 200,000 single-step Plackett-Luce draws at ~1.2 s in pure Python. A forward
simulation over ~55 laps with ~20 cars needs, per path per lap, ~19 pair draws plus ~20 survival
draws — call it 2,000 random draws per path against `04`'s ~20. **Pure Python at `04`'s N would be
of order a minute per update.** It is not close.

Two consequences:

- **Vectorize across paths with numpy.** `overtake_fit.py`'s docstring already established that
  `05` §7's no-numpy rule is scoped to Phase A3's optimizer and does not govern Lane B modules.
  Per simulated lap, one `(N, n_pairs)` uniform draw and one comparison; ~55 such steps. At
  N = 10,000 that is ~55 array operations over ~200k elements, tens of milliseconds — inside
  budget with room.
- **Pick `N` from the market's tick, not from taste.** Monte Carlo standard error is
  `sqrt(p(1−p)/N)`. Kalshi prices in whole cents, so one tick is 1.0 point.

| N | SE at p = 0.5 | SE at p = 0.9 |
|---|---|---|
| 2,000 | 1.12 pt | 0.67 pt |
| 10,000 | **0.50 pt** | 0.30 pt |
| 40,000 | 0.25 pt | 0.15 pt |

**`N = 10,000` is the spec'd value**: SE ≤ 0.5 points, half a tick, at the worst case p = 0.5.

**Pre-registered decision rule, stated before the numbers exist:** *an estimate whose Monte Carlo
standard error exceeds half the market's tick is not actionable at that N.* The layer therefore
**publishes `se_mc` per driver on every estimate** (§8), and a consumer comparing a model edge
against a market price must compare it against that number. §3's arithmetic makes this concrete
and uncomfortable: an average in-domain front-of-field pursuit moves P(win) by ~0.4 points, which
is *below* the SE at N = 10,000. Only the strong pursuits (~2 points) clear it. Publishing `se_mc`
is what stops that from being discovered by losing money.

### 7.4 Common random numbers — or the noise looks like signal

Consecutive updates one second apart must not differ by Monte Carlo noise. If they do, the layer
emits ~0.5-point jitter at 1 Hz and a consumer watching for a 1-point move sees a phantom every few
seconds.

**Rule: the uniform draw used for simulated lap `L` is deterministic in `(session_key, L, path
index)`**, via a counter-based seed, so it is byte-identical across every update within a race.
Consecutive estimates then differ **only** because the state differed. Do not cache the draws —
10,000 paths × 60 laps × 21 pairs of float64 is ~100 MB — regenerate them from the counter.

The same discipline `04` §6.2 applies with `SIM_SEED = 20260823` for reproducibility, extended to
give reproducibility *across updates* and not only across runs.

---

## 8. The output interface — a stable record, not a live display

This layer's output is specified as a stable record so that any future consumer — a debug view, a
notebook, an analysis script — has a fixed shape to write against, rather than discovering it ad
hoc.

### 8.1 The record

```
WinProbEstimate                    -- immutable once emitted, same rule as 03 sec7.4
  session_key    str      03 sec7.1
  t_feed         str      feed timestamp of the newest message folded in
  t_local        float    monotonic receipt time -- ordering and staleness only
  t_wall         str      UTC ISO 8601 -- the only clock comparable to a market timestamp
  lap_current    int?
  progress       float
  p_win          dict     code -> probability, sums to 1.0 over non-retired cars
  se_mc          dict     code -> Monte Carlo standard error (sec7.3) -- NOT optional
  prior_id       str      which Lane A prior seeded this, for provenance
  model_id       str      08 model version + theta + calibrator choice
  n_paths        int
  in_domain      list     the (pursuer, ahead, p_overtake) triples that fed step 0
  pit_offset     int      spread in completed stops across the top ten (sec5.7)
  degraded       set      inherited from the tick (03 sec8)
  stale          bool     inherited from the tick (03 sec9.4)
  reliable      bool     see below
  reasons        list     why reliable is false, if it is
```

**`t_wall` is load-bearing and it is here for the reason `03` §7.1 added it during B0's build:** a
monotonic clock has no epoch, so it cannot be subtracted from a market timestamp. Any
feed-versus-market comparison — which is the measurement `08` §12 item 7 notes B1 does *not*
currently make — needs the wall clock.

**`reliable` is False, with a reason code, whenever any of:** `stale` (`03` §9.4), `degraded`
non-empty (`03` §8), `track_status != 1` (§5.6), `pit_offset > 0` among the top three (§5.7),
`gap_after_reconnect` on the source tick (`03` §9.4), the prior has not been reconciled (§5.5), or
`max(se_mc)` exceeds half a market tick (§7.3). **It is never True by default** — it is computed,
and a consumer that ignores it is out of contract.

### 8.2 Where the output lives

Inheriting `03` §4.2's scope, one layer up:

- Output goes to a **local append-only JSONL log** under `data/live/winprob/`, gitignored,
  `03` §11.2's rule unchanged and for its unchanged reason: this is derived F1 timing data and the
  repo is public.
- An in-process consumer may subscribe by callback within the same process. That is the extent of
  the surface — no hosted or networked component, same boundary `03` §4.2 draws for the tick client
  underneath it.

---

## 9. Validation: replaying archived races

There are no live races to test against and there will not be one before Monza. The corpus is the
same archived FastF1 sessions `08` used — 12 completed 2026 rounds (`08` §13.3).

### 9.1 The replay adapter, and why it is the train/serve skew guard

Build a **tick replayer**: read an archived race and emit `03` §7.1 `Tick` records in session-time
order, from `fastf1.api.timing_data`'s stream (Position, GapToLeader, IntervalToPositionAhead),
`car_data`, `pos_data`, `track_status` and `session.laps` — the same channels `overtake_build.py`
already reads.

**The layer must consume replayed ticks through exactly the same entry point a live client would
feed.** That is the point of the exercise, not an incidental design choice: `05` §4.2's rule and
`08` §10's "feature vectors computed offline match what a live tick would produce, field for field"
are the same requirement, and this is where it gets tested for this layer.

**What replay validates, and what it does not — stated so it is not overclaimed later.** The
archive is post-processed and complete. The live feed is delta-encoded, lossy, has degraded modes
(`03` §8), reconnect gaps (`03` §9.4), and schema drift (`03` §10). **Replay validates the
estimator. It does not validate the live plumbing, and every claim about live behaviour from a
replay run is UNVERIFIED until `03` §13's acceptance run.** The replayer should deliberately be
able to inject `03` §8's degraded modes and §9.4's gaps into a replay, so the `reliable` logic is
exercised offline rather than first meeting a degraded tick during a race.

### 9.2 Scoring: race-forward, at checkpoints, against the classified result

- **The scoreable set is eight races, not twelve — R5 through R12.** `08`'s out-of-fold
  predictions exist only there (its nested folds need two races to fit the calibrator and two more
  before them to fit the logistic — `08` §11.1, and §2.4's table is over exactly those eight), and
  §5.4/§5.5 fit the background rate and hazard race-forward on top of that. Rounds 1–4 are training
  material and are never scored. **This is named here, before approval, rather than clarified once
  numbers exist** — `05` §6.4.1's erratum documents what happens when a validation protocol gets
  adjusted mid-run.
- **Checkpoints**: one estimate per lap boundary per race, plus one at lights-out. ~62 per race,
  ~500 across the eight scoreable races. Lap boundaries rather than fixed wall-clock intervals, so a 78-lap Monaco
  and a 44-lap Belgian GP contribute comparably.
- **Outcome**: the driver classified first in `session.results`, from the archive.
- **Metrics**, matching `05` §6.2 and `02` §7 so the numbers are comparable to everything else in
  the project: **multi-class Brier** (`Σ_d (p_d − outcome_d)²`), **log-loss of the winner's
  assigned probability**, and a **calibration curve**. Top-1 accuracy is reported and not optimized
  for, per `05` §6.2.
- **Reported as a curve against `progress`**, not only pooled. The interesting question is not
  "is the layer better" but "**where in the race does it become better**", and a single pooled
  number hides it. Expect — and check — that it converges toward the §2.2 ladder late.
- **Race-forward folds throughout** (`05` §6.1, `08` §8). Everything fitted — the background rate
  (§5.4), the hazard segmentation (§5.5), the position-ladder baseline (§10) — is fitted on races
  1..n and scored on race n+1. Nothing is fitted on the corpus it is scored on.

### 9.3 The power problem, which is severe and must not be hidden

**Eight scoreable races means eight winner events.** Scoring ~500 checkpoints does not give 500
observations: within one race the checkpoints are massively autocorrelated, and a race in which the
favourite led wire-to-wire contributes ~60 near-identical wins. This is the same trap `05` §6.4
names ("one race, or one season, settles nothing") and §2.2's ladder already exhibits.

Three requirements follow, all pre-registered:

1. **Report per-race won/lost out of 8** alongside any pooled number, exactly as `05` §6.4.1's
   per-season breakdown does. A pooled improvement carried by one race is not a result.
2. **Never quote a p-value or a confidence interval computed as if checkpoints were independent.**
   If an interval is wanted, block-bootstrap over whole races — 12 blocks — and report how wide it
   is. It will be wide. That is the finding, not a presentational problem.
3. **Add a secondary, higher-information diagnostic: the Plackett-Luce log-likelihood of the full
   realised finishing order**, computed from the same simulator's strengths at each checkpoint.
   A finishing order carries ~20 ordered observations per race instead of one, so it has real
   power to say whether the state estimate is improving. **It is labelled a diagnostic and it does
   not substitute for the winner metric** — `04` §6.1 established Plackett-Luce as this project's
   ranking model, so it costs nothing to compute and it is not a different model sneaking in
   through the validation section.

---

## 10. Baselines and pre-registered success criteria

Stated before the numbers exist so they cannot be moved afterward — `05` §6.4's discipline, which
is why `05` §6.4.1 is a usable negative result rather than a rationalization.

**Four baselines, all on the same folds, checkpoints and metrics:**

1. **Lane A's static pre-race number**, held constant for the whole race. The task names this as
   the baseline to beat and it is the honest floor for "does knowing the race is happening help".
2. **The position-only ladder.** P(win) from current position and `progress` alone, fitted
   race-forward from §2.2's data with no simulator, no prior and no `08`. **This is the real floor
   and it is a strong one**, because "the leader with ten laps to go wins" is most of the
   available signal (§2.2: 120 of 120). A layer that does not beat this is telling us that its
   machinery adds nothing to free information.
3. **Ablation: the same simulator with `08` switched off** — step 0 replaced by the background
   rate. This isolates `08`'s entire contribution and it is **the most important number this
   validation produces**, because it is the measurement Lane B's whole rationale for building the
   overtake model actually rests on. §3's arithmetic predicts the effect is small; this is where
   that prediction gets tested rather than asserted.
4. **The market**, on the one race where this project holds live snapshotted prices (2026 Dutch
   GP). Reported as colour on a single race, never as a baseline — `05` §6.3's rule against
   backfilling historical market prices into a comparison column applies unchanged.

**Success, defined in advance:**

- The layer **succeeds** if it beats baseline 1 on pooled log-loss *and* beats baseline 2 on pooled
  log-loss, *and* wins on the per-race breakdown in at least **6 of the 8 scoreable races**
  (§9.2). Beating baseline 1 alone
  is not success: baseline 1 ignores the race entirely and clearing it proves only that positions
  are informative.
- **`08` earns its place in this layer** only if baseline 3's ablation is measurably worse than the
  full layer, with the difference exceeding the block-bootstrap width from §9.3. **If it does not,
  the correct report is that the state estimator works and the overtake model contributes nothing
  measurable to it at this corpus size** — which, given §2.1 (pit stops cause 71% of lead changes),
  §2.4 (32 in-domain front-of-field positives), and §3's ~0.4-point average effect, is a live
  possibility that must be nameable before the run rather than argued about after it.
- **Reported regardless of outcome**: the realised `reliable = False` fraction over the eight races
  (§5.7), and the fraction of checkpoints where the model-vs-market difference exceeds `se_mc`
  (§7.3). A layer that is silent or noise-limited through most of the race is a finding about the
  layer's usefulness, not a bug.

---

## 10.1 Results — the run, 2026-09-04

**Built and validated.** Eight scoreable races (R5–R12), **520 checkpoints**, full-fidelity 1 Hz
tick replay with telemetry, `N = 40,000` paths, everything fitted race-forward. §15 reproduces it.
The success criteria below were fixed in §10 before the layer existed and are not restated here in
a weaker form.

### The pre-registered verdict

| Arm | pooled log-loss | multi-class Brier | top-1 |
|---|---|---|---|
| **The layer** | **0.79935** | **0.41938** | **0.737** |
| Ablation — the same simulator with `08` off (§10 baseline 3) | 0.80003 | 0.41974 | 0.737 |
| Position-only ladder (§10 baseline 2) | 1.00000 | 0.50328 | 0.658 |
| Lane A static pre-race number (§10 baseline 1) | 1.17665 | 0.60009 | 0.644 |

**The layer succeeds on §10's criteria.** It beats the static Lane A number on pooled log-loss
(0.799 vs 1.177) and the position-only ladder (0.799 vs 1.000), and on the per-race breakdown it
beats static in **8 of 8** races and the ladder in **7 of 8** — against a bar of 6.

Per race, mean log-loss:

| | race | layer | ablation | ladder | static |
|---|---|---|---|---|---|
| R5 | Canadian | **0.5573** | 0.5573 | 0.6683 | 1.1793 |
| R6 | Monaco | **0.1370** | 0.1370 | 0.4522 | 0.2293 |
| R7 | Barcelona | **1.4663** | 1.4684 | 1.7283 | 1.6548 |
| R8 | Austrian | **0.5219** | 0.5219 | 0.9242 | 0.7112 |
| R9 | British | 2.0395 | 2.0397 | **0.9098** | 3.4494 |
| R10 | Belgian | **0.5854** | 0.5854 | 1.2279 | 0.7342 |
| R11 | Hungarian | **0.6715** | 0.6717 | 1.0670 | 1.0018 |
| R12 | Dutch | **0.7638** | 0.7665 | 1.1701 | 1.0200 |

### The interval is wide, and on one comparison it crosses zero

§9.3 requirement 2, block-bootstrapped over whole races — 8 blocks, never over checkpoints:

| Difference | point | 95% CI |
|---|---|---|
| layer − static | **−0.4047** | [−0.7176, −0.1830] |
| layer − ladder | **−0.1756** | [−0.4381, **+0.2260**] |
| layer − ablation | −0.00064 | [−0.00145, −0.00001] |

**The layer's advantage over the position ladder is not separable from zero at eight blocks.** It
meets §10's pre-registered criteria — which are about pooled log-loss and the per-race count, and
were chosen in advance precisely so this could not be renegotiated — but the interval straddles
zero and the honest statement is that eight races cannot establish the size of the gap. §9.3 said
"it will be wide. That is the finding, not a presentational problem", and it is.

**The whole width comes from one race.** Per-race layer − ladder runs −0.11, −0.32, −0.26, −0.40,
**+1.13**, −0.64, −0.40, −0.41. R9, the British GP, is the single loss and it is a large one.

### R9, and what it says about §1.3

R9 is the race where Lane A's prior was badly wrong: static log-loss **3.4494**, i.e. `p_algo` gave
the eventual winner about 3%. The layer starts from that prior with strengths frozen for the race
(§6), so it starts wrong and only track position digs it out — it recovers to 2.04, better than
static but far worse than a ladder that never had an opinion to be wrong about.

That is §1.3 happening exactly as pre-registered: *the layer cannot fix a bad prior, and this spec
does not claim it will.* Reading the table as a whole:

- On the **seven races where the prior was reasonable**, the layer beats both baselines, and it
  beats the ladder by 0.11–0.64 log-loss — the state estimate is adding something the free
  information does not have.
- On the **one race where the prior was wrong**, the ladder wins by 1.13, and the layer's frozen
  strengths are the reason. §13 item 1 (live pace updating) is the open item that addresses this,
  and this run is the first evidence with a number attached to it.

### Where in the race the layer is better — and where it is not

§9.2 asked for a curve rather than a pooled number, because "is the layer better" hides "where".
Mean log-loss by progress decile:

| progress | n | layer | ablation | ladder | static |
|---|---|---|---|---|---|
| 0.0–0.1 | 47 | 1.0927 | 1.0925 | **1.0378** | 1.1860 |
| 0.1–0.2 | 52 | **1.0771** | 1.0771 | 1.4239 | 1.1729 |
| 0.2–0.3 | 52 | **1.0657** | 1.0657 | 1.3190 | 1.1552 |
| 0.3–0.4 | 52 | **1.0274** | 1.0274 | 1.1500 | 1.1729 |
| 0.4–0.5 | 49 | **1.0863** | 1.0864 | 1.6048 | 1.1821 |
| 0.5–0.6 | 55 | **0.9552** | 0.9579 | 1.4566 | 1.2035 |
| 0.6–0.7 | 52 | **0.8391** | 0.8394 | 1.2157 | 1.1729 |
| 0.7–0.8 | 52 | **0.5510** | 0.5549 | 0.7763 | 1.1552 |
| 0.8–0.9 | 52 | 0.2814 | 0.2814 | **0.0609** | 1.1729 |
| 0.9–1.0 | 57 | 0.1187 | 0.1187 | **0.0576** | 1.1913 |

Three readings, and the third is a defect:

1. **The layer's advantage is a mid-race advantage.** From 10% to 80% distance it beats both
   baselines, and the margin over the ladder peaks around half distance (0.96 vs 1.46) — which is
   exactly the pit-cycle window where raw track position is not the running order (§5.7).
2. **At lights-out the ladder is slightly ahead** (1.038 vs 1.093). At `progress ≈ 0` the layer is
   reproducing `p_algo` by construction (§5.5's t = 0 identity), so this is Lane A's prior losing to
   "the pole-sitter usually wins", not the estimator failing.
3. **In the last two deciles the ladder beats the layer, by a lot** — 0.061 vs 0.281 and 0.058 vs
   0.119. §10.2 is that defect, measured.

### §10.2 The leader does not converge hard enough late, and the cause is narrower than it first looked

**§11 assertion 3's band fails.** Filtered exactly as §11.3 defines it — **≤ 5 laps remaining and
green flag** — the leader's `p_win` averages **0.8973** over 42 qualifying checkpoints, and **16 of
those 42 sit below the 0.9 band**, with a minimum of **0.714** (R10 lap 39, R9 lap 47, R7 lap 61).
§2.2 measured the real leader converting 120 of 120 inside ten laps to go. This is a failure of the
assertion, not a near miss of it, and the mean alone would have hidden that.

*(An earlier draft of this section reported 0.891 at `progress ≥ 0.90` including caution
checkpoints. That is a different quantity from the one §11.3 defines — `progress ≥ 0.90` is the last
4.4 laps at Spa and the last 7.8 at Monaco — and reporting it as though it were the assertion was
wrong. Corrected here; §16.6 item 7.)*

**The diagnosis took two attempts and the first published one was wrong.** It is worth recording
both, because the wrong one is more plausible than the right one.

*Attempt one, and what it actually measured.* `probes/12b_pit_projection.py` measures that in the
archive, net displacement at five laps is **0.61×** what the same pairs' own per-lap swap rate
compounded predicts — 0.41 to 0.78 in every band and every quarter, over 13,056 pair-observations.
Swaps revert; a rate is not a net displacement. That is a real and useful fact **about the archive
and about §5.4's rate**, and it was written up here as "the simulator spreads the field about 1.6×
faster than the archive does."

**That inference was wrong, and measuring it directly is what showed it.** The simulator does not
consume the raw rate. It consumes a cell rate that has been shrunk toward its band, had
retirement-driven changes removed, and been scaled by `exp(c·(m−1))`, and it then multiplies that by
the strength tilt `2w_b/(w_a+w_b)` — which is **asymmetric**, so a strong car ahead of a weak one
swaps well below `q`. Those steps absorb the gap almost exactly.
`probes/09b_dispersion.py` runs the real `forward_simulate` for five lap-steps from observed orders
across the eight races and compares it against the same pairs' archive outcome:

| Band | mean `q` | simulator net @ 5 laps | archive net @ 5 laps | sim / archive |
|---|---|---|---|---|
| P1–P3 | 0.0606 | 0.1627 | 0.1853 | 0.88 |
| P4–P6 | 0.0631 | 0.1571 | 0.1682 | 0.93 |
| P7–P10 | 0.0653 | 0.1640 | 0.1299 | 1.26 |
| P11–P15 | 0.0774 | 0.2011 | 0.2147 | 0.94 |
| P16+ | 0.0628 | 0.1815 | 0.1851 | 0.98 |
| **Pooled** | | **0.1760** | **0.1782** | **0.99** |

**The simulator tracks the archive to within 1% pooled.** There is no general over-dispersion. The
0.61 figure is a property of the rate, not of the layer, and §5.4's shrinkage and §5.4's strength
tilt are between the two.

*Attempt two — the original diagnosis, now measured properly.* §5.4 conditions on §2.3's position
bands, so the **P1/P2 pair is handed the pooled P1–P3 band rate**. It should not be: `12b` measures
the lead pair swapping at **0.0055/lap in the final quarter against the band's 0.0351**. Restricting
`09b`'s comparison to the lead pair alone:

| Race quarter | simulator net @ 5 laps | archive net @ 5 laps | sim / archive | pairs |
|---|---|---|---|---|
| 0.00–0.25 | 0.1791 | 0.1129 | 1.59 | 124 |
| 0.25–0.50 | 0.2014 | 0.2823 | 0.71 | 124 |
| 0.50–0.75 | 0.1351 | 0.3258 | 0.41 | 132 |
| **0.75–1.00** | **0.1147** | **0.0116** | **9.87** | 86 |
| Pooled | 0.1607 | 0.1996 | 0.81 | 466 |

**Over a whole race the lead pair is fine (0.81). In the closing quarter the simulator says the
leader loses the lead 11.5% of the time over five laps where the archive says 1.2%.** That is a
factor of **9.9**, it is confined to one cell, and it is the whole of the §11.3 failure above.

**Not fixed in v1, and that is deliberate.** The repair is a finer front band — a P1-only cell in
§5.4 — and adding it now would be reconditioning the model *after* seeing the validation result,
which is the failure `05` §6.4.1 documents and the reason §10's criteria were pre-registered at
all. It is §13 item 6, with the measurement already in hand and the cell already identified. §5.4's
own stated reason for not going finer was data thinness; the lead pair carries 730 observations and
43 swaps across 12 races, which is thinner than the band but not obviously too thin, and that is the
judgement the owner now gets to make with numbers rather than in the abstract.

### §10.3 What `08` contributed — the number this validation existed to produce

§10 called the ablation "the most important number this validation produces", because it is the
measurement Lane B's whole rationale for building the overtake model rests on. Under common random
numbers (§7.4), so the comparison is paired and not swamped by either arm's own standard error:

**layer − ablation = −0.00064 pooled log-loss, 95% CI [−0.00145, −0.00001].**

By the letter of §10's rule, `08` earns its place: the ablation is worse, and the bootstrap interval
excludes zero. **Every qualification that number needs, stated with it:**

- **The magnitude is 0.4% of the layer's own margin over the position ladder** (0.00064 against
  0.1756) and 0.16% of its margin over static Lane A. `08` is not what makes this layer work.
- **The interval's upper bound is −0.00001.** It excludes zero by a rounding error's worth.
- **Per race it helps in 5, hurts in 2 and ties in 1**, and the pooled figure is carried by two
  races: R7 (−0.0022) and R12 (−0.0028). A result resting on two races out of eight is exactly what
  §9.3 requirement 1 exists to expose.
- `08` spoke at all on **46.5% of checkpoints** — 242 of 520 carried at least one in-domain pair
  after both θ and θ_front.

**This is §3's arithmetic confirmed, not contradicted.** §3 predicted an average in-domain
front-of-field pursuit worth ~0.4 points of P(win) against a 1-point market tick, and called the
signal "real and small". It is real: correctly signed, paired, and measurable. It is small: three
orders of magnitude below what the state estimator itself contributes. §2.1's finding stands
unchanged — pit cycles cause 71% of lead changes and `08` is the fourth-biggest mover of P(win).

### §10.4 Reported regardless of outcome (§5.7, §7.3)

- **`reliable = False` on 173 of 520 checkpoints — 33.3%.**
- **The pit-offset rule alone accounts for 28.5 points of that** (148 checkpoints). §5.7 required
  this measured and said: "if it lands anywhere near 34.5%, the layer is silent through most of the
  race and that is a headline result, not a tuning detail." **At 28.5% against §2.6's 34.5% of
  race-laps, it lands nearer the headline than the hoped-for narrow case.** The layer is silent, by
  its own contract, on more than a quarter of the race — and disproportionately during the pit
  window, which is where §10.1's curve shows it is *most* useful. That tension is the argument
  `docs/12` is built on.
- Caution accounted for the other 6.5% (34 checkpoints).
- **`se_mc` never triggered the reliability rule** at `N = 40,000`: mean max `se_mc` = **0.00215**,
  against the half-tick threshold of 0.005. At the spec'd serve budget of `N = 10,000` the same
  states would sit near 0.0043 — under the threshold, but not by much, which is worth knowing before
  anyone lowers `N`.
- **§9.3 requirement 3, the Plackett-Luce diagnostic**: mean per-checkpoint log-likelihood of the
  full realised finishing order improves monotonically with race progress, **−82.3 in the first
  decile to −44.4 in the last**. It carries real information — it moves — and it is still a
  diagnostic, not the winner metric.

### §10.5 The market, on one race, as colour

§10 baseline 4 and `05` §6.3's rule: **colour on a single race, never a baseline.** On the 2026
Dutch GP, the only race this project holds live snapshotted prices for:

| | log-loss on the winner |
|---|---|
| Market mean (pre-race, held constant) | 1.0682 |
| **The layer** | **0.7638** |
| Lane A static | 1.0200 |

**This comparison is not a claim to beat the market and must not be read as one.** It compares a
*static pre-race price* against a *live layer that watched the race happen*; the market repriced
continuously and those prices were never captured. One race settles nothing (`05` §6.4). It is
recorded because §10 asked for it, and with the caveat §10 asked for.

### §10.6 The variants §5.5 and §5.3 require reported alongside

Three variants, all on the same 8 races, checkpoints and folds. Each is required by a section of
this spec, and each is here because that section said "report both" rather than because a number
was wanted.

**§5.5's flat-hazard variant.** §2.5 said n = 50 retirements does not settle a hazard shape and
required the flat variant reported alongside the two-segment one, so the choice is visible rather
than assumed.

| | pooled log-loss | Brier | layer − ladder | layer − ablation |
|---|---|---|---|---|
| **Two-segment hazard (v1)** | **0.79935** | 0.41938 | −0.1756 [−0.4381, +0.2260] | −0.00064 [−0.00145, −0.00001] |
| Flat hazard | 0.80303 | 0.42131 | −0.1728 [−0.4331, +0.2230] | −0.00063 [−0.00143, −0.00001] |

**The two-segment hazard is better by 0.0037 log-loss and that is not a result.** Both variants
succeed on §10's criteria, both produce essentially identical bootstrap intervals, and both put the
leader's late `p_win` at 0.89. §2.5's caution stands unchanged: **the hazard shape is not settled
by this corpus, and v1's choice of the two-segment form is not vindicated by this run** — it is
merely not contradicted by it.

**§5.3's calibrator variant.** §5.3 chose `p_raw` directly over `08`'s damped-Platt map for v1, on
the reasoning that the raw probability already passes in-domain and one fewer fitted object is worth
more than 0.4 of a calibration-ratio point. §10 required both reported.

| | pooled log-loss | in-domain checkpoints | layer − ablation |
|---|---|---|---|
| **`p_raw` (v1)** | **0.79935** | 242 (46.5%) | −0.00064 [−0.00145, −0.00001] |
| Damped Platt | 0.79964 | 244 (46.9%) | −0.00037 [−0.00087, −0.00001] |

**§5.3's choice is vindicated, mildly.** Raw is marginally better on log-loss and its ablation
effect is larger, so the Platt map is not buying anything here that would justify the extra fitted
object. Note the ablation arm scores **identically (0.80003) under both**, which it must — `08` is
switched off in that arm — and that identity is a free consistency check on the harness.

**§9.1's degraded-mode injection.** §9.1 required the replayer to be able to inject `03` §8's
degraded modes so the `reliable` logic is exercised offline rather than first meeting a degraded
tick during a race. Injecting a missing `CarData` window on every fifth lap:

| | unreliable | reasons | in-domain checkpoints | pooled log-loss |
|---|---|---|---|---|
| Clean | 33.3% | pit 28.5%, caution 6.5% | 242 (46.5%) | 0.79935 |
| Degraded every 5th lap | **46.2%** | pit 28.5%, **degraded 19.4%**, caution 6.5% | **197 (37.9%)** | 0.79938 |

Three things this confirms, and one it caught. The `degraded` flag propagates from tick to estimate
and sets `reliable = False` with its own reason code (§8.1); `08` correctly falls silent on those
ticks — in-domain coverage drops from 46.5% to 37.9%, because §5.3 requires the feature vector to be
computable with no `None` and a missing `CarData` window means no speed, throttle or brake; and the
estimate itself is essentially unchanged (0.79938 vs 0.79935), because the simulator's own inputs —
order, laps, retirement — come from `TimingData` and are not degraded here. **What it caught:** the
first version of the injection keyed on wall-clock seconds, and since §9.2 scores at *lap
boundaries* a few-second degraded window almost never coincided with a checkpoint. That run came
back byte-identical to the clean one, which is how it was noticed. The trigger is now the lap.
A test that cannot fail is worse than no test.

### §10.7 The two things this run had to fix, recorded so they are not re-made

`08` §13.6's convention. Both were silent failures, which is why they are here.

1. **The archive's two driver key spaces are not interchangeable.** `session.laps` is keyed on the
   FIA three-letter code (`01` §8.2's canonical key); the timing stream, `car_data` and `pos_data`
   are keyed on the racing number. Joining the stream on the code matches *no rows*, so every
   telemetry field comes back `None` — which is indistinguishable from `03` §8's degraded mode
   until you notice the run produced **zero checkpoints**. The full-fidelity replay was silently
   empty until this was found.
2. **`LapCount.CurrentLap` stops at `lap_total` and stays there**, so "the leader is on the last
   lap" and "the leader has finished it" look identical on that field alone. The layer simulated one
   further partial lap after the flag and the leader stayed passable, which made §11 assertion 3's
   endgame identity unreachable. The leader's own completed-lap count is what separates the two.

---

## 11. Required assertions

Via `lib.invariants.require`, never bare `assert` — `05`/`08` convention, and `03` §12's reasoning:
a plausible wrong number is the failure mode this project has been bitten by.

1. **`p_win` sums to 1.0 (±1e-6) over non-retired cars**, and a car latched retired or stopped
   (`03` §7.4) has `p_win` **exactly** 0.0 — not merely small.
2. **The t = 0 identity (§5.5).** At `progress = 0` with the observed grid equal to the prior's
   assumed grid, `p_win` reproduces `02`'s `p_algo` within Monte Carlo tolerance (3 × `se_mc`).
   This is the assertion that proves the DNF hazard is not double-counted, and it fails loudly.
   **As amended 2026-09-03 (see §5.5's dated correction):** asserted on the reconcile band's
   *conditional* distribution, because the absolute form has a structural floor set by the mass
   `02`'s softmax puts on backmarkers the simulator says cannot win. The absolute residual and the
   tail mass are both reported per race alongside it, so the amendment narrows what is asserted
   without hiding what is not.
3. **The endgame identity (§2.2).** With zero laps remaining, the leader's `p_win` is 1.0 and
   everyone else's is 0.0. **The zero-laps half is a unit test and passes.** The ≤ 5 laps / green
   half is measured over the real replayed races by `winprob_validate.py` and asserted in
   `test_winprob.py` against the saved run — **and as of 2026-09-04 it FAILS**: 16 of 42 qualifying
   checkpoints sit below the band, minimum 0.714 (§10.2). With ≤ 5 laps remaining and green flag,
   the leader's `p_win` ≥ 0.9 —
   `08` §10's precedent for a band asserted from a small measured sample applies, so this is
   labelled a **smoke test set from 12 races, not a validated ground truth**, exactly as `08` §10's
   label-count band is.
4. **No lookahead in the replay.** An estimate at session time *t* reads no source timestamped
   after *t*. `lib/overtake_features.assert_no_lookahead` already implements this check for `08`'s
   features and must be reused, not reimplemented.
5. **Train/serve parity.** A `WinProbEstimate` computed from a replayed tick equals the one
   computed from the equivalent live-shaped tick, field for field — `05` §4.2, `08` §10.
6. **Position-change sources do not double-count (§5.4).** On the training folds, modelled
   retirement-driven position changes plus background swaps do not exceed the measured total.
7. **`se_mc` is populated on every driver of every estimate.** An estimate without it is not
   emitted. §7.3 is not advisory.
8. **`reliable` is computed, never defaulted.** An estimate whose `reasons` list is inconsistent
   with its flag values fails.
9. **Probabilities in [0,1]; `08` inputs in-domain only.** Any pair fed to step 0 with
    `p_raw < θ = 0.0037` is a bug, and any pair whose pursuer is inside the top six fed to step 0
    with `p_raw < θ_front = 0.0105` is a bug (§2.4, §5.3). Both constants are read from `08`'s fit
    output, not re-derived here, and both fail loudly if the fit no longer reports them.

---

## 12. Out of scope

- **Anything live.** `03` §4.4 as amended plus the extension §1.2 names.
- **Live pace/strength updating** (§6). §13 item 1.
- **A pit-strategy / undercut model** (§5.7). §13 item 2, and the most valuable single addition.
- **Re-applying `08` beyond step 0** (§5.2) — it would require simulating the telemetry channels
  `08`'s features are built from.
- **Outcomes other than the winner.** Podium and top-10 are the same simulator asked a different
  question and `04` §6.2 already has the machinery, but nothing here motivates building them yet.
  §13 item 4.
- **Tyre degradation, fuel effect, weather transitions.** `06`'s ensemble is a pre-race signal and
  is not wired here.
- **Any change to `02`'s locked weights or `T`.** This layer consumes them; §5.5 reconciles a
  strength vector *derived* from them and does not alter the source.

---

## 13. Open items — the owner's call

1. **Does the layer update driver strength from in-race pace, or stay frozen (§6)?**
   **This item gained its first real evidence on 2026-09-04 (§10.1).** R9 is the one race of eight
   where the layer loses to the position-only ladder, and it loses by 1.13 log-loss because Lane A's
   prior gave the eventual winner ~3% and the frozen strengths carried that all race. Seven of eight
   races say freezing is fine; the eighth says exactly what this item warns about, with a number. v1 freezes,
   on the strength of `05` §6.4.1 — A3 tried to beat A1's hand-set weights over 48 races and lost,
   so an online update over a handful of in-race laps is a weaker version of a bet already
   measured as losing. Counter-argument: track position alone gives no credit to a car that is
   visibly quick today, and the market will price that. Needs its own measurements before it is
   specced.
2. **Fund a pit-loss / undercut model (§5.7)?** §2.1 measured pit stops behind **71% of lead
   changes** and §2.6 measured 34.5% of race laps carrying a stop. This is the largest known error
   source in the layer and the largest available gain. It is a genuine second model and it should
   be a decision, not something that accretes.
3. **Is a layer whose average `08` contribution is ~0.4 points, against a 1-point market tick and a
   0.5-point Monte Carlo SE (§3, §7.3), meaningfully informative at all?** This is the sharpest form
   of `08` §12 item 5 and it is answerable *before* any live connection: §10's baseline 3 measures it
   offline. Recommended sequencing — **run §9's replay validation before B1, not after.** If the
   ablation comes back at zero, B1's result stops mattering for this chain and the owner has saved
   the live-connection risk entirely.
   **Answered 2026-09-04, and the answer is "yes for the layer, barely for `08`" (§10.3).** The
   layer beats both baselines; `08`'s own contribution is −0.00064 log-loss, 0.4% of the layer's
   margin over the position ladder, carried by two races of eight. The recommended sequencing was
   followed — this ran before B1 — and what it bought the owner is that **B1's risk now sits behind
   a layer with a measured result rather than a hoped-for one.**
4. **Does the layer also emit in-race podium / top-10 (§12)?** Free from the same simulator via
   `04` §6.2's machinery. A portfolio/completeness call, not yet motivated by anything measured.
5. **`08` §12 item 1 (the 5s horizon) is now this layer's decision too.** §5.2 lets `08` enter at
   exactly its own horizon, so a shorter, better-timed `08` (via `08` §5.2's sub-second refinement)
   translates directly into an earlier P(win) move. That reframes `08`'s open item from a modelling
   nicety into the thing this layer's timeliness depends on.
6. **§5.4's front band is too coarse at the lead pair, and the fix is deferred on purpose
   (§10.2).** In the closing quarter the simulator disperses the P1/P2 pair **9.9× faster than the
   archive** — 0.115 against 0.012 net over five laps — because §5.4 hands the lead pair the pooled
   P1–P3 band rate, and the lead pair swaps at 0.0055/lap there against the band's 0.0351. This is
   the whole of §11 assertion 3's failure. The repair is a **P1-only cell in §5.4**, refit
   race-forward and re-validated against §10's pre-registered criteria rather than tuned against
   them; doing it now would be reconditioning after seeing the result (`05` §6.4.1). §5.4's stated
   reason for not going finer was data thinness, and the number is now known: **730 lead-pair
   observations and 43 swaps across 12 races.** Thinner than the band, not obviously too thin.
   Note what this item is **not**: it is not the general over-dispersion an earlier draft claimed.
   The simulator tracks the archive to within 1% pooled (§10.2), and `docs/12` §2.3's 0.61 is a
   property of the archive's own rate rather than of this layer.

7. **B1 remains unrun and is unchanged by this document.** `08` §12 item 7's observation stands:
   `03` §3 specs B1 as feed-vs-broadcast, and §8.1's `t_wall` is what makes any future
   feed-vs-external comparison measurable when someone decides to make one.

---

## 14. What this changes in other docs

Following `03` §15's precedent of recording cross-document consequences from inside the new spec
rather than leaving them to be discovered.

- **`03` §4.4** — the 2026-08-26 amendment names `08-overtake-model.md` explicitly. Approving this
  document extends it to a second offline model, and that extension should be dated in place
  (§1.2).
- **`08` §9** — "The live win-probability model. Named as the consumer, not specced. It needs its
  own doc." That doc is this one.
- **`08` §12 item 5** — "Does the win-probability layer get specced next?" is answered by this
  document existing; it becomes the owner's approve/decline rather than an open question.
- **`08` §13.5** — the layer is no longer "not started, and deliberately so"; it is specced and
  awaiting approval.
- **`08` §11.1** — the in-domain PASS is pooled across the field. §2.4 measures it by position band
  and finds the front of the field thinner (32 positives) and worse calibrated (worst ratio 2.33 in
  five bins). That belongs in `08` as a qualification on its headline, not only here.
- **`00-roadmap.md`** — Lane B gains a **Phase B4** for this layer; Phase B2's status line points
  at it.
- **`welcome.md`** — the "Where to go next" list gains a line for `09`.
- **`02` §10 item 1** — "Track overtaking multipliers are hand-set judgements, not measurements.
  Replace with per-circuit overtake counts once A3 has the data." §5.4's background-rate fit
  produces a per-circuit residual against `m` as a by-product, which is a partial payment on that
  debt from an unexpected direction.

---

## 15. Reproducing §2's measurements

The probes are measurement scripts, not implementation — no code for this layer exists or is
authorized to exist before this spec is approved (`welcome.md`). **They are committed under
`probes/`**, so the numbers below are re-derivable rather than taken on trust, and so are the ones
that have since propagated into `08`'s status banner, `00-roadmap.md`'s Phase B4 and `welcome.md`.
`probes/README.md` carries the expected output for each.

```bash
# environment: .venv312, run from the repo root (08 sec13.2)
.venv312/bin/python probes/09_race_dynamics.py           # sec2.2, sec2.3, sec2.6
.venv312/bin/python probes/09_leadchange_attribution.py  # sec2.1, sec2.5
.venv312/bin/python probes/09_domain_bands.py            # sec2.4, position bands
.venv312/bin/python probes/09_theta_front.py             # sec2.4, theta_front
```

**And §10.1's results, which are the layer itself rather than a probe:**

```bash
.venv312/bin/python winprob_fit.py         # ~23 min: 08 fold models, background rate,
                                           #   hazard, ladder, and the sec5.5 reconciliation
.venv312/bin/python winprob_validate.py    # ~12 min: sec9's replay, sec10's four baselines
.venv312/bin/python test_winprob.py        # instant: sec11's assertions + sec8.2's interlock
```

Both write to `data/live/winprob/`, which is gitignored — it is derived F1 timing data and this
repo is public (`03` §11.2). §10.6's variants are `--flat-hazard`, `--platt` and `--degrade N`.

**Environment**: `.venv312`, per `08` §13.2 — `fastf1` is not installed anywhere else. Cache at
`data/cache/fastf1/` (`08` §13.4), warm.

| § | Quantity | Source | Method |
|---|---|---|---|
| 2.1 | 48 lead changes, 71% pit-attributable | `session.laps` Position + `PitInTime`/`PitOutTime`, 12 rounds | per-lap P1 identity; attribute a change to a pit stop if either car pitted within ±2 laps |
| 2.2 | leader-conversion ladder | same laps + `session.results` | bucket each lap by laps remaining; leader vs. eventual winner |
| 2.3 | per-lap adjacent swap rate | same laps | for each adjacent (P*k*, P*k+1*) at lap *L*, did the order invert by *L+1* |
| 2.4 | in-domain counts by position band | `data/live/overtakes/training.csv` + `overtake_fit.py` | reruns `recalibration_pass`'s nested folds (R5–R12), buckets test rows by the `position` feature at θ = 0.0037 |
| 2.4 | θ_front = 0.0105 (range 0.0095–0.0116) | same | 60th percentile of each **calibration** fold's predictions, restricted to in-domain rows with the pursuer in the top six — train+calib only, never the test fold |
| 2.5 | retirement lap distribution | `session.results.Status` + last completed lap | `Status == "Retired"` only — **`"Lapped"` is a finish**, and treating it as a retirement inflates the count from 4.2/race to 6.6/race, which was caught by checking against `04` §5.1's measured 12.5% 2025 DNF rate |
| 2.6 | pit-cycle lap fraction | `session.laps.PitInTime` | laps on which ≥1 car pitted, over total race laps |

**One correction made during the probes, recorded per `08` §13.6's convention so it is not
re-made:** the first pass classified retirements as "not `Finished` and not starting with `+`",
which swept in all 87 `Lapped` finishers and produced 79 retirements over 12 races (6.6/race) with
a *back*-loaded distribution — 49% in the final quarter. Against `04` §5.1's measured 2025 rate of
12.53% (~2.7/race) that was implausible on its face. The correct filter is `Status == "Retired"`,
giving 50 retirements (4.2/race) and the **mildly front-loaded** distribution in §2.5. The sign of
the finding reversed. It is recorded because the failure mode — a plausible number from a filter
that was never checked against a known quantity — is the one this project keeps meeting.

**Also worth recording:** §2.1's 48 and `08` §2.1's 1 look contradictory and are not. `08` counts
*on-track* lead changes through a five-filter labeller; §2.1 counts *any* change of the car in P1,
including pit cycles, through a much looser end-of-lap comparison. Both are correct answers to
different questions, and the gap between them is the design (§3).

---

## 16. Reproducing this — everything a cold session needs

`08` §13's convention. Written so a fresh agent, or the owner months later, can rebuild and
re-validate without re-deriving anything from chat history. **This section is the handoff.**

### 16.1 What exists, and what each file is for

| File | Role |
|---|---|
| `lib/winprob.py` | The state estimator: `WinProbState` (§4), `WinProbEstimate` (§8.1), `RacePrior`, the fast path (`fold`) and the slow path (`estimate`), and the `reliable` contract |
| `lib/winprob_sim.py` | §5's Monte Carlo forward simulation, vectorized across paths, with §7.4's counter-based common random numbers |
| `lib/winprob_priors.py` | §6's prior from `backfill.py`'s `p_a1`, `04` §5's `F_dnf` read race-forward, §2.5's two-segment hazard, and §5.5's IPF |
| `lib/winprob_background.py` | §5.4's background rate and §10 baseline 2's position-only ladder, both fitted race-forward |
| `lib/overtake_serve.py` | `08` as a serve-time artifact — weights, standardisation, θ, θ_front — plus its feature row built from one tick (§5.3) |
| `lib/winprob_replay.py` | §9.1's tick replayer: lap-boundary and 1 Hz-with-telemetry modes, and `03` §8 degraded-mode injection |
| `winprob_fit.py` | CLI: `08` fold models, background rate, hazard, ladder, §5.5's reconciliation → `data/live/winprob/fit.json` |
| `winprob_validate.py` | CLI: §9's replay, §10's four baselines, §9.3's block bootstrap → `data/live/winprob/validation.json` |
| `test_winprob.py` | §11's assertions and §8.2's interlock. Synthetic fixtures only, per `03` §11.2 |

### 16.2 Commands

```bash
# environment: .venv312 -- fastf1 is not installed anywhere else (08 sec13.2)
.venv312/bin/python winprob_fit.py            # ~23 min warm
.venv312/bin/python winprob_validate.py       # ~12 min warm
.venv312/bin/python test_winprob.py           # instant
```

`winprob_fit.py --rounds 5 --quick --skip-overtake` fits one race at reduced budgets, which is the
fast way to check a change; **never quote a `--quick` run**, its IPF budget is below §5.5's. Both
scripts need `data/live/overtakes/training.csv`, which `overtake_build.py` produces and which is
gitignored — `08` §13.4.

### 16.3 Expected output, so a regression is visible

- **Fit:** 8 `08` fold models with Platt `a` in **0.75–1.16** (matching `08` §11.1's own range, and
  the cheapest check that the fold structure is right); background `q(P1–P3, late)` around 0.03–0.13
  depending on circuit; hazard `a ≈ 1.36–2.00`, `b ≈ 0.67–0.88`; **IPF conditional residual ≤ 0.0002
  on every race**, absolute residual 0.009–0.028 (that is §5.5's corrected tail artifact, not a
  failure).
- **Validate:** 8 races, **520 checkpoints**, pooled log-loss **layer 0.799 / ablation 0.800 /
  ladder 1.000 / static 1.177**; layer beats static 8/8 and the ladder 7/8; `reliable = False` on
  **33.3%**; `08` in-domain on **46.5%** of checkpoints.
- **Tests:** all pass, with the t = 0 identity reported at a worst conditional residual around
  0.0001 against a tolerance of 0.00335.

If the validation reports **zero checkpoints**, read §10.7 item 1 before anything else — that is
the driver-key failure, and it looks like a degraded feed rather than like a bug.

### 16.4 Data locations

- `data/live/winprob/fit.json`, `validation.json`, and any `R<n>.jsonl` from `--emit-jsonl` —
  **gitignored** (`.gitignore`'s `data/live/`), and this is load-bearing rather than incidental:
  they are derived from F1 timing data and this repo is public (`03` §11.2, §8.2).
- `data/training/winner.csv` — committed. §6's prior comes from its `p_a1` column.
- `data/cache/fastf1/` — the archive, gitignored, reconstructible, ~GB scale.

### 16.5 State of play — where to pick up

**Done:** the layer is built, validated against §10's pre-registered criteria, and honestly
characterised. It succeeds. §10.2 and §10.4 are the two defects it does not fix.

**The top open decisions**, in the order the run suggests rather than the order §13 lists them:

1. **§13 item 2 — fund `docs/12`'s pit-strategy model?** It addresses both defects at once: the
   28.5% suppression directly, and the over-dispersion as a side effect (`docs/12` §6's
   pre-registered prediction).
2. **§13 item 6 — the over-dispersion**, if item 2 is declined. Damping `q` by the measured 0.61 is
   the cheap route and it must be re-validated on §10's criteria, not tuned against them.
3. **§13 item 1 — live pace updating.** R9 is the evidence and it is one race.

**Not started:** anything live. B1 is still unrun. `03` §4.4's live gate is untouched by all of this.

### 16.6 Corrections made during this build, so they are not re-made

`08` §13.6's convention, and the same reason: each was wrong first and measured second.

1. **§5.4 had no strength term**, so §5.5's IPF had no lever and §11 assertion 2 was unreachable.
   Found by tracing the step structure before writing the simulator. Fixed by a strength tilt that
   reduces to 1.0 on equal strengths — §5.4's dated correction.
2. **IPF against absolute `p_algo` targets cannot converge**, and the floor is `02`'s softmax tail
   rather than a bug — §5.5's dated correction, with the arithmetic.
3. **The archive's two driver key spaces are not interchangeable** (§10.7 item 1). Silent: it looks
   like a degraded feed.
4. **`LapCount.CurrentLap` does not distinguish "on the last lap" from "finished"** (§10.7 item 2).
5. **A degraded-mode injection keyed on wall-clock seconds never coincides with a lap-boundary
   checkpoint** (§10.6), so the run came back byte-identical to the clean one and the test could not
   fail.
6. **"Swapped at least once over n laps" is not "ahead after n laps"** (`docs/12` §2.3, §2.5).
   This one was made twice — once about `q` and once in the undercut comparison — which is why it
   is worth stating as a general rule rather than as two incidents.
7. **And then the correction to that correction, which is the worst one here.** Having measured that
   the archive's net displacement is 0.61× its own compounded rate, §10.2 was written up as *"the
   simulator spreads the field about 1.6× faster than the archive does"* — and that sentence reached
   two documents before it was checked. It is an **inference from archive counts, not a measurement
   of the simulator**, which consumes a shrunk, retirement-excluded, circuit-scaled rate and then
   applies an asymmetric strength tilt. Measured directly (`probes/09b_dispersion.py`) the simulator
   tracks the archive at **0.99 pooled**. The real defect is one cell — the lead pair in the closing
   quarter, 9.9× — which was the *first* diagnosis, discarded in favour of the more general-sounding
   one. **The lesson is not "measure things", which this project already knows. It is that a
   plausible general explanation displaced a correct narrow one, and the general one was written up
   because it was more satisfying.**
8. **§11 assertion 3 was reported against the wrong filter.** The assertion says "≤ 5 laps remaining
   and green flag"; the first write-up reported the mean at `progress ≥ 0.90` with caution
   checkpoints included — a different set, and one that made a failure (16 of 42 below the band)
   read as a near miss of the mean (§10.2).
