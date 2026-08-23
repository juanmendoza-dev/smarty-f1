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
| **Status** | Active — current focus | Deferred — design only, not building yet |

Lane C (trading, see below) sits downstream of both: it can trade Lane A's settled-market predictions (winner, podium) without waiting on Lane B, but in-race overtake markets need Lane B's live feed to exist first. It's tracked as its own lane rather than a sub-phase of either.

## Lane A phases

**Phase A0 — Data pipeline (current)**
Lock down sources for historical results, weather, and market odds. See `01-data-pipeline.md`.
Status: **locked and written** (2026-08-22). All sources verified live against production; every Phase A1 input (grid, sprint, standings, weather, both markets) confirmed available. Blocker noted: FastF1 needs Python >= 3.10, machine has 3.9.6 — A1/A2 deliberately do not depend on FastF1.

**Phase A1 — Rule-based winner predictor (current focus)**
Hand-weighted scoring function using grid position, season/team form, track history, weather. No training — the owner picks the weights. Output compared against Polymarket + Kalshi odds for the same race.
Status: **implemented and verified** (2026-08-22 night). See `02-winner-prediction-algo.md`. Eight features (grid 0.35, team form 0.15, sprint 0.13, driver form 0.11, track history 0.08, championship 0.08, weather 0.05, teammate H2H 0.05), softmax T=0.1168, market-blind. Design was dry-run against real Dutch GP data before locking, which added the championship feature and small-sample shrinkage on track history. `snapshot.py` pulls grid/form/track history/weather/both markets into an immutable snapshot JSON; `score.py` reads a snapshot with no network calls and computes the scorer plus market comparison; a `lib/` package holds the source clients. Scorer output reproduces `02-winner-prediction-algo.md` §9's reference table exactly — every sub-score, effective weight, raw score, and probability matches to the stated precision. `test_f7_wet_branch.py` exercises F7's wet-weather path (never run before, tonight's forecast is dry) against the archive-verified 2023 Dutch GP. Committed and pushed to main.

**Phase A2 — First live test: Dutch GP, 2026-08-23**
Qualifying already happened (2026-08-22), so grid positions are known. Build the Phase A1 predictor tonight, lock in a prediction + market snapshot before lights-out, compare against the actual result after the race.
Status: **complete** (2026-08-23). Pre-race: algo predicted NOR 36.2%, RUS 35.2%, ANT 11.4% vs. market NOR 37.2%, RUS 25.0%, ANT 25.0% — see `02-winner-prediction-algo.md` §9 for the full reference table. `postrace.py` pulls the actual finishing order from Jolpica, takes the classified P1, and writes the same Brier-score comparison `score.py --winner CODE` produces (shared logic in `compute_post_race()`/`compute_comparison()`) into its own `<snapshot>-postrace.json`, kept separate from `score.py`'s pre-race `-score.json` so a post-race run can never overwrite it; `test_postrace.py` dry-runs it against the real, already-decided 2023 Dutch GP as a pre-flight check.

Actual result: **NOR won**. Algo's top pick was correct. Brier score: algo 0.5499 vs. polymarket 0.5345, kalshi 0.5492, market_mean 0.5416 — the algo called the winner right but was **worse calibrated than the market mean**, the first real data point for A3. Full numbers in `data/snapshots/2026-12-race-20260823T031058Z-postrace.json`, committed in `0019d5b`.

Both the pre-lights-out re-snapshot and the post-race score ran via scheduled Anthropic cloud routines (`dutch-gp-lights-out-resnapshot`, `dutch-gp-postrace-score`) rather than manual triggering — first real test of that automation path. The lights-out routine actually failed this race (its cloud environment's network access was Trusted-only and blocked `api.jolpi.ca`/market/weather hosts outright, so no fresh pre-lights-out snapshot was taken — the committed snapshot is from the earlier manual run); the postrace routine hit the same block on its first scheduled fire and succeeded only after being repointed at a new `smarty-f1` cloud environment with Full network access. Whether scheduled routines become the standing mechanism for future races (vs. staying manually triggered) is open — see Open decisions.

**Phase A3 — Trained model**
Train logistic regression as a first real model and compare its calibration against the Phase A1 rule-based baseline. Move to gradient-boosted trees (XGBoost/LightGBM) once the data pipeline is trusted.
Status: not started, but **no longer blocked on waiting for races** (revised 2026-08-23). This previously read "depends on accumulating real historical prediction data from A1/A2 runs," which would have meant ~5 rows by the end of 2026. That was wrong: every A1 feature except market odds is reconstructible for any past race from Jolpica, and `test_phase_a4.py` already rebuilds a full 2023 Dutch GP snapshot from `snapshot.py`'s own functions. The realistic training set is ~200+ historical races, available now. You don't need market odds to *train* — only to *evaluate against the market*, which stays limited to races this pipeline actually snapshotted live.

What genuinely gates it:
- ~~Two future-data leaks in the backfill path~~ **fixed 2026-08-23**: `build_track_history` took `race_date` and never used it (a backfill picked up later editions *and the target race itself* — the label, inside F5), and F6's standings came from the "latest" endpoint, which on a finished season is the final table. Both now have leakage guards; see `01-data-pipeline.md` §4.6.
- ~~**Jolpica request volume, not compute.**~~ **fixed 2026-08-23**: `build_form` looped `race_results` once per prior round, so a cold-cache 200-race backfill re-walked every season's rounds from scratch for every race in it, against a free API capped at 500/hr (`01` §4.3), and `data/cache/` is gitignored so no warm cache is inherited. Replaced with `jolpica.season_results`, one paginated bulk pull per season (100-row page cap, verified live — a 22-round season is 5 pages), cached per `(season, offset)` and reused by every race in that season for the life of the cache. Verified live: fetching 2023 cold cost 5 network calls; re-running `build_form` for five more 2023 rounds afterward cost zero. 33/33 tests pass, including the real-network `test_phase_a4.py` backfill path.
- **F7 cannot be backfilled as specced** (`01` §5.6): the archive endpoint has no precipitation *probability*, only observed mm, so historical rows have no `p_max`. Train-dormant or define a wet proxy — either is train/serve skew, and the choice has to be made explicitly.
- **Model shape.** The scorer is already a softmax over the field, so the matching model is a conditional logit over driver-races (~4,000 rows at 200 races), not a per-driver binary logistic regression on ~200 winner events.

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
- Odds normalization for A1: proportional de-vig; raw + normalized both persisted
- Live data (when needed): **FastF1's free live module**, not OpenF1's paid live tier (€9.90/month) — avoided per the zero-budget constraint. Note this is the same constraint blocking Lane C's live in-race trading (Phase C1) — revisit together if the owner decides to approve the paid tier
- No paid capture hardware for Lane B — Apple TV app runs natively on Mac, so screen capture of the app window is the plan, not an HDMI capture card

## Open decisions

- Track overtaking multipliers (`02` §5.1) are hand-set judgements, not measurements — replace with real overtake data in A3
- `T=0.1168` is calibrated on a grid-only synthetic field, and the real field is **flatter** than that field, not sharper — averaging eight partly-disagreeing features compresses top-of-field gaps (0.0774 synthetic vs. 0.0032 real P1−P2). Corrected 2026-08-23; this entry previously stated the sign backwards. See `02` §10.2. Recalibrate against outcomes in A3
- Weather feature's wet branch has never executed (Dutch GP was dry) — untested before a wet weekend
- Polymarket driver-name → FIA code mapping table (no code exposed by that API; must be maintained)
- De-vig method beyond A1 (proportional vs. longshot-aware) — defer to A3 calibration data
- ~~Snapshot retention: are `data/snapshots/*.json` committed to git?~~ Resolved: yes, three commits deep now (`data/snapshots/2026-12-race-*.json`, `-score.json`, `-postrace.json`)
- FastF1 interpreter upgrade path: `brew install python@3.12` + venv vs. `uv`
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
- A3 backfill: F7's train/serve skew (`01` §5.6) needs a decision — train-dormant, or an
  archive-derived wet proxy that means something different at train and inference time
- Lane C: the illiquidity found at Monza (podium priced ~0.5 on $0–300 volume vs. the winner
  market's $1,400–$14,000) is filed under Phase A4 as a snapshot-*timing* problem. For Lane C it
  is also a **capacity** problem: a market priced at 0.5 on no volume is not mispriced, it is
  absent, and no edge can be traded into a book that thin. The two readings need separating before
  C1 picks a first market
