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

| Race (2026) | Raw single-place gains | **On-track overtakes** | Lead changes | Into top 5 |
|---|---|---|---|---|
| Dutch GP | 257 | **48** | 1 | 4 |
| Hungarian GP | 239 | **39** | 0 | 7 |
| Belgian GP | 182 | **39** | 0 | 4 |
| **Total** | 678 | **126** (≈42/race) | **1** | **15** |

The raw→on-track collapse is the pit-cycle filter: most single-place "gains" happen because the
car ahead pitted, which is not an overtake. Excluding lap 1, and any event where **either** driver
is within a pit window, takes 678 down to 126.

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

99.6% of 31,304 interval samples parse as numeric (the rest are `LAP n` strings for lapped cars,
which must be handled as a distinct category, never coerced to a number). Median 2.98s, p10 0.53s,
and **7,246 samples below 1.0 second** in a single race — i.e. the "car is closing / is within
striking distance" state is densely populated, which is what a pursuit model needs.

---

## 3. The reframing this forces, stated plainly

The owner's chain is right in structure and wrong in one link. Written as the spec builds it:

> overtake probability → **live win probability** → winner-market price → trade

The broken link is assuming win probability moves *because a lead change happens*. It mostly
doesn't, because lead changes essentially don't happen (§2.1). Win probability moves on a
**continuum**: a contender closing to within a second, a fight for P3 that costs the leader's
rival time, a car stuck behind a slower one and bleeding the gap, a pit window opening.

**Consequence for this spec, and it is a design decision, not a caveat:**

- The model trains on **all on-track overtakes** (≈42/race, ≈500 for a 12-race 2026 season) — not
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

**Cost: ~500 positive labels for the season.** Small. §7 sets the model complexity to match rather
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
- the new order persists — a swap that reverts within the debounce window is feed jitter.

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

---

## 6. Features

From `03` §7.1's tick record, so that a future live client and this offline trainer compute the
same thing. Per candidate pair at time `t`:

| Feature | Source | Note |
|---|---|---|
| `interval_to_ahead` | `IntervalToPositionAhead` | §2.3; `LAP n` is categorical, not numeric |
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
2. **Logistic regression** on the §6 features, per-pair-per-episode. With ~500 positives, this is
   the honest ceiling on complexity; gradient-boosted trees are not justified at this label count
   and would overfit ~40 features to ~500 events. Revisit after a second season, not before.

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
- label counts per race within a plausible band (single digits or >150 on-track overtakes in one
  race means the pit filter or the persistence filter broke).

---

## 11. Open items — the owner's call

1. **Prediction horizon.** 5s is the stated goal; §2.2 measures the raw label at ~3.3s resolution.
   Accept a 10s v1, or fund §5.2's sub-second refinement first?
2. **Does the win-probability layer get specced next, or does the overtake model ship alone?**
   The trading rationale only closes with that layer; the portfolio/learning value does not need it.
3. **`03` §4.3's interlock** — unchanged and still the owner's dated decision. Building this model
   does not trip it.
4. **Gate 2 (B1) is still unrun** and still the owner's stated next step. Note that as written
   (`03` §3) it measures feed-vs-*broadcast*, while the edge claim in §1 needs feed-vs-*market*.
   Kalshi's book had a trade in all 120 race minutes (`07` §10.3), so someone is already fast.
   Whether our feed leads *the market* is a different measurement from the one specced.
