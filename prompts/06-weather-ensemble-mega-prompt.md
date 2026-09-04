# Mega prompt — build `06` Weather Ensemble Signal

Paste everything below into a fresh session with the strongest available model, run from the repo root.

---

You are implementing an already-approved, already-decided spec in the f1-prediction-model repo:
docs/06-weather-ensemble-signal.md. Read that file in full before writing anything — this prompt
summarizes it but the doc is authoritative. Also read docs/welcome.md and docs/01-data-pipeline.md
§5 first, per the project's own onboarding order.

## Project rules you must follow

- No paid APIs/tiers, zero budget. This spec uses only Open-Meteo's free keyless tier — do not add
  auth or a new provider.
- Runtime invariants that guard data (not programmer errors) go through lib.invariants.require(),
  never a bare `assert` (see lib/invariants.py's docstring for the exact rule).
- Snapshots are append-only/immutable — never mutate an existing file under data/snapshots/.
- Commit messages: human-sounding, no AI attribution, no corporate boilerplate. Commit in small
  logical increments as you go (per-file/per-concern), not one giant commit at the end.
- Every number you report back to the owner must be one you actually ran, not recalled — this
  project's specs document a repeated failure mode of confident-but-wrong numbers from memory.

## What already exists (do not re-derive, verify then use)

- weather_backtest.py (repo root) already reproduces every number in §5 of the spec — a 44-race
  backtest of three candidate aggregates against observed rainfall. Run it first to confirm your
  environment reproduces the documented numbers before changing any pipeline code.
- test_f7_wet_branch.py already exists as a test scaffold — F7's wet branch has NEVER executed on
  a real race, so this test is the shipping gate for this whole change, not an afterthought.
- lib/openmeteo.py currently has two functions, forecast() and archive(), neither passes `models=`.
- snapshot.py:402 build_weather() currently computes a single p_max from the *unnamed provider
  blend* (no models= param) and returns it as weather["p_max"].
- snapshot.py:357 currently defines wet as `max_precip > 0.0` (inside build_track_history, used for
  F5's per-edition wet flag, which F7's live wet branch reads).
- score.py:189-213 compute_weather() reads algo_snapshot["weather"]["p_max"], gates F7 dormant if
  p_max < 40, else builds each driver's wet-weather rating from per_driver history where wet==True.

## Three decisions already locked by the owner — implement exactly these, do not re-litigate them

1. §6.1 (decided 2026-09-01): wet-race definition tightens from `> 0.0 mm` to `>= 0.5 mm`.
   This changes snapshot.py:357's `"wet": max_precip > 0.0` to `>= 0.5`.
2. §6.2 (decided): F7's gate input becomes `p_mean`, not the raw provider blend and not `p_max`.
   These two decisions move together — shipping one without the other reproduces exactly the
   failure the spec calls out in §6.2 ("different quantities wearing the same name"). Both changes
   land in the same commit/PR.
3. §6.3 (decided): agreement flag is `p_spread < 15pp` → "agree", else "disagree". Persist both the
   numeric p_spread and the agree/disagree flag in the snapshot.

## Exact aggregate definitions (§4.2 — order of collapse matters, this is not prose-approximate)

Let p[m][h] = model m's precipitation_probability at race-window hour h, over M = the 4 models
below. Race window = existing lights-out ± 2h inclusive (snapshot.py:410-411, reuse unchanged,
do not invent a second window definition).

```
p_mean   = max over h of ( mean over m of p[m][h] )   # collapse models first, then hours
p_max    = max over h of ( max  over m of p[m][h] )   # max over both axes
p_spread = median over h of ( max over m of p[m][h] − min over m of p[m][h] )   # MEDIAN hour,
           not max hour — the max-hour spread distribution is badly skewed (§4.2 explains why)

agree = p_spread < 15   (percentage points)
```

## The four models to request

Verified live 2026-08-24, all four confirmed global — no per-venue model list needed, works at
Zandvoort/Interlagos/Singapore:

```
models=ecmwf_ifs025,gfs_seamless,icon_seamless,gem_seamless
```

**Critical gotcha:** with `models=` set, Open-Meteo suffixes every hourly field with the model name
(e.g. `precipitation_probability_ecmwf_ifs025`), not the bare key your existing forecast() parsing
expects. This is the one place where "just add a query param" is not actually "just add a query
param" — you must rewrite the parsing, not just the request.

## Step-by-step build plan

1. lib/openmeteo.py: add a `models` parameter (or a new function `forecast_ensemble()`, your call,
   but keep the existing forecast() working unchanged for anything else that calls it) that passes
   `models=ecmwf_ifs025,gfs_seamless,icon_seamless,gem_seamless` and parses the suffixed keys per
   model into a clean per-model dict: `{model_name: {time: [...], precipitation_probability: [...],
   precipitation: [...], temperature_2m: [...], wind_speed_10m: [...], relative_humidity_2m: [...]}}`.

2. snapshot.py build_weather() (currently line 402): switch to the ensemble call. Compute p_mean,
   p_max, p_spread, agree per §4.2's exact formulas above, over the same race window it already
   computes (lines 408-424, unchanged). Persist ALL FOUR raw per-model series under a new
   `per_model` key inside the `weather` block (§4.4 — raw+normalized pattern required, matching
   01-data-pipeline.md §8.4's mandate for market odds: never persist only the aggregate). The
   snapshot's top-level shape does not change — `weather` gains `per_model` + the three aggregates,
   it does not become a new top-level snapshot key (§9 explicitly rules this out).

3. snapshot.py build_track_history() (currently line 357): change `"wet": max_precip > 0.0` to
   `"wet": max_precip >= 0.5`. This is the F5/F7 wet-history definition change (§6.1).

4. score.py compute_weather() (currently lines 189-213): change `p_max = algo_snapshot["weather"]
   ["p_max"]` to read `p_mean` instead, and gate dormant on `p_mean < 40` (still gate=40, only the
   input scalar changes — §6.2's table). Everything else in compute_weather (the per-driver wet
   rating build, shrink_by_n, field normalization) is UNTOUCHED — only the scalar entering the gate
   changes, per §7.1.

5. Wire p_spread + agree into the snapshot as a top-level-of-weather flag (§7.3's proposed minimum):
   any prediction made under `disagree` should be marked weather-uncertain in the snapshot's meta or
   provenance block, the same way 01-data-pipeline.md §5.6 already marks a wet-race A3 out-of-domain
   report. Keep this minimal — §7.3 explicitly says this is the minimum viable consumer, not a new
   feature; whether p_spread becomes a real feature is still an open item (§10 item 3), not yours to
   decide.

6. Run test_f7_wet_branch.py. This is the actual shipping gate — F7's wet branch has literally never
   executed on a real race before this change, so this test exercising it for the first time against
   the new ensemble path is what makes this safe to ship, not optional cleanup.

7. Re-run weather_backtest.py after your changes and confirm the numbers still match §5's tables
   (recall/precision under the ≥0.5mm rule: p_mean should land at 75%/60%, beating today's blended
   62%/45%; activation rate ~23% of races vs today's 27%). If your numbers don't match the spec's,
   STOP and figure out why before proceeding — do not silently ship a divergent implementation.

8. Add/update required assertions (add an invariant that p_mean/p_max/p_spread are each in [0,100]
   and that per_model has exactly 4 keys) via lib.invariants.require, not bare assert.

9. Run the full existing test suite (test_backfill.py, test_phase_a4.py, test_winprob.py, etc.) to
   confirm nothing else silently depended on the old wet threshold or the old p_max source. F7 stays
   train-dormant and out of A3's design matrix (05-trained-model.md §3.3) — confirm nothing you
   touched reopens that; it should not.

## What not to do (explicitly out of scope, per §9)

- Do not self-host GraphCast/Pangu-Weather/any ML weather model.
- Do not change F7's wet-branch scoring logic (the per-driver rating, shrinkage, field
  normalization) — only the scalar entering the >=40 gate changes.
- Do not touch the Open-Meteo ensemble-API (50-member probabilistic) path — §8 explicitly defers
  that; it can't be backtested yet (only ~93 days of history) so it doesn't get locked in now. If
  you want to start forward-collecting it per §8.3's suggestion, that's a separate, optional,
  additive step — ask before doing it, don't fold it into this change.
- Do not touch A3/trained-model code at all.
- Do not add wind/temperature/humidity features or a generalized cross-model-disagreement feature
  beyond precipitation — all four were spiked and came back null (§12 of the spec), don't re-open.

## Deliverable / how to report back

- A short summary of exactly which files changed and why, one paragraph each.
- The weather_backtest.py output before/after (should be unchanged — it's a standalone verification
  script that doesn't touch the pipeline) plus a fresh, real snapshot+score run if a valid
  races/*.json config exists, showing the new weather block's shape.
- Confirmation that test_f7_wet_branch.py and the full existing test suite pass.
- Explicitly flag anything from §10's "still open" list you ran into (rate-limit weighting is
  unverified — if you hit a 429 or see anything suggesting the 4-model call is weighted heavier
  than 1x against Open-Meteo's free 10,000/day quota, report it, don't just silently retry).
- Commit in small logical increments (openmeteo.py client change; snapshot.py wet-threshold change;
  snapshot.py ensemble aggregate change; score.py gate-input change; test/assertion additions) —
  not one giant commit.
