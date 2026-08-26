# 01 — Data Pipeline (Lane A)

Status: **locked** for Phase A1/A2. Read `welcome.md` and `00-roadmap.md` first.

This spec defines every data source Lane A is allowed to use, how each is accessed, what it
returns, and how the sources are joined into a single per-race snapshot. It is written to be
executed by an agent without further clarification. Where a decision genuinely still needs the
owner, it is listed in **§9 Open items** rather than guessed at.

**Verification note:** every endpoint, limit, and identifier in this document was called live
against production on **2026-08-22** and returned the data described. Responses quoted are real.
Anything *not* verified is explicitly marked `UNVERIFIED`.

---

## 1. Scope

Lane A is **batch/snapshot**: pull a fixed set of data once, at a defined cutoff time before a
session, compute a prediction, persist the inputs, stop. There is no streaming, no reconnect
logic, and no delay-sync problem in this document. Anything live belongs to Lane B
(`03-live-telemetry-overtakes.md`) and is out of scope here.

The pipeline has exactly one job:

> Given a `(season, round)`, produce a **race snapshot** — a single JSON file containing the
> grid, form, track history, weather, and both markets' prices — with enough provenance that the
> prediction can be reproduced and audited after the race.

The snapshot is the only interface between this document and `02-winner-prediction-algo.md`.
The scoring function reads the snapshot and nothing else. It never calls a network API.

---

## 2. Environment prerequisites

**This is a blocker for Phase A2, so it is stated first.**

The machine currently has **Python 3.9.6 only** (system Python at `/usr/bin/python3`). No
`pyenv`, `uv`, or `conda` is installed. Homebrew *is* installed at `/opt/homebrew/bin/brew`.

FastF1 3.8.3 (current release) requires **Python >= 3.10**. It therefore **cannot be installed
on this machine as-is**.

Consequence, and the reason the tiering in §3 is shaped the way it is:

- **Tier 1 (required for A1/A2): Jolpica, Open-Meteo, Polymarket, Kalshi.** All four are plain
  HTTPS + JSON, all four work on Python 3.9 with zero third-party packages (`urllib` is enough;
  `requests` is a convenience, not a requirement). **Tonight's build does not need FastF1.**
- **Tier 2 (deferred, not on A2's critical path): FastF1.** Install it behind a newer
  interpreter — `brew install python@3.12` then a dedicated venv, or install `uv` — and treat it
  as an additive validation layer once Tier 1 works end to end.

Do not block the Phase A2 prediction on getting FastF1 installed. If the interpreter upgrade is
not finished before the cutoff, run the snapshot on Tier 1 alone and record
`"fastf1": {"status": "unavailable", "reason": "python<3.10"}` in the snapshot's provenance block.

---

## 3. Sources at a glance

| Source | Role | Auth | Tier | Verified 2026-08-22 |
|---|---|---|---|---|
| Jolpica | Results, grid, standings, schedule, circuit coords | none | 1 | ✅ live, 2026 data through round 12 |
| Open-Meteo | Race-day weather forecast + historical archive | none | 1 | ✅ live |
| Polymarket (Gamma) | Winner market prices, crowd baseline | none for reads | 1 | ✅ live, Dutch GP 2026 market open |
| Kalshi | Winner market prices, regulated US venue | none for market reads | 1 | ✅ live, Dutch GP 2026 market open |
| FastF1 | Timing/telemetry, session detail, cross-check | none | 2 | ⛔ blocked on Python >= 3.10 |

Every Tier 1 source is free, keyless, and required no credential to reach. **The roadmap's note
that "owner providing API access for both" markets is, for reading prices, unnecessary** — see
§6.3 and §7.3. No account, key, wallet, or signature is needed for the read paths Lane A uses.
That removes a dependency from tonight's build.

---

## 4. Jolpica — results, grid, standings, schedule

### 4.1 What it provides

Jolpica (`jolpica-f1`) is the maintained successor to the retired Ergast API and serves the same
response shape under an `/ergast/` compatibility path. It is the **primary** source for
everything structured and historical:

- Season schedule with round numbers, race dates, and circuit IDs
- **Qualifying classification** → the starting grid (the single most important A1 feature)
- Sprint results (relevant this weekend — Zandvoort 2026 is a sprint event)
- Race results, finishing status, and DNF reasons
- Driver and constructor standings after any round
- **Circuit latitude/longitude** — this is what feeds Open-Meteo, so no separate geocoding source
  or hardcoded coordinate table is needed

### 4.2 Access

- Base URL: `https://api.jolpi.ca/ergast/f1/`
- Auth: **none**. No key, no header, no account.
- Format: append `.json` to the path, or pass `?format=json`.
- Paging: `?limit=` (default 30) and `?offset=`. `MRData.total` gives the full count. Always set
  `limit` explicitly — a 22-car field silently truncates at the default 30 only until a
  multi-session query exceeds it, which is exactly the kind of bug that shows up once and looks
  like missing drivers.

Endpoints Lane A uses, with the exact forms verified:

| Purpose | Path |
|---|---|
| Season schedule | `2026.json` |
| Qualifying / grid | `2026/12/qualifying.json?limit=30` |
| Sprint result | `2026/12/sprint.json?limit=30` |
| Race result | `2026/12/results.json?limit=30` |
| Driver standings | `2026/driverstandings.json?limit=30` |
| Constructor standings | `2026/constructorstandings.json?limit=30` |
| Circuit coordinates | `circuits/zandvoort.json` |
| Driver's history at a circuit | `circuits/zandvoort/drivers/{driverId}/results.json?limit=100` |

### 4.3 Rate limits

Documented, and low enough to matter:

- **4 requests/second** burst
- **500 requests/hour** sustained
- Both are for unauthenticated access. Token-based auth is being rolled out for higher limits but
  is not required today. The project's documented limits are **"subject to change, and will
  decrease in the future"** — so treat 500/hr as a ceiling that may shrink, not a floor.

**Policy (mandatory):** cache every Jolpica response to disk keyed by the exact URL, and read from
cache on repeat. A snapshot run needs well under 30 requests; the limit only becomes a problem
when backfilling history for Phase A3, which is precisely when a cache is worth the most. Prefer
one filtered query over N per-entity queries — `circuits/{id}/drivers/{id}/results.json` in place
of a loop over rounds (`build_track_history`), and `{season}/results.json` (paginated at a
server-enforced 100-row page cap, verified live 2026-08-23 — a 22-round season is 5 pages) in
place of one `race_results` call per prior round (`jolpica.season_results`, used by
`build_form` as of 2026-08-23). The season-level pull is cached per `(season, offset)`, so it's
fetched from the network once per season for the life of the cache and then reused by every race
in that season — ~4x fewer calls per season than the per-round loop, and the marginal cost of
each additional race backfilled from that season drops to zero instead of being merely smaller.
`jolpica.season_results` also force-refreshes and re-validates page 0 and any short trailing page
against a freshly-read row total, since a cached page from an earlier week would otherwise keep
reporting a stale total for a season still in progress — see its docstring.

### 4.4 Gotchas

- Response envelope is `MRData` → `RaceTable` / `StandingsTable` → list. An empty list is the
  normal representation of "session hasn't happened yet", **not** an error and **not** a 404.
  Code must distinguish "no data yet" from "request failed" or it will silently score a race with
  an empty grid.
- All numbers arrive as **strings** (`"position": "1"`, `"points": "224"`). Cast explicitly.
- The driver key is `Driver.driverId` (e.g. `max_verstappen`) with a separate three-letter
  `Driver.code` (e.g. `VER`). See §8 for which one is canonical.
- `Cache-Control: max-age=600` is returned; no `X-RateLimit-*` headers are exposed, so a client
  cannot see how close it is to the limit. Budget conservatively rather than reactively.

### 4.5 Verified output (2026-08-22)

`2026/12/qualifying.json` returns the full 22-car Dutch GP classification: NOR pole (1:11.163),
RUS P2, ANT P3, PIA P4, HAM P5, LEC P6, VER P7, LAW P8. `2026/12/sprint.json` returns Saturday's
sprint: RUS 1st, LEC 2nd, NOR 3rd, ANT 4th, PIA 5th, VER 6th, HAM 7th. `2026/driverstandings.json`
returns round 12 with ANT 224, HAM 171, RUS 168, LEC 145, NOR 134, VER 112, PIA 96.

**All Phase A1 grid and form inputs are therefore already available.** Nothing is waiting on data.

### 4.6 Standings as-of a round — the live/backfill split

F6 (championship, `02` §4) needs the standings as they stood **before** the race being predicted.
There are two ways to ask Jolpica for that and they are not interchangeable.

`{season}/driverstandings.json` ("latest") is correct for a **live pre-race snapshot**, and it is
the only form that captures a sprint that has already run this weekend. Jolpica stamps that list
with the *current* round even when only the sprint has been scored — §4.5 above is exactly this
case: the 2026 Dutch GP "latest" table is stamped round 12 and sits 8/7/6/5/4/3/2/1 points above
the round-11 table, one sprint's scoring for its top eight. **A stamp equal to the round being
predicted is therefore expected here and is not evidence of leakage.** A stamp *beyond* it is.

`{season}/{round-1}/driverstandings.json` is required for a **backfill of a past race**. Asking
for "latest" on a finished season returns that season's *final* table — F6 handed the answer, with
no error and a completely plausible-looking result. This was a real bug: it fed end-of-2023
standings (VER 575) into a 2023 Dutch GP backfill whose correct input was VER 314.

`snapshot.build_form` picks between them on `race_has_run`, and `jolpica.driver_standings` takes
`verify_round=` (exact match, backfill) or `max_round=` (at-most, live) so the wrong table fails
loudly instead of scoring. The stamped round is recorded on the snapshot as
`form.standings_after_round`.

**Known limitation.** A backfilled sprint weekend loses that weekend's sprint points from F6:
there is no round-indexed way to ask for "after round N-1's race plus round N's sprint." Bounded
at 8 points against leader totals in the hundreds, on a feature normalised by leader points. Not
worked around, because the workaround is a per-season sprint-scoring table (the format has changed
more than once) to recover a fraction of one 0.08-weight feature.

---

## 5. Open-Meteo — weather

### 5.1 What it provides

- **Forecast** for the race window (used at prediction time)
- **Historical archive** back to 1940 (used to build "how did this driver do here in the wet"
  features, and to label past races for Phase A3)

Both are on the same free tier and need no key.

### 5.2 Access

- Forecast base URL: `https://api.open-meteo.com/v1/forecast`
- Archive base URL: `https://archive-api.open-meteo.com/v1/archive`
- Auth: **none**.
- Required params: `latitude`, `longitude` (take them from Jolpica's `Circuit.Location`),
  `start_date`, `end_date`, `timezone`, and an `hourly=` variable list.

Canonical Lane A call, verified:

```
https://api.open-meteo.com/v1/forecast
  ?latitude=52.3888&longitude=4.54092
  &hourly=temperature_2m,precipitation_probability,precipitation,wind_speed_10m,relative_humidity_2m
  &start_date=2026-08-23&end_date=2026-08-23
  &timezone=Europe/Amsterdam
```

Pass `timezone` explicitly and use the circuit's local zone. Defaulting to UTC and then slicing
"the race hours" silently reads the wrong hours, which is a hard bug to spot because the numbers
still look plausible.

### 5.3 Rate limits

Free non-commercial tier: **< 10,000 calls/day, 5,000/hour, 600/minute**, CC-BY 4.0, no uptime
guarantee. A snapshot run makes **one** call. This limit is not a practical constraint for Lane A;
it only becomes relevant during a large Phase A3 archive backfill, where the disk cache from §4.3
applies equally.

### 5.4 Gotchas

- `precipitation_probability` is only present in the **forecast** API. The **archive** API has no
  probability field — it has observed `precipitation` in mm. Any feature defined on "chance of
  rain" cannot be computed for historical races. Define wet-race history on observed
  `precipitation > 0`, not on probability, or the feature will be uncomputable for training data.
- The archive has a multi-day ingestion lag; it is not a substitute for the forecast API on race
  weekend.
- Values are hourly arrays parallel to `hourly.time`. Join by index, and select by the local-time
  strings — do not assume a fixed offset into the array.
- Units come back in a `hourly_units` block. Read them rather than assuming °C/km/h/mm.

### 5.5 Verified output (2026-08-22)

Zandvoort, 2026-08-23, local time: 13:00 → 18.4 °C, 37% precip probability, 0.0 mm, 15.5 km/h wind;
falling to 20% by 17:00. **Dry race forecast, moderate wind, no meaningful rain signal.**

---

### 5.6 The archive endpoint cannot reproduce F7's input (decided 2026-08-23)

F7's dormancy gate is `P_max < 40`, a **precipitation probability**. The forecast endpoint serves
that field; the archive endpoint does not — it serves observed precipitation in mm only (§5.4),
and it is the only endpoint that answers for a past date. So a historical race has no `p_max` at
all, and an A3 backfill has to choose:

- **Train F7-dormant.** Every backfilled row scores F7 at NEUTRAL, i.e. 7 live features and 8 at
  inference time. Simple and honest, but the model never learns a wet-weather effect.
- **Define a wet proxy from observed mm.** Recovers wet races, but the training feature means
  "it rained" while the inference feature means "rain is forecast" — different quantities wearing
  the same name.

Both are train/serve skew; there is no third option that isn't one of these two wearing a hat.
**Whichever is chosen, A3's feature set has to match what inference actually has**, and the choice
must be recorded here rather than settled implicitly by whoever writes the backfill script.
`test_phase_a4.py` already stubs `weather = {"p_max": 0}` for its 2023 snapshot for this reason.

**Decided 2026-08-23 (owner): train-dormant.** Recorded here because this section is where the
decision was required to land; argued in full at `05-trained-model.md` §3.3. The reason for
dormant over a proxy is that F7's wet branch has never executed on a real race (`02` §10.4), so a
proxy would model a quantity that has never been validated at inference time, and it carries the
worse of the two skews — "it rained" at training time against "rain is forecast" at inference,
under one name.

One consequence is sharper than "score it NEUTRAL" and belongs here rather than only in `05`:
dormant gives every driver the same value, and in A3's conditional logit a value constant across
the field cancels out of the likelihood exactly. So `β_weather` is **unidentified** — not merely
imprecise — and F7 is dropped from A3's design matrix entirely (7 features, not 8) rather than
carried as a constant column. The standing consequence is that **A3 predicts a wet race as though
it were dry**, while A1 does have a wet term; on any race where A1's weather branch goes active,
both predictors get run and A3's number is reported as out-of-domain.

---

## 6. Polymarket — crowd market

### 6.1 What it provides

Per-driver win probability from a real-money prediction market. This is one of the two crowd
baselines the project measures itself against — per `welcome.md`, that comparison *is* the
headline feature, so this source is not optional garnish.

### 6.2 Which API to use

Polymarket exposes three services. Lane A uses **Gamma only**.

| Service | Base URL | Purpose | Lane A |
|---|---|---|---|
| Gamma | `https://gamma-api.polymarket.com` | Event/market catalogue, prices, volume | **Yes** |
| CLOB | `https://clob.polymarket.com` | Live order book, trading | No (Lane B / never) |
| Data | `https://data-api.polymarket.com` | Historical trades, positions | No |

Gamma returns everything a snapshot needs — outcome prices, best bid/ask, last trade, volume —
in one request per event. Do not reach for CLOB: it adds an order-book dependency for numbers
Gamma already provides.

### 6.3 Auth — resolves an open decision

**None required.** Gamma is fully public: no API key, no token, no wallet, no signature. This was
verified by calling it with no credentials.

Authentication on Polymarket exists only for **trading** on the CLOB (EIP-712 wallet signature to
mint L2 credentials, then HMAC-SHA256 per request). Lane A does not trade and must not implement
any of it. The roadmap's "owner providing API access" line is not a prerequisite for reading
prices.

### 6.4 Rate limits

Documented per **10-second** sliding window, Cloudflare-enforced, global (no tiers):

| Endpoint | Limit |
|---|---|
| Gamma general | 4,000 / 10s |
| `/events` | 500 / 10s |
| `/markets` | 300 / 10s |
| `/public-search` | 350 / 10s |

Exceeding a limit **throttles (delays/queues)** the request rather than rejecting it — so
overshooting shows up as latency, not as a clean `429`. A snapshot uses 1–2 requests. Not a
constraint.

### 6.5 Finding the right market — the critical gotcha

**Do not resolve a market by name search.** Slugs are reused across seasons and searching
`"Dutch Grand Prix Winner"` returns, in ranked order, the **2025** event
(`f1-dutch-grand-prix-winner`, id 12184/37567) — which is `closed: true`, ended 2025-08-31, and
has Piastri priced at 1.000 as the settled winner. Reading that event yields a confident,
well-formed, completely wrong snapshot. This was hit during verification and is the single most
dangerous failure mode in this pipeline, because it fails silently with plausible-looking data.

**Required resolution procedure:**

1. Query by date-suffixed slug: `GET /events?slug=f1-{race}-grand-prix-winner-{YYYY-MM-DD}`
   where the date is the **race** date. Verified: `f1-dutch-grand-prix-winner-2026-08-23`
   → event id `868800`.
2. Fallback if the slug pattern misses: `GET /events?tag_slug=f1&closed=false&limit=100` and
   match on title + `endDate`.
3. **Assert before use** — abort the run rather than proceed if any check fails:
   - `closed == false`
   - `endDate` is in the future
   - the event's markets include the expected driver set
   - outcome prices are not degenerate (no single outcome at 1.000)

### 6.6 Response shape

`GET /events?slug=...` returns a list; take `[0]`. Each element has `markets[]`, one binary market
per driver:

- `groupItemTitle` — driver display name (e.g. `"Kimi Antonelli"`)
- `outcomes` — JSON **string** `'["Yes","No"]'`, must be parsed
- `outcomePrices` — JSON **string** `'["0.365","0.635"]'`, must be parsed; index 0 is Yes
- `bestBid`, `bestAsk`, `lastTradePrice` — floats, may be `null` on illiquid legs
- `volumeNum`, `conditionId`, `negRisk`

The Dutch GP winner event is `negRisk: true` (mutually exclusive multi-outcome). Prices are
therefore near-normalized but not exactly so — the verified snapshot sums to **1.035**, a ~3.5%
overround. See §8.4.

### 6.7 Verified output (2026-08-22)

Event `868800`, ~$183k volume: Norris 0.365, Russell 0.255, Antonelli 0.245, Hamilton 0.050,
Leclerc 0.044, Piastri 0.036, Verstappen 0.024.

---

## 7. Kalshi — regulated market

### 7.1 What it provides

The same winner market on a CFTC-regulated US venue. Its value is precisely that it is an
*independent* crowd: where Kalshi and Polymarket disagree, that spread is itself a signal, and
agreement between them raises confidence that a divergence from our algo is our algo's problem.

### 7.2 Access

- Production REST: `https://external-api.kalshi.com/trade-api/v2`
- Production WebSocket: `wss://external-api-ws.kalshi.com/trade-api/ws/v2` (Lane B only)
- Demo REST: `https://external-api.demo.kalshi.co/trade-api/v2`
- `https://api.elections.kalshi.com/trade-api/v2` is an alternate host that serves the same
  market data; both were verified returning identical payloads. **Use the documented
  `external-api.kalshi.com` host** and treat the elections host as a fallback only.

### 7.3 Auth — resolves an open decision

**Market data endpoints require no authentication.** `GET /markets` is declared `security: []` in
Kalshi's own OpenAPI spec, and was verified returning full Dutch GP pricing with no headers set.

Authentication is required only for account and trading endpoints. For completeness, since the
roadmap listed this as open — if a future phase ever needs an authenticated endpoint:

- Generate a 2048-bit RSA key pair; register the public key at `kalshi.com/account/profile` →
  API Keys. **The private key is shown once and is not recoverable.**
- Sign, per request: `timestamp_ms + METHOD + path`, where `path` **includes** the
  `/trade-api/v2` prefix and **excludes** the query string.
- Scheme: RSA-PSS, SHA-256 for both digest and MGF1, salt length = digest length (32 bytes),
  base64-encoded.
- Headers: `KALSHI-ACCESS-KEY` (key id), `KALSHI-ACCESS-TIMESTAMP` (ms), `KALSHI-ACCESS-SIGNATURE`.

**Lane A must not implement this.** It buys nothing for reading prices and puts a trading
credential on disk for no reason.

### 7.4 Rate limits

Token-bucket, per key, seven tiers. Default **Basic: 200 read tokens/sec, 100 write/sec**; most
requests cost **10 tokens** (so ~20 reads/sec on Basic). Basic-tier read buckets hold one second
of budget, capping bursts at the per-second rate. Throttled requests return **`429` with
`{"error": "too many requests"}` and no `Retry-After` header** — so a client must use its own
exponential backoff rather than reading a hint off the response. `GET /account/endpoint_costs`
lists non-default costs; `GET /account/limits` reports the caller's tier (both authenticated).

Rate limiting of *unauthenticated* market reads is not documented. `UNVERIFIED` — assume it is at
least as strict as Basic and stay well under it. A snapshot needs one request.

### 7.5 Finding the right market

Kalshi's tickers are structured and stable, which makes this much safer than Polymarket:

- Series ticker: `KXF1RACE`
- Event ticker: `KXF1RACE-{RACE}{YY}` — verified: `KXF1RACE-DUTGP26`
- Market ticker: `{event}-{DRIVER3}` — e.g. `KXF1RACE-DUTGP26-NOR`

Canonical call:

```
GET https://external-api.kalshi.com/trade-api/v2/markets?event_ticker=KXF1RACE-DUTGP26&limit=100
```

Discover the event ticker rather than hardcoding it:
`GET /events?series_ticker=KXF1RACE&status=open`. Assert `status == "active"` and that
`expected_expiration_time` matches the expected race date before using the prices — the same
stale-market defence as §6.5. Note that `close_time` (2026-09-06) is a long-stop and is **not**
the race date; `expected_expiration_time` (2026-08-23T19:00:00Z) is.

### 7.6 Response shape

`markets[]`, one binary market per driver:

- `ticker`, `event_ticker`, `status`
- `no_sub_title` — **this is the driver name field** (e.g. `"Andrea Kimi Antonelli"`). This is
  counter-intuitive and easy to get wrong; there is no `driver` field.
- `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars` — decimal **strings**, cast them
- `volume_fp`, `open_interest_fp` — string-encoded numerics
- `expected_expiration_time`, `rules_primary`

### 7.7 Gotchas

- The `*_dollars` fields are strings. Cast.
- Kalshi lists **22 drivers**; Polymarket lists 31 markets including an `"Other"` bucket. The two
  venues' outcome sets are not identical — join on driver, never by index or list position.
- Longshots sit at bid 0.00 / ask 0.01, so mid-price = 0.005 for a dozen drivers. Naively summing
  mids gives **1.14** (14% overround) — far worse than Polymarket's 1.035, almost entirely from
  the illiquid tail. Do not compare raw Kalshi mids to raw Polymarket prices; normalize first
  (§8.4).
- Prefer mid `(yes_bid + yes_ask)/2` over `last_price` for illiquid legs — a stale last trade can
  be hours old.

### 7.8 Verified output (2026-08-22)

Event `KXF1RACE-DUTGP26`, 22 active markets: Norris mid 0.380, Antonelli 0.255, Russell 0.245,
Hamilton 0.055, Leclerc 0.050, Piastri 0.045, Verstappen 0.035.

**Cross-venue agreement is strong** (Norris 0.365 vs 0.380; Antonelli 0.245 vs 0.255). The two
markets independently corroborate each other, which is the ideal baseline condition for A2: any
divergence our algo produces is attributable to our algo, not to one venue being mispriced.

---

## 8. Joining the sources

### 8.1 Why FastF1 *and* Jolpica (redundancy)

The roadmap locks both in "used redundantly (cross-validation/backup, not additional unique data
volume)". Concretely, they justify each other because they have **uncorrelated failure modes**:

- **Different upstreams.** Jolpica is a community-maintained REST service with its own database
  and its own uptime. FastF1 reads F1's own timing endpoints. A Jolpica outage, a schema change,
  or a slow post-session ingest does not affect FastF1, and vice versa.
- **Different latency after a session.** Jolpica's results appear after its ingest completes;
  FastF1 can read a session as soon as F1's timing data is posted. On a race weekend where the
  prediction must be locked before a deadline, having a second path to the grid is the difference
  between predicting and not predicting.
- **Different granularity.** Jolpica gives classifications (position, points, status). FastF1 gives
  lap-level and telemetry detail. For Phase A1 only the classification matters, so Jolpica alone
  is sufficient — but Phase A3's richer features (pace deltas, stint data) need FastF1, and
  building the redundancy now means A3 isn't a rewrite.
- **Silent-corruption defence.** Two independent sources for the same field turn a wrong value
  into a detectable disagreement instead of an undetected bad prediction.

**Rule:** Jolpica is authoritative for Phase A1. FastF1 is a *validator*, not a second opinion to
average. When both are available and the grid disagrees, **abort and surface the conflict** — do
not silently pick one. A disagreement means something upstream is wrong and a prediction made on
either value is untrustworthy.

### 8.2 Canonical entity keys

Four sources name drivers four different ways. This must be settled once, centrally, or every
consumer reinvents it badly:

| Source | Field | Example |
|---|---|---|
| Jolpica | `Driver.driverId` | `kimi_antonelli` |
| Jolpica | `Driver.code` | `ANT` |
| Kalshi | `no_sub_title` | `Andrea Kimi Antonelli` |
| Polymarket | `groupItemTitle` | `Kimi Antonelli` |
| FastF1 | `Abbreviation` | `ANT` |

**Canonical key: the FIA three-letter code (`ANT`, `NOR`, `VER`)**, sourced from Jolpica's
`Driver.code`. It is stable within a season, shared by Jolpica and FastF1 already, and is embedded
in Kalshi's market tickers (`KXF1RACE-DUTGP26-ANT`), which makes Kalshi a free exact join.

Mapping rules:

- **Kalshi:** parse the code from the market ticker suffix. Do **not** string-match
  `no_sub_title` — `"Andrea Kimi Antonelli"` vs `"Kimi Antonelli"` and `"Carlos Sainz Jr."` will
  defeat naive matching.
- **Polymarket:** no code is exposed, so an explicit name→code table is unavoidable. Maintain it
  in one place, seeded from the current entry list, and **fail loudly on an unmapped name** —
  never silently drop a driver, because dropping the favourite produces a confident wrong answer.
- **Polymarket's `"Other"` bucket** has no driver code. Keep it as a distinct pseudo-entry so the
  probability mass is accounted for; exclude it from per-driver comparisons.

### 8.3 Snapshot artifact

One JSON file per prediction run, written **before** any scoring:
`data/snapshots/{season}-{round}-{session}-{ISO8601 UTC timestamp}.json`

It must contain:

- `meta` — season, round, race name, circuit id, circuit lat/long, race start time (UTC and local),
  snapshot timestamp, git commit of the code that produced it
- `provenance` — per source: URL called, HTTP status, response timestamp, and for FastF1 the
  `unavailable` marker from §2 when applicable
- `grid` — per driver: code, driverId, full name, constructor, qualifying position, Q3/Q2/Q1 time,
  sprint result if the weekend has one
- `form` — per driver: championship position and points, constructor position and points, recent
  finishes
- `track_history` — per driver: prior results at this circuit
- `weather` — the hourly forecast rows covering the race window
- `markets` — per venue, per driver: raw price fields as returned **and** the normalized
  probability, plus the venue's computed overround

**Snapshots are append-only and immutable.** Never overwrite one; a new pull is a new file. They
are the training set for Phase A3 and the audit trail for A2 — a mutated snapshot destroys both,
and the damage is invisible until it's too late to fix.

### 8.4 Normalization policy

Raw prices are not probabilities. Verified overrounds differ sharply by venue (Polymarket 1.035,
Kalshi 1.14), so comparing raw numbers across venues compares two different distortions.

Mandated policy for Phase A1:

1. Per driver, per venue, take **mid = (bid + ask) / 2** where both sides exist; else
   `outcomePrices[0]` (Polymarket) or `last_price` (Kalshi).
2. Compute and **record** the venue overround = sum of mids across all outcomes.
3. Normalize by dividing each mid by that sum, so the venue's probabilities sum to 1.0.
4. Persist **both** raw and normalized values in the snapshot. Never discard raw.

This is proportional (multiplicative) de-vigging. It is the simplest defensible choice and is
known to over-penalize longshots relative to favourites — which matters here, because Kalshi's
overround is concentrated almost entirely in its zero-bid tail. It is adequate for A1, where the
comparison of interest is at the top of the field. Revisit at Phase A3 with real calibration data;
flagged in §9.

---

## 9. Open items

1. **Polymarket driver-name → FIA code table.** Must be seeded from the current entry list and
   maintained. Mechanical, but it needs an owner-confirmed source of truth for the 2026 grid, and
   mid-season driver changes will break it silently if unowned.
2. **De-vig method beyond A1.** Proportional normalization is specified above as the A1 default.
   Whether to move to a longshot-aware method (Shin, or power/odds-ratio) is a real modelling
   decision and should be made against calibration evidence, not preference — so it should wait
   for Phase A3 data rather than be decided now.
3. **Snapshot retention.** Nothing in the repo yet defines where snapshots live long-term or
   whether they are committed to git. They are small JSON and are the A3 training set, which
   argues for committing them; owner's call.
4. **FastF1 interpreter upgrade.** `brew install python@3.12` + venv, vs. installing `uv`. Owner's
   preference; not on A2's critical path (§2).
5. ~~**Lane B assumption to re-examine — flagged, not resolved here.**~~ **Superseded
   2026-08-26 by `03-live-telemetry-overtakes.md`.** That document confirms FastF1's live module
   cannot parse in real time (permanent, not a version gap) and goes further: OpenF1's free tier
   turns out to exclude *all* live-window access, not just some of it, and the only remaining
   zero-budget option — a direct, unofficial connection to F1's own live timing feed — carries a
   real Terms of Service risk and a documented history of F1 actively IP-blocking third-party
   clients that use it. This does not affect Lane A. **Updated 2026-08-26:** `03` is now a build
   spec, not research — the owner took the direct-connection route knowingly (`03` §5), scoped to
   personal research/development only (`03` §4.2). See `03` §2 for the source comparison and
   `03` §16 for what is still open.

---

## 10. Sources

- [Jolpica F1 API](https://api.jolpi.ca/ergast/f1/) · [rate limits](https://github.com/jolpica/jolpica-f1/blob/main/docs/rate_limits.md)
- [Open-Meteo](https://open-meteo.com/) · [pricing/limits](https://open-meteo.com/en/pricing) · [terms](https://open-meteo.com/en/terms)
- [Polymarket docs](https://docs.polymarket.com/) · [rate limits](https://docs.polymarket.com/api-reference/rate-limits)
- [Kalshi docs](https://docs.kalshi.com/) · [API keys](https://docs.kalshi.com/getting_started/api_keys) · [rate limits](https://docs.kalshi.com/getting_started/rate_limits) · [environments](https://docs.kalshi.com/getting_started/api_environments) · [get-markets](https://docs.kalshi.com/api-reference/market/get-markets)
- [FastF1 docs](https://docs.fastf1.dev/) · [live timing client](https://docs.fastf1.dev/livetiming.html) · [PyPI](https://pypi.org/project/fastf1/)
