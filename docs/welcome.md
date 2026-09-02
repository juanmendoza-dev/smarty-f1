# Welcome, Agent

You've been brought onto the **F1 Prediction Model** project. This document is your onboarding — read it before touching any other spec in `/docs`.

## What this project is

A Formula 1 prediction system: starting with **race winner** prediction, later expanding to podium,
points finishers, DNFs, and **live in-race predictions** (overtakes, at the corner level, while a
session is happening) as a standalone Lane B feature.

This is a **personal portfolio project**, built to be shown on LinkedIn. The owner has never trained an ML model before — this project is also their learning path into applied ML, not just a finished product. Treat explanations and specs accordingly: correctness and clarity matter more than brevity when the owner is the audience, but specs written for other agents should be precise and unambiguous.

## The core differentiator

This isn't just "predict the winner." The interesting part is **triangulating multiple sources of truth**:

- **Our own algorithm's prediction** (rule-based today, trained model later)
- **Polymarket odds** (prediction market, crowd-sourced)
- **Kalshi odds** (regulated US prediction market)

The output isn't just a single probability — it's a comparison: does our algo agree with the market, where does it diverge, and over time, is our algo actually better calibrated than the crowd? That comparison *is* the headline feature.

## Hard constraints — do not violate

- **Zero budget.** This project uses free tiers and free/open data sources only. Do not introduce a paid API, paid tier, or paid service without the owner explicitly approving the cost first. (Example: OpenF1's live data tier costs €9.90/month — we deliberately avoid it. **Corrected 2026-08-26:** this used to say "and use FastF1's free live module instead," which was never true — FastF1 can't parse live data at any budget. Lane B's live source is now decided and specced; see `docs/03-live-telemetry-overtakes.md`.)
- **No implementation without an approved spec.** Specs live in `/docs` and are written before code. Don't skip ahead to building something that isn't specced yet — flag it and ask instead.

## Project philosophy — the lanes

The project is split into independent tracks. Don't conflate them — most confusion in this project happens when Lane B's complexity leaks into Lane A's scope.

- **Lane A — Batch/snapshot predictions.** Pull a fixed set of data once (before a session starts), compute a prediction, done. No live connection, no streaming, no delay/sync problem. This is where the project starts: race winner prediction. See `docs/01-data-pipeline.md`.
- **Lane B — Live/streaming predictions.** A continuous, event-driven pipeline that ingests data while a session is happening and reacts in real time (e.g., overtake probability at a specific corner, seconds before it happens). Modeled conceptually on HFT-style streaming architecture. This is a later phase — see the roadmap for sequencing.

## Build philosophy

- **Algo before model.** Every prediction starts as a hand-weighted, rule-based scoring function the owner can reason about and explain. Only after a rule-based baseline exists and there's a real historical dataset to train on do we introduce a trained model (starting with logistic regression, then gradient-boosted trees like XGBoost/LightGBM for tabular data).
- **Prove it live, cheaply, before scaling it up.** Features get validated against real upcoming races before being polished. Getting a real pass/fail result on a real race beats a more "complete" feature that hasn't been tested yet.

## Where to go next

- `docs/00-roadmap.md` — phased plan, current status, what's locked vs. still open
- `docs/01-data-pipeline.md` — data sources, access methods, redundancy strategy (Lane A)
- `docs/02-winner-prediction-algo.md` — the rule-based scoring spec (Phase A1, weights locked)
- `docs/04-outcome-expansion-algo.md` — podium, points, DNF, fastest lap (Phase A4)
- `docs/05-trained-model.md` — the trained winner model (Phase A3, **current focus**). Read `02` first; A3's whole design turns on the fact that `02`'s scorer is already a conditional logit with hand-set coefficients
- `docs/03-live-telemetry-overtakes.md` — Lane B's live data source and tick client (Phase B0). **Now a build spec** (2026-08-26), with the original source research preserved as §§1–3. Read §§4–5 before touching anything in this lane: they set the authorized scope (personal research/development only, no hosted deployment) and record the ToS risk the owner identified and knowingly accepted. The client may be built; the prediction layer on top of it is gated on B1's delay measurement
- `docs/06-weather-ensemble-signal.md` — multi-model weather ensemble spec. **Verified against 44 races, not approved, not implemented** — queries four named weather models instead of one blend, and uses their disagreement to flag when our own forecast can't be trusted. Blocked on one owner decision: whether a 0.1mm trace should keep counting as a wet race (§6.1)
- `docs/08-overtake-model.md` — the overtake model (Phase B2), **specced 2026-08-26, not approved,
  not built**. Load-bearing for Lane B: it records the decision that Lane B's output feeds a live
  win-probability model — overtake probability → live win probability — as a standalone live-prediction
  feature, and the measurements that shape it (≈38 on-track overtakes/race, but **one lead change
  across three races**, and a label time-resolution of ~3.3s against a 5-second target horizon).
  Read `03` §4.4's amendment first — the offline model is authorized, live use is not. **§13 is the
  handoff** — how to rebuild and re-validate from cold, expected outputs, where to pick up, and the
  six corrections made during the build so they are not re-made
- `docs/09-live-win-probability.md` — the live win-probability layer (Phase B4), **specced
  2026-08-27, not approved, not built**. The consumer `08` was built to feed: a state estimator
  that carries Lane A's pre-race distribution through a race by Monte Carlo forward simulation over
  field orderings, not a new predictive model. Read `08` first. Six measurements were run before it
  was written and two of them reshape the lane: **pit stops cause 71% of lead changes** (`09` §2.1),
  so `08` is the fourth-biggest mover of P(win) rather than the engine; and **`08`'s calibrated
  domain is thinnest exactly at the front of the field** (`09` §2.4), which qualifies `08` §11.1's
  headline and needs a second gate, θ_front, to recover a calibration PASS there
- `docs/10-live-viewer.md` — the local live viewer / debug UI for Lane B's captures (**specced
  2026-08-27, not approved, not built**). Tooling, not a phase: it renders
  `data/live/ticks/<slug>.jsonl` as a track map + timing tower + per-car telemetry, in replay and
  live-tail modes, as a matplotlib window on the `macosx` backend. Local only and file-only — it
  opens no socket, per `03` §11.3. **Building it is gated on `03` §13's acceptance run** (Monza FP1,
  ~2026-09-04): the tick format is still `UNVERIFIED`, and §13 item 5 — whether `Position.z` is
  broadcast at all — is a go/no-go for the track map. Its §13 flags one thing worth knowing before
  anyone screenshots it: a screenshot of a real capture is live timing data under `03` §11.2, so it
  cannot be published while `03` §16 item 4 is open

If a decision you need isn't documented, don't assume — it means it hasn't been locked in yet. Ask.
