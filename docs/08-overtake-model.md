# 08 — Overtake Model (Phase B2)

Status: **specced 2026-08-26; not approved, no implementation.** Per `welcome.md`, no code is
written without an approved spec — this is that spec, not the build. Read `welcome.md`,
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
approach to observe. It shows up directly in §12's calibration: those passes land in the
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

## 12. Results — first run, 2026-08-26

Built and validated the same day this spec was approved. `overtake_build.py` → 12 completed 2026
rounds, 428,511 rows, 1,714 positives (0.40%). `overtake_fit.py` → race-forward folds, train on
rounds 1..n, test on n+1, ten folds.

| Model | Brier (pooled, out-of-fold) | AUC |
|---|---|---|
| Base rate (baseline 2) | 0.003856 | n/a — constant, does not rank |
| Rule scorer (baseline 1) | 0.023296 | 0.7958 |
| **Logistic regression** | **0.003715** | **0.9064** |

**The model discriminates well and is not yet a usable probability. Both halves matter.**

**Discrimination is real.** AUC 0.9064 pooled out-of-fold, race-forward, and it holds per-race:
0.86–0.97 on nine of ten folds. The rule scorer's 0.7958 says the hand-weighted physics was a
sound starting point; the regression beating it by ~0.11 AUC says the fitted weights found more.

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

**Verdict: usable today as a ranker, not yet as a probability.** Feeding it to a win-probability
layer that *multiplies* it would propagate the bottom-half error. Reporting Brier alone would have
hidden this entirely — the regression "beats" the base rate 0.003715 vs 0.003856, a 3.7% relative
improvement that is almost meaningless at a 0.40% base rate. That is why §7 named calibration and
not Brier as the acceptance criterion.

**Feature weights** (standardized, final fold, so comparable to each other) are physically sensible,
which is its own check that nothing leaked: `interval` −1.74 dominates, then `interval_min_recent`
−0.71, `time_in_range` −0.38, `speed_delta` +0.30. Being close, having been close, and carrying a
speed advantage are what the model uses. `closing_rate` at +0.03 is near-inert and was expected to
matter more — a real surprise worth investigating (§13 item 3).

### What would fix the calibration

Untried, in the order worth trying — none of these are done:

1. **Isotonic or Platt recalibration** on a held-out fold. Cheapest, standard, and it directly
   targets the failure without touching the ranker that already works.
2. **Restrict the model's domain** to the top deciles and let the win-probability layer treat
   everything below as "no approach in progress." Honest, and matches where the signal is.
3. **A better closing-rate feature.** A 3s linear fit on a stream updating every ~3.3s is close to
   a two-point estimate; the near-zero weight may be measurement noise rather than a finding about
   racing.

---

## 13. Open items — the owner's call

1. **Prediction horizon.** 5s is the stated goal; §2.2 measures the raw label at ~3.3s resolution.
   v1 shipped at 10s. Accept that, or fund §5.2's sub-second refinement now?
2. **Recalibrate, or restrict the domain?** §12 lists three routes and none is taken. This is the
   one decision blocking the model from being a probability rather than a ranker.
3. **Why is `closing_rate` inert (+0.03)?** Either the feature is measurement noise at a 3.3s
   update rate, or closing speed genuinely doesn't predict completion once you know the gap.
   Those have different consequences and the spec does not know which it is — **UNVERIFIED**.
2. **Does the win-probability layer get specced next, or does the overtake model ship alone?**
   The trading rationale only closes with that layer; the portfolio/learning value does not need it.
3. **`03` §4.3's interlock** — unchanged and still the owner's dated decision. Building this model
   does not trip it.
4. **Gate 2 (B1) is still unrun** and still the owner's stated next step. Note that as written
   (`03` §3) it measures feed-vs-*broadcast*, while the edge claim in §1 needs feed-vs-*market*.
   Kalshi's book had a trade in all 120 race minutes (`07` §10.3), so someone is already fast.
   Whether our feed leads *the market* is a different measurement from the one specced.
