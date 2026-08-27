# `docs/quant/` — the quantitative / trading lane

Everything about turning this project's predictions into money — strategy, edge measurement,
position sizing, order execution, risk controls, and (later) market making — lives here. The
prediction models themselves stay in `/docs` (`02`, `04`, `05`, `08`); this folder is the layer
that sits on top of them.

## Relationship to the rest of the project

- **`../07-lane-c-trading-feasibility.md`** is the feasibility memo that led here. Read it first.
  Its headline finding: the blocker is the *edge*, not the APIs — no measured edge exists in any
  market yet — and the buildable first step is an edge-measurement + paper-trading harness. This
  folder is the build spec that follows from that memo. `07` is not moved (it's referenced from
  the roadmap, `welcome.md`, and `03`); it stays the feasibility record, this folder is the plan.
- **`07` §11** is the live market-structure survey (which markets exist on which venue, real
  book depth, spreads). The spec here builds directly on those numbers.
- **Lane A** (`02` winner, `04` podium/points/DNF/fastest-lap) is the signal source. The quant
  lane does not build new models — it consumes Lane A's probability output.
- **Lane B** (`03` live telemetry) is the signal source for the *in-race* version, which is a
  later phase (see the MM / in-race section of the spec).

## Hard constraints (inherited from `welcome.md`, restated because they bite here)

- **Zero budget.** No paid API, tier, or data feed without explicit approval.
- **No implementation without an approved spec.** The specs in this folder are written before
  code, same as `/docs`.
- **No real-money trading without separate, explicit approval** — on top of an approved spec,
  and only after risk controls (position limits, max loss, kill switch) are built and the
  edge-measurement phase has actually produced evidence of an edge.

## Contents

- `00-directional-trading-spec.md` — the first spec. Paper-trading + edge measurement on
  per-race prediction markets (winner, podium, points/top-N), both venues, with live execution
  and a market-making extension as gated later phases.

## Naming

Same convention as `/docs`: `NN-topic.md`, `README.md` unnumbered as the index.
