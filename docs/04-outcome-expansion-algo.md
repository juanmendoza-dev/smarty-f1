# 04 — Outcome Expansion: Podium, Points, DNF, Fastest Lap (Phase A4)

Status: **weights locked by owner-delegated design, 2026-08-23.** Read `welcome.md`,
`00-roadmap.md`, `01-data-pipeline.md`, and `02-winner-prediction-algo.md` first. This spec
extends `02` — it does not replace it. The win-probability pipeline in `02` is **untouched**;
every number in `02` §9 still reproduces exactly (see §10.5 erratum below for the one caveat,
which is a bugfix to a shared primitive, not a change to `02`'s formulas).

This spec defines four new outputs, market-verified live on 2026-08-23 the same way `01` did for
the winner market: **podium (top 3)**, **points (top 10)**, **DNF probability**, and **fastest
lap**. One of the four — DNF — has **no market on either venue**. That is stated plainly in §2
rather than papered over, per the task's explicit instruction: this project's differentiator is
algo-vs-market comparison, and a comparison that doesn't exist must not be faked.

---

## 1. What this produces

Four new per-driver outputs, each read from the same snapshot `02` already uses, computed by the
same `score.py` with **zero new network calls**:

| Outcome | Shape | Reuses | New machinery |
|---|---|---|---|
| Podium (top 3) | K-of-N, K=3 | `02`'s 8 features, weights, `T` — unchanged | Plackett-Luce simulation |
| Points (top 10) | K-of-N, K=10 | Same as podium | Same simulation, K=10 |
| DNF | independent Bernoulli | `is_classified()`, `shrink_by_n()` | New reliability feature |
| Fastest lap | single-winner, K=1 | 3 of `02`'s 8 features, softmax shape | New 3-feature score, borrowed `T` |

**None of these are trained.** Same philosophy as `02`: hand-set weights, explained, validated
against real data before being called done.

---

## 2. Market verification (live, 2026-08-23)

Verified the same way `01` §6.5/§7.5 verified the winner market: by fetching real events/tickers
and checking `closed`/`status`/`endDate`, not by assuming a name implies a market exists.

| Outcome | Polymarket | Kalshi |
|---|---|---|
| Podium (top 3) | ✅ `f1-{race}-grand-prix-driver-podium-{date}` | ✅ `KXF1RACEPODIUM-{RACE}{YY}` |
| Points (top 10) | ❌ not found (see below) | ✅ `KXF1TOP10-{RACE}{YY}` |
| DNF | ❌ not found | ❌ not found (see below) |
| Fastest lap | ✅ `f1-{race}-grand-prix-driver-fastest-lap-{date}` | ✅ `KXF1FASTLAP-{RACE}{YY}` |

**Points is Kalshi-only.** Searched Polymarket's `f1` tag (40 open events, 2026-08-23) and
public-search for "top 10" / "points finish" — the only points-shaped markets are per-*constructor*
("Which Constructor scores the most points?"), a different question. No per-driver top-10 market
exists on Polymarket. Confirmed live: `KXF1TOP10-DUTGP26` has 22 driver markets summing to
~10.00 (see §7.1); Polymarket has nothing to pair it with. `market_mean` for points therefore
equals Kalshi's normalized probability alone — kept in the schema for interface uniformity with
the other three outcomes, not because it's a genuine cross-venue corroboration signal.

**DNF has no market on either venue.** Checked Polymarket's full `f1`-tag event list (safety car,
red flag, winning margin, H2H, podium, fastest lap, pole — no DNF/retirement-per-race market) and
Kalshi's full series list filtered for `f1`/DNF/finish-shaped titles. The one plausible hit,
**`KXF1RETIRE`**, is a trap worth recording explicitly: `KXF1RETIRE-30VERSTAPPEN` is *"Will Max
Verstappen announce his retirement [from F1, i.e. career-end] before the 2028 season?"* —
career-retirement speculation, not a per-race DNF market. It resolves against an announcement, not
a race result. **Do not build a market comparison against it under the DNF label.** DNF probability
is computed and scored against the real race outcome (Brier vs. reality), with no market column —
exactly the "say so plainly" case the task called out.

**Kalshi opens race markets later than Polymarket does.** Checked 2026-08-23: Polymarket already
has full Italian GP (Monza, round 13, 2026-09-06) markets open for winner/podium/fastest
lap/pole/H2H. Kalshi's `KXF1TOP10` series lists every 2026 race back through round 1 but **no
Monza event yet** — same for `KXF1RACE`, `KXF1RACEPODIUM`, `KXF1FASTLAP`, all showing `DUTGP26` as
the newest open event. This is a real, load-bearing timing difference, not a bug: a snapshot
attempted too far ahead of a race will legitimately find Polymarket live and Kalshi not-yet-open
for the new outcome types. §8.2 specs how the pipeline must handle this (soft-skip per venue, not
a hard abort) for the three *new* market types — the existing winner-market hard-abort-on-missing
behavior from `01`/`02` is unchanged.

**Podium/fastest-lap markets exist two weeks out but are too illiquid to trust yet.** Verified
live 2026-08-23 against Monza (Italian GP, round 13, race date 2026-09-06 — the earliest race
with genuinely pre-race, not-yet-resolved markets): Polymarket's podium market pulled successfully
(not degenerate, `closed: false`) but its raw mids summed to **10.515** for a K=3 market — nowhere
near the ~3.0-3.3 a real market should show. Root cause, confirmed by comparing `volumeNum` across
markets: most Monza podium legs show `volume: None` or low tens/hundreds (Gasly `None`, Alonso
$40, Perez $119, Antonelli $306), against the Dutch GP winner market's thousands ($1,420-$14,390
per leg, checked the same day). With that little real trading, `bestBid`/`bestAsk` spreads span
almost the full `[0,1]` range (Gasly bid 0.01 / ask 0.99), so `(bid+ask)/2` collapses toward 0.5 for
nearly every driver regardless of actual likelihood — a real liquidity gap, not a code bug (the
same pull against the Dutch GP's podium market, while it lasted, produced a sane ~3.0-3.1 sum).
**Consequence:** the pipeline is proven to work end-to-end against real live data (§8.2's soft-fail
correctly handled Kalshi not being open yet for Monza at all), but there is currently no race where
podium/points/fastest-lap prices are simultaneously *not yet resolved* and *liquid enough to
trust*. §3's "outcome-only" rule for the Dutch GP is therefore not a one-race exception — a
genuinely trustworthy pre-race market comparison for these three outcome types needs to wait until
close to a race's lights-out, the same point at which the *winner* market's own liquidity
concentrates (`00-roadmap.md`'s pre-lights-out re-snapshot already exists for exactly this reason).
Flagged again in §11.

**K-of-N markets do not sum to 1.** Verified live: Polymarket's Dutch GP podium market (already
resolved in fact, still `closed: false`) sums to 3.0975 across 21 driver legs; Kalshi's podium
sums to 3.085; Kalshi's top-10 sums to 10.005. `01` §8.4's proportional de-vig was written for a
single-winner market (target sum 1.0) and must be generalized — see §7.1.

---

## 3. The market-comparison validity trap for the Dutch GP — read before running anything

**Every podium/points/fastest-lap price available right now is post-race.** The Dutch GP happened
2026-08-23; the frozen snapshot `data/snapshots/2026-12-race-20260823T031058Z.json` only ever
captured the **winner** market (that's all `01`/`02` specced). There is no pre-race snapshot of
podium/points/fastest-lap prices for this race and there never will be — fetching those markets
*now* would silently encode the answer (NOR/ANT/RUS podium already priced ~0.9975 each). Writing
an "algo vs. market" comparison from that would be exactly the failure `01` §6.5 calls the most
dangerous in the pipeline: confident, well-formed, and wrong.

**Consequence, stated as a rule:** the Dutch GP is **outcome-validation only** for all four new
outcome types — algo vs. the real result, via Brier score, computed from the already-frozen
pre-race snapshot's grid/form/track-history (which are untainted, pulled 2026-08-22 before any of
this existed). No podium/points/fastest-lap market fields are read for this race, ever. The first
genuine algo-vs-market comparison for these outcome types is the **next race this pipeline
snapshots pre-race** — Italian GP, Monza, 2026-09-06 (roadmap Phase B3's date; qualifying hasn't
happened yet as of this spec, so no grid exists yet either). §9 validates against Dutch GP
(outcome-only) and archived 2023 Dutch GP (outcome-only, fully resolved including fastest lap).

---

## 4. Shared primitives, reused unchanged from `02`

Nothing here is new:

- `score_all(algo_snapshot)` (score.py) — unchanged. Still produces `sub_scores`, `effective_weights`
  (after track-flex and any sprint-drop), `raw_scores` (`score_d`), and `p_algo` exactly as `02`
  specifies. Podium/points/fastest-lap read `raw_scores` and `T` off this result rather than
  recomputing anything.
- `T = 0.1168` — the locked win-market softmax temperature (`02` §5.4). Reused as-is for the
  Plackett-Luce strengths (§6) and, provisionally, for fastest lap (§7.4 — flagged as borrowed,
  not independently calibrated).
- `is_classified(status)` — the classification test, now fixed (§10.5). DNF probability's outcome
  label is defined as its exact complement: `dnf == not is_classified(status)`. No separate DNF
  status list to maintain.
- `shrink_by_n(s, n, prior=NEUTRAL)` — generalized (§10.5) to take an explicit prior instead of
  always blending toward 0.5. DNF reuses the same blend table (n≥3 unchanged, n==2 → 65/35,
  n==1 → 40/60, n==0 → prior) with `prior` = the field's own average DNF rate this season, not
  NEUTRAL. **NEUTRAL=0.5 is a nonsense prior for a DNF rate** — half of drivers do not fail to
  finish, so blending an unproven driver toward "50% DNF chance" would be a worse estimate than
  just using the field average. This is the same shrinkage *mechanism* as `02`'s F5/F7, applied to
  a *different kind* of quantity (a rate, not a normalized position score), so the prior must
  change even though the blend weights don't.

---

## 5. DNF probability

### 5.1 Feature: reliability rate

Two rates, computed from `algo_snapshot["form"]["results_by_round"]` (already in every snapshot —
no new endpoint, no new network call):

```
driver_dnf_rate = (# of this driver's entries this season, over all_rounds, where NOT is_classified) / (# entries)
team_dnf_rate   = (# of either car's entries this season, over all_rounds, where NOT is_classified) / (# entries)
```

`all_rounds` (not `recent_rounds`) — same window as F6/F8, because a reliability estimate needs
more races than a 5-race form window gives, and (unlike F4's "current form") stale data is not the
concern here: a car's mechanical failure rate this season doesn't reset every 5 races.

Both rates are shrunk toward the field average DNF rate this season (§4, `shrink_by_n` with
`prior=field_dnf_rate`), using the driver's own entry count `n` for the driver rate and the
constructor's combined entry count `n` for the team rate.

**Edge case — round 1 of a season:** `all_rounds` is empty, so `field_dnf_rate` is undefined for
the entire field. Fall back to **`DEFAULT_DNF_RATE = 0.1253`**, the real 2025 full-season DNF rate
verified live from Jolpica on 2026-08-23 (60 non-classified results out of 479 entries across all
2025 races) — a sourced number, not a guess, in the same spirit as `02` §5.4's pole-conversion
anchor. This only ever fires at round 1; every other round has at least one prior round's data.

### 5.2 Combining

```
F_dnf_d = 0.5 * shrink(driver_dnf_rate_d, n_driver_d, prior=field_dnf_rate)
        + 0.5 * shrink(team_dnf_rate_d,   n_team_d,   prior=field_dnf_rate)

p_dnf_d = F_dnf_d
```

No softmax, no normalization across the field — DNF is not a single-winner competition, each
driver's non-finish is a roughly independent event, and each rate is already a genuine probability
in `[0,1]`. This is a deliberate, stated departure from `02`'s score→softmax pipeline, not an
oversight.

**Even split (driver 0.5 / team 0.5) is a stated assumption, not a measurement**, and here's the
real limitation forcing it: `02026`'s season data collapses every non-crash, non-mechanical,
non-DNS retirement cause into the single literal status `"Retired"` (confirmed live, `/2026/status.json`
— only four status values exist all season: `Finished` 120, `Lapped` 87, `Retired` 50, `Did not
start` 7). There is no field distinguishing "driver put it in the wall" from "engine let go," so
driver-attributable and car-attributable failure rates **cannot be separated from this season's
data**, and 0.5/0.5 is the only defensible default until a data source with cause-level detail is
added (flagged in §11).

### 5.3 What DNF does *not* include (v1 scope)

- **No grid-position term.** A plausible "further back → more first-lap contact risk" effect is
  real in principle but not built here — it would be a genuinely new, unverified hand-guess with
  no anchor, unlike every other feature in this project which either reuses `02`'s already-vetted
  primitives or is sourced from real data (§5.1). Flagged in §11 rather than guessed at.
- **No circuit-attrition term.** Some circuits (street circuits especially) have materially higher
  historical DNF rates. Computable from Jolpica in principle, deferred to keep v1's scope matched
  to the other three outcomes' first-cut simplicity.
- **DNF is not fed into the podium/points simulation.** See §6.3 for why — this is a specific,
  reasoned decision, not an omission.

---

## 6. Podium (top 3) and points (top 10)

### 6.1 Why Plackett-Luce, not a new hand-tuned score→probability mapping

`02`'s win probability already assigns every driver a "win strength"
`w_d = exp((score_d - max(score)) / T)`, and `p_algo_d = w_d / Σw`. The **Plackett-Luce model**
extends this exact same strength to a *full ranking*, not just a winner: draw a random permutation
by repeatedly picking the next finisher from those remaining, each time with probability
proportional to their strength among what's left. Its win marginal is provably identical to
`02`'s softmax — so this isn't a new, separately-tuned model bolted onto podium/points; it's the
same win-strength numbers, asked a different question ("who's in the top 3/10 of the full order"
instead of "who's 1st"). **Zero new weights.** The only new hand-set constants are structural
(K=3, K=10) and computational (simulation size/seed, §6.2), not modeling judgments.

### 6.2 Computing it: exact draw, Monte Carlo estimate

Exact closed-form top-K marginals for a 22-driver Plackett-Luce field require either enumerating
size-K subsets (infeasible: `C(21,9)` for K=10) or a numerical-integration approach (exact, but
adds machinery — quadrature, a Poisson-binomial CDF — that's disproportionate to this project's
"hand-rule, explainable" philosophy for a first cut). Monte Carlo simulation is simpler, exact in
expectation, and easy to explain, at the cost of a small, stated sampling error.

**Method** — the "exponential race" equivalence (a standard, exact way to sample a Plackett-Luce
permutation, not an approximation): for each simulated race, draw `U_d ~ Uniform(0,1)` independently
per driver, compute `key_d = -ln(U_d) / w_d`, and sort ascending. This ordering is an exact draw
from the Plackett-Luce distribution over full rankings (sorting independent
`Exponential(rate=w_d)` variables — smallest first — is mathematically equivalent to Luce's
sequential choice-by-relative-strength process). One simulated race gives one full order; a driver
is "podium" if their sorted rank ≤ 3, "points" if ≤ 10, and — as a free byproduct — "win" if rank
== 1 for the self-consistency check below. Because all three counts come from the **same** sorted
order within a run, `p_win ≤ p_podium ≤ p_points` holds **exactly** by construction, not just in
expectation — a driver counted in the top-1 set is necessarily in the top-3 and top-10 sets of that
same simulated race.

**Locked constants:**

```
SIM_N    = 200_000
SIM_SEED = 20260823
```

`random.Random(SIM_SEED)`, called in a fixed, code-order sequence of driver codes (sorted
alphabetically) — deterministic and reproducible across runs on the same snapshot, same as `T`
being a fixed constant rather than fit per race. Benchmarked at ~1.2s in pure Python (no numpy —
kept out deliberately; `01`'s zero-third-party-dependency-for-network-calls norm extends here to
avoid adding a dependency for something the stdlib does adequately at this field size).

**Stated precision — this is not exact-to-the-digit like `02` §9's closed-form win table.**
Standard error of a Monte Carlo proportion is `sqrt(p(1-p)/N)`; at N=200,000 this is ≤0.11
percentage points for any `p`. Reported probabilities are accurate to **within roughly ±0.3
points (≈3 standard errors)**, not bit-reproducible the way `p_algo` is. This is a deliberate,
documented precision tradeoff — state it, don't hide it.

**Self-consistency assertion (implementation-bug guard):** for every driver, the simulation's own
top-1 empirical frequency must match `02`'s closed-form `p_algo` within 1 percentage point (≈9
standard errors — generous enough not to flake on legitimate sampling noise, tight enough to catch
a real bug, e.g. a strength computed from the wrong `T` or a code-ordering mismatch). This is
free — the top-1 marginal has a known right answer — and it also means the podium/points machinery
is validated by the same reference numbers `02` §9 already locked, every time it runs.

**Reported win probability stays `02`'s closed-form `p_algo`, untouched.** The simulation's own
top-1 count is used *only* for the self-consistency check above, never surfaced as an output — no
reason to trade an exact number for a noisy one when the exact one already exists.

### 6.3 Why DNF is not folded into the simulation

An earlier draft of this design drew an explicit DNF/no-DNF coin flip per driver before running the
ranking simulation, removing DNF'd drivers from the field. **This double-counts DNF risk and was
rejected.** `02` §5.4 calibrated `T` against "the long-run rate at which pole converts to a win" —
a *realized historical rate*, which already includes every case where the pole-sitter retired.
`w_d` therefore already has DNF risk priced into it implicitly, the same way it's priced into every
other win/podium/points-relevant historical rate this project uses. Layering an explicit Bernoulli
DNF draw on top would count the same risk twice, and — worse — it would silently produce a
simulated P(win) that no longer equals `02`'s locked closed-form number, breaking §6.2's
self-consistency check and leaving two different "true" win probabilities with no principled way
to say which one is right.

**Practical cost, stated with real numbers rather than hedged language.** This is not a small
edge-case discount — run against the real Dutch GP snapshot, it produces an internally
*incoherent* pair of numbers for several front-runners: NOR's `p_points = 100.0%` sits right next
to `p_dnf = 27.3%`; PIA is `p_points = 98.6%` / `p_dnf = 27.3%`; VER is `p_points = 97.9%` /
`p_dnf = 25.0%`. Read literally, a driver cannot simultaneously have a >25% chance of not finishing
and a ~100% chance of finishing top-10 — `p_points` should never exceed `1 - p_dnf`, and for these
three it does, by a wide margin. This is `02`'s existing implicit treatment of DNF risk (via `T`'s
historical calibration) made visible rather than a new gap introduced here — the win probability
has the same property, it's just less visually jarring at 3-4% than at 97-100%. **Consequence for
market comparison, stated in §6.4:** any points/podium edge this produces for a heavy favorite
should be read as *this artifact*, not necessarily as the algo disagreeing with the market, until
DNF risk and finishing-order strength are reconciled in one model — revisit together with `02`'s
`T` recalibration once Phase A3 has real outcome data to separate "won despite high DNF risk" from
"won because DNF risk didn't materialize." Logged again in §11, not patched around here.

### 6.4 K-of-N market comparison and Brier

**Normalization (generalizes `01` §8.4):** raw mids for a K-of-N market sum to approximately K, not
1 (verified live, §2). Per driver: `normalized_d = mid_d * K / overround_raw`, where
`overround_raw = Σ mid` over all active driver legs. This makes `overround_raw / K` the venue's
effective overround fraction in the same units as the winner market's (e.g. Polymarket podium
0.9975+... summed to 3.0975, i.e. an effective 3.25% overround per leg — consistent with
Polymarket's winner-market overround of ~3.5%, a sanity check that the generalization is doing the
right thing).

**Degenerate-price assertion, generalized:** `01` §6.5 aborts a winner-market pull if any single
outcome prices at ≥0.999 (looks already-settled). For K-of-N: abort if the **count** of driver legs
priced at ≥0.99 is **≥ K** — a live K-of-N market can plausibly have up to K-1 near-certain legs
(e.g. a dominant favorite's podium spot effectively locked in) without being settled, but K or more
near-certain legs means the market already knows the full top-K, which pre-race is implausible and
post-race is exactly the trap §3 warns about.

**Brier score, different shape from `02`'s — not comparable across outcome types.** `02` §7's
winner Brier is a **sum** over the whole field of `(p_d - outcome_d)^2`, a proper multi-class Brier
score in `[0,2]`. Podium/points are K-of-N: each driver has their own independent binary
outcome ("did they finish top-K"), so the natural score is a **per-driver binary Brier**
`(p_d - outcome_d)^2` in `[0,1]`, and the reported number is the **mean across the field**, not the
sum (a sum would scale with field size and isn't a stable quantity to compare race-to-race).
**Do not compare a podium Brier number to a winner Brier number** — they're different metrics that
happen to share a name and a formula shape.

**A points/podium edge at the top of the grid is expected to carry a systematic artifact, not
just noise, until §6.3's coherence issue is resolved.** A real market prices DNF risk into a
points-lock the algo doesn't: Kalshi's actual Dutch GP top-10 legs for the eventual finishers
priced at 0.995, never 1.000 (verified live, §2) — leaving room for the driver not finishing.
The algo's `p_points` for the same tier runs to 97.9-100.0% regardless of that driver's own
`p_dnf`. Read a positive points/podium edge on a heavy favorite as *this*, first, before reading
it as insight — and note this is exactly the one outcome type (points) with no second venue to
cross-check the edge against (§2, §11).

`outcome_d` for podium/points requires checking classification, not just position number — **Jolpica
assigns a finishing position even to retirees** (verified: 2026 R12 has VER at position 22,
status `Retired`; 2026 R1 has PIA/HUL at positions 21/22, status `Did not start`). The correct
label is:

```
podium_outcome_d = 1 if is_classified(status) and position <= 3  else 0
points_outcome_d = 1 if is_classified(status) and position <= 10 else 0
```

A position-only test would wrongly score a retiree as a podium/points finisher in a
high-attrition race.

---

## 7. Fastest lap

### 7.1 Feature set — deliberately smaller than the other three outcomes

Fastest lap is a single-lap effort, not a race-long placement, and most of `02`'s 8 features have
no clear causal link to it. Grid position, track history, championship standing, and teammate H2H
are dropped rather than force-included:

| # | Feature | Weight (sprint weekend) | Weight (non-sprint) |
|---|---|---|---|
| FL1 | Team/car form (`02` F2, reused as-is) | **0.55** | 0.6471 |
| FL2 | Driver recent form (`02` F4, reused as-is) | **0.30** | 0.3529 |
| FL3 | Sprint result (`02` F3, reused as-is) | **0.15** | dropped |

**Reasoning:** the fastest lap of a race is set almost entirely by which car is fastest this
weekend and which driver is currently in form — not by where they started or how they've done at
this circuit historically. On a sprint weekend, sprint pace is a same-week, same-track single-lap
signal directly relevant to a one-lap effort, so it's included; off a sprint weekend it's dropped
and the remaining two renormalized to sum to 1.0, exactly `02` §5.2's rule.

**Known simplification, stated rather than modeled:** in reality, an early DNF sharply reduces a
driver's chance of holding the fastest lap by race end (most fastest laps come late, on
fresh tyres/low fuel), and fastest lap sometimes goes to a driver with nothing left to race for
(a well-known real pattern, not modeled here). Neither effect is built into `raw_score_fl_d` in
v1 — flagged in §11 rather than force-fitting an unverified correction.

### 7.2 Score and probability

```
raw_score_fl_d = Σ w_f * s_f_d   over FL1-FL3 (using sub_scores already computed by score_all())
p_fastlap_d    = exp((raw_score_fl_d - max) / T_FL) / Σ_e exp((raw_score_fl_e - max) / T_FL)
```

Single-winner shape (exactly one driver sets the fastest lap), so this reuses `02` §5.4's softmax
form exactly, standard numerical-stability subtraction included.

**`T_FL = T = 0.1168`, borrowed from the win-market calibration, not independently anchored.**
An honest placeholder: deriving a real fastest-lap-specific temperature would need the same kind
of calibration `02` §5.4 did (a synthetic scenario anchored to a real long-run conversion rate —
here, "how often does the fastest car/driver combination actually set the fastest lap"), and that
number doesn't exist yet. Borrowing `T` avoids fabricating false precision; flagged in §11 for
replacement once real fastest-lap outcome data accumulates (Phase A3-style).

### 7.3 Market comparison and Brier

Single-winner, sum≈1 on both venues (verified live, §2) — reuses `01` §8.4's proportional de-vig
and `02` §6/§7's comparison/Brier machinery **unchanged, with the outcome label swapped from race
winner to fastest-lap holder**. This is the one new outcome type directly comparable in shape to
`02`'s winner Brier (both single-winner softmax multi-class scores).

### 7.4 Data availability — fastest lap has a real ingest-lag gotcha

Jolpica's race-results response carries fastest lap under each result row's `FastestLap.rank`
(`"1"` for the driver who set it). **Verified live 2026-08-23: this field is `None` for every
driver in round 12's (Dutch GP, today's race) results, while it's fully populated for round 5
(2026) and for the archived 2023 Dutch GP** (`ALO` rank `"1"` despite finishing P2 — confirms
fastest lap is independent of finishing position, as expected). This is an ingest-lag effect, not
a missing field — Jolpica hasn't finished processing lap-time detail for a just-completed race yet.

**Required handling:** the result-extraction code must distinguish "nobody's `FastestLap` is
populated yet" (fail loudly — data isn't ready) from "this driver's `FastestLap` is `None` because
someone else set it" (normal — only one row has rank `"1"`). Silently reporting "no fastest lap
winner found" for the former would look identical to a data error nobody noticed. See §8.1.

---

## 8. Data pipeline extensions

### 8.1 Jolpica — no new endpoint, more of the existing response parsed

`race_results()` (`lib/jolpica.py`) already returns each row's full `FastestLap` sub-object; it
was simply never read. `cast_result_row()` (snapshot.py) gains one field:

```python
"fastest_lap_rank": (r.get("FastestLap") or {}).get("rank")  # nullable string, e.g. "1"
```

A new postrace helper, `find_full_result(season, round_, cache_dir)`, replaces the current
winner-only `find_winner()` (which becomes a one-line wrapper around it, to avoid a second network
call for the same data): returns every driver's `{code, position, status, classified,
fastest_lap_rank}`. It must:

- Assert exactly one classified `position == 1` (existing `find_winner` behavior, preserved).
- **Raise loudly if every row's `fastest_lap_rank` is `None`** (§7.4's ingest-lag case) rather than
  silently reporting no fastest-lap winner. If exactly one row has `fastest_lap_rank == "1"`, that's
  the winner; more than one is a data-integrity failure worth aborting on, same spirit as the
  existing "expected exactly one classified P1" assertion.

### 8.2 Snapshot schema — additive, winner market untouched

`snapshot["markets"]` currently holds the winner market flat at the top level
(`polymarket`/`kalshi`/`market_mean`) — **left exactly as-is**, so the already-committed Dutch GP
snapshot and every existing `score.py`/`postrace.py` code path keep working unmodified. Three new
sibling keys are added, each following the same `{polymarket?, kalshi?, market_mean}` shape:

```
markets.podium       = {polymarket: {...}, kalshi: {...}, market_mean: {...}}
markets.points       = {kalshi: {...}, market_mean: {...}}     # no polymarket key -- §2
markets.fastest_lap  = {polymarket: {...}, kalshi: {...}, market_mean: {...}}
```

**Winner-market pull stays hard-fail** (unchanged `01`/`02` behavior: a missing/stale winner
market aborts the whole snapshot). **The three new pulls are soft-fail, independently, per venue**:
if a market isn't open yet (§2's Kalshi-opens-later finding) or fails resolution, record
`{"status": "unavailable", "reason": "<message>"}` in that slot instead of aborting the run. This
is new functionality layered onto a pipeline that already works for the winner market — its
absence must never block a snapshot that would otherwise succeed. Every consumer of these three
blocks must check for a `"status"` key before assuming the normal `by_code`/`overround` shape is
there.

`lib/polymarket.normalize()` and `lib/kalshi.normalize()` gain a `k=1` parameter (default
preserves exact existing behavior for the winner market): `normalized_d = mid_d * k / overround`.
`resolve_event()`/`resolve_markets()` gain the same `k=1` parameter, used only to select the
degenerate-price check's threshold (§6.4) — at `k=1` the check is byte-for-byte the existing
`01` §6.5 single-outcome-at-0.999 logic, unchanged.

### 8.3 CLI surface

`snapshot.py` gains per-outcome slug/ticker overrides (`--polymarket-podium-slug`,
`--polymarket-fastestlap-slug`, `--kalshi-podium-ticker`, `--kalshi-top10-ticker`,
`--kalshi-fastlap-ticker`), following the exact pattern the existing `--polymarket-slug`/
`--kalshi-event-ticker` args already use — race-specific, not derived, same as today.

---

## 9. Reference run — Dutch GP 2026 (outcome-only, per §3)

Real data: the frozen pre-race snapshot (`02` §9's reference run, unchanged) scored with this
spec's additions, then compared against the real 2026-08-23 result. **A correct implementation
reproduces the `p_win`/`p_dnf`/`p_fastlap` columns exactly** (closed-form) **and the
`p_podium`/`p_points` columns to within ±0.3 percentage points** (Monte Carlo, `SIM_N=200_000`,
`SIM_SEED=20260823` — §6.2's stated precision, not a looser standard for this table specifically).
`p_win` is shown post-§10.5-erratum (36.1%, not `02`'s originally-locked 36.2% — see §10.5 for why
that 0.1pp move is expected and not a bug).

| Driver | p_win | p_podium | p_points | p_dnf | p_fastlap |
|---|---|---|---|---|---|
| NOR | 36.1% | 85.9% | 100.0% | 27.3% | 6.5% |
| RUS | 35.1% | 85.3% | 100.0% | 15.9% | 31.9% |
| ANT | 11.3% | 47.8% | 100.0% | 11.4% | 9.8% |
| LEC | 4.5% | 20.6% | 99.3% | 13.6% | 24.5% |
| HAM | 3.8% | 17.3% | 98.7% | 4.5% | 17.8% |
| PIA | 3.6% | 16.6% | 98.6% | 27.3% | 1.7% |
| VER | 3.2% | 14.6% | 97.9% | 25.0% | 6.1% |
| LAW | 0.7% | 3.1% | 64.0% | 15.9% | 1.2% |
| TSU | 0.2% | 1.1% | 30.0% | 15.1% | 0.2% |
| LIN | 0.2% | 1.2% | 29.3% | 9.1% | 0.1% |

`field_dnf_rate` this season (season-to-date, before this race): **21.1%**. `p_win ≤ p_podium ≤
p_points` holds for every row above, as required (§10 assertion 1) — and NOR/PIA/VER's
`p_points`/`p_dnf` pairs are exactly §6.3's coherence-violation example, visible directly in this
table rather than only described in prose.

### Post-race: algo vs. real outcome (no market columns — this snapshot predates Phase A4's
market pulls, §3)

Real result: podium **NOR / ANT / RUS**. DNF (not classified): **VER, ALB, BOT, OCO, STR, BEA**.
Fastest lap: **unavailable** — Jolpica's `FastestLap` field was `None` for every driver in this
round at time of scoring (§7.4's ingest lag, reproduced live, not simulated — `find_fastest_lap()`
correctly raised `FastestLapNotIngestedError` rather than guessing).

| Outcome | `brier_algo` (mean per-driver binary Brier, §6.4 — not comparable to `02`'s winner Brier) |
|---|---|
| Podium | 0.0198 |
| Points | 0.1598 |
| DNF | 0.1790 |
| Fastest lap | not computed (data unavailable) |

Podium's low Brier reflects the algo correctly favoring NOR/RUS (both >85%) and ANT (47.8%, the
field's clear third-highest) for the three spots that actually podiumed. See `test_phase_a4.py`
for the full assertion suite this table and `02` §9's archived-2023 companion run are both checked
against.

---

## 10. Required assertions

In addition to `02` §8's assertions (all still apply, unchanged):

1. `p_win_d ≤ p_podium_d ≤ p_points_d` for every driver (holds by construction, §6.2 — assert it
   anyway as a regression guard).
2. Simulation self-consistency: `|simulated_top1_d - p_algo_d| < 0.01` for every driver (§6.2).
3. `Σ p_dnf_d` is not asserted against any fixed total — DNF is independent per driver, not a
   distribution over a single race event, so there's no sum-to-N constraint to check.
4. K-of-N market normalization: `Σ normalized_d ≈ K` (±1e-6) per venue, when that venue's market
   was successfully resolved.
5. Fastest-lap probabilities sum to 1.0 (±1e-6) — same tolerance as `02`'s winner softmax.
6. `find_full_result()` raises if `fastest_lap_rank` is `None` for every row (§7.4, §8.1) or if
   more than one row has `fastest_lap_rank == "1"`.

### 10.5 Erratum — classification bug found and fixed while building this spec, not caused by it

While instrumenting DNF probability (which leans directly on `is_classified()`), verification
against live 2026 data (`/2026/status.json`) showed the 2026 season spells a lapped-but-classified
finish as the literal status `"Lapped"` (statusId 143, 87 occurrences season-to-date), not the
older `"+1 Lap"` / `"+2 Laps"` convention `02` §3.4 and the original `is_classified()` assumed.
This is a **pre-existing bug in Phase A1's shared classification primitive**, not something this
spec introduces — it silently scored every 2026 lapped-but-classified finisher as a DNF (0.0) in
F4 (driver form) and F8 (teammate H2H), and in the already-frozen snapshot's baked-in F5/F3
classification flags.

**Fixed:** `is_classified()` now accepts `"Lapped"` alongside `"Finished"` and the `"+"`-prefix
form. `score.py`'s F3/F5/F7 computations were also changed to derive `classified` from the raw
`status` string at score time via `is_classified()`, rather than trusting a pre-baked boolean —
because the already-committed snapshot's F5/F3 `"classified"` flags were computed by the old,
buggy logic, and snapshots are immutable (§8.3 of `01`), this was the only way to fix F5/F3 for the
existing frozen snapshot without mutating it.

**Verified impact — negligible, does not reopen A2's conclusion:**

- `02` §9's top-7 `raw_scores` are **bit-identical** before/after the fix (NOR 0.7856, RUS 0.7824,
  ANT 0.6501, ... unchanged to the displayed precision) — none of the podium contenders had a
  "Lapped" finish in their own recent-form/track-history window. Only midfield/backmarker
  probabilities moved (e.g. LAW 0.5%→0.7%), which very slightly redistributes the softmax
  denominator and shifts the *displayed* leader percentages by 0.1 point (NOR 36.2%→36.1%, RUS
  35.2%→35.1%, ANT 11.4%→11.3%).
- Re-running `postrace.py` on the frozen snapshot (fresh cache, not overwriting the committed
  artifact): `brier_algo` moves from the committed **0.5499** to **0.5504** — a 0.0005 shift.
  `top_pick` (NOR, correct) and `beat_market_mean_on_brier` (still `False`) are unchanged.
- **The committed `2026-12-race-20260823T031058Z-postrace.json` still says 0.5499 and is left
  as-is** — it's the historical audit record of what Phase A2 actually concluded at the time, and
  the discrepancy is small enough that silently regenerating it would trade a real audit trail for
  a cosmetic refresh. **If a future run of `postrace.py` on this same snapshot produces 0.5504
  instead of 0.5499, that is this fix, not a new bug** — recorded here so the next agent doesn't
  waste time re-diagnosing it.

`02`'s locked §9 table itself is **not edited** — it's frozen historical reference output, and the
delta is within its own stated precision. This erratum is the record of the fix.

---

## 11. Open items

1. **Podium/points precision is Monte Carlo, not exact** (§6.2) — ±0.3pp, not bit-reproducible.
   Revisit if an exact top-K marginal algorithm (e.g. numerical integration over the exponential-race
   representation) is worth the added complexity later.
2. **DNF's driver/team 0.5/0.5 split is unmeasurable this season** (§5.2) — 2026 status data has
   no crash-vs-mechanical breakdown. Revisit if Jolpica (or another source) ever exposes cause-level
   detail, or once enough seasons accumulate to fit the split against real outcomes in A3.
3. **DNF has no grid-position or circuit-attrition term** (§5.3) — plausible real effects, not
   built because neither has a sourced anchor the way `02`'s other features do.
4. **Fastest lap's `T_FL` is borrowed from the win market, not independently calibrated** (§7.2) —
   needs its own anchor once real fastest-lap outcome data exists.
5. **Fastest lap doesn't model the "DNF drivers can't set a late fastest lap" or "no-strategic-reason
   driver goes for it" effects** (§7.1) — stated simplifications, not built.
6. **`p_points`/`p_podium` can exceed `1 - p_dnf` — a real coherence violation, not a rounding
   artifact** (§6.3/§6.4). Direct consequence of item 6's design (DNF not fed into the simulation):
   run against the real Dutch GP snapshot, NOR shows `p_points = 100.0%` next to `p_dnf = 27.3%`;
   PIA `p_points = 98.6%` / `p_dnf = 27.3%`; VER `p_points = 97.9%` / `p_dnf = 25.0%`. A driver
   cannot coherently have both. Not patched here because the obvious fix — multiply by
   `(1 - p_dnf)` after the fact — breaks the `p_win ≤ p_podium` assertion (§10 item 1) and
   reopens the double-counting problem §6.3 rejected an explicit DNF draw over. The right fix is a
   single model that prices DNF risk into finishing-order strength once, not two models bolted
   together — that's `02`'s `T` recalibration in Phase A3, not something to improvise now.
   **Until then, treat any points/podium edge for a high-p_dnf favorite as suspect** (§6.4).
7. **The Dutch GP has zero market validation for podium/points/fastest-lap** (§3) — outcome-only.
   The first real algo-vs-market data point for these three outcome types is whatever race this
   pipeline snapshots *before* it happens next — Monza is the earliest candidate but isn't
   guaranteed to be it (depends on when a pre-race snapshot actually gets run).
8. **Points market comparison is single-venue** (§2, §6.4) — Kalshi only. No independent
   corroboration the way the winner/podium/fastest-lap markets have from Polymarket + Kalshi
   agreeing; a large points-market edge could be our algo or could be Kalshi's own book being
   thin, and there's no second venue to tell which.
9. **`KXF1RETIRE`'s presence in Kalshi's series list is worth re-checking periodically** — if
   Kalshi ever launches a genuine per-race DNF market (as opposed to the career-retirement market
   that exists today), §2's "no DNF market" finding needs re-verification, not an assumption that
   it's still true.
10. **Podium/fastest-lap prices two weeks pre-race are too illiquid to trust** (§2) — verified
    against real Monza data, not assumed. No liquidity filter (e.g. a minimum-volume threshold) is
    built for v1; that would be a new modeling decision needing its own justification, and the
    simpler fix is timing — snapshot these markets close to lights-out, not two weeks out. Revisit
    if a genuinely early pre-race comparison is ever wanted for its own sake.
