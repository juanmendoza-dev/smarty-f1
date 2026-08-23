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
Status: feature list/weights not yet defined — next step.

**Phase A2 — First live test: Dutch GP, 2026-08-23**
Qualifying already happened (2026-08-22), so grid positions are known. Build the Phase A1 predictor tonight, lock in a prediction + market snapshot before lights-out, compare against the actual result after the race.
Status: pending Phase A1.

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

- Feature list + weights for the Phase A1 rule-based score
- Polymarket driver-name → FIA code mapping table (no code exposed by that API; must be maintained)
- De-vig method beyond A1 (proportional vs. longshot-aware) — defer to A3 calibration data
- Snapshot retention: are `data/snapshots/*.json` committed to git?
- FastF1 interpreter upgrade path: `brew install python@3.12` + venv vs. `uv`
- Lane B: FastF1's live module does **not** parse in real time (records raw for post-session parsing, ~2h connection cap) — the B0 premise needs revisiting. See `01-data-pipeline.md` §9.5
- Whether Apple's broadcast even displays a persistent on-screen data overlay worth targeting for Phase B2 (unknown until observed)
