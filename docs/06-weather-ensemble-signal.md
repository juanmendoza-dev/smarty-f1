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
  IFS/AIFS (widely regarded as the strongest global model for Europe, where every 2026 circuit
  used by this project sits). The "advanced model" the project actually gets access to, for free,
  is ECMWF — the ensemble adds cross-model agreement as a second signal on top of it.

**Decision this spec proposes:** no self-hosted model. Multi-model ensemble via Open-Meteo only.

## 3. Data source

Same base URL as `01-data-pipeline.md` §5.2 (`https://api.open-meteo.com/v1/forecast`), no new
auth, no new rate-limit exposure — this is still one HTTPS call. The only change is the
`models=` parameter, which Open-Meteo supports today and which the current pipeline does not set
(so it currently receives the provider's own best-blended default, not a named model list).

Proposed models to request explicitly:

| Model | Provider | Why included |
|---|---|---|
| `ecmwf_ifs025` | ECMWF | Strongest general-purpose model for Europe; already the de facto standard this project benefits from even unnamed |
| `gfs_seamless` | NOAA | Independent modeling group and physics, US-run, different bias profile than ECMWF |
| `icon_seamless` | DWD (Germany) | Independent European model, historically competitive with ECMWF at short range over Europe specifically |
| `gem_seamless` | ECCC (Canada) | Third independent center, adds a model not built on shared European NWP lineage |

Four models, one call, all free, all already exposed by Open-Meteo's `models` parameter — this is
a config change to the existing call, not a new integration.

## 4. What the ensemble produces

For each hourly step in the race window, per model: `precipitation_probability`,
`precipitation` (mm), and whichever other Tier-1 fields `01` §5.2 already pulls
(`temperature_2m`, `wind_speed_10m`, `relative_humidity_2m`).

From the four models' values at each hour, compute:

- **`p_mean`** — mean precipitation probability across models. Proposed replacement for the single
  provider-blended value F7 currently reads.
- **`p_spread`** — max − min precipitation probability across models. Low spread means the models
  agree (high-confidence read, whichever direction); high spread means genuine forecast
  uncertainty at that lead time.
- **`p_max`** — the maximum across models. `02`'s F7 gate already keys off a `P_max < 40` dormancy
  threshold (`01` §5.6) — this is a fully compatible drop-in resolution of what "max" means, since
  today `p_max` is read off a single blended source, not a true max over independent models.
- **Agreement flag** — `agree` (spread below some threshold, TBD — see §7) vs. `disagree`, recorded
  alongside the numeric spread so a downstream consumer doesn't have to re-derive a threshold
  decision Lane A already made.

All four raw per-model series are persisted alongside the derived fields — never only the
aggregate — matching the snapshot's existing raw+normalized pattern for market odds (`01` §8.4).

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
