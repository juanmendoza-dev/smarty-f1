# Mega prompt — build `12` Pit-Strategy Model

Paste everything below into a fresh session with the strongest available model, run from the repo root.

---

You are implementing docs/12-pit-strategy-model.md in the f1-prediction-model repo — but this spec
is explicitly marked "not approved" as of the writing of this prompt. Your FIRST action, before
touching any code, is to confirm with whoever is running this session that the owner has explicitly
approved building this model (the project's welcome.md rule: "no implementation without an approved
spec" — this is not a formality here, it's a standing hard rule for this project). If you cannot get
that confirmation, stop and report back instead of proceeding.

Read the full spec (docs/12-pit-strategy-model.md) yourself before starting — this prompt condenses
it but the doc is authoritative and longer than what's reproduced here. Also read, in this order:
docs/09-live-win-probability.md (§2.1, §2.6, §5.4, §5.7, §10's results), docs/08-overtake-model.md
§2.1, and docs/11-features-tested-and-rejected.md's pit-execution entry. And docs/welcome.md /
docs/00-roadmap.md for project-wide conventions.

## What this model is, precisely — read this twice before coding

This is a PROJECTION, not a prediction. Given that a car is observed IN the pit lane right now
(CarState.in_pit is an observation, not something inferred), the model computes where it will
rejoin relative to the field, using one measured constant (δ, the per-circuit time cost of a stop)
and the gaps the live tick already carries. It slots into 09's Monte Carlo state estimator as a
track-position correction, replacing "suppress the estimate" with "correct the order and keep
publishing" for the duration of the pit cycle only.

**This model does not predict when a car will stop.** That was measured and explicitly rejected as
in-scope (§2.4): stint-age hazard only swings by ~5x on a 3.7% base rate and is non-monotone — not
enough signal, and predicting stop timing would need tyre compound / stint plan / fuel data this
project doesn't have. If you find yourself building anything that guesses *when* a car will pit,
you have gone out of scope — stop.

## Measurements already done — treat these as ground truth, do not re-derive from scratch

Reproducible via `.venv312/bin/python probes/12_pit_loss.py` and
`.venv312/bin/python probes/12b_pit_projection.py`, run from repo root; .venv312 is required, fastf1
lives only there per 08 §13.2; fastf1 cache at data/cache/fastf1/, should already be warm.

- §2.1: δ (pit-loss time) pooled median = 22.8s, MAD 3.7s, over 286 stops / 12 archived 2026 rounds,
  using a tightened green-lap filter (1.45x in/out cap, 1.15x baseline). Per-circuit table exists in
  the spec — USE IT, don't refit from scratch. Circuits with <10 measured stops (China, n=4) or no
  measured δ at all fall back to the pooled 22.8s and get flagged as such.
- §2.3 (**the most important correction in the doc**, read the correction box in §2.3 carefully): a
  raw per-lap adjacent-swap rate over-disperses vs. reality by ~1.6x if you treat every swap as
  permanent — BUT 09's actual simulator does NOT do that (it shrinks toward band, removes
  retirement-driven changes, applies a strength tilt) and measures at 0.99 vs the archive, i.e.
  correctly calibrated already. Do not "fix" something that measurement shows isn't broken in 09
  itself. The 1.6x figure is a property of the RAW rate and a warning for how you must NOT reuse q
  naively — see the double-count rule below.
- §2.5: undercut is a real but modest effect — 14.9% success in 154 clean attempts vs a matched
  background (adjacent pairs, same span, NEITHER car stopped) of 9.9% at the undercut's mean 4.7-lap
  span. Read this as roughly a 1.5x lift, not more — n is small (154 attempts across 12 races,
  clustered, not independent). This model does NOT model the undercut as a decision — it only
  projects a stop already in progress (§2.5's effect is background evidence, not a feature to build).
- §3: 09's B4 layer is `reliable=False` on 33.3% of checkpoints, and 28.5 of those 33.3 points come
  from exactly one rule — pit_offset > 0 among the top three. That 28.5% is the number this model
  exists to shrink. This is the funding argument, not a target you need to hit a specific number on.

## The four-component design (§4–§5 — build in this dependency order)

1. **δ_circuit as a served constant.** Fit offline from the archive exactly as §2.1 measured it,
   stored as a per-circuit lookup table, refit only when the archive grows — NEVER computed live
   from the race in progress (same rule 08 §11.1 applies to θ: a live consumer sees one tick at a
   time and cannot take a median over a race that hasn't finished). Missing/thin (<10 stops)
   circuits fall back to pooled 22.8s and get flagged.

2. **A pit-state machine** on top of 03 §7.1's CarState.in_pit / pit_out fields (see
   lib/livetiming_tick.py CarState class — in_pit and pit_out are already parsed fields;
   lib/livetiming_parse.py lines ~205-217 is where they come off the wire). States:
   `RUNNING -> ENTERING -> IN_PIT -> OUT_LAP -> RUNNING`. Use the SAME latch discipline 03 §7.4
   uses for terminal states elsewhere in this codebase — a transition that un-happens (flickers
   back) is a parsing artifact, not a real state change, and must not be allowed to run backwards
   within one cycle.

3. **Rejoin projection** — pure arithmetic on quantities the tick already carries, nothing modeled:

   ```
   projected_gap_after(c) = gap_leader(c) + (δ_circuit − time_already_elapsed_in_cycle)
   ```

   Projected position = rank among still-circulating cars by projected gap. This projection is
   PROVISIONAL — the instant pit_out fires and a real numeric gap arrives, the observed value
   replaces the projection on that tick with no blending, no carry-over (this is a required
   assertion, #3 below).

4. **Corrected order** handed to 09's simulator (lib/winprob.py / lib/winprob_sim.py — inspect these
   files to see how 09's state estimator currently consumes track position) in place of raw track
   position, for the duration of the pit cycle only.

## Mandatory, non-optional side effect: the double-count fix

The moment this model is active, 09 §5.4's background swap rate `q` (in lib/winprob_background.py
or wherever it's computed — locate it) MUST be refit with pit-cycle swaps removed. If you ship this
model without refitting q, pit cycles get counted twice — once by this model's explicit projection,
once by q still implicitly containing pit-cycle swap statistics. This is the same double-count 04
§6.3 already rejected elsewhere in this project and 09 §5.4 already handles for retirements — treat
it as the same category of bug, not a nice-to-have. Required assertion #4 below exists specifically
to make this fail LOUDLY (not silently produce a plausible-looking wrong number) if someone runs
this model against an un-refit q.

**Be honest about what this refit does and doesn't fix:** it should move the raw net-at-5-laps /
compounded-rate ratio from 0.61 toward 1.0 (§6 outcome 2 — this is the sharpest, most falsifiable
test in the spec). It should NOT be expected to fix 09's separate, already-diagnosed late-race
leader-pair error (confined to the closing quarter, 9 §10.2, owned by 09 §13 item 6 — a finer front
band, unrelated to pit strategy). Do not claim credit for fixing that if your pooled log-loss number
happens to improve in the last two deciles — check §6 outcome 3 closely and attribute correctly.

## What must be refused, not approximated (§5.3 — hard "return no projection" cases)

- **A stop under SC/VSC** (track_status != 1): the four noisiest circuits in §2.1's table (Canadian,
  Australian, Belgian, British — MAD 5-8s vs ~2s elsewhere) are exactly the caution-heavy ones; a
  compressed field makes δ a genuinely different quantity, not just noisier. Refuse, don't widen
  the error bar.
- **gap_leader in the "LAP n" form** (lapped-car notation) — 08 §13.6 item 3 already found 72% of
  those rows are actually at Position 1 and its semantics are UNVERIFIED. Drop it, never coerce it
  to a number.
- **A red-flag stop** — 03 §9.5's session-change handling governs; discard pit state and re-derive
  from the first tick after the restart.

## Required assertions

Via `lib.invariants.require` — see lib/invariants.py — NEVER a bare `assert`; this project's stated
rule is that anything guarding data, not just programmer logic, must raise unconditionally even
under `python -O`.

1. **δ=0 is the identity:** with δ_circuit=0, projected order == observed order, field for field.
2. **A projection never gains a place for free:** a car's projected position is never ahead of where
   its own pre-stop gap would place it.
3. **The projection is provisional:** the instant pit_out fires with a numeric gap, that value
   replaces the projection on that tick — no blending, no carry-over across ticks.
4. **No pit-cycle double count:** with this model active, 09 §5.4's background rate must be the
   refit one — scoring against a q that still contains pit swaps must fail LOUDLY, not silently
   produce a plausible wrong number.
5. **Nothing is projected under caution, on a degraded tick, or from a non-numeric gap** (§5.3, above).
6. **Latch discipline:** the pit state machine never runs backwards within one cycle.
7. **09's §11 assertions all still hold** with this model active — in particular the t=0 identity,
   since at lights-out no car is in the pit lane and this model must not disturb that baseline case.

## Validation protocol — pre-registered, do not deviate or cherry-pick after seeing results

Same corpus/folds/discipline as 09 §9: the 8 scoreable races R5–R12 (check which winprob_*.py
scripts define this fold split — likely winprob_fit.py / winprob_validate.py), race-forward fits,
checkpoints at lap boundaries, block-bootstrap over WHOLE races (not individual laps — 09 §9.3's
power warning about small-sample independence applies here just as strongly, don't relax it).
Reproduce §3's baseline numbers first with:

```
.venv312/bin/python winprob_fit.py       (~23 min)
.venv312/bin/python winprob_validate.py  (~12 min)
```

Confirm you get the same 28.5%/33.3% suppression numbers §3 reports BEFORE building anything — if
your baseline doesn't match, your environment/fold-split is wrong and you must fix that first.

**Three outcomes are pre-registered** (stated before the model exists, per this project's
discipline of not moving the goalposts after seeing numbers):

1. **Coverage:** the pit_offset suppression fraction must fall from 28.5%. If it doesn't fall by at
   least HALF, report that plainly — the projection isn't doing the job it was funded for.
2. **The rate prediction:** net-at-5-laps/compounded ratio should move from 0.61 toward 1.0 once q
   is refit with pit swaps removed. This is a prediction about a quantity already measured and can
   fail cleanly — treat a failure here as a real, reportable result, not something to explain away.
3. **Mid-race scoring improves, NOT late-race:** 09's layer's biggest margin over the position-only
   ladder is already around half-distance (0.96 vs 1.46 log-loss) — that's the pit window, and
   that's where this model should show its improvement. The final two deciles (where the ladder
   currently beats the layer) are explicitly NOT this model's target — attribute any change there
   to the front-band fix (09 §13 item 6), not to this model.

**And one pre-registered null result to watch for:** 09's layer already meets its success criteria
WITHOUT this model. If this model improves coverage but does NOT improve pooled log-loss beyond the
bootstrap confidence width, the correct, honest report is "this buys availability, not accuracy" —
write that up as a real finding if that's what you get, don't strain to claim more.

## Explicitly out of scope — do not build

- Predicting stop timing (§2.4 — measured, not supported by this corpus).
- Tyre degradation, compound, stint planning, fuel — none are in the tick contract, none measured.
- The undercut as a decision/policy model (§2.5's effect is descriptive background only).
- Pit-crew execution quality as a feature (11's null stands, this document does not reopen it).
- Anything LIVE. This is strictly an offline model against the archive, same terms 08 and 09 were
  built and validated under (03 §4.4 as amended still governs — live use of anything in this chain
  remains gated on the still-unrun B1 delay measurement).
- Any change to 02's locked weights or T, or to 09's reconciliation logic beyond the q-refit above.

## Four owner-level decisions this spec leaves open (§9) — do not decide these yourself, surface them

1. Is δ per-circuit, or per-circuit-and-season? (286 stops/12 races supports per-circuit only, not
   a trend — regs change season to season.)
2. Does a caution-time δ get measured separately eventually, or does the model keep permanently
   refusing to project under caution? (Refusing is cheap/safe now; measuring is a 5th probe script,
   future work, not yours to add unasked.)
3. Does this model also correct 09 §5.7's published pit_offset diagnostic field, or only the
   internal order used for scoring? Something downstream may already read that field raw.

If you hit any of these mid-build, stop and ask rather than picking a default silently.

## Deliverable / how to report back

- Confirmation of the approval check from the top of this prompt.
- Reproduced §3 baseline numbers, matching before you built anything.
- The new model's code, with the 7 required assertions wired via lib.invariants.require and a test
  file (test_pit_strategy.py or similar, matching this repo's test_*.py convention) exercising all
  7, plus the "refuse under caution / non-numeric gap / red flag" cases explicitly.
- The three pre-registered validation outcomes' actual numbers, reported honestly even if #1 or #2
  come back negative — this project's specs explicitly value a clean negative result over a
  strained positive one (05 §6.4.1 is the precedent: a trained model that lost was reported as
  losing, not massaged).
- Updates to docs/09-live-win-probability.md §5.7 and §5.4 per §10 of the 12 spec ("what this changes
  in other docs") — reflect the refit q and the narrowed reliable=False condition there, dated.
- Commit in small logical increments (δ table + fitting script; pit state machine; rejoin
  projection; q refit; simulator wiring; assertions; tests; doc updates) — not one giant commit.
