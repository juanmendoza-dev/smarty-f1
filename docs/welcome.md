# Welcome, Agent

You've been brought onto the **F1 Prediction Model** project. This document is your onboarding — read it before touching any other spec in `/docs`.

## What this project is

A Formula 1 prediction system that forecasts race outcomes — starting with **race winner**, later expanding to podium, points finishers, DNFs, and eventually **live in-race predictions** (overtakes, at the corner level, while a session is happening).

This is a **personal portfolio project**, built to be shown on LinkedIn. The owner has never trained an ML model before — this project is also their learning path into applied ML, not just a finished product. Treat explanations and specs accordingly: correctness and clarity matter more than brevity when the owner is the audience, but specs written for other agents should be precise and unambiguous.

## The core differentiator

This isn't just "predict the winner." The interesting part is **triangulating multiple sources of truth**:

- **Our own algorithm's prediction** (rule-based today, trained model later)
- **Polymarket odds** (prediction market, crowd-sourced)
- **Kalshi odds** (regulated US prediction market)

The output isn't just a single probability — it's a comparison: does our algo agree with the market, where does it diverge, and over time, is our algo actually better calibrated than the crowd? That comparison *is* the headline feature.

## Hard constraints — do not violate

- **Zero budget.** This project uses free tiers and free/open data sources only. Do not introduce a paid API, paid tier, or paid service without the owner explicitly approving the cost first. (Example: OpenF1's live data tier costs €9.90/month — we deliberately avoid it and use FastF1's free live module instead.)
- **No implementation without an approved spec.** Specs live in `/docs` and are written before code. Don't skip ahead to building something that isn't specced yet — flag it and ask instead.

## Project philosophy — the two lanes

The project is split into two independent tracks. Don't conflate them — most confusion in this project happens when Lane B's complexity leaks into Lane A's scope.

- **Lane A — Batch/snapshot predictions.** Pull a fixed set of data once (before a session starts), compute a prediction, done. No live connection, no streaming, no delay/sync problem. This is where the project starts: race winner prediction. See `docs/01-data-pipeline.md`.
- **Lane B — Live/streaming predictions.** A continuous, event-driven pipeline that ingests data while a session is happening and reacts in real time (e.g., overtake probability at a specific corner, seconds before it happens). Modeled conceptually on HFT-style streaming architecture. This is a later phase — see the roadmap for sequencing.

## Build philosophy

- **Algo before model.** Every prediction starts as a hand-weighted, rule-based scoring function the owner can reason about and explain. Only after a rule-based baseline exists and there's a real historical dataset to train on do we introduce a trained model (starting with logistic regression, then gradient-boosted trees like XGBoost/LightGBM for tabular data).
- **Prove it live, cheaply, before scaling it up.** Features get validated against real upcoming races before being polished. Getting a real pass/fail result on a real race beats a more "complete" feature that hasn't been tested yet.

## Where to go next

- `docs/00-roadmap.md` — phased plan, current status, what's locked vs. still open
- `docs/01-data-pipeline.md` — data sources, access methods, redundancy strategy (Lane A)
- `docs/02-winner-prediction-algo.md` — the rule-based scoring spec (written once features/weights are defined)
- `docs/03-live-telemetry-overtakes.md` — Lane B spec (stub for now, future phase)

If a decision you need isn't documented, don't assume — it means it hasn't been locked in yet. Ask.
