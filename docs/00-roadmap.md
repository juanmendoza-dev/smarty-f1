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
| **Status** | Active — current focus (Phase A3) | Deferred — design only, not building yet |

Lane C (trading, see below) sits downstream of both: it can trade Lane A's settled-market predictions (winner, podium) without waiting on Lane B, but in-race overtake markets need Lane B's live feed to exist first. It's tracked as its own lane rather than a sub-phase of either.

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
Status: **backfill running; fitter written and tested, real evaluation pending** (2026-08-24). See `05-trained-model.md`.

`backfill.py` is built (`b9c21bb`) and the full `--seasons 2014-2026` run is in progress — **205 of 264 races** written (4,150 rows, 2014 through 2024 R12), no races skipped, exactly one label-1 row per race. Pace is set by `httpcache`'s 450 fetches/hour sustained cap, so the run spends most of its wall-clock parked in a rate-limit pause; expect the remainder to take hours, not minutes, and expect re-runs to be near-free (`05` §5.3 — track history is cached per circuit-driver, so later editions of an already-touched circuit cost nothing).

**`fit.py` and `test_fit.py` are written and passing (2026-08-24), and the Phase A3 result is *not* in yet.** The fitter implements `05` §6/§7 in full: the conditional-logit NLL, its analytic gradient and Hessian, Newton–Raphson with a backtracking line search on the penalized objective, the season-forward splits, all four §6.2 metrics, and both §6.3 baselines. Hand-rolled in pure Python against the system 3.9.6 per §7 — no numpy, no scipy, and the `.venv312/` that now exists for FastF1 is deliberately not used here. 66 tests pass in 4s, including the gradient and Hessian against central finite differences (the check scipy would otherwise have done for us, and the one nothing else would catch — a wrong gradient still converges, just to the wrong place) and the optimizer against the closed-form MLE `log(k/(n−k))` of a one-feature logit over two-driver races.

**What is deliberately *not* done: the §6.4 evaluation.** `fit.py` has two modes. `--mode dev` runs season-forward folds over the pre-holdout seasons only and structurally cannot read a holdout season; `--mode final` is the one run that touches `HOLDOUT_SEASONS = (2024, 2025, 2026)`, and it refuses to start unless the corpus is complete — 264 races with contiguous rounds. Making the held-out period a fixed season set rather than "the last N seasons" is what keeps §6.1's *touched exactly once* an enforceable property while rows are still arriving: a count-based rule would name a different experiment on every run. As of now `--mode final` correctly refuses. Still missing: **2024 R13–24, all of 2025, 2026 R1–12**, plus the **~11 bug-hole races** (2021 R1/R8/R9/R10, 2022 R1/R10/R11, 2023 R1/R9/R10, 2024 R11) that need the second `backfill.py` pass described above. A finished process is not a finished corpus, which is why the guard checks round contiguity rather than trusting `ps`.

**Preliminary dev-fold numbers, which are not the A3 result and must not be quoted as one.** On 205 of 264 races, 7 folds (2017–2023): A3 pooled Brier 0.5874 / log-loss 1.3056, A1 0.5929 / 1.3143, grid-only floor 0.6977 / 1.5913. Two things in that are worth carrying forward rather than re-deriving:
- Validation selected the **A1-implied prior at the top of the λ grid**, and the sweep is flat across its last four rows — β has collapsed onto A1's hand-set coefficients (max |Δ| < 0.001). So on this partial corpus the dev folds prefer A1's ratios to anything the data fitted, and the A3-vs-A1 gap cannot be a fitting effect: it has to come from a structural difference that survives maximal shrinkage. **Attributed, not guessed:** of the two candidates, D3 (no sprint-weekend renormalization) is *inert* on the 2017–2020 folds, because sprints did not exist before 2021 — and that is precisely where the whole gap lives. Pooled Brier over 2017–2020 is A3 0.64477 vs A1 0.65731 (gap +0.0125, 79 races); over 2021–2023 it is A3 0.50644 vs A1 0.50206, i.e. **the gap reverses** (−0.0044, 56 races). So D3 explains none of it, D4 (dropping the per-circuit `m`) is the only difference left standing, and its sign is not stable across eras. The zero-prior arm's own best (λ=0.01, Brier 0.59158) also edges A1, so this is not simply "fitting doesn't work."
- The unregularized fit puts **β_sprint at 2.48 and β_champ above its hand-set value**, and β_sprint collapses to 0.34 under mild shrinkage — exactly the wide interval `05` §3.4 predicted for a coefficient estimated off a ninth of the corpus.

The separation check (§3.6) ran and is clean: no feature is the winner's strict argmax in every race (`grid` in 102 of 194), so no coefficient is being driven to infinity.

**A third bug, found mid-run by diffing the CSV's round numbers (2026-08-24).** `build_track_history` selected the 3 most recent *seasons* but weighted them off a 3-slot list, which is only safe if a season holds one race per circuit — COVID's 2020 calendar put two at each of `bahrain`, `silverstone` and `red_bull_ring`, and 2021 two more at `red_bull_ring`. Affected drivers got 4–5 rows for 3 slots and the race died on a bare `IndexError`. Because that surfaces as a *skip* rather than a crash, the only symptom was races quietly missing: 2021 R1/R8/R9/R10 and 2022 R1 were already gone, with six more due in 2022–2024 — **~11 races, ~4% of the corpus, and not randomly distributed** (it removes three specific circuits across 2021–2024). Fixed by ranking editions by date and capping at 3, which is identical to the old behaviour on every single-race season; `02` §F5 records the clarified semantics and §9's reference run is verified unchanged. The holes get refilled by a second `backfill.py` pass once the current run ends — `already_done()` means it attempts only the missing races, off a warm cache.

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

**Phase B0 — Design only (current)**
Conceptual design of the streaming/event-driven pipeline: tick-based state per car (position, speed, gap, brake/throttle/DRS), trigger conditions (approaching a known corner/braking zone), scoring logic for overtake probability. See `03-live-telemetry-overtakes.md` (stub).
Status: conceptual, not specced in detail.

**Phase B1 — Delay/sync investigation**
Determine the real gap between live data feed timing and broadcast timing for the owner's actual watching setup (Apple TV app, either on Mac directly or the physical Apple TV box). Approach: auto-record the broadcast + auto-log the data feed in parallel, compare after the fact — not manual real-time comparison.
Status: deferred. Originally planned to test during the 2026-08-23 Dutch GP, but deprioritized in favor of Phase A2 (higher ROI for a single day's build).

**Phase B2 — Automated trigger recognition**
Computer vision on screen-captured broadcast frames, targeting broadcast graphic overlays (pit boards, safety car flags, lights-out gantry) rather than raw scene content — a more tractable detection target. Requires reference footage of Apple's actual broadcast graphics first (their first season broadcasting F1 in the US, so no existing reference material).
Status: blocked on B1.

**Phase B3 — Second test window: Italian GP (Monza), 2026-09-04 to 09-06**
Target date for a live-ish test of the Lane B pipeline, once B1/B2 groundwork exists.
Status: not started.

## Lane C phases

**Phase C0 — Goal statement**
Auto-trade YES/NO shares on Polymarket + Kalshi F1 markets (race winner, podium/placement, and eventually in-race overtake markets) off this project's own prediction pipeline.
Status: goal adopted (2026-08-23). No design work started — this section exists to make the dependency chain and blockers explicit before any is.

**Phase C1 — Real-time data feed (blocking)**
The in-race trading case (driver closing on a braking zone, trade before the overtake resolves) needs sub-second telemetry. The project's only live data source considered so far, FastF1's live module, does **not** parse in real time — it records raw and parses after the session, with a ~2h connection cap (see Lane B's open decision on B0's premise, and `01-data-pipeline.md` §9.5). That's a hard blocker on live in-race markets specifically; it is not a Lane B computer-vision problem and B1/B2 don't unblock it.
Status: not started. This is the first fork and gates everything else in Lane C:
- Revisit the zero-budget constraint and take on OpenF1's paid live tier (€9.90/mo) — requires explicit owner approval per `welcome.md`'s hard constraint, not a default
- Or scope Lane C down to markets that don't need corner-level timing (winner, podium — settled after the fact, no live feed required) as the first cut

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
- Odds normalization for A1: proportional de-vig; raw + normalized both persisted
- Live data (when needed): **FastF1's free live module**, not OpenF1's paid live tier (€9.90/month) — avoided per the zero-budget constraint. Note this is the same constraint blocking Lane C's live in-race trading (Phase C1) — revisit together if the owner decides to approve the paid tier
- No paid capture hardware for Lane B — Apple TV app runs natively on Mac, so screen capture of the app window is the plan, not an HDMI capture card

## Open decisions

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
- Lane B: FastF1's live module does **not** parse in real time (records raw for post-session parsing, ~2h connection cap) — the B0 premise needs revisiting. See `01-data-pipeline.md` §9.5
- Whether Apple's broadcast even displays a persistent on-screen data overlay worth targeting for Phase B2 (unknown until observed)
- Hosting: owner has a homelab that could run this instead of a laptop-on-demand model — likely relevant for automating the pre-lights-out re-snapshot (cron) and for Lane B's continuous-during-a-session workload; doesn't help Lane B's screen-capture step as currently scoped, since that specifically targets the Apple TV app on the Mac. Zero-budget-compliant since it's already-owned hardware. Not decided, not needed yet — revisit when Lane B design actually starts. Made more relevant by the 2026-08-23 routine run: the cloud environment's default network access (Trusted) silently blocks any non-package-registry host, which cost real setup time to diagnose and fix — a homelab wouldn't have that failure mode. Directly relevant to Lane C too, if trading ever needs to run unattended.
- Whether future races' pre-lights-out snapshot + post-race score should run by default via scheduled cloud routines (now validated working on the `smarty-f1` environment), or stay manually triggered — not decided
- Lane C: both venues' terms of service on automated/programmatic trading — unconfirmed, needs checking before Phase C2
- Lane C: Kalshi's CFTC-regulated status vs. Polymarket's structure may carry different compliance obligations for an automated trader — unconfirmed
- Lane C: realistic latency here is home network + broadcast delay, not co-located/exchange-proximity infrastructure — "HFT" in the goal statement means "fast relative to a slow-to-reprice retail market," not literal microsecond HFT; keep that honest in any future spec
- Lane C: whether to scope the first cut to settled markets only (winner/podium, no live feed needed) before attempting live overtake markets — leaning yes, not decided
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
- A3: the pooled model **drops the per-circuit `m`** (`05` §3.5, D4), and on the preliminary dev
  folds that is the *only* structural difference that can explain the A3-vs-A1 gap — D3 is
  provably inert on 2017–2020 and that is where the entire gap sits (see the Phase A3 entry).
  What it does **not** support is "dropping `m` helps": the gap is +0.0125 on 2017–2020 and
  −0.0044 on 2021–2023, so the sign flips by era on a partial, non-randomly-holed corpus. That is
  a reason to run `05` §3.5's fitted tier interaction, which measures the multiplier directly
  instead of inferring it from a difference of differences — not a reason to touch `02` §5.1's
  numbers yet
- Lane C: the illiquidity found at Monza (podium priced ~0.5 on $0–300 volume vs. the winner
  market's $1,400–$14,000) is filed under Phase A4 as a snapshot-*timing* problem. For Lane C it
  is also a **capacity** problem: a market priced at 0.5 on no volume is not mispriced, it is
  absent, and no edge can be traded into a book that thin. The two readings need separating before
  C1 picks a first market
