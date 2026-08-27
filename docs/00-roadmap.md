# Roadmap

Read `docs/welcome.md` first if you haven't. This doc tracks phases, status, and what's locked vs. still open. Keep it up to date as decisions get made — this is the single source of truth for "where are we."

## Ultimate goal (updated 2026-08-23)

The project's endpoint is no longer just "predict and compare against the market." It's an **automated trading bot** that trades YES/NO shares on Polymarket + Kalshi F1 markets — race winner, podium/placement, and (once live data supports it) in-race overtake markets — using this project's own prediction pipeline as its edge. The thesis: fast-moving, high-information moments (a driver closing on a braking zone, a grid or weather change) can outpace how quickly retail-priced prediction markets reprice, and Lane A/B's output is what would drive the trade decision.

Lanes A and B remain the prediction engine, unchanged in scope. **Lane C** (new, below) is the trading layer built on top of them — it does not get built until its own blocking questions are resolved and, per the zero-budget hard constraint in `welcome.md`, any paid dependency it needs is explicitly approved first. This is a goal/roadmap-level change, not a build authorization — no Lane C code until it has its own approved spec, same as any other phase.

## Structure: Lane A vs Lane B

| | Lane A — Batch predictions | Lane B — Live predictions |
|---|---|---|
| **What** | Race winner, podium, points, DNF probability | Overtake prediction, corner-level, real-time |
| **Data pattern** | Pull once before a session, compute, done | Continuous stream during a session, react in real time |
| **Complexity driver** | Feature quality, market comparison | Latency, broadcast delay-sync, event detection |
| **Status** | Active — current focus (Phase A3) | Source decided and specced (`03`); client build authorized, model layer gated on B1 |

Lane C (trading, see below) sits downstream of both: it can trade Lane A's settled-market predictions (winner, podium) without waiting on Lane B, but in-race overtake markets need Lane B's live feed to exist first. **Corrected 2026-08-26 (`07` §10):** the second clause assumed a market that does not exist. Measured across both venues, open and closed, there is **no overtake market on Polymarket or Kalshi** — so no feed makes it tradeable. What the same measurement did find is that Kalshi's *race winner* market trades heavily while the race runs (48.5% of its lifetime volume inside the two-hour window, a trade in all 120 race minutes), which is a live-feed trading case aimed at a different market and a different model. It's tracked as its own lane rather than a sub-phase of either.

## Lane A phases

**Phase A0 — Data pipeline**
Lock down sources for historical results, weather, and market odds. See `01-data-pipeline.md`.
Status: **locked and written** (2026-08-22). All sources verified live against production; every Phase A1 input (grid, sprint, standings, weather, both markets) confirmed available. Blocker noted: FastF1 needs Python >= 3.10, machine has 3.9.6 — A1/A2 deliberately do not depend on FastF1.

**Phase A1 — Rule-based winner predictor**
Hand-weighted scoring function using grid position, season/team form, track history, weather. No training — the owner picks the weights. Output compared against Polymarket + Kalshi odds for the same race.
Status: **implemented and verified** (2026-08-22 night). See `02-winner-prediction-algo.md`. Eight features (grid 0.35, team form 0.15, sprint 0.13, driver form 0.11, track history 0.08, championship 0.08, weather 0.05, teammate H2H 0.05), softmax T=0.1168, market-blind. Design was dry-run against real Dutch GP data before locking, which added the championship feature and small-sample shrinkage on track history. `snapshot.py` pulls grid/form/track history/weather/both markets into an immutable snapshot JSON; `score.py` reads a snapshot with no network calls and computes the scorer plus market comparison; a `lib/` package holds the source clients. Scorer output reproduces `02-winner-prediction-algo.md` §9's reference table exactly — every sub-score, effective weight, raw score, and probability matches to the stated precision. `test_f7_wet_branch.py` exercises F7's wet-weather path (never run before, tonight's forecast is dry) against the archive-verified 2023 Dutch GP. Committed and pushed to main.

**Phase A2 — First live test: Dutch GP, 2026-08-23**
Qualifying already happened (2026-08-22), so grid positions are known. Build the Phase A1 predictor tonight, lock in a prediction + market snapshot before lights-out, compare against the actual result after the race.
Status: **complete** (2026-08-23). Pre-race: algo predicted NOR 36.2%, RUS 35.2%, ANT 11.4% vs. market NOR 37.2%, RUS 25.0%, ANT 25.0% — see `02-winner-prediction-algo.md` §9 for the full reference table. `postrace.py` pulls the actual finishing order from Jolpica, takes the classified P1, and writes the same Brier-score comparison `score.py --winner CODE` produces (shared logic in `compute_post_race()`/`compute_comparison()`) into its own `<snapshot>-postrace.json`, kept separate from `score.py`'s pre-race `-score.json` so a post-race run can never overwrite it; `test_postrace.py` dry-runs it against the real, already-decided 2023 Dutch GP as a pre-flight check.

Actual result: **NOR won**. Algo's top pick was correct. Brier score: algo 0.5499 vs. polymarket 0.5345, kalshi 0.5492, market_mean 0.5416 — the algo called the winner right but was **worse calibrated than the market mean**, the first real data point for A3. Full numbers in `data/snapshots/2026-12-race-20260823T031058Z-postrace.json`, committed in `0019d5b`.

Both the pre-lights-out re-snapshot and the post-race score ran via scheduled Anthropic cloud routines (`dutch-gp-lights-out-resnapshot`, `dutch-gp-postrace-score`) rather than manual triggering — first real test of that automation path. The lights-out routine actually failed this race (its cloud environment's network access was Trusted-only and blocked `api.jolpi.ca`/market/weather hosts outright, so no fresh pre-lights-out snapshot was taken — the committed snapshot is from the earlier manual run); the postrace routine hit the same block on its first scheduled fire and succeeded only after being repointed at a new `smarty-f1` cloud environment with Full network access. Whether scheduled routines become the standing mechanism for future races (vs. staying manually triggered) is open — see Open decisions.

**Phase A3 — Trained model (current focus)**
Train a conditional logit as a first real model and compare its calibration against the Phase A1 rule-based baseline. Move to gradient-boosted trees (XGBoost/LightGBM) once the data pipeline is trusted.
Status: **§6.4 evaluation run, twice by mistake, both agree: A3 does not succeed. A1 stays production** (2026-08-26). See `05-trained-model.md` and the erratum at the bottom of this entry.

`backfill.py` is built (`b9c21bb`) and the full `--seasons 2014-2026` run is **complete**: **264 of 264 races, 5,350 driver-race rows**, committed at `data/training/winner.csv` (`b881256`). Every season contiguous with no gaps, exactly one label-1 row per race, all seven features inside `[0,1]`, no blank cells, every race's `p_a1` summing to 1.0, and **29 sprint weekends** — matching `05` §4.3's count exactly.

Two figures to reconcile against `05` §4.3: rows are **5,350** against an estimate of ~5,300, and distinct circuits are **32, not 33** — `madring` is on the 2026 calendar but does not race until 2026-09-13, so it has no result to label. It will be F5-cold on debut, meaning `track_n = 0` field-wide and F5 field-constant for that race, which by §3.2 cancels out of the likelihood.

Wall-clock was **~6h50m** for the primary run (15:59–22:49), paced by `httpcache`'s 450 fetches/hour sustained cap rather than by compute — most of it parked in rate-limit pauses. Re-runs are near-free exactly as `05` §5.3 predicted: the refill pass re-attempted the 11 lost races off a warm cache and finished in **under a minute**.

**`fit.py` and `test_fit.py` are written and passing (2026-08-24), and the Phase A3 result is *not* in yet.** The fitter implements `05` §6/§7 in full: the conditional-logit NLL, its analytic gradient and Hessian, Newton–Raphson with a backtracking line search on the penalized objective, the season-forward splits, all four §6.2 metrics, and both §6.3 baselines. Hand-rolled in pure Python against the system 3.9.6 per §7 — no numpy, no scipy, and the `.venv312/` that now exists for FastF1 is deliberately not used here. 66 tests pass in 4s, including the gradient and Hessian against central finite differences (the check scipy would otherwise have done for us, and the one nothing else would catch — a wrong gradient still converges, just to the wrong place) and the optimizer against the closed-form MLE `log(k/(n−k))` of a one-feature logit over two-driver races.

**What is deliberately *not* done: the §6.4 evaluation.** `fit.py` has two modes. `--mode dev` runs season-forward folds over the pre-holdout seasons only and structurally cannot read a holdout season; `--mode final` is the one run that touches `HOLDOUT_SEASONS = (2024, 2025, 2026)`, and it refuses to start unless the corpus is complete — 264 races with contiguous rounds. Making the held-out period a fixed season set rather than "the last N seasons" is what keeps §6.1's *touched exactly once* an enforceable property while rows are still arriving: a count-based rule would name a different experiment on every run. The guard did its job: it refused for as long as the corpus was holed, and **as of 2026-08-24 22:50 it passes** — 264 races, no round gaps. `--mode final` is therefore **unblocked and has not been run.** Being a once-only measurement, it is not something to fire off as a status check; it wants a deliberate decision that nothing else is going to change first. Note that the guard checking round contiguity rather than trusting `ps` is what made this safe: the primary run exited at 22:49 with 11 races still missing, so a finished process was genuinely not a finished corpus.

**Dev-fold numbers, re-run 2026-08-26 on the complete 264-race corpus — still not the A3 result and must not be quoted as one, since the holdout stays untouched by `--mode dev`.** This supersedes the 2026-08-24 run, which was computed on 205 of 264 races before the track-history refill (the missing 11 races were concentrated in exactly the 2021–2024 window the era-split argument below turns on). Pooled over 7 folds (2017–2023, 145 races): A3 Brier 0.5852 / log-loss 1.2900, A1 0.5890 / 1.2933, grid-only floor 0.6754 / 1.5337 — same ordering as before, closer together. Two things worth carrying forward:
- Validation again selected the **A1-implied prior at the top of the λ grid** (β collapsed onto A1, max |Δ| < 0.001), so the A3-vs-A1 gap is still not a fitting effect. **Era split holds up on the full corpus:** pooled Brier over 2017–2020 is A3 0.6448 vs A1 0.6573 (gap −0.0125 in A3's favor, 79 races); over 2021–2023 it's A3 0.5138 vs A1 0.5072 (gap +0.0066, **reversed**, 66 races — slightly wider than the 2026-08-24 run's −0.0044, still the same sign flip). D3 (sprint renormalization) is inert pre-2021 by construction, so D4 (dropping per-circuit `m`) remains the only structural candidate, and its sign is still not stable across eras. Zero-prior's own best (λ=0.01, Brier 0.58684) again edges A1.
- The unregularized fit now puts **β_sprint at 1.25** (A1 1.11) — much closer to A1 than the 2026-08-24 run's 2.48, i.e. the wide interval `05` §3.4 predicted has narrowed with 59 more races feeding that fold's training set. **β_champ still runs above its hand-set value** (1.16 vs A1's 0.68), unchanged in direction.

**Both things gating `--mode final` are now resolved (2026-08-26).** The round-contiguity guard doesn't catch a holdout season still in progress — 2026 has 12 of its rounds in the corpus, with Monza and `madring` still to come — so running `--mode final` on the default `HOLDOUT_SEASONS` today would measure a truncated 2026 and burn §6.1's "touched exactly once" on it. Resolved: invoke it as `--holdout 2024,2025`, which quarantines 2026 out of the run entirely (see Locked decisions). And `05` §3.5's fitted tier interaction — the one open design-matrix question that had to land *before* the final run, since adding it after would force a second one — is decided: it did not clearly vindicate `02` §5.1's ordering on the dev folds (see the entry below), so it does not go in, and the design matrix stays the same 7 features it's been.

**`--mode final --holdout 2024,2025 --dev-exclude 2026` (the clean-tuning run) was run 2026-08-26. Result: A3 does not succeed — A1 stays production.** Pooled over the 48 held-out races (2024–2025): A3 Brier 0.6349 vs A1 0.6179 vs grid-only 0.6054 — A3 loses to A1 on both Brier and log-loss, and neither model clears the grid-only floor by much. §6.3 pre-registered exactly this outcome's reading: "if A3 does not beat grid-only by a clear margin, the other six features are not earning their place." Per-season, 2024 (A3 0.7176 vs A1 0.6981) and 2025 (A3 0.5523 vs A1 0.5376) both go the same way, so this isn't one bad season carrying the pooled number. At the selected shrinkage the fitted β sits within 0.0006 of A1's hand-set values on every feature — the finding is about the feature set, not about whose weighting of it is better.

**Erratum, disclosed rather than smoothed over: the holdout got touched twice, and the first touch used a contaminated hyperparameter selection.** `--holdout 2024,2025` excludes those two seasons from `--mode final`'s scoring, but the code's `dev_seasons` was simply "everything not in `--holdout`" — so 2026's 12 completed rounds silently became a dev-tuning fold, something every earlier `--mode dev` run had correctly excluded. That flipped hyperparameter selection from the `a1/λ=30` every clean run chose to `zero/λ=0.01`, letting β drift far from A1 (β_sprint collapsed to 0.03) and producing a pooled Brier of 0.6470 — `data/training/a3_final_result_v1_contaminated_tuning.json`. Fixed immediately with a new `--dev-exclude` flag (seasons dropped from dev tuning without being scored, distinct from `--holdout`), and re-run to get the clean selection quoted above — `data/training/a3_final_result_v2_clean_tuning.json`. **That second run re-scored the same 48 held-out races**, which is exactly the §6.1 violation this whole design exists to prevent, and it should not have happened; the fix belonged in a dev-only diagnostic, not a second `--mode final` invocation. It is recorded here rather than deleted because both runs point the same direction — A3 loses on both (0.6470 and 0.6349 vs A1's 0.6179) — so the finding is not an artifact of which run gets quoted, but the process was not "touched exactly once" as documented, and no more `--mode final` runs should ever be made against 2024/2025 now that it's spent twice over. `fit.py`'s `--dev-exclude` flag stays, to stop the *original* contamination from recurring on a future season, even though this run is the one that found the bug.

**What this means for production:** A1 (the hand-set rule-based scorer) stays the model behind `score.py`/`postrace.py`/live predictions. A3's fitted coefficients are kept as evidence about which features the data thinks matter (§6.4's own instruction for this outcome) — worth reading in `05-trained-model.md`'s coefficient tables, not worth deploying. Phase A3 is closed.

The separation check (§3.6) ran and is clean: no feature is the winner's strict argmax in every race (`grid` in 102 of 194), so no coefficient is being driven to infinity.

**A third bug, found mid-run by diffing the CSV's round numbers (2026-08-24).** `build_track_history` selected the 3 most recent *seasons* but weighted them off a 3-slot list, which is only safe if a season holds one race per circuit — COVID's 2020 calendar put two at each of `bahrain`, `silverstone` and `red_bull_ring`, and 2021 two more at `red_bull_ring`. Affected drivers got 4–5 rows for 3 slots and the race died on a bare `IndexError`. Because that surfaces as a *skip* rather than a crash, the only symptom was races quietly missing: 2021 R1/R8/R9/R10 and 2022 R1 were already gone, with six more due in 2022–2024 — **~11 races, ~4% of the corpus, and not randomly distributed** (it removes three specific circuits across 2021–2024). Fixed by ranking editions by date and capping at 3, which is identical to the old behaviour on every single-race season; `02` §F5 records the clarified semantics and §9's reference run is verified unchanged. **Refilled 2026-08-24 22:50.** The second `backfill.py` pass recovered all 11 off a warm cache in under a minute, reporting "no races skipped" — `already_done()` meant it attempted only the missing races. Every winner checks out against the record (2022 R10 → SAI, 2024 R11 → RUS, and so on), and the count of skipped races over the whole run was **exactly the 11 predicted from the calendar arithmetic, race for race**, with 2025 and 2026 coming through clean as forecast.

**§9's assertion 10 was run for the first time on 2026-08-24 and it did its job.** Six of the seven features reproduce `02` §9's reference table exactly, which is the real content of the check — `05` §4.2's shared-code-path rule holds, so the matrix is being built by the same scorer that runs at inference. The seventh (F6) differs by *exactly* the 2026 R12 sprint points, which is `05` §4.4 item 4's accepted train-only skew, now measured rather than bounded. Two real bugs surfaced in the same pass and are fixed (`b0f6683`, `f9c8110`): an empty `results.json` cached nine hours before lights-out made every local run believe the Dutch GP was never run, and `find_full_result`'s `SystemExit` escaped `main()`'s `except Exception`, so one resultless race would have aborted the whole run — on its *last* race, and on the one race the pipeline predicted live. It is **not blocked on waiting for races** (revised 2026-08-23). This previously read "depends on accumulating real historical prediction data from A1/A2 runs," which would have meant ~5 rows by the end of 2026. That was wrong: every A1 feature except market odds is reconstructible for any past race from Jolpica, and `test_phase_a4.py` already rebuilds a full 2023 Dutch GP snapshot from `snapshot.py`'s own functions. Verified live: **264 races with a result across 2014–2026**, 29 of them sprint weekends, run at 33 distinct circuits — ~5,300 driver-race rows. (The sprint count was first recorded as 26 off a `limit=100` query that silently truncated at the page cap — Jolpica pages by result row, not by race, the same trap `ee45ecc` fixed inside `season_results`. Corrected 2026-08-24; see `05`'s verification note.) You don't need market odds to *train* — only to *evaluate against the market*, which stays limited to races this pipeline actually snapshotted live.

The spec's central claim, which reshapes the phase: **`02`'s scorer is already a conditional logit with hand-set coefficients.** Dividing the weighted sum by `T` inside the softmax distributes, so `β_f = w_f_eff / T` — A1 is this model with the coefficients filled in by hand rather than fitted. Consequences: `T` is not a separate parameter and needs no recalibration step (it is the scale of `β`); the 0.42 pole-conversion anchor becomes unnecessary; and the weights-sum-to-1.0 constraint, which existed only to give `T` its meaning, is dropped. A3 fits what A1 guessed.

The immediate next build step is `backfill.py` (`05` §5), which has one **hard prerequisite**: `CIRCUIT_TIMEZONE` (`snapshot.py:43`) covers 15 of the corpus's 33 circuits and is indexed as a bare dict lookup at `snapshot.py:261`/`:321`, so 18 circuits are an immediate `KeyError` rather than a degraded feature. Fill them in first.

What genuinely gates it:
- ~~Two future-data leaks in the backfill path~~ **fixed 2026-08-23**: `build_track_history` took `race_date` and never used it (a backfill picked up later editions *and the target race itself* — the label, inside F5), and F6's standings came from the "latest" endpoint, which on a finished season is the final table. Both now have leakage guards; see `01-data-pipeline.md` §4.6.
- ~~**Jolpica request volume, not compute.**~~ **fixed 2026-08-23**: `build_form` looped `race_results` once per prior round — up to ~21 calls per season, not reused across the different target races in a backfill until each round happened to get touched individually. §4.3's own advice — one filtered query over N per-entity queries — wasn't followed here. Replaced with `jolpica.season_results`, one paginated bulk pull per season (100-row page cap, verified live — a 22-round season is 5 pages), cached per `(season, offset)` and reused by every race in that season for the life of the cache: ~4x fewer calls per season, and the repeat-race cost drops to zero instead of being merely smaller. Two real bugs found and fixed in the same pass, both verified live: pagination is by result row not by race, so a round can straddle a page boundary and arrive split across two pages (2016 round 5: 12+10) — fixed by merging rows per round instead of overwriting; and a season's row `total` lives inside a cached page, so a warm cache from an earlier week would silently keep answering with a stale, truncated total for a season still in progress — fixed by force-refreshing page 0 and re-validating every other page's row count against the fresh total, refetching on mismatch. A position-contiguity check per round now catches either failure mode loudly if it recurs. 33/33 tests pass, including the real-network `test_phase_a4.py` backfill path.
- ~~**F7 cannot be backfilled as specced**~~ **decided 2026-08-23: train-dormant** (`01` §5.6, `05` §3.3). The archive endpoint has no precipitation *probability*, only observed mm, so historical rows have no `p_max`. Dormant beats a wet proxy because F7's wet branch has never executed on a real race, so a proxy would model something never validated at inference. Sharper than it first looks: a dormant feature is constant across the field, and a within-race constant cancels exactly out of a conditional logit's likelihood — so `β_weather` is *unidentified*, and F7 is dropped from the design matrix entirely (7 features, not 8). Standing consequence: **A3 predicts a wet race as though it were dry**; on a wet weekend both predictors run and A3's number is reported out-of-domain.
- ~~**Model shape.**~~ **decided** — conditional logit over driver-races (~5,300 rows at 264 races), not a per-driver binary logistic regression on ~264 winner events. Moved to Locked decisions.
- ~~**Fitting environment, not yet decided**~~ **decided 2026-08-24: hand-roll in pure Python 3.9** (`05` §7). The deciding argument was not ergonomics but that the scipy path silently settles the interpreter-upgrade item still open below — which has its own undecided fork (`brew python@3.12` vs. `uv`) — and couples A3's timeline to it. It is a decision about the *optimizer only*: the likelihood, gradient, splits, metrics and baselines are identical either way, so moving to scipy later changes ~20 lines and no results. Moved to Locked decisions. The upgrade is still forced eventually — GBTs don't run on 3.9 — just not by this phase.

**Phase A4 — Expand outcome types**
Podium, points finishers, DNF probability, fastest lap — same batch pattern, new labels.
Status: **implemented and validated against real data** (2026-08-23). See
`04-outcome-expansion-algo.md`. Market-verified first: podium and fastest lap exist on both
Polymarket and Kalshi; points exists on Kalshi only (no per-driver points market on Polymarket);
DNF has **no market on either venue** — Kalshi's `KXF1RETIRE` is a career-retirement-announcement
market, not a per-race DNF market, and is not used as one. Podium/points reuse Phase A1's locked
win-strength scores unchanged via a Plackett-Luce Monte Carlo simulation (200,000 draws, seed
20260823, ~1.2s) rather than a new hand-tuned mapping — zero new feature weights, self-consistency
checked against `02`'s closed-form win probability every run. DNF gets a new reliability-rate
feature (driver + team, 50/50, shrunk toward the field's own season DNF rate — 2026's status data
can't separate crash-caused from mechanical DNFs, so the split isn't measurable this season, stated
as a limitation rather than guessed at more finely). Fastest lap reuses 3 of the 8 win features
(team form, driver form, sprint) with the win market's `T` borrowed rather than independently
calibrated. `snapshot.py` now pulls all three new market types, soft-failing per venue (a market
not open yet doesn't block a snapshot that would otherwise succeed); `score.py`/`postrace.py` gained
the new scoring functions. Validated against two real, fully-resolved data points: the archived 2023
Dutch GP (fresh snapshot, exercises fastest lap end to end — real podium VER/ALO/GAS, verified live)
and the just-completed 2026 Dutch GP (frozen snapshot, real result) — 23 tests passing, see
`test_phase_a4.py`.

**Found and fixed along the way, not caused by this phase:** `is_classified()` didn't recognize
2026's `"Lapped"` status literal (only the older `"+1 Lap"` form), silently scoring lapped-but-
classified 2026 finishers as DNFs in F4/F8. Verified negligible impact on the locked A2 winner
numbers (bit-identical top-7 raw scores; `postrace.json`'s committed Brier 0.5499 vs. a fresh run's
0.5504 — see `04` §10.5 for the full erratum). Also caught a real market-data gap: Polymarket's
podium/fastest-lap markets exist ~2 weeks before a race but are too illiquid to price meaningfully
(Monza's podium market, checked live, priced almost every driver near 0.5 on ~$0-300 volume vs. the
winner market's $1,400-$14,000) — no fix built, the fix is timing (snapshot close to lights-out).

**What's still open, not yet real:** no genuine pre-race algo-vs-market comparison exists yet for
podium/points/fastest lap — the Dutch GP predates the market-pulling code, and the next race
(Monza, 2026-09-06) is currently too far out for its markets to be liquid. The first real market
comparison for these three outcome types happens whenever this pipeline snapshots a race close
enough to its lights-out to have priced markets — not guaranteed to be Monza specifically.

## Lane B phases

**Phase B0 — Live data source + tick client**
Tick-based state per car (position, speed, gap, brake/throttle, and whatever channel 45 turns out to carry — see below), then trigger conditions (approaching a known corner/braking zone) and scoring logic for overtake probability. See `03-live-telemetry-overtakes.md`, which is now a build spec rather than a research memo.
Status: **source decided and specced 2026-08-26. Client build authorized; the prediction layer on top of it is not.**

The data-source question that blocked this phase is closed: Lane B connects **directly to F1's own live timing feed** over the unauthenticated SignalR Core endpoint at `livetiming.formula1.com/signalrcore` — `03` §2.4's fourth row, chosen with the ToS and IP-blocking findings in `03` §2.3 in full view and knowingly accepted (`03` §5). Not FastF1's live module, which can't parse live at any budget; not OpenF1, whose free tier has no live access at all and whose paid tier costs money that hasn't been approved.

`03` §4.2 draws the scope tightly and the tightness is part of the decision: personal research and development only — capture, parse, shadow-mode predictions logged locally and read by nobody else. No hosted or public deployment (the one documented F1 enforcement action was against a *hosted* instance), no redistribution, and a hard interlock against Lane C consuming any of it (`03` §4.3). `03` §9 specs graceful backoff and a hard stop on any 401/403/429, explicitly not aggressive retry; `03` §10 specs fail-loud on schema drift via `lib/invariants.require`, the same convention `fit.py`/`backfill.py` use; `03` §11 keeps every capture local and gitignored.

**Gate:** the client and B1's delay measurement may be built now. The overtake model on top of them may not, until B1 comes back with a workable gap (`03` §4.4). A multi-minute broadcast delay kills the premise no matter how good the feed is.

Two things `03` turned up while being written into a spec, neither of them in the original memo:
- **The feed moved endpoints, and is still unauthenticated.** F1 introduced `/signalrcore` in May 2025 and retired the legacy `/signalr` around June 2026 (it returns 401 now). The new one is a different wire protocol — SignalR Core, `\x1e`-framed — but needs **no account, no F1TV subscription, no token**: measured token-less vs. garbage-token during Zandvoort FP1 with byte-identical results, and corroborated against `slowlydev/f1-dash`, a 1,907-star public dashboard with no login whose client contains no auth code at all (`03` §6.4). Zero-budget survives intact and the risk acceptance in `03` §5 is unaffected — it was always a decision to connect anonymously. **This was initially specced against the dead legacy endpoint and corrected the same day**; see `03`'s correction banner for what the bad inference was.
- **DRS doesn't exist in 2026, and nothing replaced it in the feed.** The FIA replaced DRS with active aero. Channel 45 used to carry DRS state; **measured against the full 2026 Dutch GP from the archive, it is constant zero — 944,196 samples, 22 drivers, no other value** (`03` §7.3). So there is no DRS analogue available to an overtake model this season, not merely an unknown encoding. B0 carries the field opaque as a drift tripwire. The old description line on this phase said "brake/throttle/DRS" and was wrong.

**Gate 4 (tradeable in-race markets) — run 2026-08-26, result split.** See `07` §10. No overtake market exists on Polymarket or Kalshi (387 F1 events incl. 333 closed swept on Polymarket; all 13,545 Kalshi series enumerated), so Lane B's corner-level trading rationale has no market. But Kalshi's F1 books stay open through the race and its winner market traded 826,229 of its 1,703,263 lifetime contracts inside the 2h race window. Gate 4 therefore kills the *overtake* trading case and strengthens a *winner* trading case. It does not decide Lane B's fate, and the trading-vs-learning fork behind Lane B remains unpicked — `07` §10.5/§10.6.

**Phase B1 — Delay/sync investigation**
Determine the real gap between live data feed timing and broadcast timing for the owner's actual watching setup (Apple TV app, either on Mac directly or the physical Apple TV box). Approach: auto-record the broadcast + auto-log the data feed in parallel, compare after the fact — not manual real-time comparison.
Status: **unblocked as of 2026-08-26 — B0's source is decided, and B1 is now the gate on everything above it.** `03` §4.4 makes the overtake model conditional on this measurement rather than merely informed by it, and `03` §13's first-connection acceptance run is designed so the same capture serves both. `03` §3 found that one existing hobbyist project solves broadcast sync manually — the viewer sets a delay buffer (up to three minutes) from their own experience, not from measurement — which isn't rigorous enough to reuse but is a useful sanity check that real delay can run into the low minutes on some setups. The check itself, unchanged now that the source is settled: a single manual side-by-side observation (start a live source and the Apple TV broadcast together, compare one clearly-timestamped event like lights-out) to see whether the gap is roughly seconds or roughly minutes. A multi-minute gap would close B0 outright, independent of which data source gets chosen, since Lane B's whole premise needs the gap to be workable for a real-time trade. Not run yet.

**Phase B2 — Overtake model (new 2026-08-26)**
Specced in `08-overtake-model.md`. The owner decided to build it and gave the rationale that closes Lane B's trading-vs-learning fork: the overtake model is an **intermediate signal feeding a live win-probability model**, which trades the race-winner market `07` §10.3 measured as liquid throughout a race (48.5% of lifetime volume in-race, a trade in all 120 race minutes). `03` §4.4's gate is amended accordingly — the **offline** model is authorized; running it live and trading on it stay gated on B1 and on `03` §4.3's interlock.
Status: **specced, not approved, not built.** Three things were measured before the spec was written: ≈38 on-track overtakes per race (115 across three 2026 races, so ≈450 labels a season); **one lead change across those three races**, which is why the model trains on all overtakes and lets the win-probability layer decide what matters; and a `Position`-stream label resolution of ~3.3s, which is why v1 is specced at a 10-second horizon and the owner's 5-second target is an open item rather than an assumption.

**Phase B2b — Automated trigger recognition**
Computer vision on screen-captured broadcast frames, targeting broadcast graphic overlays (pit boards, safety car flags, lights-out gantry) rather than raw scene content — a more tractable detection target. Requires reference footage of Apple's actual broadcast graphics first (their first season broadcasting F1 in the US, so no existing reference material).
Status: blocked on B1. (Renumbered from B2 on 2026-08-26 when the overtake model took that slot.)

**Phase B3 — Second test window: Italian GP (Monza), 2026-09-04 to 09-06**
Target date for a live-ish test of the Lane B pipeline, once B1/B2 groundwork exists.
Status: not started.

## Lane C phases

**The build spec for this lane now lives in `docs/quant/`** (`quant/00-directional-trading-spec.md`,
started 2026-08-26). `07` stays the feasibility record; `docs/quant/` is the plan that follows
from it. The phase names below (C0–C3) map onto the quant spec's Q1–Q5; the quant doc is
authoritative for scope and sequencing, this section is the pointer. Owner directed the
reframe 2026-08-26: the lane is now understood as **directional trading on per-race markets
first** (winner + podium + points/top-N, both venues), with market making as a documented later
phase (`quant/00` §Q5) rather than the near-term goal.

**Phase C0 — Goal statement**
Auto-trade YES/NO shares on Polymarket + Kalshi F1 markets (race winner, podium/placement, and eventually in-race overtake markets) off this project's own prediction pipeline.
Status: goal adopted (2026-08-23). Feasibility researched 2026-08-26 — see `07-lane-c-trading-feasibility.md`. Headline finding: the blocker is the **edge, not the APIs**. A1 lost to the market mean on the one live race, A3 is a closed negative result, and podium/points/fastest-lap have zero pre-race market comparisons — so there is no measured edge in any market, including the settled ones C1 would scope down to. Buildable now at zero budget: an edge-measurement + paper-trading harness (`quant/00` §Q1, was `07` §7), which produces exactly that missing evidence. No live-execution design until the edge question, the jurisdiction fork, and risk controls all resolve. **2026-08-26:** §11 of `07` added a live book-depth survey — Kalshi's per-race winner book is ~7× deeper at the touch than Polymarket's 10 days out, so the quant spec recommends Kalshi (demo host first) as the execution venue despite the owner's stated Polymarket preference; Polymarket winner markets are `negRisk` (linked legs) and carry live liquidity-rewards params.

**Phase C1 — Real-time data feed (blocking)**
The in-race trading case (driver closing on a braking zone, trade before the overtake resolves) needs sub-second telemetry.

**Rewritten 2026-08-26.** This entry used to say the only live source considered was FastF1's live module, "with a ~2h connection cap." Both halves are superseded. B0 now has a real live source (the direct SignalR connection, `03` §6), and the ~2h cap turns out to be a *server-initiated disconnect that FastF1's client simply doesn't reconnect from* — its source carries a literal `# TODO: enable auto reconnect?` — not a property of the feed that a client has to live with (`03` §9.1). A client that reconnects doesn't have a 2h ceiling.

What that does **not** mean is that C1 is unblocked. Two things still gate live in-race trading, and neither is a data-plumbing problem:
- **`03` §4.3's interlock.** Lane B's spec explicitly does not authorize any Lane C component consuming its output — that's the line where "personal, non-commercial use" stops being an available reading of what this project is doing, and it needs its own decision with a date on it. See the open decisions below.
- **B1's delay measurement**, still unrun. If the broadcast/feed gap is minutes, in-race trading is dead regardless.
- **The market itself, checked 2026-08-26 (`07` §10).** C1's premise sentence above — "trade before the overtake resolves" — has no market behind it on either venue. The in-race market that *is* liquid is race winner. If C1 is ever unblocked, it is unblocked toward a live win-probability model, not toward corner-level overtakes. Owner's call, recorded in `07` §10.6.

Status: not started. Still the first fork in Lane C. The remaining option if the above stays blocked: scope Lane C's first cut to markets that don't need corner-level timing (winner, podium — settled after the fact, no live feed required).

**Phase C2 — Order execution**
Both venues' *read* endpoints are public and credential-free (Lane A locked decision). Placing orders is a different API surface entirely and needs real authentication: Polymarket order flow goes through CLOB, which Lane A's locked decisions explicitly scoped out ("Gamma only, not CLOB/Data") — that decision is now reopened for Lane C, see Locked decisions below. Needs account/API key setup on both venues, order placement + fill confirmation, and a mapping from the algo's probability output to trade size and price.
Status: not started.

**Phase C3 — Risk controls**
Position sizing per market, a max loss per race/session, and a kill switch, decided and built before any order-placement code runs against real money — an auto-trading bot with no cap on it is the actual failure mode here, independent of prediction quality.
Status: not started.

## Locked decisions

- Historical results: **FastF1 + Jolpica**, used redundantly (cross-validation/backup, not additional unique data volume — they mostly cover the same races)
- Weather: **Open-Meteo** (free, confirmed)
- Market odds: **Polymarket + Kalshi**, both confirmed to have active Dutch GP winner markets. **No API credentials needed** — both venues' price-read endpoints are fully public (verified live 2026-08-22). See `01-data-pipeline.md` §6.3, §7.3.
- Phase A4 market coverage (verified live 2026-08-23, see `04-outcome-expansion-algo.md` §2):
  podium and fastest lap on **both** venues; points on **Kalshi only** (no per-driver Polymarket
  market); DNF on **neither** venue (Kalshi's `KXF1RETIRE` is career-retirement speculation, not a
  per-race DNF market — do not use it as one)
- Market data access: Polymarket **Gamma** API only (not CLOB/Data); Kalshi `GET /markets` on `external-api.kalshi.com`. This was a *read-only* scoping decision for Lane A/B. Lane C's order execution needs Polymarket's CLOB (or equivalent) for placing trades — reopened, not yet decided, see Phase C2
- Canonical driver key across all sources: **FIA three-letter code** (`ANT`, `NOR`), sourced from Jolpica `Driver.code`
- Phase A3 model shape: **conditional logit over driver-races**, race as the choice set (`05` §3.1). Follows from `02` being one already with hand-set coefficients — not a free choice
- Phase A3 F7: **train-dormant**, F7 excluded from the design matrix (`01` §5.6, `05` §3.3)
- Phase A3 label window: **2014 onward** (hybrid-era regulation boundary). Features look back freely across it — `driver_track_history` has no season filter, only a date guard — so 2014 rows are not F5-cold (`05` §4.3)
- Phase A3 training rows are built by calling `snapshot.build_*` and `score.compute_*` **unchanged**, never a reimplementation (`05` §4.2). Train/serve skew here would be invisible: a re-typed `pos_score` with a different `K` produces plausible numbers that simply aren't the feature the scorer computes
- Phase A3 validation is **season-forward, never random k-fold** (`05` §6.1) — random folds leak the future through every season-long feature
- Phase A3 fitting environment: **hand-rolled in pure Python 3.9**, no scipy (`05` §7, decided 2026-08-24). Keeps the still-open interpreter upgrade decoupled from this phase; affects the optimizer only
- Phase A3 training matrix (`data/training/winner.csv`) is **committed to git**, per `05` §5.1's own reasoning that CSV was chosen to keep it diffable. `data/cache/` stays ignored — that's reconstructible HTTP responses, not a deliverable
- Phase A3 `--mode final` invocation: **`--holdout 2024,2025 --dev-exclude 2026`**. 2026 is still in progress (12 rounds in as of 2026-08-26); `--holdout` alone keeps it out of scoring but *not* out of dev tuning (see the Open decisions entry for the incident this caused), so `--dev-exclude` is required too. Moot now that the run has actually happened — recorded for the record and in case a future season ever needs the same treatment (decided 2026-08-26)
- Phase A3 track multiplier `m`: **stays dropped for v1.** The fitted tier interaction (`05` §3.5) was tested on dev folds, per-fold rather than on one fold: `grid_x_hard` is negative in all 7 folds (a stable inversion of `02` §5.1's predicted ordering), `grid_x_easy` flips sign three times (not identified from this corpus either direction). Not a permanent close the way F7 or `T` are — revisit the easy-tier side if more seasons accumulate at its five circuits (decided 2026-08-26)
- Odds normalization for A1: proportional de-vig; raw + normalized both persisted
- ~~Live data (when needed): **FastF1's free live module**, not OpenF1's paid live tier (€9.90/month) — avoided per the zero-budget constraint.~~ **Superseded 2026-08-26 — this was never a real choice.** FastF1's live module doesn't provide live data at any budget; it can't parse in real time, full stop. It was never the zero-budget alternative to OpenF1's paid tier, because it doesn't do the thing the paid tier does. See `03-live-telemetry-overtakes.md` §2.4 for what the real options were. **Replaced 2026-08-26 by the entry immediately below.**
- **Lane B live data source (decided 2026-08-26, specced in `03`): a direct connection to F1's own live timing feed**, legacy unauthenticated SignalR at `livetiming.formula1.com/signalr`, `clientProtocol=1.5`, hub `Streaming`. Zero-budget and genuinely live; the other three candidates in `03` §2.4 are each disqualified on one of those two. The decision was taken **with the ToS and enforcement risk in `03` §2.3 identified and knowingly accepted** — F1's legal notices name "live timing data" as protected and restrict it to personal, non-commercial use, and a hobbyist project's hosted deployment has already been IP-blocked. `03` §5 records the acceptance; don't re-litigate it, hold the conditions it came with. Those conditions are part of the decision, not commentary on it: personal research/development scope only (`03` §4.2), no hosted or networked deployment, no redistribution, all captures local and gitignored (`03` §11), graceful backoff and a hard stop on any refusal rather than retry or evasion (`03` §5, §9.3), and a hard interlock against Lane C consuming Lane B output without a separate decision (`03` §4.3)
- Lane B tick client scope (`03` §7): parse the feed down to per-car position, gap/interval, in-pit/retired, speed/throttle/brake/gear/rpm, and X/Y — keyed on the FIA three-letter code, same canonical key as Lane A (`01` §8.2). Twelve channels subscribed, not the ~30 the feed carries; media, weather, and presentational channels deliberately left out. Channel 45 (DRS pre-2026) is carried as an **opaque integer** — measured constant-zero across a full 2026 race, so it carries no signal this season and no model may treat it as a DRS analogue (`03` §7.3)
- No paid capture hardware for Lane B — Apple TV app runs natively on Mac, so screen capture of the app window is the plan, not an HDMI capture card

## Open decisions

- ~~**New 2026-08-26:** Lane B's live data source (`03-live-telemetry-overtakes.md` §2.4). Four
  candidates, none clean: FastF1's live module is zero-budget and ToS-clean but not actually live
  (confirmed permanent limitation, not a version gap); OpenF1's free tier is the same —
  zero-budget, ToS-clean, not live — and its paid tier (€9.90/mo) is live but costs money and
  needs explicit approval per `welcome.md`; a direct connection to F1's own live timing feed is
  zero-budget and genuinely live but sits against F1's own legal notices, read directly rather
  than paraphrased (`03` §2.3): they name "live timing data" as protected, restrict site
  materials to personal/non-commercial use, and prohibit reverse-engineering "the site" — with a
  real caveat that those are the *website's* terms and the live timing feed is a separate host,
  so how far they reach isn't fully settled by that page alone. What isn't ambiguous: this
  project's end goal (an automated trading bot) is about as far from "personal, non-commercial
  use" as a use case gets, and one hobbyist client already had its hosted deployment IP-blocked
  by F1 for doing exactly this. Owner's call: approve the paid tier, accept the legal/stability
  risk of the direct connection (worth an actual legal read, not a default), or leave Lane B
  blocked.~~ **Decided and closed 2026-08-26: the direct connection, risk knowingly accepted.**
  See the Locked decisions entry above for what was accepted and on what conditions, and `03`
  §§4–13 for the spec that came out of it. The four open items that survive are below and in
  `03` §16.
- ~~**New 2026-08-26:** if the unauthenticated live timing endpoint closes, what replaces it?~~
  **Downgraded to a standing contingency the same day — not a live decision** (`03` §6.4, §16
  item 1). It was filed on the belief that F1 had put live timing behind an F1TV subscription
  token. That was wrong: the 401 belongs to the *retired* legacy endpoint, and the current
  `/signalrcore` endpoint accepts unauthenticated connections — verified by controlled measurement
  during Zandvoort FP1 and against a large public dashboard that connects with no login. No money
  decision is needed, and no account gets bound to F1's terms. Kept on the books because F1 has
  now migrated this feed once (2025 introduce, 2026 retire), so a future migration is a question
  of when rather than whether — and if one of those closes the anonymous path instead of moving
  it, the options (pay for F1TV / pay OpenF1 / stop Lane B) are the owner's and are recorded in
  `03` §16.
- ~~**New 2026-08-26 (gate 4, `07` §10.6):** which of Lane B's two justifications governs —
  trading or learning?~~ **Decided 2026-08-26, same day: trading**, via overtake probability →
  live win probability → the race-winner market. Recorded in `08` §1. This repoints the lane
  rather than merely picking a side: the original corner-level-overtake trading case is dead
  (`07` §10.1 — no such market on either venue), and this replaces it with one aimed at a market
  measured to trade in-race.
- ~~**New 2026-08-26 (gate 4, `07` §10.6):** does the in-race winner market replace corner-level
  overtakes as Lane B's target?~~ **Answered 2026-08-26: yes** — and the overtake model survives
  as the feature generator feeding it (`08` §3), not as the thing traded directly. What stays
  open is whether the win-probability layer gets specced next (`08` §11 item 2).
- **New 2026-08-26 (gate 4, `07` §10.6):** **two read-only market-data scope questions.** Whether
  to fold Kalshi's unauthenticated `candlesticks` endpoint into the locked scope (used once under
  an explicit flag to measure *when* volume traded), and whether to reopen Polymarket CLOB/Data
  read-only for the same measurement — the only way to close the UNVERIFIED in `07` §10.4.
  Distinct from Phase C2's CLOB-for-execution question.
- **New 2026-08-26:** **whether Lane B's output may ever feed Lane C.** `03` §4.3 turns this from
  a sequencing detail into an explicit interlock: no Lane C module imports from the Lane B
  client, and the Lane B client has no path to an order interface. Flipping that switch is the
  moment "personal, non-commercial research" stops being an available description of what this
  project does — so it should be a dated decision taken on that basis, not something that happens
  because two modules both happened to finish. Related to, but separate from, the existing Lane C
  item on venue ToS.
- **New 2026-08-26:** **whether Lane B appears in the portfolio/LinkedIn writeup at all.**
  `welcome.md` says this project exists partly to be shown. Lane B is the one lane where being
  seen carries its own risk — the enforcement precedent in `03` §2.3 was against the most visible
  instance of this behaviour. Options run from omitting the lane, through describing the
  architecture without naming the endpoint, to writing it up in full. Worth deciding before
  anything about this lane is published rather than after.
- **The B1 delay observation is now a gate, not a nice-to-have** (`03` §4.4, carried from `03`
  §3). Still unrun, still free. B0's client may be built without it; the overtake model on top
  may not. Do it at the first available session, alongside `03` §13's acceptance run — the same
  capture serves both.
- ~~**New 2026-08-26:** `--mode final`'s holdout (`HOLDOUT_SEASONS = (2024, 2025, 2026)`) includes
  a season still in progress~~ **Resolved and closed 2026-08-26.** `--holdout 2024,2025` correctly
  keeps 2026 out of the *scored* seasons — `season_forward_folds` only evaluates the seasons named
  in `--holdout`, and every fold for 2024/2025 trains on seasons strictly before it. What the
  original writeup got wrong: `--holdout` alone does **not** keep a season out of *dev tuning* —
  `dev_seasons` was simply "everything not in `--holdout`," so 2026's 12 rounds silently became a
  dev fold and picked the hyperparameters for the first `--mode final` run. Real consequence, see
  the Phase A3 entry's erratum: this was caught only after that run, and fixing it required a
  second `--mode final` invocation, which re-scored 2024/2025 — a real double-touch of the holdout,
  not a hypothetical one. Fixed going forward with `fit.py --dev-exclude`, which excludes a season
  from dev tuning without either holding it out or scoring it. Phase A3 itself is closed either
  way — see the Phase A3 entry for the result.
- **New 2026-08-24:** multi-model weather ensemble spec drafted, then revised and verified against
  44 races the same day (`06-weather-ensemble-signal.md`) — queries Open-Meteo with an explicit
  `models=` list (ECMWF IFS, GFS, ICON, GEM) instead of the provider's blended default. **Not
  approved, not implemented.** No self-hosted ML weather model (GraphCast/Pangu-Weather rejected as
  out of scope/zero-budget-incompatible); Open-Meteo's real 50-member ensemble API considered and
  deferred, since its ~93-day past window can't be backtested (`06` §8).
  - The draft's motivation — Zandvoort's market under-pricing rain risk — is **withdrawn**. It
    asserted the race was wet; the roadmap's own open items and `02` §10.4 both say it was dry, it
    ran full distance, and the observation archive shows 0.0–0.1mm. Neither venue exposes
    historical odds, so the market half was never checkable either.
  - What replaced it is a measured result (`06` §5.3): across 44 races, today's weather-gate errors
    concentrate in the 43% of races where the four models disagreed by ≥15pp — **5 of 6 under the
    wet rule in force, 9 of 9 under a ≥0.5mm rule.** The value of querying several models is the
    disagreement, not the average. Reproduce with `weather_backtest.py`.
  - The agreement threshold (15pp) and race window (`snapshot.py:320`, lights-out local ±2h) are
    both settled. **One blocking item is left, and it's the owner's:** `snapshot.py:288` calls a
    0.1mm trace a wet race, and which aggregate feeds F7's gate flips on that definition —
    `p_max` if `>0.0mm` stays, `p_mean` if it tightens to `≥0.5mm`. `06` §6.1 recommends
    tightening and deliberately does not act on it.
- Track overtaking multipliers (`02` §5.1) are hand-set judgements, not measurements. A3 **drops `m` entirely in v1** and tests the claim instead of assuming it: a fitted `s_grid` × circuit-tier interaction on `02`'s existing three tiers costs two parameters, not 33 (a per-circuit term would fit noise at ~8 races per circuit). Ordered as predicted → replace the hand-set numbers with the fitted ones; flat or inverted → drop the multiplier. Either result closes this (`05` §3.5)
- ~~`T=0.1168` needs recalibrating against outcomes in A3~~ **Resolved 2026-08-23: `T` dissolves.** It existed only because `02` constrains the weights to sum to 1.0, which fixes their ratios but discards their scale — `T` put the scale back. A fitted `β` carries both, so there is no `T` left to recalibrate and no separate calibration step (`05` §3.1). The observation this entry recorded stands and is still the reason A1 alone runs flat: averaging eight partly-disagreeing features compresses top-of-field gaps (0.0774 synthetic vs. 0.0032 real P1−P2, `02` §10.2), which is precisely the compression a fit absorbs into coefficient scale
- The 0.42 pole-conversion anchor (`02` §10.3) is a rounded historical figure — **no longer needs recomputing**, A3 doesn't use it at all (`05` §3.1)
- `K_GRID`/`K_SPRINT`/`K_FIN` stay unfitted after A3 — they live *inside* the sub-scores, which A3 treats as fixed inputs, so a linear-in-`β` model can't tune them. Would need a grid search wrapping the whole fit, or a model over raw positions (`05` §10.3)
- Weather feature's wet branch has never executed (Dutch GP was dry) — untested before a wet weekend
- Polymarket driver-name → FIA code mapping table (no code exposed by that API; must be maintained)
- De-vig method beyond A1 (proportional vs. longshot-aware) — **re-filed 2026-08-23.** This was "defer to A3 calibration data," but A3 is market-blind by construction, so nothing in that phase produces evidence about de-vigging. It needs live-snapshotted races with realized outcomes (currently n=1) — the same scarce resource that limits the market baseline — so it's gated on race count, not on A3
- ~~Snapshot retention: are `data/snapshots/*.json` committed to git?~~ Resolved: yes, three commits deep now (`data/snapshots/2026-12-race-*.json`, `-score.json`, `-postrace.json`)
- ~~FastF1 interpreter upgrade path: `brew install python@3.12` + venv vs. `uv`~~ Resolved 2026-08-24: went with `brew install python@3.12` + a project-local `.venv312/` (gitignored). `fastf1==3.8.3` installs clean and smoke-tested against the real 2023 Dutch GP session (`data/cache/fastf1/`, also gitignored under the existing `data/cache/` rule) — result matches Jolpica (VER/ALO/GAS podium). Not wired into `snapshot.py`/`score.py` yet; that's its own follow-up, not done here
- ~~Lane B: FastF1's live module does **not** parse in real time (records raw for post-session parsing, ~2h connection cap) — the B0 premise needs revisiting.~~ Resolved 2026-08-26 by `03`: FastF1 is out as a live source entirely, B0 connects to the underlying feed directly (`03` §6), and the ~2h cap is a server-side disconnect FastF1 just never reconnects from rather than a hard limit (`03` §9.1)
- Whether Apple's broadcast even displays a persistent on-screen data overlay worth targeting for Phase B2b (unknown until observed)
- Hosting: owner has a homelab that could run this instead of a laptop-on-demand model — likely relevant for automating the pre-lights-out re-snapshot (cron) and for Lane B's continuous-during-a-session workload; doesn't help Lane B's screen-capture step as currently scoped, since that specifically targets the Apple TV app on the Mac. Zero-budget-compliant since it's already-owned hardware. Not decided, not needed yet — revisit when Lane B design actually starts. Made more relevant by the 2026-08-23 routine run: the cloud environment's default network access (Trusted) silently blocks any non-package-registry host, which cost real setup time to diagnose and fix — a homelab wouldn't have that failure mode. Directly relevant to Lane C too, if trading ever needs to run unattended.
- Whether future races' pre-lights-out snapshot + post-race score should run by default via scheduled cloud routines (now validated working on the `smarty-f1` environment), or stay manually triggered — not decided
- Lane C: both venues' terms of service on automated/programmatic trading — unconfirmed, needs checking before Phase C2
- Lane C: Kalshi's CFTC-regulated status vs. Polymarket's structure may carry different compliance obligations for an automated trader — unconfirmed
- Lane C: realistic latency here is home network + broadcast delay, not co-located/exchange-proximity infrastructure — "HFT" in the goal statement means "fast relative to a slow-to-reprice retail market," not literal microsecond HFT; keep that honest in any future spec
- Lane C: whether to scope the first cut to settled markets only (winner/podium, no live feed needed) before attempting live overtake markets — leaning yes, not decided
- **New 2026-08-26 (`07` §11):** a live structure survey of every open F1 market on both venues
  settles *which* market Lane C would trade if it ever does: **per-race winner, top ~5 drivers,
  both venues** — the only market where a model output, a real book, and a spread tight enough
  for an edge to survive all coincide. Kalshi's winner book is 1¢-spread and liquid ~10 days out
  (contradicts A4's "only liquid near lights-out", which was Polymarket-specific). Three
  corrections fall out: (a) `07` §5's "tens of dollars / not an income stream" ceiling is wrong
  for the season Drivers'-Champion market ($201M vol, 0.1–1.1% spreads) — but that market is
  efficient and the project has no championship model, so capacity and model-fit are in disjoint
  markets; (b) H2H is the best structural fit for A1's teammate feature but the book is dead ($22
  lifetime volume); (c) the §7 harness must compute edge against `bestBid`/`bestAsk`, never
  Polymarket midpoints — wide-spread podium/H2H midpoints fabricate ~35pp fake edges on illiquid
  midfield legs. Also an erratum for `01` §7.6: Kalshi's API fields are now `volume_fp` /
  `yes_bid_dollars` etc., and the old names return null
- ~~Phase A4: podium/points precision is Monte Carlo (±0.3pp) — revisit if an exact top-K
  marginal algorithm is worth the added complexity~~ Resolved 2026-08-23, split by K: **podium is
  now exact** (`lib/simulate.exact_top3_probabilities`, O(n³) ≈ 10k terms, ~2.5ms, sums to 3.0 as
  its own numerical guard), with the simulation kept as an independent cross-check. **Points stays
  Monte Carlo**: a Plackett-Luce denominator depends on *which* drivers were already placed, not
  just how many, so there is no DP that collapses a 10-deep sum. Wall-clock is unchanged — the
  simulation still runs for K=10 — so this buys precision and a second estimator, not speed
- Phase A4: DNF's driver/team split is a hand-set 50/50, unmeasurable with 2026's status data
  (no crash-vs-mechanical breakdown) — revisit if a richer status source appears or once enough
  seasons accumulate to fit the split in A3
- Phase A4: fastest lap's `T` is borrowed from the win-market calibration, not independently
  anchored — needs its own real-outcome-based anchor eventually
- Phase A4: DNF risk is not fed into the podium/points simulation (deliberate — an explicit DNF
  draw would double-count risk already implicit in the win market's `T` calibration and break
  reproducing `02`'s locked win numbers) — revisit together with a `T` recalibration in A3
- Phase A4: points market comparison is Kalshi-only, no independent second-venue corroboration
  the way winner/podium/fastest-lap have — a large points edge could be the algo or could be a
  thin Kalshi book, and there's currently no way to tell which
- Phase A4: no genuine pre-race market comparison exists yet for podium/points/fastest lap (see
  Phase A4 status above) — needs a race snapshotted close enough to lights-out to have liquid
  markets, which hasn't happened yet as of this writing
- A3 backfill: a backfilled sprint weekend loses that weekend's sprint points from F6, because no
  round-indexed endpoint answers "after round N−1's race plus round N's sprint" (`01` §4.6).
  Bounded at 8 points on a leader-normalised 0.08-weight feature — revisit only if A3 shows F6
  mattering more than that
- ~~A3 backfill: F7's train/serve skew (`01` §5.6) needs a decision~~ Resolved 2026-08-23:
  train-dormant, see the A3 phase entry above. What remains open is the *consequence* — A3 has no
  wet term at all, so a wet race is out-of-domain for it. Fixing that needs either a weather
  feature the archive can also produce, or enough live-snapshotted wet races to fit a term on
  forecast probabilities directly (`05` §10.4)
- A3: the regularization prior — shrink `β` toward zero, or toward A1's implied `β`? The latter is
  more informative and makes "how far did the data move the weights?" readable straight off the
  fit, but it is also a thumb on the scale for the baseline A3 is being tested against. Decide on
  a validation split, not by argument (`05` §10.1). **Mechanism built, answer not final
  (2026-08-24):** `fit.py` implements both arms and selects between them on the dev folds, so this
  no longer needs deciding by hand — but the only run so far is on 205 of 264 races, and it
  selected the A1 prior at a strength that pins `β` to it exactly. That is the outcome §10.1
  warned about (the informative prior *is* the baseline, so the comparison stops measuring
  fitting), and it should not be read as settled off a partial, non-randomly-holed corpus. Re-read
  the sweep after `--mode final` runs. One thing the prior choice does *not* affect: the two arms
  are identical at λ=0 by construction, so a λ=0 win would carry no content either way — the code
  says so explicitly rather than leaving it to be noticed
- ~~A3: the pooled model **drops the per-circuit `m`** (`05` §3.5, D4)~~ **Resolved 2026-08-26:**
  ran `05` §3.5's fitted tier interaction on the full-corpus dev folds (`tier_interaction_backtest.py`),
  fold by fold rather than on a single fold. The era-split argument that motivated running it still
  holds on the complete corpus (gap +0.0125 on 2017–2020, +0.0066 on 2021–2023 — see the Phase A3
  entry above). The interaction itself is **not vindicated, but the two halves land differently**:
  `grid_x_hard` is negative in all 7 unregularized dev folds — a stable inversion of `02` §5.1's
  predicted ordering, not noise — while `grid_x_easy` flips sign three times across the same folds,
  i.e. not identified from this corpus in either direction. `m` stays dropped for v1; unlike F7's
  train-dormancy or `T`'s dissolution, this is not treated as permanently closed — the easy-tier
  side specifically could resolve differently with more seasons at its five circuits. Closes the
  open item for now without touching the holdout.
- Lane C: the illiquidity found at Monza (podium priced ~0.5 on $0–300 volume vs. the winner
  market's $1,400–$14,000) is filed under Phase A4 as a snapshot-*timing* problem. For Lane C it
  is also a **capacity** problem: a market priced at 0.5 on no volume is not mispriced, it is
  absent, and no edge can be traded into a book that thin. The two readings need separating before
  C1 picks a first market
