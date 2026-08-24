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

## 5. How this feeds the rest of the project

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

## 6. What this spec does not do

- Does not add a new API, key, or paid tier. Same Open-Meteo endpoint, one added query parameter.
- Does not self-host GraphCast, Pangu-Weather, or any other ML weather model.
- Does not change F7's train-dormant status or A3's design matrix.
- Does not implement trade logic. No Lane C code is authorized by this document.
- Does not change the snapshot schema's top-level shape (`01` §8.3) — `weather` gains richer
  per-model content, it doesn't become a new top-level key.

## 7. Open items

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

## 8. Sources

- [Open-Meteo](https://open-meteo.com/) · [forecast API docs / `models` parameter](https://open-meteo.com/en/docs)
- [GraphCast (Google DeepMind, GitHub)](https://github.com/google-deepmind/graphcast)
- [Pangu-Weather (Huawei, GitHub)](https://github.com/198808xc/Pangu-Weather)
