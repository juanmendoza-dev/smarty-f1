# 02 — Rule-Based Winner Prediction (Phase A1)

Status: **weights locked by owner, 2026-08-22.** Read `welcome.md`, `00-roadmap.md`, and
`01-data-pipeline.md` first.

This spec defines the Phase A1 scoring function completely enough to implement without further
questions. Every constant is stated. Every edge case has a defined behaviour.

**Design was validated before locking.** The full function was dry-run against real Dutch GP 2026
data (§9). That run changed the design twice — it revealed a missing championship feature and a
small-sample defect in track history, both fixed below. The numbers in §9 are the reference
output: an implementation that is correct will reproduce them.

---

## 1. What this produces

Input: one race snapshot (`01-data-pipeline.md` §8.3).
Output: a probability per driver, summing to 1.0, over "who wins this race".

**The algo is market-blind.** It never reads Polymarket or Kalshi. Those prices live in the same
snapshot and are compared only *after* scoring. This is not a stylistic preference — per
`welcome.md`, "is our algo better calibrated than the crowd?" is the headline feature, and a
predictor that has seen the crowd's answer cannot be scored against it. An implementation that
passes market data into the scoring path is **wrong**, however good its output looks.

Pipeline:

```
snapshot -> 8 feature sub-scores in [0,1] -> weighted sum -> softmax(T) -> probabilities
                                                                              |
                snapshot markets ------------------------------------------> compare
```

---

## 2. Weights (locked)

| # | Feature | Weight | Measures |
|---|---|---|---|
| F1 | Grid position | **0.35** | Where they start. Strongest single predictor of an F1 win. |
| F2 | Team/car form | **0.15** | Constructor pace. The car sets the ceiling. |
| F3 | Sprint result | **0.13** | Race pace, this weekend, this track. Sprint weekends only. |
| F4 | Driver recent form | **0.11** | Last 5 races. Current form, not season history. |
| F5 | Track history | **0.08** | Prior results at this circuit. |
| F6 | Championship standing | **0.08** | Season-long standing. Who is actually winning the title. |
| F7 | Weather | **0.05** | Wet-weather adjustment. Dormant unless rain is forecast. |
| F8 | Teammate H2H | **0.05** | Driver vs. identical machinery. Separates driver from car. |
| | **Total** | **1.00** | |

Weights **must** sum to 1.0. Assert this at startup; a silent drift makes every downstream
number meaningless.

### 2.1 Note on correlated features

F1, F3, and F8 all partly measure the same underlying thing: current car+driver pace this
weekend. They are **not** independent evidence. The weights above deliberately fund F3 and F8 out
of the features they overlap with (F4, F5) rather than out of F1, so that a single strong Saturday
is not counted three times. Anyone retuning these weights must preserve that property or the
model will systematically over-rate whoever qualified well.

---

## 3. Shared primitives

### 3.1 Position score

Finishing/starting positions convert to `[0,1]` by exponential decay:

```
pos_score(p, k) = exp(-(p - 1) / k)
```

`p` is 1-indexed. `pos_score(1, k) == 1.0` always. Decay constants:

| Constant | Value | Used by |
|---|---|---|
| `K_GRID` | **4.0** | F1 grid |
| `K_SPRINT` | **3.5** | F3 sprint |
| `K_FIN` | **5.0** | F4 recent form, F5 track history |

Exponential rather than linear because F1 win probability collapses steeply with position — P1 to
P3 matters enormously, P14 to P16 does not matter at all. A linear ramp would give P15 a
meaningful score, which is false.

### 3.2 The neutral value

`NEUTRAL = 0.5`.

**A missing feature scores NEUTRAL, never 0.** This rule is mandatory and is the single most
common way a hand-built scorer goes silently wrong. A rookie with no track history has not
demonstrated that they are *bad* at the circuit — the evidence is absent, not negative. Scoring
absence as 0 punishes drivers for the shape of the dataset rather than for their performance.

### 3.3 Field normalization

Where a feature is defined as "share of the field's best", divide by the **maximum observed value
across the field for this race**, so the best driver scores 1.0. Apply this only where §4 says to.

### 3.4 Classification test

A result counts as classified if its Jolpica `status` is `Finished` or starts with `+` (i.e.
`+1 Lap`). Everything else — `Retired`, `Accident`, `Engine`, `Undertray` — is a **DNF and scores
0.0** for that race. A DNF is genuine negative evidence, unlike missing data, so 0.0 is correct
here and NEUTRAL is not.

---

## 4. Feature definitions

### F1 — Grid position (0.35)

```
s_grid = pos_score(quali_position, K_GRID)
```

Source: Jolpica `{season}/{round}/qualifying.json`. Use the **qualifying classification**, not the
grid after penalties, unless a penalty is confirmed applied in the snapshot; record which was used.
Driver absent from qualifying → `s_grid = 0.0` (they are not starting from a good position; this
is real information, not missing data). Pit-lane start → treat as `p = field_size + 1`.

### F2 — Team/car form (0.15)

Sum the constructor's points over the **last 5 completed rounds**, then normalize by the field
maximum (§3.3).

```
s_team = constructor_points_last5 / max(constructor_points_last5)
```

Both of a team's drivers get the same value — that is intended; this feature measures the car.

### F3 — Sprint result (0.13)

```
s_sprint = pos_score(sprint_position, K_SPRINT)   if classified
         = 0.0                                     if DNF
         = NEUTRAL                                 if the driver has no sprint entry
```

**Non-sprint weekends:** the feature is *undefined for the entire field*. Drop F3 and renormalize
the remaining seven weights to sum to 1.0 (§5.2). Do **not** give everyone NEUTRAL — a constant
across the field adds nothing but silently rescales every other weight.

### F4 — Driver recent form (0.11)

Over the **last 5 completed rounds**, per race compute `pos_score(finish_position, K_FIN)` (DNF →
0.0), take the mean, then normalize by the field maximum (§3.3).

Driver with no races in the window (mid-season debut) → `NEUTRAL` before normalization.

### F5 — Track history (0.08)

Look back at the **3 most recent editions** of this circuit. Per appearance compute
`pos_score(finish_position, K_FIN)` (DNF → 0.0). Recency-weight them: most recent ×1.0, next ×0.7,
oldest ×0.5. Take the weighted mean, normalize by the field maximum (§3.3).

**"Edition" means a race, not a season** (clarified 2026-08-24 — see below). The candidate pool is
still the 3 most recent *seasons* in which anyone on the grid raced here; within that pool, a
driver's appearances are ranked **by date** and capped at 3.

The distinction is silent in a normal season and load-bearing in an abnormal one. COVID put two
races at the same circuit in a single season — `bahrain` (Bahrain GP + Sakhir GP), `silverstone`
(British + 70th Anniversary) and `red_bull_ring` (Austrian + Styrian) in 2020, plus `red_bull_ring`
again in 2021 (Styrian + Austrian). A driver in that window has **4 or 5 appearances inside a
3-season pool**, and the original implementation indexed a 3-slot weight list by position and died
with a bare `IndexError`.

It surfaced as a *skipped race*, not a crash, so the damage was invisible: 11 races across
2021–2024 were being dropped from the A3 training set before anyone diffed the round numbers. Fixed
in `snapshot.py`; regression-tested in `test_backfill.TestDoubleHeaderSeasons`.

Ranking by date is identical to ranking by season for every season that ran one race here, so no
other race in the corpus moves — including §9's reference run, which is verified unchanged.

A related consequence, same cause: **each edition's weather is keyed by date, not season.** On a
season key the two 2020 Bahrain races collapse into whichever was seen first, and the survivor
donates its `wet` flag to the other — so a dry race could inherit its twin's rain. F7's live wet
branch reads that flag.

**Then shrink toward neutral by sample size** — mandatory:

| Appearances `n` | Blend |
|---|---|
| `n >= 3` | `s` (unchanged) |
| `n == 2` | `0.65 * s + 0.35 * NEUTRAL` |
| `n == 1` | `0.40 * s + 0.60 * NEUTRAL` |
| `n == 0` | `NEUTRAL` |

Without this, one race becomes a verdict. In the §9 reference run, Antonelli's single Zandvoort
appearance (P16, 2025) scored 0.058 raw — treating one bad afternoon as a settled fact about the
championship leader. Shrinkage lifts it to 0.323. This is the same intuition as regularization in
a trained model: pull weakly-evidenced estimates toward the prior.

### F6 — Championship standing (0.08)

```
s_champ = driver_points / leader_points
```

From `{season}/driverstandings.json` after the most recent completed round. Leader scores 1.0.
Driver not in standings → 0.0 (they have genuinely scored no points).

This is the season-long counterweight to F4's five-race window. Without it the model has no
concept of who is actually winning the championship — which is exactly the defect the §9 dry run
exposed.

### F7 — Weather (0.05)

Let `P_max` be the maximum `precipitation_probability` across the forecast hours covering the race
window (`01-data-pipeline.md` §5).

- **`P_max < 40%` → dormant.** Every driver gets `NEUTRAL`. The feature contributes an identical
  constant to every score and therefore cannot change the ranking. This is the intended behaviour
  for dry races; do not drop the weight.
- **`P_max >= 40%` → active.** `s_weather` = the driver's wet-weather rating: mean
  `pos_score(finish_position, K_FIN)` over their races where observed `precipitation > 0 mm`
  (per `01-data-pipeline.md` §5.4 the *archive* API has no probability field, so wet races must be
  identified by observed rainfall), normalized by field maximum, with the F5 shrinkage table
  applied on the count of wet races. Fewer than 1 wet race → `NEUTRAL`.

### F8 — Teammate H2H (0.05)

Over all completed rounds this season, count races where **both** teammates were classified. `s` =
fraction of those the driver finished ahead in.

Fewer than 3 such races, or no teammate → `NEUTRAL`. Below 3 the sample is noise.

---

## 5. Combining

### 5.1 Track overtaking flex

Circuits differ in how much starting position determines the result. Scale F1's weight by a
per-circuit multiplier `m` in `[0.85, 1.15]`, then rescale the other seven weights so the total
stays 1.0:

```
w_grid_eff  = 0.35 * m
scale       = (1 - w_grid_eff) / (1 - 0.35)
w_other_eff = w_other * scale
```

| `m` | Circuits |
|---|---|
| **1.15** | Zandvoort, Monaco, Hungaroring, Singapore, Imola |
| **1.00** | Silverstone, Suzuka, Barcelona, Austin, Melbourne (default) |
| **0.85** | Monza, Baku, Jeddah, Spa, Interlagos |

Any circuit not listed defaults to **1.00**. These are hand-set judgements about overtaking
difficulty, not measurements — flagged in §10 for replacement with real data in A3.

At Zandvoort (`m = 1.15`) the effective weights are:

```
grid 0.4025 | team 0.1379 | sprint 0.1195 | driver 0.1011
track 0.0735 | champ 0.0735 | weather 0.0460 | teammate 0.0460
```

### 5.2 Dropping an unavailable feature

If a feature is undefined **for the entire field** (no sprint; weather data unavailable), remove
it and divide the remaining weights by their sum so they total 1.0. Record every dropped feature
in the snapshot's provenance block — a prediction made with six features is not comparable to one
made with eight, and A3 calibration will need to know which is which.

Never drop a feature because it is missing for *one* driver. That is what NEUTRAL is for (§3.2).

### 5.3 Raw score

```
score_d = sum over features f of ( w_f_eff * s_f_d )
```

`score_d` lies in `[0,1]`. It is **not** a probability and must never be presented as one.

### 5.4 Score to probability

```
p_d = exp(score_d / T) / sum over all drivers e of exp(score_e / T)
```

**`T = 0.1168`** (locked).

Straight division by the sum would produce a hopelessly flat field — the favourite would land
near 8–10% where the market says 37%. Softmax's exponential sharpens the spread, and `T` is the
single knob controlling how decisive the model is: low `T` = peaked and confident, high `T` = flat
and hedged.

**How T was derived (market-blind).** `T` is anchored to F1's own history, not to market prices —
anchoring it to the market would partly fit the algo to the thing it is meant to be tested
against. Calibration scenario: a synthetic 20-car field on a neutral track (`m = 1.00`) with no
sprint, where driver *i* starts *i*-th and **every other feature is NEUTRAL**. Solve for `T` such
that the pole-sitter's probability ≈ **0.42**, the long-run rate at which pole converts to a win
in the modern era. That yields `T = 0.1168`, and the reference field:

```
P1 42.0%  P2 18.2%  P3 9.5%  P4 5.7%  P5 3.8%  P6 2.8%  ...  P11-P20 combined 10.9%
```

Recompute `T` only if the calibration *rule* changes. Do not retune it per race — a `T` that moves
makes calibration across races unmeasurable.

Implementation note: subtract `max(score)` from every score before exponentiating. Standard
softmax numerical-stability practice; the result is identical and it cannot overflow.

---

## 6. Comparison output

After scoring, and only after, load both venues' normalized probabilities from the snapshot
(`01-data-pipeline.md` §8.4) and emit per driver:

- `p_algo`
- `p_polymarket`, `p_kalshi`, and `p_market_mean`
- `edge = p_algo - p_market_mean`
- `venue_spread = |p_polymarket - p_kalshi|` — when this is small the venues corroborate each
  other, so a large `edge` is attributable to our algo rather than to one venue being off

Report the largest positive and negative edges explicitly. **The divergences are the product**, not
a defect to be tuned away.

---

## 7. Scoring ourselves

Per race, persist alongside the snapshot:

- Brier score for the algo, Polymarket, Kalshi, and the market mean
- Whether the algo's top pick won
- Whether the algo beat the market mean on Brier

Brier score for a winner market: `sum over drivers of (p_d - outcome_d)^2`, where `outcome_d` is 1
for the winner and 0 otherwise. Lower is better. One race proves nothing — a favourite losing is
normal, not a refutation. The comparison only becomes meaningful across a season, which is why
every run must be persisted from the very first race.

---

## 8. Required assertions

Fail loudly rather than emit a plausible wrong number:

1. Base weights sum to 1.0 (±1e-9); effective weights sum to 1.0 after flex and any drops.
2. Every sub-score is within `[0, 1]`.
3. `T > 0`.
4. Output probabilities sum to 1.0 (±1e-6).
5. Driver set matches the qualifying classification exactly.
6. No market field was read before scoring completed (§1).
7. All `01-data-pipeline.md` §6.5 market-staleness assertions passed.

---

## 9. Reference run — Dutch GP 2026 (Phase A2)

Real data, pulled 2026-08-22. Zandvoort, `m = 1.15`, sprint weekend, weather **dormant**
(`P_max = 37% < 40%`). **A correct implementation reproduces this table.**

| Driver | Grid | grid | sprint | team | form | champ | track (n) | H2H | Score | **p_algo** |
|---|---|---|---|---|---|---|---|---|---|---|
| NOR | 1 | 1.000 | 0.565 | 0.733 | 0.905 | 0.598 | 0.450 (3) | 0.500 | 0.7856 | **36.2%** |
| RUS | 2 | 0.779 | 1.000 | 0.931 | 0.943 | 0.750 | 0.412 (3) | 0.375 | 0.7824 | **35.2%** |
| ANT | 3 | 0.607 | 0.424 | 0.931 | 0.770 | 1.000 | 0.323 (1) | 0.625 | 0.6501 | **11.4%** |
| LEC | 6 | 0.287 | 0.751 | 1.000 | 0.838 | 0.647 | 0.248 (3) | 0.556 | 0.5421 | **4.5%** |
| HAM | 5 | 0.368 | 0.180 | 1.000 | 1.000 | 0.763 | 0.188 (3) | 0.444 | 0.5220 | **3.8%** |
| PIA | 4 | 0.472 | 0.319 | 0.733 | 0.508 | 0.429 | 0.785 (3) | 0.500 | 0.5158 | **3.6%** |
| VER | 7 | 0.223 | 0.240 | 0.802 | 0.916 | 0.500 | 1.000 (3) | 1.000 | 0.5009 | **3.2%** |

Remaining 15 drivers share ~2.1%.

### Algo vs. market

| Driver | Algo | Polymarket | Kalshi | Market mean | Edge |
|---|---|---|---|---|---|
| NOR | 36.2% | 36.5% | 38.0% | 37.2% | **−1.0** |
| RUS | 35.2% | 25.5% | 24.5% | 25.0% | **+10.2** |
| ANT | 11.4% | 24.5% | 25.5% | 25.0% | **−13.6** |
| HAM | 3.8% | 5.0% | 5.5% | 5.3% | −1.5 |
| LEC | 4.5% | 4.4% | 5.0% | 4.7% | −0.2 |
| PIA | 3.6% | 3.6% | 4.5% | 4.0% | −0.5 |
| VER | 3.2% | 2.4% | 3.5% | 3.0% | +0.2 |

Venue spread is ≤1.5 points on every driver, so the two markets corroborate each other and both
divergences are attributable to our algo.

**Five of seven drivers land within 1.5 points of the market.** The model is not producing noise.

**The two real disagreements, stated plainly:**

- **Russell +10.2.** He won today's sprint from the second row and Mercedes has the strongest
  recent constructor form. F1 and F3 both reward him, and the algo rates a fresh, track-specific
  race-pace demonstration more highly than the crowd does.
- **Antonelli −13.6.** The market backs the championship leader; the algo sees P3 on the grid, a
  P4 sprint, and one weak Zandvoort appearance. F6 at weight 0.08 cannot fully offset that.

These are the A2 test. If Russell wins, the algo's weighting of the sprint is vindicated against
the crowd. If Antonelli wins, F6 is underweighted and the algo is systematically undervaluing
season-long standing — a concrete, actionable finding either way.

**Top-2 concentration is 71.4% against the market's 62.3%** — the algo is somewhat more confident
than the crowd. Expected: correlated features (§2.1) amplify score spread beyond what the
grid-only calibration scenario in §5.4 anticipated. Do not patch `T` to hide this. Record it, and
let a season of Brier scores (§7) decide whether it is real overconfidence.

---

## 10. Open items

1. **Track overtaking multipliers are hand-set judgements**, not measurements. Replace with
   per-circuit overtake counts once A3 has the data.

   **PAID, 2026-09-04, and the answer is that the `m = 1.15` tier is not a coherent group.** `09`
   §5.4's background-rate fit was built and run (`09` §10.1), and it reports the per-circuit
   residual as a by-product exactly as promised below. Measured per-lap adjacent-pair swap rate,
   over the 11 circuits in the 2026 archive, against the rate the fitted model predicts for that
   circuit's `m`:

   | Circuit | `m` | pairs | observed | predicted | obs/pred |
   |---|---|---|---|---|---|
   | Shanghai | 1.00 | 841 | 0.0868 | 0.0635 | **1.37** |
   | Spa | **0.85** | 805 | 0.0770 | 0.0850 | 0.91 |
   | Barcelona | 1.00 | 1,132 | 0.0769 | 0.0640 | 1.20 |
   | Red Bull Ring | 1.00 | 1,239 | 0.0726 | 0.0635 | 1.14 |
   | **Hungaroring** | **1.15** | 1,335 | **0.0719** | 0.0469 | **1.53** |
   | Miami | 1.00 | 955 | 0.0691 | 0.0631 | 1.10 |
   | Silverstone | 1.00 | 1,035 | 0.0667 | 0.0628 | 1.06 |
   | Suzuka | 1.00 | 1,028 | 0.0632 | 0.0632 | 1.00 |
   | Melbourne | 1.00 | 924 | 0.0574 | 0.0639 | 0.90 |
   | Montréal | 1.00 | 1,112 | 0.0477 | 0.0642 | 0.74 |
   | **Monaco** | **1.15** | 1,340 | **0.0201** | 0.0473 | **0.43** |

   **Monaco and the Hungaroring carry the same hand-set `m = 1.15` and their measured swap rates
   differ by 3.6×** — 0.0201 against 0.0719. Worse for the tier: the Hungaroring's rate is *above*
   the `m = 1.00` circuits' average of about 0.065, so a circuit labelled "position hard to change"
   reorders its field **more** than the default group does. Monaco is the only circuit in the corpus
   that clearly behaves like the label.

   Spa, on the other side, is labelled `m = 0.85` ("position easy to change") and comes in at 0.0770
   against the default group's ~0.065 — a 1.18× lift where the tier implies a large one.

   **A second, independent symptom that the circuit effect is not identified from twelve races:**
   `09` §5.4 enters circuit as a single fitted slope on `m`, and fitted race-forward that slope moves
   from **−7.2 on R1–R8 to −2.0 on R1–R11** — it collapses the moment the Hungaroring arrives and
   contradicts Monaco. One parameter over three tier values is already the most this corpus supports,
   and it is not stable.

   **This corroborates `05`'s A3 finding from a different direction**, which matters because the two
   measurements share no machinery: `05` §3.5's fitted tier interaction found `grid_x_hard` negative
   in all seven folds — "a stable inversion of `02` §5.1's predicted ordering" — and that is the
   `m = 1.15` tier again, failing again. `05`'s locked decision to drop `m` from A3 v1 stands, and
   this is now the second line of evidence for it.

   **What this does NOT license.** It does not license re-tuning `m` in `02` from these numbers. An
   adjacent-pair swap rate is not the quantity `m` scales — `m` scales the *weight on grid position*
   in a pre-race scorer, and `09` §5.4's rate contains pit cycles and retirements as well as on-track
   passes (`09` §2.3). The finding is that **the tier grouping is wrong**, not that a particular
   replacement value is right. Replacing `m` needs its own measurement of the thing `m` actually
   claims, and that is still this item's open half.

   Earlier note, kept for the record — **partial payment then available from an unexpected direction
   (2026-08-27):** `09` §5.4's background per-lap transition model enters
   circuit as `m` rather than as a free per-circuit parameter, so fitting it produces a per-circuit
   residual against `m` as a by-product. `09` §2.3 already measures the raw per-lap adjacent-pair
   swap rate across 12 races — about 6% at the front of the field and 7–8% in the midfield, which
   is a **flatter gradient than `02` §5.1's multipliers assume.**
2. **`T` is calibrated on a grid-only synthetic field, and the real field is *flatter*, not
   sharper.** This item previously said the calibration "understates the score spread of a real,
   correlated field." Measured against §9's own reference run, the sign is the other way round:

   | | P1−P2 raw-score gap |
   |---|---|
   | Synthetic calibration field (grid ramp, everything else NEUTRAL) | 0.0774 |
   | Real 2026 Dutch GP (NOR vs. RUS) | 0.0032 |

   Averaging eight partially-disagreeing features **compresses** top-of-field separation relative
   to a single-feature grid ramp — the favourite is rarely top of all eight. So the same `T`
   yields a flatter favourite than the calibration targeted: 36.1% at `T = 0.1168` against the
   0.42 anchor, and it would take `T ≈ 0.0871` to put the real field's favourite at 0.42.

   This matters for A3 because it sets the *direction* of the recalibration, and the previous
   wording pointed the wrong way. Caveat: n=1, and the Dutch GP had two near-tied favourites, so
   the magnitude is not established — but the compression argument is structural, not specific to
   this race. Recalibrate against realised outcomes in A3, and check the sign against more than
   one race before trusting a number.
3. **The 0.42 pole-conversion anchor is a rounded historical figure.** It should be recomputed
   from the actual Jolpica record over a defined era rather than assumed.
4. **Weather (F7) has never executed in its active branch** — the reference run was dry. Its wet
   path is unvalidated and must be tested before being trusted on a wet race weekend.
5. **`K_GRID`, `K_SPRINT`, `K_FIN` were chosen for plausible shape**, not fitted. They are
   candidates for tuning in A3.
