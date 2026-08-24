# 06 — Multi-Model Weather Ensemble Signal

Status: **drafted 2026-08-23, not approved, not implemented.** This is a spec only — no code
should be written against it until the owner locks it in, per `welcome.md`'s "no implementation
without an approved spec" rule. Read `welcome.md` and `01-data-pipeline.md` §5 first.

---

## 1. Use case — why this exists

**This section was wrong in the first draft and is rewritten (2026-08-24).** It said the 2026
Dutch GP was priced dry by Polymarket/Kalshi and that "it rained." Neither half was sourced, and
checking them changed what this spec is for.

### 1.1 What the record actually says about Zandvoort

`00-roadmap.md`'s open items and `02` §10.4 both call the Dutch GP **dry** — F7's wet branch has
still never executed on a real race. The race ran its full 72 laps with the top seven on the lead
lap. Open-Meteo's observation archive returns **0.0–0.1 mm** across the race window. (Provisional:
that was pulled one day post-race and `01` §5.4 warns the archive lags several days. The pattern
holds on races well past the lag, so the reading is believable, but treat the exact figure as
unconfirmed.) In racing terms, it did not rain.

And yet `snapshot.py:288` defines a wet race as `max mm > 0.0` over the race window — so 0.1 mm
clears the bar. **The same race is wet in the code and dry in the prose.** That gap is not a
footnote; §5.4 below shows it decides which aggregate this spec should feed F7, so it is the first
thing an owner has to rule on.

The claim that the market under-priced rain is **withdrawn**. Neither venue exposes historical
odds, the one snapshot that exists (`p_max: 37`, `weather_dormant: true`) records our own forecast
rather than the market's implied one, and on the evidence above there was no meaningful rain to
under-price. Nothing in this document rests on it any more.

### 1.2 What does motivate this spec

Replayed across 44 races (§5), the single blended call the pipeline makes today has two measurable
weaknesses:

1. **It misses wet races.** At the racing-relevant threshold — observed ≥ 0.5 mm — today's call
   fires F7's gate on 5 of 8 wet races, at 45% precision. A four-model mean gets 6 of 8 at 60%.
   Modest, but it is a real gain on the exact quantity F7 keys off.
2. **It cannot tell you when to distrust it.** This is the bigger one. A single number carries no
   indication of its own reliability. Across those 44 races, every one of the blended gate's nine
   errors fell in the 43% of races where the four models disagreed by ≥ 15 pp — and in the 25
   races where they agreed, it was **never** wrong (§5.3).

That second finding is what this document is now built on. The value of querying several models is
less that their average is a better forecast, and more that their **disagreement marks the races
where any single forecast — ours or the crowd's — is unreliable.**

## 2. What "super advanced" means here — and what it doesn't

The request that prompted this was for "a super advanced open-source weather model." Two paths
were considered:

- **Self-host a research-grade ML weather model** (GraphCast, Pangu-Weather, Aurora — all
  open-source on GitHub). Rejected for this project: they forecast on a global grid from ERA5
  initial conditions, need GPU/TPU compute to run, and their edge over operational models is at
  multi-day global lead times — not obviously better at a single circuit's rain-in-the-next-6-hours
  question that matters here. Self-hosting one is a real ML infrastructure project in its own
  right, orthogonal to this one, and violates zero-budget the moment it needs rented GPU time.
- **Query multiple existing operational models through one free API and treat their spread as the
  signal.** This is what's specced below. Open-Meteo is not itself a model — it's a free,
  keyless aggregator in front of several state-of-the-art operational models, including ECMWF's
  IFS, generally the strongest of the global deterministic models. The "advanced model" the
  project gets access to for free is ECMWF; the ensemble adds cross-model agreement on top of it.

  The first draft justified this by saying ECMWF is strongest over Europe, "where every 2026
  circuit used by this project sits." **That is false** — `02` §5.1's own multiplier table lists
  Singapore, Suzuka, Austin, Melbourne, Baku, Jeddah and Interlagos. The calendar is global, so a
  Europe-specific argument doesn't carry, and the reason to prefer ECMWF has to be its
  general-purpose skill rather than a regional one. This matters practically: it rules out picking
  regional models (ICON-EU, HRRR) that would go blind for half the season.

**Decision this spec proposes:** no self-hosted model. Multi-model ensemble via Open-Meteo only.

## 3. Data source

Same base URL as `01-data-pipeline.md` §5.2 (`https://api.open-meteo.com/v1/forecast`), no new
auth. The only change is the `models=` parameter, which the current pipeline does not set — so it
receives the provider's own blended default rather than a named model list (`lib/openmeteo.py`
builds the call without it).

Proposed models to request explicitly:

| Model | Provider | Why included |
|---|---|---|
| `ecmwf_ifs025` | ECMWF | Strongest general-purpose global deterministic model |
| `gfs_seamless` | NOAA | Independent modeling group and physics, US-run, different bias profile |
| `icon_seamless` | DWD (Germany) | Independent European centre, competitive with ECMWF at short range |
| `gem_seamless` | ECCC (Canada) | Third independent centre, not built on shared European NWP lineage |

### 3.1 Verified live (2026-08-24)

Everything below was called before being written down, per this project's own bar (`01` opens by
stating every source was verified against production).

```
GET api.open-meteo.com/v1/forecast
    ?latitude=52.3888&longitude=4.54092
    &hourly=precipitation_probability,precipitation
    &models=ecmwf_ifs025,gfs_seamless,icon_seamless,gem_seamless
→ 200, 8 hourly series, 48/48 non-null on every model
```

- **The response shape changes.** With `models=` set, Open-Meteo suffixes every field with the
  model name — `precipitation_probability_ecmwf_ifs025`, not `precipitation_probability`. Existing
  parsing in `build_weather` (`snapshot.py:320`) reads the unsuffixed keys and would find nothing.
  This is the one place the "config change, not an integration" framing understates the work.
- **All four models are global.** Verified non-null at Zandvoort, Interlagos and Singapore. This
  closes the first draft's open item about non-European circuits: no coverage gap, and no need to
  vary the model list by venue.
- **The models genuinely disagree.** At Interlagos, one forecast hour returned ECMWF 4, GFS 16,
  ICON 0, GEM 22. Four independent centres, not four dressings of the same run — which is the
  premise the whole spread signal rests on.

### 3.2 Race window

**No new rule needed** — the first draft listed this as open, but it is already decided in code.
`snapshot.py:320` takes lights-out in circuit-local time ± 2h inclusive, and the wet-history path
at `snapshot.py:288` uses the same window against the archive. Every number in §5 uses it. Reuse
it; do not invent a second definition.

### 3.3 Gotchas

In the style of `01` §5.4, and additional to it:

- **Per-model probability history starts around 2024-05.** The historical-forecast endpoint
  (`historical-forecast-api.open-meteo.com`) serves archived past runs and accepts `models=`, but
  for earlier dates it returns the per-model keys with **every value null and HTTP 200** — a
  silent gap, not an error. 82 of 126 races 2021–2026 drop out this way. Anything that backtests
  this signal has to check for nulls rather than trusting the status code.
- **The blended default is not one of the four.** Calling without `models=` returns a provider
  blend that tracks none of the named models exactly, so "what we get today" has to be replayed
  separately rather than approximated by ECMWF alone. §5 does this.
- **Rate limit: unverified.** This is one HTTP request either way, but Open-Meteo's free tier
  counts *weighted* calls, and whether four models × five variables counts as one call or twenty
  against the 10,000/day allowance is not something the response reveals — no quota headers come
  back. The first draft asserted "no new rate-limit exposure"; that assertion is withdrawn pending
  a check. Lane A makes one snapshot call per race, so even a 20× weighting is immaterial; a Phase
  A3 backfill over hundreds of races is where it could bite.

## 4. What the ensemble produces

Per model, for each hour in the race window (§3.2): `precipitation_probability`, `precipitation`
(mm), and the other Tier-1 fields `01` §5.2 already pulls (`temperature_2m`, `wind_speed_10m`,
`relative_humidity_2m`).

### 4.1 What F7 actually consumes — a correction

The first draft called `p_mean` a "replacement for the single provider-blended value F7 currently
reads." **There is no such value.** `02` §4's F7 consumes exactly one scalar: `P_max`, the maximum
`precipitation_probability` across the race-window hours, compared against 40. That is the entire
weather interface.

So the question is not which of these aggregates replaces which existing field. It is: **which
single number goes into that one slot.** §6 answers it with the backtest rather than by preference.

### 4.2 The three aggregates

Written as expressions because the order of collapse changes the answer, and prose hides that.
Let `p[m][h]` be model `m`'s probability at window hour `h`, over `M` = the four models.

```
p_mean   = max over h of ( mean over m of p[m][h] )
p_max    = max over h of ( max  over m of p[m][h] )
p_spread = median over h of ( max over m of p[m][h] − min over m of p[m][h] )
```

Three things this pins down that the first draft left loose:

- **`p_mean` collapses models first, then hours.** `mean(max(...))` is a different and larger
  number; taking the max of already-averaged hours is the one that answers "what does the ensemble
  as a whole say at its wettest point."
- **`p_max` is a max over both axes**, so it composes cleanly with F7's existing max-over-hours —
  but it is a genuinely different quantity from today's blended `P_max`, not a drop-in. §5 shows
  it fires roughly a third more often.
- **`p_spread` uses the median hour, not the max hour.** The max-over-window spread distribution
  is badly skewed — across 44 races its quartiles are 0 / 11 / 50 / 98 pp — so one volatile hour
  would set the flag for an entire race. The median hour is stable: quartiles 0 / 5 / 33 pp.
  *(The first draft proposed max − min without saying over what; this is a change, and it was made
  after looking at the distribution, so it is a fitted choice rather than a principled one.)*

### 4.3 Agreement flag

`agree` when `p_spread < 15pp`, else `disagree`, persisted next to the numeric spread so a
downstream consumer isn't left re-deriving a threshold decision. The value is justified in §5.3.

### 4.4 Persistence

All four raw per-model series are persisted alongside the derived fields — never only the
aggregate — matching the raw+normalized pattern `01` §8.4 mandates for market odds. Concretely the
snapshot's `weather` key gains a `per_model` block and the three aggregates; its top-level shape
(`01` §8.3) does not change.

## 5. Backtest — 44 races (verified 2026-08-24)

The first draft decided nothing and admitted it had verified nothing. This section is the evidence
that was missing. It is reproducible: `weather_backtest.py` at the repo root prints every table
below, and imports nothing from the snapshot path.

### 5.1 Method

For every race from 2021 on that publishes a start time, over the §3.2 window:

| | source | quantity |
|---|---|---|
| **observed** | archive API | `precipitation` mm → wet |
| **blended** | historical-forecast API, no `models=` | what the pipeline asks for today, replayed |
| **ensemble** | historical-forecast API, `models=` × 4 | `p_mean`, `p_max`, `p_spread` per §4.2 |

126 races had a start time and a result; **44 had four-model coverage** (2024-05-05 → 2026-08-23),
the rest lost to the null-before-2024-05 gap in §3.3.

Three caveats, all of which cut against over-reading what follows:

- **n = 44, with 8–17 wet races depending on the rule.** Every rate below rests on single-digit
  event counts. This is enough to reject a claim, not enough to tune one finely.
- **The replay is optimistic on lead time.** The historical-forecast endpoint serves the most
  recent archived run for a date. A real snapshot is taken race morning (the Dutch GP's was 10h
  before lights-out), so live forecasts will be somewhat worse than these.
- **"Wet" is measured, not observed at the track.** It is Open-Meteo's own archive over a ~9 km
  grid cell, which is not the same as a race being run on wet tyres.

### 5.2 Which number should feed F7's gate

Gate is F7's existing `P >= 40` (`02` §4). Wet is evaluated three ways, because §1.1 showed the
project's own definition is doing more work than anyone intended.

**Wet = observed > 0.0 mm — `snapshot.py:288`'s actual rule (17/44 wet)**

| gate input | TP | FP | FN | TN | recall | precision |
|---|---|---|---|---|---|---|
| blended default (today) | 11 | 0 | 6 | 27 | 65% | 100% |
| `p_mean` | 10 | 0 | 7 | 27 | **59%** | 100% |
| `p_max` | 14 | 2 | 3 | 25 | **82%** | 88% |

**Wet = observed ≥ 0.5 mm (8/44 wet)**

| gate input | TP | FP | FN | TN | recall | precision |
|---|---|---|---|---|---|---|
| blended default (today) | 5 | 6 | 3 | 30 | 62% | 45% |
| `p_mean` | 6 | 4 | 2 | 32 | **75%** | **60%** |
| `p_max` | 6 | 10 | 2 | 26 | 75% | **38%** |

**Wet = observed ≥ 1.0 mm (6/44 wet)**: all three reach 83% recall; precision is 45% blended, 50%
`p_mean`, 31% `p_max`.

**The two tables disagree, and the disagreement is the finding.** Under the project's `> 0.0 mm`
rule `p_max` is clearly best and `p_mean` is *worse than what already runs*. Under a
racing-relevant rule `p_mean` is best on both axes and `p_max` is the worst thing on the table.

Why: every wet race `p_max` catches that the blended call misses is a **trace** — Mexico City 2024
(0.1 mm), Singapore 2025 (0.1 mm), Spa 2026 (0.2 mm), Zandvoort 2026 (0.1 mm) — with Miami 2025
(0.9 mm) the sole exception. Taking a max over four models finds the one model that saw a shower,
which is exactly right if 0.1 mm counts as wet and exactly wrong if it doesn't. Its two false
positives are stark: Singapore 2024 with `p_max` 100 against 0.0 mm observed, and Montreal 2026
with 55 against 0.0 mm.

**Activation rate**, which is what actually lands on the rest of the project: blended 27% of
races, `p_mean` 23%, `p_max` 36%.

### 5.3 `p_spread` — the result that justifies this spec

Take today's blended gate as the thing being judged, score it against observed ≥ 0.5 mm, and split
the 44 races by whether the four models agreed:

| | races | blended gate wrong |
|---|---|---|
| `p_spread` < 15 pp (agree) | 25 | **0 (0%)** |
| `p_spread` ≥ 15 pp (disagree) | 19 | **9 (47%)** |

All nine of the gate's errors fall in the 43% of races where the models disagreed. Median spread
was 35 pp on the races it got wrong versus 2 pp on the races it got right.

Sensitivity, because a single threshold that looks clean is usually fitted:

| agree threshold | n agree | errors | n disagree | errors |
|---|---|---|---|---|
| < 10 pp | 24 | 0 (0%) | 20 | 9 (45%) |
| < 15 pp | 25 | 0 (0%) | 19 | 9 (47%) |
| < 20 pp | 26 | 1 (4%) | 18 | 8 (44%) |
| < 25 pp | 28 | 2 (7%) | 16 | 7 (44%) |
| < 30 pp | 31 | 3 (10%) | 13 | 6 (46%) |

Anywhere in 10–18 pp gives a clean split; 15 sits mid-plateau rather than on an edge. Two honesty
notes: **15 pp was the first draft's guessed value** and it survived contact with the data, but the
*statistic* it applies to was changed after seeing the distribution (§4.2), so the threshold is
inherited and the statistic is fitted. And the low-spread bucket is dominated by obviously-dry
races, so 0/25 is partly "easy cases are easy." The defensible claim is the narrower one: **a
≥ 15 pp spread captured 9 of 9 of the gate's errors while flagging only 43% of races.**

### 5.4 Every wet race in the corpus

| date | race | obs mm | blended | `p_mean` | `p_max` | `p_spread` |
|---|---|---|---|---|---|---|
| 2025-04-06 | Japanese GP | 4.9 | 95 | 71.0 | 95 | 68 |
| 2025-07-06 | British GP | 4.7 | 97 | 96.2 | 100 | 26 |
| 2025-03-16 | Australian GP | 2.9 | 100 | 98.8 | 100 | 70 |
| 2024-11-03 | São Paulo GP | 2.5 | 100 | 93.5 | 100 | 28 |
| 2025-05-18 | Emilia Romagna GP | 1.0 | **0** | 15.0 | 32 | 25 |
| 2026-05-03 | Miami GP | 1.0 | 43 | 81.0 | 100 | 63 |
| 2025-05-04 | Miami GP | 0.9 | **37** | 59.5 | 96 | 61 |
| 2024-09-01 | Italian GP | 0.7 | **18** | 7.8 | 31 | 18 |

Worth seeing directly: the four genuinely wet races at the top were called by **everything**,
including the single blended call the pipeline already makes. Nobody needed an ensemble for
Silverstone 2025. The misses are all at the 0.7–1.0 mm margin, and Imola 2025 (blended 0, ensemble
15–32, observed 1.0 mm) is a race no configuration in this document would have caught.

That is the honest ceiling on §5.2's gains: **a better point estimate buys one marginal race in
44.** The spread result in §5.3 is where the value is.

## 6. Decisions this spec proposes

Three, in dependency order. The first is not this document's to make.

### 6.1 The wet definition is the blocking question — owner's call

`snapshot.py:288` calls a race wet at `max mm > 0.0`, and `02` §4's F7 builds each driver's
wet-weather rating from races meeting that rule. A 0.1 mm trace therefore counts as a wet race,
both for the historical feature and, by §5.2, for anything we tune a gate against.

**This spec does not change it.** That rule lives in `01`, `02` and `snapshot.py`; changing it
would silently redefine F7's active-branch feature for every driver, and `welcome.md` is explicit
that undocumented decisions get asked about rather than assumed. What §5.2 establishes is that the
rule is now load-bearing in a way it wasn't when it was written, and that it has to be settled
before the rest of this spec can be locked.

The recommendation, offered for that decision and not acted on here: **tighten to ≥ 0.5 mm.**
A trace that leaves the track dry is not the phenomenon F7 exists to model, and §5.4 shows the
races that actually matter clear 0.5 mm by a wide margin.

### 6.2 Gate input — conditional on §6.1

| if the wet rule is… | then F7's gate reads… | because |
|---|---|---|
| `> 0.0 mm` (today) | **`p_max`** | 82% vs 65% recall against today's blended call; `p_mean` at 59% would be a regression on what already runs |
| `≥ 0.5 mm` (recommended) | **`p_mean`** | 75%/60% recall/precision, beating today's 62%/45%; `p_max` collapses to 38% precision |

**These have to move together.** Adopting `p_mean` while the code still calls 0.1 mm wet ships a
gate tuned for material rain against a history feature built on traces — the same "different
quantities wearing the same name" failure `01` §5.6 spends a page rejecting for the backfill.

`p_max`, `p_mean` and `p_spread` are all persisted either way (§4.4); this decides only which one
is compared against 40.

### 6.3 Agreement threshold — `p_spread < 15 pp`

Resolved by §5.3, with the fitted-statistic caveat recorded there. Anywhere in 10–18 pp works;
15 pp is proposed because it was the value guessed before the data was seen and it sits in the
middle of the flat region.

## 7. How this feeds the rest of the project

- **F7 (weather feature, `02`).** `p_mean`/`p_max` replace the single-model reads `01` §5 currently
  produces; F7's dormancy gate and wet-branch logic (`02` §5, `01` §5.6) are otherwise unchanged.
  This is a data-quality upgrade to an existing feature, not a new feature.
- **A3 (`05`).** F7 stays **train-dormant** per the existing locked decision (`01` §5.6, `05`
  §3.3) — the archive API still has no precipitation-probability field for historical backfill, so
  an ensemble forecast at inference time does not change what training data can see. This spec
  does not reopen that decision.
- **New signal: `p_spread` as a confidence gate.** This is the part that's actually new relative to
  today's single-call approach. Proposed use: when `p_spread` is high near a race's lights-out, that
  is itself information — it means the crowd's pricing (which has to settle on *some* single
  implied weather assumption) is more likely to be wrong in either direction, i.e. more likely to
  be exploitable once resolved. This is a Lane A/Lane C signal, not a Lane B one — it operates on
  the batch pre-race snapshot, same cadence as everything else in `01`.
- **Lane C (trading, `00-roadmap.md`).** The stated use case — catching a market that's mispriced
  a rain-driven race the way Zandvoort's was — is a Lane C concern. This spec produces the signal;
  it does not decide sizing or trade logic, which stays out of scope until Lane C has its own
  approved spec per the roadmap's standing rule.

## 9. What this spec does not do

- Does not add a new API, key, or paid tier. Same Open-Meteo endpoint, one added query parameter.
- Does not self-host GraphCast, Pangu-Weather, or any other ML weather model.
- Does not change F7's train-dormant status or A3's design matrix.
- Does not implement trade logic. No Lane C code is authorized by this document.
- Does not change the snapshot schema's top-level shape (`01` §8.3) — `weather` gains richer
  per-model content, it doesn't become a new top-level key.

## 10. Open items

1. **Agreement threshold for the `agree`/`disagree` flag (§4).** Needs a value before this is
   buildable — e.g. spread < 15pp = agree — but that number should be picked by looking at how
   much these four models actually disagree in practice over a few real forecast windows, not
   guessed. Owner's call, or defer to whoever implements this and record the reasoning here.
2. **Model list (§3).** Four is proposed as "enough independent centers without diminishing
   returns," not verified against Open-Meteo's full model catalogue. Worth a quick live check of
   which named models Open-Meteo actually serves for these circuits' coordinates before locking
   the list — some regional models may not cover, e.g., a circuit outside Europe.
3. **Race window definition.** "The race window" needs a concrete rule (e.g. lights-out time ± 2h,
   local) applied consistently to which hourly rows get aggregated — not specified yet, should
   reuse whatever `01`'s existing weather pull already does for hour selection if that's already
   decided there.
4. **How `p_spread` actually gets used downstream (§5).** Flagged as a signal, not specced as a
   feature — whether it becomes a literal input to a future F7 variant, a Lane C confidence gate,
   or just a human-readable flag in the snapshot for now is undecided.
5. **Verification.** Nothing in this document has been verified live against Open-Meteo yet
   (contrast with `01-data-pipeline.md`, which states everything was called live before being
   written down). This spec should not be treated as build-ready until that verification pass
   happens, matching this project's own stated bar for its specs.

---

## 11. Sources

- [Open-Meteo](https://open-meteo.com/) · [forecast API docs / `models` parameter](https://open-meteo.com/en/docs)
- [GraphCast (Google DeepMind, GitHub)](https://github.com/google-deepmind/graphcast)
- [Pangu-Weather (Huawei, GitHub)](https://github.com/198808xc/Pangu-Weather)
