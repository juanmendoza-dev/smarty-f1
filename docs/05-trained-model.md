# 05 — Trained Winner Model (Phase A3)

Status: **spec written 2026-08-23, not yet implemented.** Read `welcome.md`, `00-roadmap.md`,
`01-data-pipeline.md`, and `02-winner-prediction-algo.md` first. `04-outcome-expansion-algo.md`
is relevant but not a prerequisite.

This spec defines Phase A3 completely enough to implement without further questions, in the same
sense `02` did for A1: every decision this phase needs is either stated here as locked, or listed
in §10 as genuinely still open with the reason it could not be settled from the data available.

**Verification note:** every count in §4.3 and §5.3 was computed live against Jolpica production on
**2026-08-23**, and the round-1 behaviour in §4.3 was executed end to end on **2026-08-24**. The
package availability in §7 was checked on the project machine on 2026-08-23. Anything not verified
is marked `UNVERIFIED`.

**Counting caveat, learned the hard way while writing this spec.** The sprint-weekend count in
§4.3 was first recorded as 26 and is actually **29**. The first query passed `sprint.json?limit=100`
and counted races in the response — but Jolpica pages by *result row*, not by race, so at ~20 rows
per sprint a 100-row page caps at 5 races. Three consecutive seasons reporting exactly 5 was the
truncation signature, and each response's own `total: 120` said a second page existed. This is the
same failure `ee45ecc` fixed inside `jolpica.season_results`, rediscovered from outside it. Any
count taken from a paginated Jolpica endpoint must page to `total` or divide `total` by field size
— never trust `len(RaceTable.Races)` from a single request.

---

## 1. Scope

Phase A3 trains a model for **race winner** on backfilled historical races and evaluates its
calibration against the Phase A1 rule-based scorer.

**A3 does not retire A1.** A1 is the baseline A3 has to beat, and a baseline you have deleted is
not a baseline. Both stay runnable against the same snapshot, and `score.py`'s existing output is
unchanged by this phase. If A3 loses to A1 on the §6 protocol, the correct outcome is that A1
stays the production predictor and A3 is recorded as a negative result — that is a real finding,
not a failure to be tuned away.

**Market-blindness carries over unchanged** (`02` §1). The model never sees Polymarket or Kalshi
prices, at train time or inference time. Market data is not a feature, not a label, and not a
sample weight. An implementation that passes market data into the fit is **wrong**, however good
its output looks — the entire point of the project is asking whether our own signal beats the
crowd's, and a model fitted on the crowd cannot answer that.

**Out of scope for this phase:**

- Podium, points, and fastest lap. Those stay on `04`'s Plackett-Luce simulation, which consumes
  whatever win-strength scores it is handed — so once A3's scores are trusted, `04` upgrades for
  free with no new spec. DNF is genuinely separate (its own reliability feature, `04` §5.1) and
  is not touched here.
- Gradient-boosted trees. The roadmap names XGBoost/LightGBM as A3's second step; this spec covers
  the first one only. See §7 — GBTs force an interpreter decision that the first model does not.

---

## 2. Decisions locked by this spec

| # | Decision | Value | Where argued |
|---|---|---|---|
| D1 | Model shape | Conditional logit over driver-races, race as the choice set | §3.1 |
| D2 | F7 weather | **Train-dormant** — F7 is not a column at all | §3.3 |
| D3 | F3 sprint | One column, structurally 0 on non-sprint races | §3.4 |
| D4 | Track multiplier `m` | **Dropped** in v1; fitted tier interaction is the upgrade path | §3.5 |
| D5 | `T` | **Dissolved** — absorbed into coefficient scale, not a separate parameter | §3.1 |
| D6 | Label | Classified P1 of the race, one winner per race | §4.1 |
| D7 | Era window | Labels from **2014** (start of the hybrid era) | §4.3 |
| D8 | Backfill output | Feature matrix (CSV), not one snapshot JSON per race | §5.1 |

D2 is the decision that was escalated to the owner and approved on 2026-08-23. It is also
recorded in `01-data-pipeline.md` §5.6, which is where that spec required it be written down.

---

## 3. The model

### 3.1 A1 is already a conditional logit — this is the whole design

`02` §5.3–5.4 computes, per driver `d`:

```
score_d = sum over features f of ( w_f_eff * s_f,d )
p_d     = exp(score_d / T) / sum over e of exp(score_e / T)
```

Substitute the first line into the second and the division by `T` distributes over the sum:

```
p_d = exp( sum_f (w_f_eff / T) * s_f,d ) / sum_e exp( sum_f (w_f_eff / T) * s_f,e )
```

Define `β_f = w_f_eff / T`. That is exactly the likelihood of a **conditional logit** (McFadden's
choice model) over the drivers in one race, with the coefficient vector `β` fixed by hand instead
of estimated. A1 is not "a scoring heuristic that a model will later replace" — it is this model
with the coefficients already filled in.

At Zandvoort (`m = 1.15`, sprint weekend, `T = 0.1168`) A1's implied coefficients are:

| Feature | `w_f_eff` | implied `β_f` |
|---|---|---|
| F1 grid | 0.4025 | **3.446** |
| F2 team form | 0.1379 | 1.181 |
| F3 sprint | 0.1195 | 1.023 |
| F4 driver form | 0.1011 | 0.866 |
| F5 track history | 0.0735 | 0.629 |
| F6 championship | 0.0735 | 0.629 |
| F7 weather | 0.0460 | 0.394 |
| F8 teammate H2H | 0.0460 | 0.394 |

Three consequences follow, and they are the reason this framing is worth the section:

**`T` stops being a parameter.** It only ever existed because `02` forced the weights to sum to
1.0, which fixes their *ratios* but throws away their *scale* — and softmax needs a scale. `T` put
the scale back. A fitted `β` carries ratio and scale together, so there is nothing left for `T` to
do. This retires `00-roadmap.md`'s "recalibrate `T` against outcomes in A3" and `02` §10.2 as a
separate task: no recalibration step exists, because the fit *is* the calibration.

**The 0.42 pole-conversion anchor becomes unnecessary.** `02` §10.3 flags it as a rounded
historical figure that ought to be recomputed from the record. Under A3 it is not recomputed, it
is not used — the data sets the scale directly, which is strictly better than anchoring to a
summary statistic of the same data.

**The sum-to-1.0 constraint is dropped.** `02` §8's first assertion exists to protect `T`'s
meaning. With no `T`, `β` is free and unconstrained, and asserting anything about `sum(β)` would
be meaningless. This does not apply to A1's own assertions, which stay exactly as they are.

**One caveat, stated precisely.** A1's `β` is not constant across races: `effective_weights()`
(`score.py:260`) rescales all eight weights by the per-race multiplier `m`, and renormalizes seven
of them on non-sprint weekends. So A1 is a conditional logit with a *different* fixed `β` per
`(m, sprint-regime)` combination — not one model but six. A3 fits a **single pooled `β`**, which
is what D4 (drop `m`) and D3 (no renormalization) are for. After this phase the two predictors no
longer share an exact functional form; they share a family. §6.3 compares them as separate
predictors, which is the honest way to do it regardless.

### 3.2 What cancels: the within-race constant rule

In a conditional logit, **any feature that takes the same value for every driver in a race
contributes exactly nothing to that race's likelihood.** If `s_f,j = c` for all `j` in race `r`:

```
p_d = exp(β_f·c + rest_d) / sum_e exp(β_f·c + rest_e)
    = exp(β_f·c)·exp(rest_d) / ( exp(β_f·c)·sum_e exp(rest_e) )
    = exp(rest_d) / sum_e exp(rest_e)
```

The `exp(β_f·c)` factor divides out of numerator and denominator. This is not an approximation and
does not depend on `c`.

This one fact resolves three separate problems that would otherwise need hand-written rules, and
it is why the pooled design in §3.3–3.5 is safe:

- **`02` §5.2's drop-and-renormalize rule is not needed here.** That rule exists because in A1, a
  field-constant feature *does* distort the result — not through the score (it cancels there too)
  but because leaving the weight in place changes what the other weights mean relative to `T`.
  With no `T` and no normalization constraint, a field-constant column is simply inert.
- **A race where a feature is constant does not inform that feature's coefficient** — it neither
  helps nor corrupts it. Feature-poor races are self-correcting rather than harmful (§4.3).
- **A feature that is constant in *every* training race has an unidentified coefficient.** No
  amount of data will estimate it. This is exactly F7's situation, next.

### 3.3 F7 weather — train-dormant (D2, locked)

`01-data-pipeline.md` §5.6 states the problem: F7's live input is a precipitation *probability*
from the forecast endpoint, and the archive endpoint — the only one that answers for a past date —
serves observed millimetres only. A backfilled race therefore has no `p_max`, and §5.6 required
the choice between training F7-dormant and inventing a wet proxy be recorded rather than settled
implicitly. **Owner decision, 2026-08-23: train-dormant.**

The rationale for dormant over a proxy: the wet branch has never executed on a real race
(`02` §10.4 — the Dutch GP was dry, and `test_f7_wet_branch.py` exercises the code path against
archived data, not the model's ability to predict a wet race). A proxy would be modelling a
quantity that has never been validated at inference time, and it would carry the deeper skew of
the two — "it rained" at train time against "rain is forecast" at inference time, wearing one
name. Dormant's skew is more honest and easier to reason about: the feature is absent, not lying.

**What dormant actually means here is stronger than "score it NEUTRAL," and the difference
matters.** Dormant scores every driver `NEUTRAL = 0.5`. By §3.2 that is a within-race constant, in
*every* training race by construction. So `β_weather` is not merely poorly estimated — it is
**unidentified**, and no fitting procedure can return a meaningful value for it.

Therefore:

> **F7 is not a column in the design matrix.** The A3 model has **7 features, not 8**. Do not
> include a constant 0.5 column and report whatever coefficient the optimizer emits for it; that
> number is an artifact of the regularizer, not an estimate.

**The consequence at inference time, stated plainly, because this is the skew and it should not
be discovered later:** on a wet race weekend (`P_max >= 40%`), A1 has a wet-weather term and A3
does not. A3 will predict a wet race as though it were dry. The spec does not paper over this.
Until it is fixed, on any race where A1's weather branch goes active:

1. Run both predictors and record both.
2. Treat A3's number as out-of-domain and report it as such — it is not a like-for-like comparison
   with its dry-race performance, and pooling wet races into A3's Brier average without a note
   would quietly understate the model.

Fixing it properly needs either an inference-time feature that the archive can also produce, or
enough live-snapshotted wet races to fit a wet term on forecast probabilities directly. Both are
future work; see §10.

### 3.4 F3 sprint — one column, zero on non-sprint races (D3, locked)

A pooled model cannot have a design matrix whose column count varies by race, so `02` §5.2's
"drop F3 and renormalize the other seven" is not available. Set `s_sprint = 0` for the entire
field on a non-sprint race and keep the column.

By §3.2 this is **exactly equivalent to dropping it for those races** — a field-constant column
cancels out of the likelihood — so the choice of 0 over any other constant carries no modelling
content. 0 is chosen because it makes the "this race did not inform F3" case visible in the matrix
rather than confusable with a real neutral score.

`02` §4's per-driver rules are unchanged on races that *do* have a sprint: a classified driver
gets `pos_score(sprint_position, K_SPRINT)`, a DNF gets 0.0, and a driver with no sprint entry
gets `NEUTRAL`. Only the whole-field case is redefined.

Sprint weekends are **29 of the 264 label races** (§4.3) — about 11%, all from 2021 onward. `β_3`
is therefore estimated from a ninth of the corpus and should be expected to have a wide interval.
Report it with one.

### 3.5 The track multiplier `m` — dropped in v1 (D4, locked)

`02` §5.1's per-circuit multiplier is a hand-set judgement, flagged in `02` §10.1 and
`00-roadmap.md` for replacement with real data in this phase. A3 v1 **drops it entirely**: no
per-race rescale, one pooled `β_grid` for every circuit.

Dropping is the honest v1 because the multiplier's claim — that grid position matters more at
Zandvoort than at Monza — is a claim the model can *test* rather than assume, and asserting it as
a fixed rescale would prevent that test.

**The specified replacement**, once v1 exists, is a fitted interaction on `s_grid` using the three
tiers `02` §5.1 already defines (1.15 / 1.00 / 0.85), which costs **two extra parameters**, not 33.
A per-circuit interaction is not available at this sample size — 33 circuits against 264 races is
roughly 8 races per circuit and would fit noise. If the two tier coefficients come back ordered as
`02` §5.1 predicts, the hand-set judgement is vindicated and can be replaced with the fitted
numbers; if they come back flat or inverted, `02` §10.1 has its answer and the multiplier should
go. Either result closes the open item. Gate this on v1 fitting cleanly first.

Note the tier table itself is defined only for the 15 circuits in `lib/circuits.py`; everything
else defaults to 1.00. For the interaction, the 18 circuits added in §5.2 need tier assignments or
an explicit "default tier" bucket — a small decision, listed in §10.

**Run 2026-08-26, on the dev folds — closed: `m` stays dropped.** `tier_interaction_backtest.py`
builds `grid_x_hard`/`grid_x_easy` (`s_grid` masked to circuits `lib.circuits.tier_for` calls
"hard"/"easy"; every circuit missing from `OVERTAKING_MULTIPLIER` — including all 17 the backfill
added — lands in the same "default" bucket as the explicitly-1.00 circuits, per §10 item 2's own
resolution, not tiered by hand). Reusing `fit.py`'s likelihood/gradient/evaluation code unchanged,
on the same 2017–2023 dev folds: validation selects the same top-of-grid A1 prior as the 7-feature
model and crushes both new coefficients to ≈0 (pooled Brier 0.58517, identical to the baseline to
5 decimal places — the interaction is invisible at that shrinkage). The informative signal is in
the **unregularized** fit on the largest fold (trained 2014–2022): `grid_x_hard` = **−0.89**,
`grid_x_easy` = **−0.52** — both negative, where §5.1 predicts hard positive and easy negative.
Under mild shrinkage (λ=0.01, zero prior) both collapse toward zero and become unstable
(`grid_x_hard` flips to −0.05, `grid_x_easy` flips to +0.12) — the sign isn't just wrong, it isn't
stable, consistent with ~8 races per non-default tier being too little data to pin two extra
parameters down. Verdict: **flat/inverted, not vindicated.** `02` §5.1's hand-set ordering is not
supported by the fitted interaction, so `m` stays dropped for v1 and is not coming back — this
closes the open item without needing the holdout. `test_tier_interaction.py` covers the tier
lookup and column-append logic (12 tests); `tier_interaction_backtest.py` itself is standalone
verification tooling in the `weather_backtest.py` mold, not part of the fitting path `fit.py`'s
tests check.

### 3.6 Identification and regularization

- **No intercept.** A per-race intercept is a within-race constant and cancels by §3.2. A global
  intercept is the same thing. Do not fit one.
- **L2 penalty on `β`**, strength chosen on the §6.1 validation splits, not on the test period.
  Not L1: with 7 correlated features (`02` §2.1 — F1/F3/F8 partly measure the same thing) the
  goal is shrinkage, not selection, and dropping one of a correlated pair at random makes the
  coefficients less interpretable, which is the opposite of what this project wants from them.
- **Do not penalize toward zero blindly.** Consider penalizing toward A1's implied `β` from §3.1
  as the prior. It is a defensible informative prior — it encodes the owner's domain judgement —
  and it makes "how far did the data move the weights?" directly readable off the fit, which is
  the question A3 is actually being asked. Flagged in §10 as a choice to make with evidence rather
  than settled here.
- **Separation check.** If any feature perfectly separates winners from non-winners within every
  race, its coefficient diverges. With `s_grid` this is not expected (poles lose regularly) but it
  must be checked rather than assumed, because the failure mode is a coefficient that grows until
  the optimizer stops rather than an error.

---

## 4. The training set

### 4.1 Unit of observation

One row per **driver-race**. The choice set / group key is `(season, round)`. The label is 1 for
the driver classified P1 and 0 for everyone else — exactly one positive per group.

The winner is taken from the same code path `postrace.py` already uses: the classified P1 from
Jolpica's race results, with `is_classified()` (`lib/features.py`) deciding classification. Do not
reimplement that test — it has already been wrong once (`04` §10.5, the `"Lapped"` literal).

A race whose result has no classified P1 is dropped with a loud log line, not silently skipped.

### 4.2 Feature vector — computed by A1's own code, not reimplemented

The seven columns are `02`'s sub-scores: `grid`, `team`, `sprint`, `driver_form`, `track`,
`champ`, `teammate` (F7 excluded per §3.3), each already in `[0,1]`.

**This is the anti-skew requirement and it is mandatory:** the backfill harness must produce these
by calling `snapshot.build_grid` / `build_form` / `build_track_history` and `score.py`'s
`compute_*` functions **unchanged**, exactly as `test_phase_a4.py:163-193` already does for the
2023 Dutch GP. It must not contain its own copy of the feature logic.

The reason is that train/serve skew in this project would be invisible: a reimplemented `pos_score`
with a different `K` produces perfectly plausible numbers that are simply not the same feature the
scorer computes at inference. Sharing the code path makes that class of bug impossible rather than
merely unlikely. It also means any future fix to a feature applies to both sides at once.

Persist alongside the seven columns, for diagnostics and for §6.1's grouping: `season`, `round`,
`race_date`, `circuit_id`, `driver_code`, `constructor_id`, `is_sprint_weekend`, `quali_position`,
`finish_position`, `status`, `label`, and `track_n` (F5's appearance count, which records how much
of `s_track` is shrinkage rather than evidence).

### 4.3 Coverage (verified live, 2026-08-23)

| Quantity | Value |
|---|---|
| Rounds scheduled, 2014–2026 | 275 |
| Rounds with a result (2014–2025 complete, 2026 through R12) | **264** |
| Distinct circuits in that window | 33 |
| Sprint weekends (2021–2026) | 29 |
| Approximate driver-race rows at ~20 cars | **~5,300** |

**Era boundary: 2014**, the first year of the hybrid power-unit regulations. A regulation boundary
is the right kind of cut because the features are about competitive order, and a rule change is
what most sharply redraws it. It also lands on the roadmap's own "~200+ historical races" estimate
without straining for volume.

**2014 rows are not F5-cold.** `build_track_history` calls
`circuits/{id}/drivers/{id}/results.json`, which has no season filter — the only filter is the
date guard at `snapshot.py:244`. So a 2014 race's track history reaches back into 2011–2013
naturally. The era boundary limits which races get *labels*, not how far features may look back,
and no separate warm-up window is needed.

**Round 1 of each season is structurally feature-poor**, and this is a property of the features,
not of the window: F2, F4, F6, and F8 are all computed from *this season's* completed rounds
(`build_form`), of which round 1 has none. Those columns are field-constant on round 1, so by §3.2
those 13 races inform only `s_grid` and `s_track` and cannot corrupt the other coefficients.
**Keep them.** Dropping them would discard genuine grid/track-history evidence to avoid a problem
the model does not have. The same reasoning applies with diminishing force to rounds 2–5, where
the five-race windows are partially filled.

**Verified by execution, 2026-08-24**, because "it will be field-constant" and "it will build at
all" are different claims and only the first follows from §3.2. A full round-1 backfill of the 2015
Australian GP through `build_grid` → `build_form(race_has_run=True)` → `build_track_history` →
`score_all` completes without error and yields exactly one distinct value across the field for each
of the four features:

| Feature | Round-1 value | |
|---|---|---|
| F2 team | 0.0 | constant |
| F4 driver form | 1.0 | constant (NEUTRAL for everyone, then field-normalized to 1.0) |
| F6 championship | 0.0 | constant |
| F8 teammate H2H | 0.5 | constant |

Probabilities sum to 1.0 and the ranking is driven by grid and track history alone, as expected.
The constant differs per feature; by §3.2 the value is irrelevant, only the constancy matters.

Two guards make this work and both should be left alone: `build_form` short-circuits on
`prior_round < 1` and never calls `driver_standings`, and `compute_champ` (`score.py:177`) falls
back to `leader_points = 0.0` on an empty standings list rather than raising. The first guard is
load-bearing in a way worth naming — `jolpica.driver_standings` branches on `if round_`, and **0 is
falsy in Python**, so a round-0 request that reached it would silently take the "latest standings"
path and hand a finished season's final table to F6. That is precisely the leak `8dcc18d` fixed,
and the only thing preventing it here is that `build_form` never makes the call. Do not "simplify"
that guard.

### 4.4 Leakage rules

Two leaks were found and fixed in the backfill path on 2026-08-23 (`9ec25ea`, `8dcc18d`,
`01-data-pipeline.md` §4.6). They are fixed in `snapshot.py`, and §4.2's rule that the harness
reuses that code is what keeps them fixed. The harness must additionally guarantee:

1. **No feature may read the target race's own result.** The guard for F5 is the strict
   `race["date"] >= race_date` filter at `snapshot.py:244` — note the target race's date is
   *exactly* `race_date`, so a non-strict comparison silently trains on the label.
2. **F6 standings must be sourced with `race_has_run=True`**, which routes to
   `{season}/{round-1}/driverstandings.json`. Passing the live path on a backfill returns a
   finished season's *final* table. This was a real bug and it looks entirely plausible in the
   output (`01` §4.6).
3. **F2/F4/F8 use rounds `1..round-1` only**, never `1..round`.
4. **Known accepted limitation, not a bug:** a backfilled sprint weekend loses that weekend's
   sprint points from F6, because no round-indexed endpoint answers "after round N−1's race plus
   round N's sprint" (`01` §4.6). Bounded at 8 points on a leader-normalised 0.08-weight feature.
   It is *train-only* skew — a live snapshot does capture those points — so it belongs in this
   spec's ledger even though it is not worth working around.

   **Measured 2026-08-24** on the 2026 Dutch GP, the one race where both sides exist (see §9's
   assertion 10). Largest sub-score shift is RUS, 0.750 → 0.731; the leader's own total moved
   219 → 224. One subtlety this entry originally missed and which is worth stating, because it
   changes what the skew *does* rather than just its size: because F6 is normalised by the leader's
   points and **the leader scored in that sprint too**, the skew moves drivers in *both*
   directions. HAM took 2 sprint points and his backfilled `champ` is *higher* than the live one
   (0.772 vs. 0.763), since losing 5 points from the denominator outweighs losing 2 from his own
   numerator. So this is not uniformly "backfilled drivers look slightly worse" — it is a mild
   rescaling of the whole column, which is a gentler failure for a conditional logit than a
   directional bias would be.

A leakage check belongs in the harness rather than in a reviewer's head: assert that every result
row feeding any feature has `date < race_date`, per race, at build time.

### 4.5 What the harness must not do

No market data is pulled for backfilled races and none belongs in the matrix (§1). Polymarket and
Kalshi F1 markets do not exist for most of this window in any case, but the rule is about
market-blindness, not availability.

---

## 5. The backfill harness (`backfill.py`)

### 5.1 Output: a feature matrix, not snapshot JSONs (D8, locked)

The design fork is whether the harness writes one snapshot JSON per historical race and then
scores each, or assembles feature rows directly. **Rows directly**, to `data/training/`, as CSV.

Reasons, in order of weight:

- A snapshot is defined (`01` §8.3) as carrying a `markets` block, and §4.5 forbids one here. A
  market-less snapshot is a different artifact wearing the snapshot's name.
- `01` §8.3's immutability rule ("snapshots are append-only; a new pull is a new file") is a rule
  about *prediction runs* — it protects the audit trail for a prediction that was actually made. A
  backfill is a derived dataset that will legitimately be rebuilt whenever a feature changes;
  filing it under the same rule would either freeze the features or fill `data/snapshots/` with
  files that were never a prediction.
- `test_phase_a4.py:163-193` already assembles features from `build_*` without routing through
  `main()`, so the path is proven.

A consequence worth stating: this route never calls `snapshot.main()`, so `build_markets`'s
hard-fail on an unresolvable market (`snapshot.py:652`) is never reached and **no `--skip-markets`
flag is needed.** If a future version does route through the CLI, that flag becomes a prerequisite
— it would be a ~10-line addition mirroring `--skip-extended-markets` at `snapshot.py:605`/`:659`.

CSV over anything richer because there is no pandas on this machine (§7) and the matrix is ~5,300
rows — the stdlib `csv` module is entirely adequate, and a text format keeps the training set
diffable in git.

### 5.2 Prerequisite: `CIRCUIT_TIMEZONE` covers 15 of 33 circuits

**This blocks the harness on its first non-2026 circuit and must be fixed before any backfill
runs.** `CIRCUIT_TIMEZONE` (`snapshot.py:43`) is indexed as a bare dict lookup at `snapshot.py:261`
and `:321`, so a missing circuit is an immediate `KeyError`, not a degraded feature.

The 18 circuits in the 2014–2026 window with no entry (verified live 2026-08-23):

```
bahrain    hockenheimring  istanbul   losail   madring   miami
mugello    nurburgring     portimao   red_bull_ring       ricard
rodriguez  sepang          shanghai   sochi    vegas      villeneuve
yas_marina
```

Fill all 18 in before running. This is mechanical, but it is not optional and it is not a small
number — it is more than half the circuits in the corpus.

By contrast `lib/circuits.py`'s `OVERTAKING_MULTIPLIER` **degrades gracefully** — `multiplier_for`
defaults to 1.00 for anything unlisted. That would matter if `m` were used, but §3.5 drops it, so
the only place the tier table is needed again is the §3.5 interaction.

### 5.3 Request budget

`01` §4.3: 4 requests/second burst, 500/hour sustained, documented as likely to *decrease*. A
backfill is precisely the case that spec says a cache is worth the most for.

Per race, cold: 1 schedule (per season, not per race), 1 qualifying, 1 sprint, 1 circuit, 1
standings, 5 season-results pages (per season), and **one `driver_track_history` call per driver on
the grid** — ~20, which dominates everything else.

Naively that is ~20 × 264 ≈ 5,300 calls for track history alone, which at 500/hour is over ten
hours. It is much better than that, because of a property worth stating explicitly:

> `jolpica.driver_track_history` is keyed `(circuit_id, driver_id)` and returns that driver's
> **entire** history at that circuit in one response. Every edition of a circuit therefore shares
> one cached call. Backfilling all 12 Zandvoort editions costs the same ~20 calls as backfilling
> one.

So the real cost is roughly `(distinct circuits) × (drivers who ever raced there in-window)`, not
`races × drivers` — the corpus is 33 circuits, and the marginal cost of each additional edition of
an already-touched circuit is zero. Budget the first full run in hours, not minutes, and expect
re-runs to be nearly free. The harness must be **resumable**: write rows incrementally and skip
races already present in the output, so a rate-limit stall costs the remaining races and not the
completed ones.

### 5.4 Cache staleness rule

The whole-history response in §5.3 is exactly the shape of cached data that went stale in the
`season_results` bug fixed on 2026-08-23 (`ee45ecc`): a response cached in an earlier week keeps
answering for a season that has since moved on. Here the failure is quieter still, because the
leakage filter at `snapshot.py:244` drops anything on or after the target date — **a stale cache
yields silently fewer prior editions rather than an error**, which lands as an F5 shrunk further
toward NEUTRAL for no stated reason.

Rule: **refetch `driver_track_history` when the cached entry's fetch timestamp is earlier than the
target `race_date`.** If the fetch time is `>= race_date` then every edition strictly before it
had already happened at fetch time and is present in the response, so the cached copy is complete
for that target. For a backfill of past races this is satisfied trivially by any cache written
today, so it costs nothing; it bites only on live pre-race snapshots, which is where the
correctness matters most.

This requires a fetch timestamp on the cache entry. **It already exists** — `cached_get_json`
(`lib/httpcache.py:57`) writes `meta["timestamp"]` as an ISO-8601 UTC string at fetch time, and a
cache *hit* returns that original stamp with `cached: True` rather than overwriting it with the
read time, which is exactly the semantics the rule needs. Compare `meta["timestamp"][:10]` against
`race_date`. No change to `httpcache.py` is required.

**Implementation note (2026-08-24):** `backfill.build_race_rows` *detects and warns* on a stale
entry rather than refetching it. That is a deliberate divergence from this section's "refetch"
wording and the code is the better of the two: for a backfill of past races every entry is written
today, so the rule is satisfied trivially and a refetch would spend rate-limit budget for nothing.
The warning is what earns its place — it says "this run could not prove completeness" without
paying to prove it. Treat the wording above as describing the *rule*, and the warning as the
chosen enforcement.

### 5.5 The same staleness class, on results — and why §5.4's rule misses it

Found by running §9's assertion 10 on 2026-08-24. `2026/12/results.json` was cached at
**04:17Z on race day, nine hours before lights out**, holding an empty result. That entry never
expires, so every local run afterwards concluded the Dutch GP had never been run — including the
backfill, and including `postrace.py`.

**§5.4's rule does not catch this**, and the reason is worth recording: it compares
`meta["timestamp"][:10] < race_date`, at **day granularity**. Here the fetch and the race share a
date, so `"2026-08-23" < "2026-08-23"` is false and the entry reads as fresh. Day granularity is
adequate for `driver_track_history` (whose editions are whole races on earlier days) and is exactly
wrong for a same-day pre-race fetch.

The fix chosen is narrower than a timestamp comparison and does not need one: **an empty result
is re-asked once before it is believed** (`postrace.find_full_result`). It costs a request only on
the path that was about to fail anyway.

The tempting general version — refetch *any* empty response — is wrong and was rejected on
measurement: an empty `sprint` response for a 2014–2018 round is **correct and permanent** (sprints
begin in 2021), and the cache currently holds **93** of them. A blanket rule would refetch all 93
on every backfill, converting a correct cache into a per-run rate-limit cost.

---

## 6. Validation protocol

### 6.1 Splits are time-ordered, never random

**Do not use random k-fold.** Random folds put later races in the training set and earlier ones in
the test set, which leaks the future through every season-long feature and produces a validation
number that cannot be reproduced in production. This is the single most common way a project like
this fools itself.

Use **season-forward validation**: train on all races through season `Y`, evaluate on season
`Y+1`, step forward, and report per-season results as well as the pooled number. Split on whole
seasons rather than a race count so no season is half in and half out — F2/F4/F6 are within-season
features and splitting mid-season puts near-identical rows on both sides.

The final held-out period must be touched exactly once, at the end. Regularization strength and
every other choice are selected on earlier folds.

### 6.2 Metrics

Per race, then averaged, with the per-season breakdown kept:

- **Multi-class Brier score** — `sum over drivers of (p_d - outcome_d)^2`, the same definition
  `02` §7 already uses, so A1 and A3 numbers are directly comparable and comparable to the market
  numbers already persisted for the Dutch GP.
- **Log-loss** of the winner's assigned probability. More sensitive than Brier to confident misses,
  which is the failure mode that matters for a predictor a trading layer might later consume.
- **Top-1 accuracy** — reported, but not optimized for and not used to choose between models. It
  is nearly uninformative at this sample size and it ignores calibration entirely, which is the
  thing the project actually cares about.
- **A calibration curve**: bucket predicted probabilities, plot against realized win frequency.
  This is what answers "is the model over-confident?", which `02` §9's top-2 concentration note
  raises and which a single Brier number cannot settle.

### 6.3 Baselines — both of them, on the same splits

1. **The A1 scorer**, run over the same backfilled rows via its own unchanged code path. This is
   the comparison the phase exists for.
2. **Grid-only**: a conditional logit with `s_grid` as its single feature, its one coefficient
   fitted on the same training splits. This is the floor. If A3 does not beat grid-only by a clear
   margin, the other six features are not earning their place and that is the finding to report.

   Fit the coefficient rather than pushing `s_grid` through a softmax at some temperature — a
   fixed temperature would smuggle `T` back in as an arbitrary constant in the one place it is
   least defensible, and a baseline handicapped by an unfitted scale is not a floor, it is a
   strawman.

Report the market as a fourth column **only** for races this pipeline actually snapshotted live —
currently one, the 2026 Dutch GP. Do not backfill historical market prices to fill that column;
the venues' historical coverage does not extend across this window and a partially-populated
market baseline invites exactly the comparison it cannot support.

### 6.4 What counts as success

Stated before the numbers exist, so it cannot be moved afterward:

A3 succeeds if it beats the A1 scorer on **pooled multi-class Brier over the held-out seasons**,
and does not lose on log-loss. Anything else — including a better top-1 accuracy with worse Brier
— is not success, and the honest report is that A1 stays production while A3's coefficients are
kept as evidence about which features matter.

One race, or one season, settles nothing (`02` §7). The per-season breakdown exists to show
whether any advantage is stable, not to let a good season be quoted on its own.

---

## 7. Fitting environment — decided 2026-08-24: hand-roll in pure Python

**Decision: hand-roll the fit in pure Python 3.9.** The interpreter upgrade stays open as its own
roadmap item and is *not* forced by this phase.

The discriminating argument is not "which is nicer to write" but that the second option smuggles in
an unresolved decision: the interpreter upgrade has its own undecided fork (`brew python@3.12` vs.
`uv`), and choosing scipy here would settle it silently and couple A3's timeline to an infra change.
Hand-rolling keeps the two decoupled. `welcome.md`'s framing of this project as the owner's learning
path into applied ML points the same way, and §7's original text already leaned there.

This is a decision about the **optimizer only**. Data loading, the negative log-likelihood, the
analytic gradient, the §6.1 splits, the §6.2 metrics and the §6.3 baselines are identical under
either path, so a later move to scipy changes roughly twenty lines and no results.

The project machine has **Python 3.9.6 with no numpy, scipy, pandas, scikit-learn, or statsmodels**
(verified 2026-08-23; consistent with `01` §2, which notes FastF1 needs ≥3.10 and that Tier 1 uses
`urllib` alone).

The two paths as originally stated, for the record:

- **Hand-roll the fit in pure Python 3.9.** The conditional-logit negative log-likelihood is convex
  with 7 parameters over ~5,300 rows; gradient descent or Newton–Raphson on it is a page of code
  and runs in seconds. Keeps the project's zero-dependency property, and — per `welcome.md`, this
  project is the owner's learning path into applied ML — writing the likelihood and its gradient by
  hand is genuinely the most instructive version of this phase.
- **Resolve the interpreter upgrade** already open in the roadmap (`brew install python@3.12` + a
  venv, or `uv`) and use scipy. Faster to a result and better-tested numerics.

Both are zero-budget compliant. The decision is forced eventually regardless: the roadmap's
gradient-boosted-tree step needs the upgrade, since neither XGBoost nor LightGBM runs on 3.9
without one. It is not forced *now*, which is why v1 is specified as a model that either path can
fit — and that property is what makes the decision above cheap to revisit.

---

## 8. What A3 closes from the existing backlog

| Open item | Where | How A3 answers it |
|---|---|---|
| Recalibrate `T` against outcomes | roadmap, `02` §10.2 | Dissolved — `T` is not a parameter (§3.1) |
| 0.42 pole-conversion anchor is a rounded guess | `02` §10.3 | Unnecessary — data sets the scale (§3.1) |
| Track overtaking multipliers are hand-set | roadmap, `02` §10.1 | Tested via the tier interaction, then replaced or dropped (§3.5) |
| `K_GRID`/`K_SPRINT`/`K_FIN` chosen for shape, not fitted | `02` §10.5 | **Not closed** — they live inside the sub-scores, not the coefficients. See §10 |
| F7 train/serve skew undecided | roadmap, `01` §5.6 | Decided: train-dormant (§3.3) |
| Model shape undecided | roadmap A3 | Decided: conditional logit (§3.1) |
| De-vig method beyond A1 | roadmap, `01` §9.2 | **Not closed** — it is a market-side question and A3 is market-blind. See §10 |

---

## 9. Required assertions

Fail loudly rather than emit a plausible wrong number (`02` §8's principle, and use
`lib/invariants.require`, not bare `assert` — `lib/invariants.py` explains why).

1. Exactly one label-1 row per `(season, round)` group.
2. Every feature value in `[0, 1]`; no NaN, no missing cells.
3. No F7 column present in the design matrix (§3.3).
4. `s_sprint == 0` for **every** driver in a race, or for **none** of them (§3.4).
5. Every result row feeding any feature has `date < race_date`, per race (§4.4).
6. F6's standings round equals `round - 1` on every backfilled race (§4.4).
7. No market field appears anywhere in the training matrix or the fitting path (§1, §4.5).
8. Train and test groups share no `(season, round)` key.
9. Fitted probabilities sum to 1.0 (±1e-6) within each race.
10. The harness reproduces `02` §9's reference sub-scores when pointed at the 2026 Dutch GP — the
    same fixed point `test_phase_a4.py` already uses, and the cheapest possible check that §4.2's
    shared-code-path rule has not been quietly violated.

    **Amended 2026-08-24, after running it.** As first written this assertion said "exactly", and
    it is not satisfiable that way — it contradicts §4.4 item 4. 2026 R12 is a **sprint weekend**,
    and a backfilled sprint weekend provably cannot see its own sprint points, so F6 must differ on
    the one race this assertion names as its fixed point. The correct form:

    - **Six features reproduce `02` §9 exactly**: `grid`, `team`, `sprint`, `driver_form`, `track`,
      `teammate`, plus `track_n` and the winner label. Verified to the three decimal places §9
      prints.
    - **F6 `champ` differs, and by exactly R12's sprint points.** Adding them back reproduces §9's
      column for all seven drivers to the same precision. This is §4.4 item 4's accepted train-only
      skew, now *measured* rather than bounded.
    - `p_a1` therefore differs slightly too (36.1% vs. §9's 36.2% for NOR), entirely as a
      consequence of F6.

    Encoded as `test_backfill.TestReproducesTheDutchGPReferenceTable`. Note that §3.4's
    zero-the-sprint-column rule does **not** apply to this race, precisely because it is a sprint
    weekend — the `sprint` column is compared directly.

---

## 10. Open items

1. **The regularization prior.** Shrink toward zero, or toward A1's implied `β` (§3.6)? The
   latter is more informative and more readable, but it is also a thumb on the scale in favour of
   the baseline A3 is being tested against. Decide with a validation-set comparison, not by
   argument.
2. ~~**Tier assignments for the 18 new circuits** (§5.2), needed only if the §3.5 interaction is
   built.~~ **Resolved 2026-08-26**: an explicit "default" bucket, not hand assignment — see §3.5's
   2026-08-26 entry. Every circuit missing from `OVERTAKING_MULTIPLIER` gets `tier_for() ==
   "default"`, the same bucket as the explicitly-1.00 circuits.
3. **`K_GRID`, `K_SPRINT`, `K_FIN` are still unfitted.** They live inside the sub-scores, which
   this phase treats as fixed inputs, so a linear-in-`β` model cannot tune them. Fitting them
   means either a grid search over the three constants wrapping the whole fit, or moving to a
   model that consumes raw positions instead of pos-scores. Neither belongs in v1.
4. **F7's inference-time gap is unresolved, only documented** (§3.3). A wet race will be predicted
   as dry by A3. Needs either an archive-reproducible weather feature or enough live-snapshotted
   wet races to fit a forecast-probability term.
5. **The sprint-points limitation in F6 is train-only skew** (§4.4). Bounded and accepted, but it
   is skew and should be revisited if F6's fitted coefficient turns out to matter more than its
   hand-set 0.08 suggested.
6. **De-vig method** (`01` §9.2) is still open and A3 does not close it. It was filed as "defer to
   A3 calibration data," but A3 is market-blind, so nothing here produces evidence about it. It
   needs live-snapshotted races with realized outcomes — the same scarce resource §6.3 constrains
   the market baseline to — and should be re-filed against that, not against this phase.
7. **Nothing here addresses whether the features are the right features.** A3 fits coefficients on
   `02`'s eight-feature design; it cannot discover a feature that was never built. Pit-stop
   strategy, tyre allocation, car upgrades, and reliability (which `04` §5.1 builds for DNF but
   the winner model does not use) are all absent. That is a Phase A5 question, not an A3 one, but
   it bounds how much a better fit can buy.
