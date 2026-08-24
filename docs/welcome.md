# Welcome, Agent

You've been brought onto the **F1 Prediction Model** project. This document is your onboarding — read it before touching any other spec in `/docs`.

## What this project is

A Formula 1 prediction system whose ultimate goal is an **automated trading bot** that trades YES/NO shares on Polymarket + Kalshi F1 markets, using the project's own predictions as its edge. Getting there starts with **race winner** prediction, later expanding to podium, points finishers, DNFs, and **live in-race predictions** (overtakes, at the corner level, while a session is happening) — the trading layer is built on top of both once they exist and its own open questions (real-time data, order execution, risk controls) are resolved. See the roadmap's Lane C for where that stands.

This is a **personal portfolio project**, built to be shown on LinkedIn. The owner has never trained an ML model before — this project is also their learning path into applied ML, not just a finished product. Treat explanations and specs accordingly: correctness and clarity matter more than brevity when the owner is the audience, but specs written for other agents should be precise and unambiguous.

## The core differentiator

This isn't just "predict the winner." The interesting part is **triangulating multiple sources of truth**:

- **Our own algorithm's prediction** (rule-based today, trained model later)
- **Polymarket odds** (prediction market, crowd-sourced)
- **Kalshi odds** (regulated US prediction market)

The output isn't just a single probability — it's a comparison: does our algo agree with the market, where does it diverge, and over time, is our algo actually better calibrated than the crowd? That comparison *is* the headline feature — and, per the project's ultimate goal, it's also the signal an automated trader would act on.

## Hard constraints — do not violate

- **Zero budget.** This project uses free tiers and free/open data sources only. Do not introduce a paid API, paid tier, or paid service without the owner explicitly approving the cost first. (Example: OpenF1's live data tier costs €9.90/month — we deliberately avoid it and use FastF1's free live module instead.)
- **No implementation without an approved spec.** Specs live in `/docs` and are written before code. Don't skip ahead to building something that isn't specced yet — flag it and ask instead.
- **No real-money trading without separate, explicit approval.** This applies on top of the spec rule above, not instead of it: an approved spec for Lane C's trading logic authorizes building it, not placing live trades with real funds. That's a distinct go-ahead from the owner, given only after risk controls (position limits, max loss, kill switch) are decided.

## Project philosophy — the lanes

The project is split into independent tracks. Don't conflate them — most confusion in this project happens when Lane B's complexity leaks into Lane A's scope.

- **Lane A — Batch/snapshot predictions.** Pull a fixed set of data once (before a session starts), compute a prediction, done. No live connection, no streaming, no delay/sync problem. This is where the project starts: race winner prediction. See `docs/01-data-pipeline.md`.
- **Lane B — Live/streaming predictions.** A continuous, event-driven pipeline that ingests data while a session is happening and reacts in real time (e.g., overtake probability at a specific corner, seconds before it happens). Modeled conceptually on HFT-style streaming architecture. This is a later phase — see the roadmap for sequencing.
- **Lane C — Automated trading.** Built on top of Lane A (and, for in-race markets, Lane B): takes their prediction output and places YES/NO trades on Polymarket/Kalshi. This is the project's ultimate goal, but it's the newest and least-specced lane — real-time data, order execution, and risk controls are all still open. See the roadmap's Lane C phases for what's decided and what isn't.

## Build philosophy

- **Algo before model.** Every prediction starts as a hand-weighted, rule-based scoring function the owner can reason about and explain. Only after a rule-based baseline exists and there's a real historical dataset to train on do we introduce a trained model (starting with logistic regression, then gradient-boosted trees like XGBoost/LightGBM for tabular data).
- **Prove it live, cheaply, before scaling it up.** Features get validated against real upcoming races before being polished. Getting a real pass/fail result on a real race beats a more "complete" feature that hasn't been tested yet.

## Where to go next

- `docs/00-roadmap.md` — phased plan, current status, what's locked vs. still open
- `docs/01-data-pipeline.md` — data sources, access methods, redundancy strategy (Lane A)
- `docs/02-winner-prediction-algo.md` — the rule-based scoring spec (Phase A1, weights locked)
- `docs/04-outcome-expansion-algo.md` — podium, points, DNF, fastest lap (Phase A4)
- `docs/05-trained-model.md` — the trained winner model (Phase A3, **current focus**). Read `02` first; A3's whole design turns on the fact that `02`'s scorer is already a conditional logit with hand-set coefficients
- `docs/03-live-telemetry-overtakes.md` — Lane B spec. **Not written yet** — the file does not exist; Lane B is conceptual only, see the roadmap's Lane B phases
- `docs/06-weather-ensemble-signal.md` — multi-model weather ensemble spec. **Drafted, not approved, not implemented** — a proposal to strengthen F7's weather input and surface cross-model forecast disagreement as a trading signal
- Lane C (trading bot) has no spec file yet — see the roadmap's Lane C phases for what's decided and what's still open

If a decision you need isn't documented, don't assume — it means it hasn't been locked in yet. Ask.
