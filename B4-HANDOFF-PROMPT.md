# Handoff prompt — build Phase B4 (live win-probability), then spec the pit-strategy model

Copy everything below the line into the stronger model. It assumes that model has shell access to
the repo at `/Users/juanmendoza/Desktop/f1 prediction model` (a git repo, branch `main`).

---

## Your role

You are a senior engineer picking up an F1 prediction project. Two tasks, in order:

1. **Build Phase B4's offline layer** — the live win-probability state estimator — implementing
   `docs/09-live-win-probability.md` in full. This was approved for build by the owner on
   2026-09-03 (offline only; live use stays gated).
2. **Then write `docs/12-pit-strategy-model.md`** — a spec for an undercut / pit-loss model,
   built around measurements you run first, not from mechanism.

Work in small commits, push every turn (see workflow rules). Do not build task 2's model — only
spec it.

## The project in one paragraph

A Formula 1 prediction system, personal portfolio project, **zero budget** (no paid APIs/services
without explicit owner approval), **no code without an approved spec** (specs live in `docs/`).
Two lanes: **Lane A** is pre-race snapshot prediction (race winner + podium/points/fastest-lap +
DNF) — built and validated. **Lane B** is live in-race prediction — a live telemetry feed client
(`03`), an offline overtake model (`08`, built), and now this live win-probability layer (`09`).
A trading/quant goal was **removed from this repo** on 2026-09-01 — it becomes a separate project;
Lane B is a standalone live-prediction feature. Do not re-introduce trading concepts.

## Environment

- **Lane B / FastF1 work uses `.venv312`** (Python 3.12): `.venv312/bin/python <script>`. `fastf1`
  is installed *only* there. Run scripts from the repo root.
- Lane A code targets **system `python3` (3.9.6), pure stdlib — no numpy/scipy** (deliberate,
  `05` §7). `09`'s layer is Lane B; you may use `.venv312`, but check whether the existing Lane B
  offline code (`overtake_fit.py`) stayed pure-Python — match it. `08`'s fitter is hand-rolled
  pure Python. Prefer the same unless `09` says otherwise.
- **FastF1 archive cache: `data/cache/fastf1/`** — warm, ~1.3 GB, gitignored. Holds the 12
  completed 2026 rounds (R1–R12) plus a 2023 race. A cold race download is slow; warm is minutes.
- **`data/live/overtakes/training.csv`** — the `08` training matrix, 428k rows, **gitignored**
  (it is F1 timing data and this repo is public — `03` §11.2). Rebuild with
  `.venv312/bin/python overtake_build.py` if missing.
- **`data/live/overtakes/fit_recal.json`** — `08`'s fitted logit + recalibration/domain-gate
  results, committed-adjacent. `08`'s serve constant is **θ = 0.0037**; `09` adds **θ_front =
  0.0105**.
- `data/training/winner.csv` — Lane A's 264-race training matrix, committed.

## Workflow rules (from the owner's global config — follow exactly)

- Commit and push **proactively, in small logical increments** (~10–20 commits for a multi-file
  build, not one big commit). Push each one. A Stop hook blocks ending a turn with uncommitted or
  unpushed changes, so "clean tree, pushed" is the end state of every turn.
- Commit messages read as **written by a human** — no corporate boilerplate, no file-by-file
  lists, no AI-authorship mentions, no `Co-Authored-By`. Minor grammar slips are fine.
- Commits are SSH-signed automatically — don't pass `--no-gpg-sign`.
- End every commit message with this trailer line:
  `Claude-Session: https://claude.ai/code/session_01U2EfrycPw37Q3Adj97zCLo`
- Never force-push `main`. Never squash-merge.
- This project's own discipline: **measurements come before specs** (`08` §2, `docs/11`), and
  **specs are also the decision record** — corrections are made in place with a dated note, not
  silently. Every number quoted in a spec must be re-derivable from a script in `probes/`.

## Reading order (do this before writing any code)

1. `docs/welcome.md` — the lanes, the constraints, the "where to go next" list.
2. `docs/00-roadmap.md` — Lane B section + "Locked decisions" + Phase B4 entry. This is the
   single source of truth for status.
3. `docs/03-live-telemetry-overtakes.md` — **§4.4** (the gate and its 2026-09-03 extension —
   read the amendment blocks), **§7** (the tick contract `WinProbState` reads from — `CarState`,
   `LapCount`, `track_status`, `t_wall`, terminal-state latch in §7.4), **§8** (degraded modes).
4. `docs/08-overtake-model.md` — **all of it**, especially §2.1 (one on-track lead change in 3
   races), §3 (the fitted model), §5.2–5.3 (10 s horizon, episode structure), §7 (calibration
   bar), §11.1 (domain gate, θ), §13 (cold-start handoff: how to rebuild + expected numbers).
5. `docs/09-live-win-probability.md` — **the whole document, twice.** This is your spec. Key
   sections: §2 (the six measurements), §3 (design in one page), §4 (`WinProbState`), §5
   (propagation — §5.2 step structure, §5.3 which pairs `08` speaks for, §5.4 background rate,
   §5.5 the DNF-hazard / `T`-calibration reconciliation via IPF, §5.6 SC/VSC/red, §5.7 pit
   cycles = the limitation), §6 (initialising from Lane A), §7 (Monte Carlo N, standard error,
   `reliable` flag), §8 (the output record + the `03` §4.3 interlock as an enforced import check),
   §9 (replay validation), §10 (four baselines + the `08`-off ablation — pre-registered), §11
   (required assertions), §13 (open items — owner's call, don't decide these), §15 (repro
   commands).
6. `docs/02-winner-prediction-algo.md` §§4–5, §9–10 — the Lane A scorer `09` initialises from; §9
   is the reference field. `docs/05-trained-model.md` §6 — why the trained model lost (relevant:
   `09` §1.3, §6.4.1 — the prior has no measured edge).
7. `docs/04-outcome-expansion-algo.md` §5 (DNF reliability rate `F_dnf_d`) and §6 (the
   Plackett-Luce Monte Carlo `09` §5.5 reuses — `lib/simulate.py`).

## What already exists (reuse, don't reinvent)

- `lib/simulate.py` — `simulate_topk_probabilities(weights_by_code, ks, n, seed)` and
  `exact_top3_probabilities(...)`. The exponential-race Plackett-Luce draw. `09`'s forward sim is
  "this, one level up — simulate the evolution of the order, not one draw."
- `lib/overtakes.py` — the `08` labeller (`find_passes`, `find_episodes`, `pit_windows`,
  `position_stream`, `interval_stream`). `overtake_fit.py` — the fitted logit + `predict`,
  `platt_fit`, `calibration`, `recalibration_pass`.
- `lib/overtake_features.py` — builds the `08` feature vector. `09` §5.3 needs this computed from
  a single tick.
- `lib/livetiming_tick.py` / `lib/livetiming_client.py` — the `03` §7 tick contract and replay.
  `09`'s layer consumes ticks and nothing else (`03` §7).
- `score.py` — `score_all(snapshot)` produces Lane A's per-driver `p_algo` and raw scores via the
  locked weights. `09` §6 initialises from `w_d = exp((score_d − max score) / T)`, `T = 0.1168`.
- `probes/09_race_dynamics.py`, `probes/09_leadchange_attribution.py`, `probes/09_domain_bands.py`,
  `probes/09_theta_front.py` — the measurement scripts behind `09` §2. `probes/README.md` has
  expected output for each.
- `probes/12_pit_loss.py` — the first pit-strategy measurement pass (already committed).

## Measurements already in hand (from `09` §2 and the pit probe)

- **Lead changes:** P1 changes hands ~4×/race (48 over 12 races). **71% pit-attributable**, 2%
  retirement, ≤27% on-track. `08` (on-track passes) is the *fourth* biggest mover of P(win).
- **Leader-conversion ladder:** leader wins 120/120 inside 10 laps to go. Condition on race
  *progress* (fraction), never on absolute laps remaining (composition artifact).
- **Background adjacent-pair swap rate:** ~6%/lap front, ~7–8% midfield, shallow gradient. This
  is the propagation model outside `08`'s 10 s window.
- **`08`'s front-of-field domain is thin:** 32 in-domain positives at P1–P3 across 8 test races,
  68% retention (vs 88–95% elsewhere), worst calibration ratio 2.33 → needs θ_front = 0.0105,
  which recovers a PASS (worst 1.31) at the cost of 23% of front overtakes.
- **`08`'s average contribution to P(win) at the front ≈ 0.4 points** vs a 1-point market tick and
  a 0.5-point Monte Carlo SE. Rises to ~1.0 above θ_front, ~1.9 on the strongest third. The
  signal is real and small — `09` §10's ablation exists to check it survives MC noise at all.
- **Retirements:** 50 over 12 races (4.2/race), mildly front-loaded (34% in first quarter).
  `Status == "Retired"` only — `"Lapped"` is a finish (a correction already made; see `09` §15).
- **Pit cycles:** 34.5% of race-laps carry ≥1 stop. Naive "silence the layer during a pit cycle"
  would blank a third of the race.
- **Pit probe (`probes/12_pit_loss.py`):** pit δ pooled median **23.0 s** (IQR 20.6–26.3),
  per-circuit 19–30 s over 306 stops. Eventual top-6 move a net ~0 through the pit phase
  (|move|≥2 in 27%). Only **38%** of pit-attributable P1 changes stuck to the flag. Undercut
  succeeds **15%** of 154 clean attempts — barely above background over the same span. Early
  read: the pit-*timing* effect in 2026 data looks smaller than the mechanism suggests.

## Hard constraints — do not violate

- **Offline only.** You may replay archived races. You may NOT open a network connection to F1's
  live timing feed, or run anything "live." That stays gated on B1 (an unrun broadcast-delay
  measurement). `03` §4.4.
- **`03` §4.3 interlock:** nothing in Lane B may import from / be imported by a trading component.
  `09` §8.2 wants this as an enforced test over the module graph — implement it.
- **Do not change `02`'s locked weights or `T`.** `09` consumes them; §5.5 reconciles a strength
  vector *derived* from them (IPF) without altering the source.
- **Do not decide `09` §13's open items** (freeze vs. live pace update, funding a pit model,
  whether the layer emits podium/top-10, the 5 s horizon). Those are the owner's. Build v1 as
  specced: driver strength frozen, no explicit pit model, winner only.
- Zero budget. No new dependency without asking. Match the existing code's pure-Python-where-
  possible style.
- Every number you put in a doc must be reproducible from a committed `probes/` script.

## Deliverables — Phase B4

Follow `09` exactly; broadly:

1. The state estimator module (name it per repo convention, e.g. `lib/winprob.py`) — builds
   `WinProbState` from a tick (`09` §4), runs the Monte Carlo forward simulation (`09` §5), emits
   the `09` §8 output record with the `reliable` flag and reason codes.
2. The offline **per-race reconciliation** (`09` §5.5): IPF of Lane A strengths against the
   explicit two-segment DNF hazard, once per race at N ≥ 200,000, cached by `prior_id`. The
   **t = 0 identity** (layer reproduces `02`'s `p_algo` at lights-out within MC tolerance) is an
   acceptance assertion.
3. The background per-lap transition model (`09` §5.4), fitted **race-forward** (races 1..n →
   race n+1), conditioned on progress + position band + circuit-via-`m`. Report the per-circuit
   residual against `02` §5.1's `m` (pays a debt flagged in `02` §10 item 1).
4. A **replay/validation harness** (`09` §9) over the 8 scoreable archived races, producing:
   - `09` §10's four baselines (Lane A static number; position-only ladder; **the `08`-off
     ablation** — the single most important number; the market as colour on the 2026 Dutch GP
     only).
   - Pooled + per-race log-loss with the block-bootstrap CI (`09` §9.3).
   - The realised `reliable = False` fraction and the pit-cycle suppression fraction (`09` §5.7
     requires this measured and reported — near 5% = fine, near 34.5% = headline).
5. `09` §11's required assertions as tests. Match `test_overtakes.py` / `test_livetiming.py` style.
6. Update `docs/09` in place: drop "not built", record what the validation found (including if it
   found the layer adds nothing — `09` §1.3 and §10 pre-register that as a legitimate result),
   and update `00-roadmap.md` Phase B4 + `probes/README.md`.

**Success is pre-registered in `09` §10:** the layer succeeds only if it beats *both* the static
Lane A number *and* the position-only ladder on pooled log-loss, and wins ≥6 of 8 races.
`08` earns its place only if the ablation is measurably worse than the full layer beyond the
bootstrap width. Report honestly either way.

## Deliverable — `docs/12-pit-strategy-model.md` (after B4)

`09` §5.7 and §13 item 2 name this as "the most valuable single addition" to the layer. Spec it,
don't build it. First run more measurements (extend `probes/12_pit_loss.py` or add
`probes/12b_*.py`):

- Refine **δ per circuit** — the current probe has some SC-lap contamination in a few circuits'
  IQRs; tighten the green-lap filter, report δ as median + robust spread per circuit, and check
  it against published pit-loss figures.
- The **pit-cycle suppression fraction** from the B4 replay (you'll have it by then).
- **Is stop timing predictable live at all?** A model that must predict *when* a car will stop is
  far harder than one that projects a stop already in progress (via `CarState.in_pit`). Measure
  before committing the spec to either.
- **Undercut/overcut effect size** beyond background, properly normalised for the multi-lap span.

Then write the spec: scope (v1 = project a stop-in-progress onto post-cycle track position using
δ_circuit; predicting stop timing is likely out of v1), the offline measurements, how it slots
into `09` §5.7 as a replacement for the background-rate treatment, required assertions, and open
items. Cross-reference `docs/11`'s pit-execution null (that was *race-aggregate pre-race crew
speed* — a different claim from a live pit-cycle model; `11` already says "reopen if" and points
here). Keep it out of `09` — §5.7 explicitly names folding it in as scope creep.

## First steps

1. Read the docs in the order above. `git log --oneline -30` for recent context (esp. the
   2026-09-01 trading-strip commit `d1c8d11` and the 2026-09-03 B4 approval).
2. Re-run the probes to confirm the environment: `.venv312/bin/python probes/09_race_dynamics.py`
   and `probes/12_pit_loss.py` should reproduce the numbers above.
3. Draft the B4 module skeleton + the t=0 identity test first — that assertion is the backbone.
4. Commit + push. Keep going in small increments.

There is a `MONZA-2026-RUNBOOK.md` in the repo root for a separate, calendar-driven task (live
telemetry acceptance run at Monza FP1, ~2026-09-04, and a lights-out snapshot Sunday). That is not
your job unless the owner says so, but it explains what else is happening this week.
