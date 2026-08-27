# 08 — Overtake Model (Phase B2)

Status: **built and validated 2026-08-26; recalibration + domain gate added 2026-08-27.** The
model reaches AUC 0.906 race-forward and — restricted to its top ~20% of pairs by score, which
hold 89% of overtakes — clears §7's calibration bar (§11.1), so it is a usable in-domain
probability, not just a ranker. Offline only: live use and trading stay gated on B1 and `03`
§4.3's interlock. Read `welcome.md`,
`00-roadmap.md` (Lane B), `03-live-telemetry-overtakes.md` (§4.4's amended gate, §7's tick
contract, §7.3's DRS finding), and `07-lane-c-trading-feasibility.md` §10 (the market evidence
this model's rationale rests on) first.

---

## 1. What this is, and the decision that authorizes it

The owner decided on 2026-08-26 to build an overtake model, and gave the rationale that resolves
Lane B's trading-vs-learning fork: **the overtake model is not a terminal product.** It is an
intermediate signal feeding a live win-probability model, which trades the race-winner market.
The chain, in the owner's words: predict an overtake ~5 seconds before it happens, and once it
happens the race winner could change — so the winner market reprices and we are ahead of it.

That chain is coherent and it is aimed at a market that demonstrably exists: `07` §10.3 measured
826,229 of 1,703,263 lifetime contracts — **48.5%** — trading inside the Dutch GP's two-hour race
window on Kalshi, with a trade in all 120 race minutes. This is the strongest form the Lane B
rationale has ever had, and it is a real improvement on the roadmap's original premise, which
aimed at an overtake market that `07` §10.1 proved does not exist on either venue.

`03` §4.4's gate is **amended, not reinterpreted** (see the banner in that section): the offline
model specced here is authorized; running it live and trading on it are not.

**What this spec does not authorize:** any live connection (`03` §4.4), any Lane C hookup
(`03` §4.3's interlock stays intact), and the win-probability layer itself, which is named here
only as the consumer this model is shaped for.

---

## 2. Three measured facts that shape the design

Measured 2026-08-26 against the 2026 Dutch, Hungarian and Belgian GPs from the historical archive
via FastF1 3.8.3, before any of this spec was written. Documentary research on this project has a
demonstrated failure mode of confident wrong answers (`03`'s correction banner, §7.3's throttle
incident), so the numbers came first.

### 2.1 On-track overtakes are plentiful; lead changes are not

| Race (2026) | Raw single-place gains | Pit/lap-1 filtered | +debounce | **Final** (+reversion) | Lead changes |
|---|---|---|---|---|---|
| Dutch GP | 257 | 48 | 43 | **41** | 1 |
| Hungarian GP | 239 | 39 | 38 | **38** | 0 |
| Belgian GP | 182 | 39 | 34 | **32** | 0 |
| **Total** | 678 | 126 | 115 | **111** | **1** |

**Full season, built 2026-08-26** (`overtake_build.py`, all 12 completed 2026 rounds):
**432 on-track overtakes**, 428,511 sampled rows, **1,714 positives (0.40%)**. Rounds 13–23 have
no archive because those races have not happened yet, which the builder reports as `[future]`
rather than as a failure.

The raw→filtered collapse is the pit-cycle filter: most single-place "gains" happen because the
car ahead pitted, which is not an overtake. Excluding lap 1, and any event where **either** driver
is within a pit window, takes 678 down to 126.

**The debounce is not a no-op — measured.** Requiring the new order to still hold 10s later drops a
further 11 events (126 → 115, 8.7%). The third and fourth columns are reported separately because
an earlier draft of this spec quoted the 126 figure against the four-filter procedure of §5.1,
which is not the procedure that produced it. **115 is the number §5.1 actually yields.**

**The load-bearing number is the lead-change column: one, across three races.** The owner's chain
as literally stated — overtake happens, *therefore the race winner changes* — has almost no events
to trade. This does not break the architecture; it relocates where the value is, see §3.

### 2.2 The label's time resolution is ~3.3s, against a 5-second target horizon

The archive carries the feed's own timing stream (`fastf1.api.timing_data` → `Time, Driver,
Position, GapToLeader, IntervalToPositionAhead`; 31,336 rows for the Dutch GP). A driver's
`Position` field updates at a **median interval of 3.3 seconds**.

So a label built from `Position` alone is accurate to roughly ±3.3s, which is the same order as
the 5-second prediction horizon the owner asked for. **A 5s-ahead model cannot be honestly
validated against a label that is only 3.3s-accurate** — the horizon and the label noise overlap.
§5.2 specs the refinement that fixes this; until it is built and checked, any claim about a
specific horizon shorter than ~10s is **UNVERIFIED**.

### 2.3 `IntervalToPositionAhead` is dense, numeric, and is the core feature

99.6% of 31,304 interval samples parse as numeric. Median 2.98s, p10 0.53s, and **7,246 samples
below 1.0 second** in a single race — i.e. the "car is closing / is within striking distance"
state is densely populated, which is what a pursuit model needs.

**The 111 non-numeric values are NOT simply "lapped cars" — measured, and an earlier draft of this
spec asserted that wrongly.** 80 of the 111 (72%) occur at `Position == 1`, i.e. the race leader,
who has no car ahead and therefore no interval to report. Two distinct string forms appear —
`LAP n` (e.g. `LAP 25`) and `1 L` (13 occurrences) — concentrated in four drivers. The leader case
is structurally explicable; **the exact semantic of each form, and of the 31 non-leader rows, is
UNVERIFIED.** Required handling: never coerce to numeric, never silently drop. Treat "no car ahead"
as its own state, and treat an unrecognised form as schema drift and fail loud (`03` §10), rather
than guessing a meaning — which is the failure `lib/invariants.py` exists to prevent.

### 2.4 Not every overtake can be anticipated — a measured recall ceiling

Only **67% (Dutch), 74% (Hungarian) and 88% (Belgian)** of on-track overtakes are preceded by a
tracked pursuit episode — a stretch where the pursuer was within 2.0s of that specific car. The
rest arrive without one: the interval stream jumps from out-of-range straight to a completed pass,
or the pursuer arrives on an out-lap, or the car ahead has a problem.

**This is a hard ceiling on recall, not a tuning parameter.** Between roughly 12% and 33% of real
overtakes cannot be anticipated from this feature set at any horizon, because there is no
approach to observe. It shows up directly in §11's calibration: those passes land in the
lowest-probability bins by construction and hold the observed rate there above zero.

---

## 3. The reframing this forces, stated plainly

The owner's chain is right in structure and wrong in one link. Written as the spec builds it:

> overtake probability → **live win probability** → winner-market price → trade

The broken link is assuming win probability moves *because a lead change happens*. It mostly
doesn't, because lead changes essentially don't happen (§2.1). Win probability moves on a
**continuum**: a contender closing to within a second, a fight for P3 that costs the leader's
rival time, a car stuck behind a slower one and bleeding the gap, a pit window opening.

**Consequence for this spec, and it is a design decision, not a caveat:**

- The model trains on **all on-track overtakes** (≈38/race, so ≈450 for a 12-race 2026 season) — not
  only lead changes, which would give ~4 positive labels a season and is untrainable.
- The model outputs a **calibrated per-pair probability**, not an alert. It is a feature generator.
- **The win-probability layer decides what matters.** An overtake for P14 gets predicted just the
  same; it simply moves win probability by ~0. That layer is out of scope here (§9).

This is why the model is worth building even though §2.1 looks discouraging: the discouraging
number only bites if lead changes are the trade trigger, and they should not be.

---

## 4. Training data

**Source: the historical archive via FastF1, not the live feed.** This is the decision that
decouples the model from every open gate — no live connection, no ToS exposure beyond what the
archive already is, no dependency on B1. It also means the model can be built, validated, and
shown in a portfolio *today*.

**Corpus: 2026 races only.** Non-negotiable, for two independent reasons:

1. **Channel 45 is measured dead** (`03` §7.3: 944,196 samples, all 22 drivers, every value zero).
   DRS was the single strongest classical overtake predictor, and 2026 has no analogue in the feed.
   A model trained on 2024–25 archives would lean on a `DRS` column that is identically zero at
   serve time — train/serve skew of exactly the kind `05` §4.2 exists to prevent.
2. The 2026 regulations replaced DRS with active aero plus a battery-boost overtake mode available
   within one second of the car ahead. Closing dynamics are structurally different, so pre-2026
   races are not the same process.

**Cost: ~450 positive labels for the season.** Small. §7 sets the model complexity to match rather
than pretending otherwise.

**Storage: the derived matrix is NOT committed.** `data/training/winner.csv` is committed because
it is Jolpica classification data. This matrix is F1 timing data and this repo is public, so
`03` §11.2's reasoning applies unchanged: it lives under `data/live/` (already gitignored), and
tests use hand-written synthetic fixtures, never a truncated real capture.

---

## 5. The label

### 5.1 Definition

A **positive** is an on-track overtake: an ordered pair `(overtaker, overtaken)` whose relative
order in the feed's `Position` stream inverts, subject to all of:

- the overtaker's `Position` decreases by exactly 1, and the driver who held that position
  immediately prior is identifiable and unique (ambiguous multi-car shuffles are dropped, not
  guessed);
- the event is after the lap-1 timestamp (start-lap churn is a different process);
- **neither** driver is inside a pit window, padded ±10s (`PitInTime`/`PitOutTime` from
  `session.laps`);
- the new order persists — a swap that reverts within the debounce window is feed jitter;
- **and the event is not itself the reversion of a swap the same pair made moments earlier.**
  Added at implementation, found by a synthetic fixture rather than by reading: feed jitter
  produces a phantom pass *and* a phantom re-pass, and the persistence check rejects only the
  first of the two while silently keeping the second. This drops a further 4 events across the
  three reference races (115 → 111).

Measured yield of exactly this procedure: §2.1's table.

### 5.2 Fixing the 3.3s timing problem — required before any sub-10s horizon is claimed

`Position` says an overtake happened between two updates; it does not say when. Refine the event
timestamp by intersecting it with the 240ms-resolution channels (`pos_data` X/Y, `car_data` speed):
the pass moment is where the two cars' along-track order actually inverts. **The refinement must be
validated before use** — report the distribution of (refined − raw) timestamps, and treat a
refinement that disagrees with the `Position` stream by more than one update interval as a parse
failure, not a correction.

Until that lands, the model is specced and trained at a **10-second horizon**, which the raw label
supports. The 5-second target is a goal, not an assumption.

### 5.3 Negatives, and why they are the harder half

The natural unit is not "a moment in a race" but a **pursuit episode**: a continuous stretch where
one car is within a threshold interval of the car ahead. Positives are episodes that end in a pass;
negatives are episodes that don't. §2.3's 7,246 sub-1.0s samples per race are the negative pool.

Sampling negatives wrongly is the most likely way to get a beautiful, useless model here. Two
traps, both to be guarded by assertion:

- **Class balance is an artifact of sampling rate**, not of the sport. Sample per *episode*, never
  per raw telemetry row, or the model learns "long episodes are negative."
- **Leakage through the horizon window.** A negative drawn from a window that overlaps a later
  positive is mislabeled. Episodes must be disjoint from any positive's horizon window.

> **Amended 2026-08-26, at implementation, with the episode supply measured.** The rule above is
> superseded by **row-level lookahead labeling**, for a reason the original framing missed.
>
> Measured on the Dutch GP: **356 episodes** (interval < 2.0s, same car ahead, ≥3s long), median
> duration 46.4s, **38,943 episode-seconds** in one race. One row per episode would throw away
> almost all of that and leave far too few positives to train on.
>
> The deeper problem is that the original design was **asymmetric**: positives sampled at
> `t_event − H` and negatives sampled anywhere would make "time until the episode ends" a constant
> for every positive and uniform for every negative. Every feature correlated with
> proximity-to-pass then leaks, and the model discriminates on the sampling scheme rather than on
> physics.
>
> **The rule is now:** sample a decision time `t` on a fixed 1 Hz cadence inside every episode
> **without reference to outcome**, then label by looking forward — `label = 1` iff that pursuer
> passes *that specific car* within `(t, t+H]`. A positive episode sampled 40s before the pass
> correctly gets label 0, and those "closing but doesn't complete" rows are the hard negatives that
> carry the discriminative work.
>
> This does not reintroduce the length artifact the original rule guarded against: that artifact
> came from *episode-level* labels, where a long episode is structurally negative. Under row-level
> lookahead labeling, a long episode simply contributes many rows at the true base rate. Folds are
> race-forward (§8), so rows from one episode can never split across train and test.

---

## 6. Features

From `03` §7.1's tick record, so that a future live client and this offline trainer compute the
same thing. Per candidate pair at time `t`:

| Feature | Source | Note |
|---|---|---|
| `interval_to_ahead` | `IntervalToPositionAhead` | §2.3; non-numeric forms are a separate state, semantics UNVERIFIED |
| `d_interval_dt` | derived, over a short lookback | closing *rate* — the actual signal |
| `interval_min_recent` | derived | how long the pursuer has been in range |
| `speed_delta` | `car_data.Speed` | both cars |
| `throttle`, `brake`, `gear`, `rpm` | `car_data` | both cars; throttle is **not** bounded by 100 (`03` §7.3's incident) |
| `track_position` | `pos_data` X/Y projected to centerline | *where* on the lap — corners differ enormously |
| `laps_remaining`, `lap_number` | `session.laps` | |
| `tyre_age_delta`, `compound` | `timing_app_data` | deferred if it complicates v1 |
| `track_status` | race control | SC/VSC/yellow suppresses overtaking entirely |

**Explicitly excluded: any DRS analogue.** `03` §7.3 forbids treating channel 45 as one, and it is
constant zero regardless. A future reader who finds a `drs` feature here should treat it as a bug.

`track_status` is not optional garnish: under safety car, overtaking is forbidden, so unfiltered
SC laps inject guaranteed-negative episodes that teach the model nothing except the base rate.

---

## 7. Model shape

**Algo before model**, per `welcome.md`. Two stages, and stage 1 ships first:

1. **A hand-weighted rule-based scorer** the owner can reason about — monotone in closing rate and
   interval, suppressed under SC/VSC, modulated by track position. This is the same discipline
   `02` applied to the winner algo, and it gives the trained model a baseline to beat.
2. **Logistic regression** on the §6 features, per-pair-per-episode. With ~450 positives, this is
   the honest ceiling on complexity; gradient-boosted trees are not justified at this label count
   and would overfit ~40 features to ~450 events. Revisit after a second season, not before.

Output is a **calibrated probability**, and calibration is the acceptance criterion, not accuracy:
a feature generator that is 80% accurate but systematically overconfident is worse than useless to
a win-probability layer that multiplies it.

---

## 8. Validation

- **Race-forward, never random k-fold.** Identical reasoning to `05` §6.1: episodes within one race
  share track, weather, and tyre state, so random folds leak. Train on races 1..n, test on n+1.
- **Beat two baselines or the model is not worth having:** (a) the §7 rule-based scorer, and (b) a
  constant base rate. `05` is a closed negative result precisely because this discipline was
  applied; the same honesty applies here.
- **Report calibration** (reliability curve + Brier), not just discrimination (AUC).
- **Report per-horizon.** A model good at 10s and useless at 5s is a real and useful finding, and
  it is the finding §2.2 says is most likely.

---

## 9. Out of scope

- **The live win-probability model.** Named as the consumer, not specced. It needs its own doc.
- **Any live connection or trading.** `03` §4.4 as amended, `03` §4.3's interlock.
- **Corner geometry as a first-class model.** v1 uses track position as a continuous feature;
  named braking zones are a later refinement.
- **Predicting the *consequences* of an overtake** (position knock-ons, undercut chains).

---

## 10. Required assertions

Via `lib.invariants.require`, never bare `assert` (stripped under `python -O`):

- every labeled pair has two distinct, identifiable drivers;
- no episode used as a negative overlaps a positive's horizon window (§5.3);
- probabilities in [0,1]; calibration bins monotone before the model is accepted;
- `throttle` is **not** asserted ≤100 — measured to exceed it on 10.3% of samples, max 104;
- feature vectors computed offline match what a live tick would produce, field for field — the
  train/serve skew guard `05` §4.2 exists for;
- label counts per race within a plausible band — **UNVERIFIED, set from the three races measured
  in §2.1 (34–43) plus domain expectation, not from a validated ground truth**. Single digits, or
  >150 on-track overtakes in one race, means the pit filter or the persistence filter broke.
  Tightening this against race-control messages or an independent overtake count is worth doing
  before the band is trusted as an assertion rather than a smoke test.

---

## 11. Results — first run, 2026-08-26

Built and validated the same day this spec was approved. `overtake_build.py` → 12 completed 2026
rounds, 428,511 rows, 1,714 positives (0.40%). `overtake_fit.py` → race-forward folds, train on
rounds 1..n, test on n+1, ten folds.

| Model | Brier (pooled, out-of-fold) | AUC |
|---|---|---|
| Base rate (baseline 2) | 0.003856 | n/a — constant, does not rank |
| Rule scorer (baseline 1) | 0.003996 | 0.7914 |
| **Logistic regression** | **0.003715** | **0.9064** |

The rule scorer's intercept is set to the training fold's log-odds rather than left at the
hand-picked `RULE_BIAS = -3.2`. That matters: −3.2 implies ~4% baseline odds against a measured
0.40% base rate, and scoring it that way gave it a Brier of 0.0233 — an order-of-magnitude loss
that reflected only where its intercept was guessed, not whether its physics ranks. Its four
feature weights remain hand-set and untouched. **Corrected before publication; the first draft of
this section reported the unfair number.**

**The model discriminates well and is not yet a usable probability. Both halves matter.**

**Discrimination is real.** AUC 0.9064 pooled out-of-fold, race-forward, and it holds per-race:
**0.86–0.97 on eight of ten folds** (0.859 on a ninth, 0.605 on Monaco). The rule scorer's 0.7914
says the hand-weighted physics was a sound starting point; the regression beating it by ~0.12 AUC
says the fitted weights found more.

**Baseline 1 loses to baseline 2 on Brier, once fairly scored** — 0.003996 against the base rate's
0.003856. The hand-weighted scorer *ranks* well (AUC 0.79) but its probabilities are worse than
simply predicting the field-wide rate. That is a clean statement of what "algo before model" buys
and does not buy here: the physics was the right shape and the wrong magnitude.

**The single exception is instructive.** Round 6, Monaco, is the worst fold at AUC 0.605 — and
Monaco produced only 14 on-track overtakes all race against a 33–50 range elsewhere. The model is
weakest exactly where overtaking barely happens, which is the right place to be weak but a real
limit on a circuit where the winner market would still be trading.

**Calibration fails §7's acceptance criterion**, which is the criterion this spec set precisely so
that a good-looking AUC could not carry the decision on its own:

| Quantile bin | Predicted | Observed | Ratio |
|---|---|---|---|
| q1–q5 (bottom half) | 0.00000–0.00026 | 0.00025–0.00052 | 0.01–0.49 |
| q6 | 0.00057 | 0.00047 | 1.21 |
| q7 | 0.00120 | 0.00074 | 1.61 |
| q8 | 0.00255 | 0.00140 | 1.81 |
| q9 | 0.00596 | 0.00441 | 1.35 |
| q10 | 0.02796 | 0.02970 | 0.94 |

Five of ten bins land within 2x, and they are the top five — the operationally relevant range,
where a win-probability layer would actually act. The failure is concentrated in the bottom half,
where the model says "essentially zero" and the observed rate is a small non-zero floor. **Part of
that floor is structural, not a fixable modelling error:** §2.4 measured that 12–33% of real
overtakes have no tracked pursuit episode before them, so they cannot be anticipated from this
feature set and land in the lowest bins by construction.

**Verdict (first run): usable as a ranker, not yet a probability across its whole range.** Feeding
the full-range output to a win-probability layer that *multiplies* it would propagate the
bottom-half error. Reporting Brier alone would have hidden this entirely — the regression "beats"
the base rate 0.003715 vs 0.003856, a 3.7% relative improvement that is almost meaningless at a
0.40% base rate. That is why §7 named calibration and not Brier as the acceptance criterion.
**§11.1 (2026-08-27) revises this:** restricted to its domain, the model *is* a calibrated
probability — read that before quoting the ranker line.

**Feature weights** are reported as the **mean across all ten folds with sign stability**, not from
one fold. Reporting a single fold would invite exactly the error the roadmap already made once with
`grid_x_easy`: calling a coefficient "small" when it is really unidentified.

| Feature | Mean | Range | Sign |
|---|---|---|---|
| `interval` | −1.7214 | −1.80 … −1.61 | stable |
| `interval_min_recent` | −0.7604 | −0.85 … −0.69 | stable |
| `time_in_range` | −0.3787 | −0.60 … −0.25 | stable |
| `throttle_ahead` | +0.3767 | +0.25 … +0.53 | stable |
| `speed_delta` | +0.2903 | +0.20 … +0.37 | stable |
| `closing_rate` | +0.0389 | +0.02 … +0.13 | stable |
| `position`, `laps_remaining`, `lap_number`, `throttle_pursuer`, `under_caution` | ≈0 | crosses zero | **FLIPS — unidentified** |

Physically sensible, which is its own check that nothing leaked: being close, having been close,
and carrying a speed advantage are what the model uses. Two findings worth separating:

- **`closing_rate` is small but genuinely identified** (+0.0389 mean, never flips sign across ten
  folds). It is not "inert" in the unidentified sense — this corpus does have an opinion, and the
  opinion is that closing *rate* adds little once you know the *gap*. Counter-intuitive and real.
- **Five features flip sign across folds and are unidentified from twelve races**, `under_caution`
  among them — which is surprising, since overtaking is forbidden under safety car. The likely
  cause is that episodes under caution are rare in this corpus rather than uninformative; the
  honest statement is that this corpus cannot tell. **UNVERIFIED.**

### 11.1 Recalibration + domain gate — the "do both" run, 2026-08-27

Owner's call on §12's calibration item was **do both**: recalibrate *and* expose a
confidence-gated domain flag. `overtake_fit.py` now runs a nested race-forward pass — train the
logistic on rounds 1..n−2, fit the calibrator on rounds n−1 and n (two races, ~250 positives —
one race's ~130 was too thin and a single-race isotonic fit actually worsened pooled Brier),
score round n+1. Test rounds R5–R12, 264,049 pairs, 986 overtakes. Two calibrators are fitted:
isotonic (hand-rolled PAV) and Platt (1-D logistic on the log-odds). The domain flag is
`in_domain = raw p ≥ θ`.

| Model | bins within 2× | worst ratio | §7 acceptance |
|---|---|---|---|
| Raw logistic, all test rows | 5 / 10 | 0.01 | FAIL |
| Isotonic-recalibrated, all rows | 6 / 10 | 0.02 | FAIL |
| Platt-recalibrated, all rows | 6 / 10 | 0.02 | FAIL |
| Raw logistic, **in-domain only** | **10 / 10** | 1.71 | **PASS** |
| Isotonic-recalibrated, in-domain only | 8 / 10 | 0.32 | FAIL |
| **Platt-recalibrated, in-domain only** | **10 / 10** | **1.28** | **PASS** |

**The domain gate is the load-bearing half.** It retains **89.2% of real overtakes in 20.6% of
pairs** — the bottom ~80% of the score distribution holds only ~11% of overtakes, and those are
the structurally-unpredictable ones from §2.4 (12–33% of overtakes have no tracked pursuit
episode). No calibrator clears §7's bar across the whole prediction range and none ever will —
that floor is structural. The bar is cleared **only inside the gate**, and there both the raw
probability (worst bin ratio 1.71) and a light Platt map (1.28) pass.

**The threshold θ is a serve-time constant, not a percentile computed on the fly.** A live
consumer sees one tick at a time and cannot take the 80th percentile of a race in progress, so
`overtake_fit.py` computes θ as the 80th percentile of the **train+calibration** predictions only
(never the test fold) and reports it per fold: **mean 0.0037, range 0.0023–0.0059**. The
win-probability layer hard-codes **θ = 0.0037** (and refits it whenever the model is retrained)
and gates on `p_raw ≥ θ`.

**On Platt.** This data is numerically hostile to a 1-D logistic recalibration — at a 0.4% base
rate the score distribution is near-separable, so plain gradient descent drives the slope to zero
and collapses the map, while an undamped Newton step overshoots to `a ≈ 1e10` and diverges (both
measured, and recorded here so they are not re-hit). `platt_fit` uses a ridge-damped Newton with a
backtracking line search; the fitted slope then sits at `a ≈ 0.75–1.16` across the eight folds — a
genuine mild recalibration, not a collapse. In-domain it tightens the worst-bin ratio from 1.71 to
1.28. That is a real but small gain on top of the domain gate.

**What the win-probability layer consumes:** for pairs with `p_raw ≥ 0.0037`, either `p_raw`
directly (simplest, already passes) or the damped-Platt map of it (worst-ratio 1.71 → 1.28);
everything below θ is "no approach in progress". `overtake_fit.py` prints the whole table plus the
per-fold θ and Platt `(a,b)`; `data/live/overtakes/fit_recal.json` records it (gitignored, `03`
§11.2).

### The three routes, scored

1. **Isotonic / Platt recalibration** (§11 route 1). Isotonic never clears the bar. Damped Platt
   clears it *in-domain* (10/10, worst 1.28) — a small polish on top of the gate, not a
   substitute for it. Neither helps across the full range.
2. **Restrict the model's domain** (§11 route 2). **This is the fix:** `p_raw ≥ 0.0037`, 10/10
   bins within 2×, 89% of overtakes retained.
3. **A better closing-rate feature** (§11 route 3). Still untried; unrelated to the calibration
   fix, but it is the most interesting open thread the fit turned up (§12 item 3).

---

## 12. Open items — the owner's call

1. **Prediction horizon.** 5s is the stated goal; §2.2 measures the raw label at ~3.3s resolution.
   v1 shipped at 10s. Accept that, or fund §5.2's sub-second refinement now?
2. ~~**Recalibrate, or restrict the domain?**~~ **RESOLVED 2026-08-27: do both; the domain
   restriction is what carries it** (§11.1). Held-out, isotonic doesn't clear §7's bar and
   full-range recalibration never will. The **domain gate does** — `p_raw ≥ 0.0037` (top ~20% of
   pairs, 89% of overtakes), 10/10 bins within 2×. A light damped-Platt map on top tightens the
   worst-bin ratio 1.71 → 1.28 in-domain; the consumer can take it or leave it. The model is a
   usable in-domain probability now, not just a ranker.
3. **`closing_rate` is small but sign-stable across all ten folds** (+0.0389). So the question is
   no longer "is it identified" — it is: does closing rate genuinely add little once the gap is
   known, or is a 3s linear fit on a ~3.3s-update stream too noisy to carry the signal? §11's
   third fix would answer it. Not urgent, but it is the most interesting thing the fit turned up.
4. **Five features are unidentified from twelve races** (§11), including `under_caution`. More
   seasons would settle them; dropping them now would be premature. No action needed unless the
   owner wants the feature set trimmed for explainability.
5. **Does the win-probability layer get specced next, or does the overtake model ship alone?**
   The trading rationale only closes with that layer; the portfolio/learning value does not need
   it. With the calibration item resolved, this is now the top open decision for the lane.
6. **`03` §4.3's interlock** — unchanged and still the owner's dated decision. Building this model
   does not trip it.
7. **Gate 2 (B1) is still unrun** and still the owner's stated next step. Note that as written
   (`03` §3) it measures feed-vs-*broadcast*, while the edge claim in §1 needs feed-vs-*market*.
   Kalshi's book had a trade in all 120 race minutes (`07` §10.3), so someone is already fast.
   Whether our feed leads *the market* is a different measurement from the one specced. **The B0
   client it runs against is built as of 2026-08-27** (`03` §13); B1 runs at Monza FP1.

---

## 13. Reproducing this — everything a cold session needs

Written so a fresh agent (or the owner, months later) can rebuild and re-validate without
re-deriving anything from chat history. **This section is the handoff.**

### 13.1 What exists, and what each file is for

| File | Role |
|---|---|
| `lib/overtakes.py` | Labelling: `AheadIndex`, `find_passes` (§5.1's five filters), `find_episodes` (§5.3) |
| `lib/overtake_features.py` | Feature vectors (§6), lookahead labelling, `assert_no_lookahead` |
| `overtake_build.py` | CLI: archive → training matrix |
| `overtake_fit.py` | CLI: rule scorer (§7 stage 1), hand-rolled logistic regression (stage 2), race-forward validation (§8), isotonic/Platt recalibration + the domain gate (§11.1) |
| `test_overtakes.py` | Synthetic-fixture tests — no real telemetry, per `03` §11.2 |

### 13.2 Commands

```bash
# environment: use the 3.12 venv -- fastf1 is not installed anywhere else
.venv312/bin/python overtake_build.py          # ~20 min cold, ~2 min warm
.venv312/bin/python overtake_fit.py            # ~3 min
.venv312/bin/python test_overtakes.py          # instant
```

`overtake_build.py --rounds 12` builds a single race, which is the fast way to check a change to
the labeller. `overtake_fit.py --json out.json` records the run.

### 13.3 Expected output, so a regression is visible

A correct run reports, in this order:

- **Build:** 12 races, 428,511 rows, **432 on-track overtakes**, 1,714 positives (0.40%).
  Rounds 13–23 print `[future]` — those races have not happened yet and skipping them is correct.
- **Fit:** pooled out-of-fold Brier — base 0.003856, rule 0.003996, **logit 0.003715**; logit
  **AUC 0.9064**; **5 of 10 calibration bins within 2x** (q6–q10); `ACCEPTANCE (sec7): FAIL`.
- **Recalibration + domain gate** (§11.1): nested folds R5–R12, 264,049 pairs, 986 overtakes.
  Domain gate retains **89.2% of overtakes in 20.6% of pairs**. `raw_logit_in_domain` and
  `platt_in_domain` both report **10/10 bins within 2×, ACCEPTANCE: PASS** (worst ratio 1.71 and
  1.28); every all-rows line and `isotonic_in_domain` is FAIL. Per-fold Platt `a` sits at
  0.75–1.16. `data/live/overtakes/fit_recal.json` records it.

**The all-rows FAIL is still the correct output** — the model is not calibrated across its whole
range and never will be (§2.4's structural floor). What changed 2026-08-27 is that the *in-domain*
line now prints PASS, and that is also correct. If `raw_logit_in_domain` ever prints FAIL, the
domain gate or the fit has regressed — check there first.

Per-race overtake counts land in 14–50. Monaco (R6) is the low outlier at 14 and that is real, not
a bug: it is the hardest circuit on the calendar to overtake at, and it doubles as a sanity check
that the labeller is measuring racing rather than noise.

### 13.4 Data locations

- **FastF1 archive cache: `data/cache/fastf1/`** — gitignored, reconstructible, ~GB-scale. A cold
  build downloads car + position data per race; a warm one is minutes.
- **Training matrix: `data/live/overtakes/training.csv`** — gitignored, and this is load-bearing,
  not incidental. `data/training/winner.csv` is committed because it is Jolpica classification
  data; this matrix is F1 *timing* data and this repo is public, so `03` §11.2 applies. Verified
  matched by `.gitignore:16` (`data/live/`), not merely untracked.

### 13.5 State of play — where to pick up

**Done:** the model is built, validated race-forward, honestly characterised, and — as of
2026-08-27 — **calibrated within its domain** (§11.1). AUC 0.906 race-forward; restricted to the
top ~20% of pairs by score (89% of overtakes), 10/10 calibration bins within 2×. Recalibration
isotonic doesn't help; damped Platt helps a little *in-domain* only; the domain gate is the fix.

**The top open decision** is now §12 item 5: does the **live win-probability layer** get specced
next? That is the consumer this model was shaped for and the piece that closes the trading chain
in §1. It needs its own doc (`welcome.md` bars building it without one).

**Not started, and deliberately so:**
- The **live win-probability layer** (§9) — the consumer this model was shaped for. Needs its own
  spec. This is the piece that closes the trading chain in §1.
- **Gate 2 / B1**, the broadcast-delay measurement, still unrun and still the owner's stated next
  step for the *live* half. Note `03` §3 specs it as feed-vs-*broadcast*, while §1's edge claim
  needs feed-vs-*market* — a different measurement (`07` §10.3 measured a trade in all 120 race
  minutes on Kalshi, so someone is already fast).
- **Anything live or trading.** `03` §4.4's amendment authorizes the offline model only, and
  `03` §4.3's interlock — no Lane B output reaching a Lane C component — is untouched by this work
  and remains a separate dated decision.

**Related lane, not this one:** `docs/quant/` holds the Lane C directional-trading spec, written in
parallel on 2026-08-26. It consumes Lane A's per-race predictions, not this model's output, and the
interlock above is why. Don't wire them together without that decision.

### 13.6 Corrections made during this build, so they are not re-made

Each of these was wrong first and measured second. They are recorded because the failure mode they
share — a confident, plausible, unverified claim — is the one this project has been bitten by
repeatedly (`03`'s correction banner, §7.3's throttle incident).

1. **Ordering cars by integrated distance does not work.** `add_distance()` integrates each car's
   own telemetry, so cumulative distance drifts between cars; ranking by it matched FastF1's
   official per-lap `Position` only **44.7%** of the time and invented 828 "overtakes" in one race.
   The feed's own Position stream is ground truth. Do not rebuild this on distance.
2. **The debounce filter is not a no-op** (126 → 115 across three races), and a fifth filter was
   needed on top of it: jitter produces a phantom pass *and* a phantom re-pass, and persistence
   alone rejects only the first (115 → 111).
3. **`LAP n` interval values are not "lapped cars"** — 72% occur at `Position == 1`, the leader,
   who has no car ahead. Exact semantics remain UNVERIFIED; never coerce them to a number.
4. **The original sampling design was asymmetric** and would have leaked (§5.3's amendment).
5. **The rule baseline was scored unfairly** at first — a hand-picked intercept implying ~4% odds
   against a 0.40% base rate. Fairly scored it *loses* to the base rate on Brier while still
   ranking well.
6. **Feature weights from one fold are not evidence.** Reported across all ten with sign stability;
   five features flip and are unidentified from twelve races.
