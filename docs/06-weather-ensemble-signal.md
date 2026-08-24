# 06 — Multi-Model Weather Ensemble Signal

Status: **drafted 2026-08-23; revised and verified live 2026-08-24; not approved, not
implemented.** Still a spec only — no code gets written against it until the owner locks it in,
per `welcome.md`'s "no implementation without an approved spec" rule. Read `welcome.md` and
`01-data-pipeline.md` §5 first.

**Blocked on one decision that is not this document's to make:** the wet-race definition
(§6.1). `snapshot.py:288` counts a 0.1 mm trace as a wet race, and §5.2 shows that rule decides
which aggregate should feed F7's gate — the answer flips depending on it. Everything else here is
settled and verified.

The first draft's factual errors are corrected in place rather than quietly deleted, since the
project's specs are also its decision record: §1 (the Zandvoort premise was invented), §2 (the
calendar is not European), §3.3 (the rate-limit claim was unfounded), §4.1 (F7 does not read what
the draft said it reads), §7.1 (this changes behaviour, it is not just cleaner data).

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
footnote; §5.2 below shows it decides which aggregate this spec should feed F7, so it is the first
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
where our own forecast is unreliable.** (Whether the same holds for the crowd's implied forecast is
a separate and untested claim — §7.4.)

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

For every race from 2021 on that publishes a start time, over the §3.2 window (the script's
default range, so a bare `python3 weather_backtest.py` reproduces the counts below):

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

### 7.1 F7 — this changes behaviour, it is not just cleaner data

The first draft called this "a data-quality upgrade to an existing feature, not a new feature."
That was wrong. Swapping the gate input changes **how often F7's wet branch fires**: 27% of races
today, 23% under `p_mean`, 36% under `p_max` (§5.2).

That matters more here than it would in most features, because of what sits behind the branch:

- F7's wet branch has **never executed on a real race** (`02` §10.4). Changing the gate input is
  the act that first fires untested code, so `test_f7_wet_branch.py` is the gate on shipping this,
  not an afterthought.
- The Dutch GP is the worked example. It ran with `p_max: 37, weather_dormant: true`. Under
  §6.2's `p_max` branch it would have scored 88 and gone **active** — on a race that produced
  0.1 mm. Under `p_mean` it scores 32.5 and stays dormant. One config choice, opposite behaviour,
  on the only race this pipeline has ever predicted live.

F7's wet-branch *logic* — the per-driver wet rating, the shrinkage, field normalization — is
untouched (`02` §4). Only the scalar entering the gate changes.

### 7.2 A3 — unchanged, but it inherits a wider out-of-domain window

F7 stays **train-dormant** and stays out of A3's design matrix (`01` §5.6, `05` §3.3). Nothing here
reopens that: the archive still has no probability field, so a better *forecast* at inference time
changes nothing about what training rows can see.

The consequence is second-order and worth stating because §5.2 quantifies it. `01` §5.6's standing
rule is that whenever A1's weather branch goes active, A3 is out-of-domain and both predictors get
reported. A gate that fires more often makes that reporting path more common — under `p_max`,
roughly one race in three rather than one in four. That is an argument for `p_mean` that is
independent of the backtest, and it also means the dual-report path stops being a rare edge case
someone can leave half-built.

### 7.3 `p_spread` — verified as forecast reliability

§5.3 supports exactly one claim, and it is a Lane A claim: **when the four models disagree by
≥ 15 pp, our own weather gate is unreliable** — 9 of its 9 errors, versus 0 in 25 agreeing races.

Proposed use, and the minimum this spec should ship: persist `p_spread` and the `agree`/`disagree`
flag in the snapshot, and mark any prediction made under `disagree` as weather-uncertain in the
same way `01` §5.6 marks a wet race out-of-domain for A3. That is a data-quality guard, needs no
market data, and is verified.

### 7.4 Lane C — a plausible trading rationale, explicitly not verified

Keep this separate from §7.3. Nothing in the backtest is evidence about market pricing; neither
venue exposes historical odds, so the argument below is **unbacktested reasoning, not a result.**

The first draft argued that high spread means the crowd is "more likely to be wrong in either
direction, i.e. more likely to be exploitable." That doesn't close — direction-free wrongness isn't
tradable on binary YES/NO shares. The better form of the argument is directional:

> A wet race redistributes win probability away from whoever qualified well (`02` §5.1's grid
> weight is 0.35, the largest in the model). A market that must settle on a single implied weather
> assumption will, under genuine forecast uncertainty, tend to price closer to the dry case than
> the probability-weighted blend of both. If so, the favourite is **over**-priced and the field
> **under**-priced whenever `disagree` holds — which is a direction, and therefore a trade.

Testing that needs snapshotted odds on high-spread races, which the project accumulates at n≈1 per
wet weekend. It is a Lane C hypothesis to log and check, not a reason to build anything. Sizing and
trade logic remain out of scope until Lane C has its own approved spec.

## 8. The real Ensemble API — considered, deferred

The first draft called itself an ensemble spec without mentioning that Open-Meteo runs an actual
ensemble API. That was a gap, so it goes on the record here.

### 8.1 What it is (verified 2026-08-24)

```
GET ensemble-api.open-meteo.com/v1/ensemble?...&models=ecmwf_ifs025
→ 200, precipitation + precipitation_member01..50
```

Free, keyless, same provider, no new dependency. `ecmwf_ifs025` returns the control run plus **50
perturbed members**; it also serves `precipitation_probability` per member. Other centres' ensembles
are exposed too (`ncep_gefs025`, `icon_eu_eps`) — note `icon_eu_eps` is the regional ICON, which
§2 already rules out for a global calendar.

### 8.2 Why it is the better instrument in principle

This spec's `p_spread` is `max − min` over four numbers. **A range statistic on n = 4 is about the
noisiest uncertainty estimator available** — it is defined entirely by two extreme values, has no
notion of how the middle is distributed, and moves whenever any single centre has an outlier run.
§5.3 shows it works anyway, which is a statement about how large real disagreement is, not a
defence of the estimator.

Fifty members give a distribution instead: quantiles, an interquartile spread, and a probability of
exceedance computed directly rather than inherited from a provider.

There is a second, sharper reason. Members return **precipitation in mm**, so probability of rain
can be derived as *the fraction of members exceeding a threshold* — say, members over 0.5 mm. That
quantity is defined on mm, which is exactly what the archive serves (`01` §5.4). It does **not**
dissolve the train/serve skew behind the train-dormant decision (`01` §5.6) — a single archived
observation is still not a distribution, and that decision stands — but "fraction of members over
0.5 mm" at inference against "observed over 0.5 mm" at training is far closer in kind than a
provider probability against observed mm. If F7's wet handling is ever revisited, this is the
version worth revisiting it with.

### 8.3 Why it is not proposed now

**It cannot be backtested, so locking it would violate this project's own bar.** The ensemble
endpoint's past window is roughly 93 days (it rejected 2026-05-03 with *"out of allowed range from
2026-05-23"*), and it is sparse inside that window — 2026-07-19 returned all 50 members null.
§5's 44-race replay is simply not runnable against it, and `welcome.md` plus `01`'s
verified-before-written standard both say an unverifiable design doesn't get locked in.

Recommended disposition: **start collecting it forward now, decide later.** Adding the ensemble
call to the snapshot alongside the four-model read costs nothing but response size, is pure data
capture with no consumer, and after a season there is enough to run §5's comparison properly. That
is a smaller ask than adopting it as F7's input today, and it is the only path to the evidence
that would justify adopting it.

One cost to weigh: 51 series versus 4, against a weighted call quota this document could not
verify (§3.3).

## 9. What this spec does not do

- Does not add a key, a paid tier, or a new provider. Everything here is Open-Meteo's free keyless
  tier. §8.3's forward-collection suggestion does use a second Open-Meteo endpoint, and is a
  suggestion, not part of what this spec proposes locking.
- Does not self-host GraphCast, Pangu-Weather, or any other ML weather model.
- Does not change F7's train-dormant status or A3's design matrix (`01` §5.6, `05` §3.3).
- **Does not change the wet-race definition** (`snapshot.py:288`, `02` §4). §6.1 argues it should
  change and hands the decision to the owner; nothing here acts on it.
- Does not change F7's wet-branch logic — only the scalar that enters its gate (§7.1).
- Does not implement trade logic. §7.4 is a labelled hypothesis. No Lane C code is authorized here.
- Does not change the snapshot schema's top-level shape (`01` §8.3) — `weather` gains a `per_model`
  block and three aggregates, it doesn't become a new top-level key.

## 10. Open items

Resolved since the first draft, kept visible so the trail is readable:

- ~~**Agreement threshold**~~ — **15 pp** on `p_spread`, §5.3/§6.3. Sensitivity checked; 10–18 pp
  all give a clean split.
- ~~**Model list unverified**~~ — all four verified live and global (§3.1). No coverage gap at
  non-European circuits, no per-venue model list needed.
- ~~**Race window undefined**~~ — already fixed in code at `snapshot.py:320`, lights-out local
  ± 2h inclusive (§3.2). Reuse it.
- ~~**Nothing verified live**~~ — the call, the response shape, coverage at three circuits, the
  pre-2024 null gap, and a 44-race backtest are all verified (§3.1, §5). Reproduce with
  `weather_backtest.py`.

Still open:

1. **The wet-race definition — blocking, owner's call (§6.1).** `> 0.0 mm` counts a 0.1 mm trace
   as a wet race. Recommend tightening to `≥ 0.5 mm`. **§6.2's gate choice cannot be locked until
   this is settled**, because the two answers point at different aggregates. This is the one item
   holding up the whole document.
2. **Rate-limit weighting (§3.3).** Whether four models counts as one weighted call or twenty
   against the 10,000/day free allowance is unverified — no quota headers are returned. Immaterial
   for Lane A's one call per race; worth knowing before an A3 backfill.
3. **`p_spread`'s downstream consumer (§7.3).** The proposed minimum is a snapshot flag plus a
   weather-uncertain marker on predictions. Whether it ever becomes a feature in its own right, or
   a Lane C gate, is not decided.
4. **The Lane C hypothesis (§7.4) is untested** and needs snapshotted odds on high-spread races,
   which accrue at roughly one per wet weekend. Log the data; don't build on it.
5. **`test_f7_wet_branch.py` is the shipping gate (§7.1).** Changing the gate input is what first
   fires a branch that has never run on a real race. That test existing is not the same as it
   having been exercised against the ensemble path.
6. **Backtest scale.** n = 44, with 8 wet races at the ≥ 0.5 mm rule, and the replay is optimistic
   on lead time (§5.1). Re-run `weather_backtest.py` as seasons accumulate; the per-model history
   only began in 2024-05, so the corpus grows on its own.

---

## 11. Sources

- [Open-Meteo](https://open-meteo.com/) · [forecast API docs / `models` parameter](https://open-meteo.com/en/docs)
- [Open-Meteo historical forecast API](https://open-meteo.com/en/docs/historical-forecast-api) — archived past runs, the basis for §5
- [Open-Meteo ensemble API](https://open-meteo.com/en/docs/ensemble-api) — §8
- [GraphCast (Google DeepMind, GitHub)](https://github.com/google-deepmind/graphcast)
- [Pangu-Weather (Huawei, GitHub)](https://github.com/198808xc/Pangu-Weather)
- `weather_backtest.py` (this repo) — reproduces every figure in §5
