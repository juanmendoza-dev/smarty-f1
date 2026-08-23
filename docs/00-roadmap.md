# Roadmap

Read `docs/welcome.md` first if you haven't. This doc tracks phases, status, and what's locked vs. still open. Keep it up to date as decisions get made — this is the single source of truth for "where are we."

## Structure: Lane A vs Lane B

| | Lane A — Batch predictions | Lane B — Live predictions |
|---|---|---|
| **What** | Race winner, podium, points, DNF probability | Overtake prediction, corner-level, real-time |
| **Data pattern** | Pull once before a session, compute, done | Continuous stream during a session, react in real time |
| **Complexity driver** | Feature quality, market comparison | Latency, broadcast delay-sync, event detection |
| **Status** | Active — current focus | Deferred — design only, not building yet |

## Lane A phases

**Phase A0 — Data pipeline (current)**
Lock down sources for historical results, weather, and market odds. See `01-data-pipeline.md`.
Status: **locked and written** (2026-08-22). All sources verified live against production; every Phase A1 input (grid, sprint, standings, weather, both markets) confirmed available. Blocker noted: FastF1 needs Python >= 3.10, machine has 3.9.6 — A1/A2 deliberately do not depend on FastF1.

**Phase A1 — Rule-based winner predictor (current focus)**
Hand-weighted scoring function using grid position, season/team form, track history, weather. No training — the owner picks the weights. Output compared against Polymarket + Kalshi odds for the same race.
Status: **implemented and verified** (2026-08-22 night). See `02-winner-prediction-algo.md`. Eight features (grid 0.35, team form 0.15, sprint 0.13, driver form 0.11, track history 0.08, championship 0.08, weather 0.05, teammate H2H 0.05), softmax T=0.1168, market-blind. Design was dry-run against real Dutch GP data before locking, which added the championship feature and small-sample shrinkage on track history. `snapshot.py` pulls grid/form/track history/weather/both markets into an immutable snapshot JSON; `score.py` reads a snapshot with no network calls and computes the scorer plus market comparison; a `lib/` package holds the source clients. Scorer output reproduces `02-winner-prediction-algo.md` §9's reference table exactly — every sub-score, effective weight, raw score, and probability matches to the stated precision. `test_f7_wet_branch.py` exercises F7's wet-weather path (never run before, tonight's forecast is dry) against the archive-verified 2023 Dutch GP. Committed and pushed to main.

**Phase A2 — First live test: Dutch GP, 2026-08-23**
Qualifying already happened (2026-08-22), so grid positions are known. Build the Phase A1 predictor tonight, lock in a prediction + market snapshot before lights-out, compare against the actual result after the race.
Status: **snapshot + scorer + post-race auto-scorer built, reference prediction verified** — see `02-winner-prediction-algo.md` §9. Algo: NOR 36.2%, RUS 35.2%, ANT 11.4%. Market: NOR 37.2%, RUS 25.0%, ANT 25.0%. Two testable divergences (RUS +10.2, ANT -13.6). A real first snapshot is committed in `data/snapshots/`. `postrace.py` now pulls the actual finishing order from Jolpica, takes the classified P1, and writes the same Brier-score comparison `score.py --winner CODE` produces (that shared logic was pulled into `compute_post_race()`/`compute_comparison()` so both paths use it) into its own `<snapshot>-postrace.json`, kept separate from `score.py`'s committed pre-race `-score.json` audit trail so a post-race run can never overwrite it; `test_postrace.py` dry-runs it against the real, already-decided 2023 Dutch GP (correctly finds VER as winner, pulling the round live off Jolpica's schedule rather than hardcoding it) since this weekend's result isn't ingested yet. Remaining: re-snapshot the markets close to lights-out (13:00 UTC, 2026-08-23 — owner triggers manually), then run `postrace.py` for real once Jolpica has this race's result.

**Phase A3 — Trained model**
Once enough historical race data (features + outcomes) has been collected, train logistic regression as a first real model and compare its calibration against the Phase A1 rule-based baseline. Move to gradient-boosted trees (XGBoost/LightGBM) once the data pipeline is trusted.
Status: not started — depends on accumulating real historical prediction data from A1/A2 runs.

**Phase A4 — Expand outcome types**
Podium, points finishers, DNF probability, fastest lap — same batch pattern, new labels.
Status: not started.

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

## Locked decisions

- Historical results: **FastF1 + Jolpica**, used redundantly (cross-validation/backup, not additional unique data volume — they mostly cover the same races)
- Weather: **Open-Meteo** (free, confirmed)
- Market odds: **Polymarket + Kalshi**, both confirmed to have active Dutch GP winner markets. **No API credentials needed** — both venues' price-read endpoints are fully public (verified live 2026-08-22). See `01-data-pipeline.md` §6.3, §7.3.
- Market data access: Polymarket **Gamma** API only (not CLOB/Data); Kalshi `GET /markets` on `external-api.kalshi.com`
- Canonical driver key across all sources: **FIA three-letter code** (`ANT`, `NOR`), sourced from Jolpica `Driver.code`
- Odds normalization for A1: proportional de-vig; raw + normalized both persisted
- Live data (when needed): **FastF1's free live module**, not OpenF1's paid live tier (€9.90/month) — avoided per the zero-budget constraint
- No paid capture hardware for Lane B — Apple TV app runs natively on Mac, so screen capture of the app window is the plan, not an HDMI capture card

## Open decisions

- Track overtaking multipliers (`02` §5.1) are hand-set judgements, not measurements — replace with real overtake data in A3
- `T=0.1168` is calibrated on a grid-only synthetic field and understates real correlated spread — recalibrate against outcomes in A3
- Weather feature's wet branch has never executed (Dutch GP was dry) — untested before a wet weekend
- Polymarket driver-name → FIA code mapping table (no code exposed by that API; must be maintained)
- De-vig method beyond A1 (proportional vs. longshot-aware) — defer to A3 calibration data
- Snapshot retention: are `data/snapshots/*.json` committed to git?
- FastF1 interpreter upgrade path: `brew install python@3.12` + venv vs. `uv`
- Lane B: FastF1's live module does **not** parse in real time (records raw for post-session parsing, ~2h connection cap) — the B0 premise needs revisiting. See `01-data-pipeline.md` §9.5
- Whether Apple's broadcast even displays a persistent on-screen data overlay worth targeting for Phase B2 (unknown until observed)
