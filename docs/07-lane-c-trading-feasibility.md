# 07 — Lane C Trading: Feasibility & Path

Status: **researched 2026-08-26; not approved, no implementation.** This is a research memo
toward Lane C (`00-roadmap.md`'s Phase C0–C3), in the same spirit as `03` is for Lane B —
**nothing here authorizes writing code**, and per `welcome.md` no real-money trading happens
without a separate explicit go-ahead on top of an approved spec. Read `welcome.md`,
`00-roadmap.md` (Lane C section + Lane C open decisions), and `01-data-pipeline.md` §6–§8 first.

Scope of this memo, per the owner: **feasibility and the buildable path only.** It does not
work out order-execution auth flows, position-sizing math, or a full venue-by-venue trading
design — those belong in a Lane C spec written after the blockers below are resolved.

---

## 1. Verdict, layered

| Question | Answer |
|---|---|
| Can we **measure** whether our prediction has a tradeable edge? | **Yes, now.** Zero budget, no credentials, no approval. This is the first buildable increment and it does not exist yet. |
| Do we **have** a measured edge to trade on? | **No.** In every market type, for both venues, the evidence is either absent or negative (see §2). This is the tightest constraint on Lane C, ahead of any API question. |
| Can we **paper-trade** a strategy end to end? | **Yes, now** for Lane A markets — off the Gamma/Kalshi reads `snapshot.py` already makes, plus (for order mechanics) Kalshi's public demo host. Zero budget. |
| Can we **place live trades** off Lane A predictions (winner, podium)? | **Blocked**, not on tech but on: (a) no measured edge, (b) venue access depends on the owner's jurisdiction (§3), (c) real-money approval per `welcome.md`, (d) risk controls must be built first (§6). |
| Can we trade **in-race** markets (overtakes, corner-level)? | **The overtake market does not exist** — measured 2026-08-26 across both venues, open and closed (§10.1). What *does* exist is a race-winner market that trades heavily through the race (48.5% of lifetime volume inside the 2h window, §10.3). This row's original answer — "blocked behind Lane B" — was wrong about *why*: the blocker was never only the feed. |

The short version: **the trading layer is not the hard part — the edge is.** Lane C's premise,
stated in `welcome.md` and the roadmap, is "our prediction is the edge that drives the trade."
That edge has not been demonstrated in any market this project tracks.

---

## 2. The edge problem — this is the blocker

Lane C is worth building only if this pipeline's probabilities beat the market's. Everything the
project has measured so far says they do not, or says nothing:

- **Winner, A1 (production model), live:** Dutch GP 2026, the one real pre-race test. A1 called
  the winner correctly (NOR) but was **worse calibrated than the market mean** — Brier 0.5499
  vs. market_mean 0.5416, vs. Polymarket 0.5345. `n = 1`.
  (`data/snapshots/2026-12-race-20260823T031058Z-postrace.json`.)
- **Winner, A3 (trained model):** lost to A1 over 48 held-out races (2024–2025), Brier 0.6349 vs
  0.6179, and barely cleared the grid-only floor (0.6054). `05` §6.3 pre-registered that reading:
  "if A3 does not beat grid-only by a clear margin, the other six features are not earning their
  place." Phase A3 is closed as a negative result.
- **Podium / points / fastest lap:** **zero** genuine pre-race market comparisons exist, ever
  (`00-roadmap.md` Phase A4, "what's still open"). The Dutch GP predates the market-pulling code;
  Monza's podium market was too illiquid to price (§5). There is no evidence in either direction.

So even the "scope Lane C down to settled markets" fallback (`00-roadmap.md` Phase C1) runs into
the same wall: there is no measured edge in the settled markets either. A trading bot with no
demonstrated edge is a random position-taker paying fees.

**Second-order problem, derivable from `01` §8.4:** the de-vig method matters for trading in a
way it doesn't for a calibration report. `01` §8.4 mandates proportional de-vig and documents
that it **over-penalizes longshots** — and Kalshi's ~14% overround is almost entirely in its
zero-bid tail (`01` §7.7). Computing `edge = p_algo − p_market_normalized` will therefore
**manufacture systematic fake edges in the longshot tail** — which is exactly where the books
are thinnest (§5) and where a naive bot would most want to bet. `01` §9 item 2 already lists the
de-vig choice as unresolved and gated on race count; for Lane C it is load-bearing, not a
modelling nicety.

---

## 3. Venue access & regulatory (jurisdiction-dependent)

The owner has not stated a jurisdiction, so both branches are recorded. **This fork gates half
the Lane C analysis and must be resolved before a spec.**

### Polymarket
- **Polymarket Global** (the `gamma-api` / CLOB platform Lane A reads) has **geo-blocked US
  persons since the January 2022 CFTC settlement.** Reads are unaffected; trading is not
  available to US persons on this platform.
- **Polymarket US** (QCX LLC) is a separate, CFTC-regulated Designated Contract Market, created
  via the July 2025 QCEX acquisition; CFTC issued an Amended Order of Designation 2025-11-25, and
  Polymarket filed a formal application 2026-04-28 to resume US retail trading. A new CFTC probe
  opened June 2026. **As of this writing US retail trading on Polymarket US is not yet
  broadly open**, requires full KYC (ID + SSN via the iOS app), and is fully-collateralized only
  (no margin) — verify current status at spec time, this is moving.
- **Non-US persons:** Polymarket Global's CLOB is the accessible path. Order execution is a
  different API surface than Lane A's Gamma reads — CLOB, EIP-712 wallet-signed orders, USDC on
  Polygon, L2 credentials. `01` §6.2 explicitly scoped this out for Lane A ("Gamma only, never
  CLOB"); that decision is reopened for Lane C and not yet made.

### Kalshi
- **CFTC-regulated, US-based.** Legal for US persons; generally not available outside the US.
- **Automated / programmatic trading is explicitly supported** — Kalshi publishes a REST trading
  API for exactly this. Not a blanket exemption from market-conduct rules (no wash trading,
  spoofing, self-matching), and the ToS should be read directly at spec time, but there is no
  "no bots" prohibition to design around.
- Order execution: RSA-PSS request signing (`01` §7.3 already documents the scheme, for
  completeness — Lane A does not implement it), a funded account, KYC.
- **Kalshi runs a public demo host** — `external-api.demo.kalshi.co` (`01` §7.2). Order
  placement and fill handling can be built and tested **end to end with no money at risk.** This
  is the single most useful asset for Lane C's C2 work.

### Net
- **US owner:** Kalshi is the only realistic live venue. Polymarket is read-only for the market
  baseline until Polymarket US opens.
- **Non-US owner:** Polymarket CLOB is the live venue; Kalshi likely unavailable.
- Either way, **the cross-venue comparison that `welcome.md` calls the headline feature survives**
  — both venues' *reads* stay public and keyless. Only execution is venue-gated.

---

## 4. Fees vs. edge

A fee larger than the edge is a feasibility answer by itself.

- **Kalshi:** taker fee `= 0.07 × C × (1 − C)` per contract, peaking at **1.75¢ per contract at
  C = 0.50**; maker fee is 25% of that. On a 50¢ contract that's a **3.5% round-trip taker
  cost** (1.75¢ in, 1.75¢ out, on a $1 notional pair). Shrinks toward the price extremes. ACH
  deposit/withdraw free.
- **Polymarket:** historically ~0% maker / 0–2% taker; category-based taker fees rolled out
  through 2026 (~0.75% sports, up to ~1.8% crypto), sells often not charged, some markets free.
  Gas is paid by the relayer, not per-fill (~$0.003–0.005 equivalent, immaterial).

**Implication:** at Kalshi's mid-price fee, a strategy needs a **>3.5% calibration edge on
taker fills** just to break even before variance, or must be a maker (resting orders, no
guaranteed fill). The Dutch GP result had A1 *behind* the market. The fee math says: only trade
where the measured edge is large and the price is away from 50¢, or don't take liquidity.

---

## 5. Liquidity / capacity

Even with an edge, there may be nothing to win at this project's scale:

- Winner markets: Kalshi/Polymarket Dutch GP winner volume was in the **$1.4k–$14k** range per
  driver (Polymarket's total event ~$183k); `04`/roadmap A4 notes.
- Podium markets: Monza's, checked live, priced **almost every driver near 0.5 on $0–300
  volume** vs. the winner market's thousands (`00-roadmap.md` Phase A4).

A market priced at 0.5 on ~$0 volume is **not mispriced, it is absent** — no edge can be traded
into a book that thin. Realistic position size into the liquid winner markets is **tens of
dollars**; a genuine 5pp edge on a $30 stake returns **~$1.50 before fees.** `00-roadmap.md`'s
Lane C open decisions already separate this from the A4 "snapshot timing" reading — for Lane C it
is a hard capacity ceiling, and it means Lane C is a **learning / proof-of-concept exercise, not
an income stream**, at least until it demonstrates edge on paper.

---

## 6. Risk controls come first (C3 before C2)

`00-roadmap.md` Phase C3 already sequences this correctly and it's worth restating: position
limit per market, max loss per race/session, and a kill switch are **decided and built before
any order-placement code runs against real money.** "An auto-trading bot with no cap on it is
the actual failure mode here, independent of prediction quality." A paper-trading harness (§7)
is the right place to exercise these before they gate anything real.

---

## 7. The buildable path (zero budget, no approval needed)

The first Lane C increment is **not** order execution. It is the evidence Lane C is missing.
None of this needs a paid tier, a credential, or a real-money go-ahead:

1. **Edge-measurement harness.** For every race this pipeline snapshots: log `p_algo`,
   `p_polymarket`, `p_kalshi`, and the realized outcome, for winner *and* the Phase A4 outcome
   types. `score.py`/`postrace.py` already compute most of this for winner — extend the record,
   accumulate `n`, and track "is the algo better calibrated than each venue" as a running series
   rather than a single race. This directly produces the number §2 says is absent.
2. **Paper-trading simulator.** Given a snapshot's prices and a strategy (e.g. "buy YES when
   `edge > X` and price is in `[a, b]`"), simulate fills at the quoted book, apply the real fee
   schedules from §4, settle against the actual result. Run it over the accumulating race set.
   Zero money, and it exercises the C3 risk controls (§6) before they matter.
3. **Order-execution dry run on Kalshi demo** (`external-api.demo.kalshi.co`). Only once §1–§2
   show an edge worth pursuing: build and test the RSA-PSS signing + order + fill path against
   the demo host. Still zero budget, still no real-money approval — it's the C2 surface proven
   out without risk.
4. **Everything past that** — funding an account, live orders, real position sizing — needs a
   Lane C spec, the jurisdiction fork (§3) resolved, risk controls (§6) locked, and the separate
   real-money approval `welcome.md` requires. Not before.

**De-vig decision (§2) should be made against the harness data from step 1**, same as `01` §9
item 2 says — it's the same scarce resource (live-snapshotted races with outcomes) blocking both.

---

## 8. Open items — the owner's call, not guessable

1. **Jurisdiction (§3).** Gates which venue Lane C can execute on, and whether Polymarket is
   live-tradeable at all. Nothing downstream of C1 can be specced without this.
2. **Is Lane C worth pursuing given §2 and §5?** The edge is unproven and the capacity is tens
   of dollars. Reasonable answers: (a) build the §7 harness, let the edge question answer itself
   over a season, revisit; (b) treat Lane C as a portfolio/learning artifact where "we built a
   safe paper-trading bot and honestly showed the edge wasn't there" is itself the deliverable;
   (c) shelve it. Not (d) build live execution now.
3. **Reopen `01` §6.2's "Gamma only" for Lane C?** CLOB is required for Polymarket execution.
   Decision deferred until §3 and item 2 resolve.
4. **De-vig method (§2, `01` §9.2).** Proportional de-vig fabricates longshot edges; for a
   trading bot this is a bug. Resolve against harness data before any execution code.
5. ~~**In-race markets are downstream of Lane B (`03` §5).** No separate Lane C decision needed
   until Lane B's data-source question resolves.~~ **Superseded 2026-08-26 by §10.** Lane B's
   data-source question did resolve, and the market check that nobody had run came back split:
   no overtake market exists on either venue, but Kalshi's *winner* market trades through the
   race with real volume. The live decisions are now §10.6's, not this one.
6. **Both venues' current ToS on automated trading, read directly** — Kalshi's looks permissive,
   Polymarket US's is unwritten here; confirm at spec time, both move.

---

## 10. Gate 4 — do in-race / overtake markets actually exist? (measured 2026-08-26)

Run as **Lane B gate 4** (`00-roadmap.md`'s Lane B gate list): the roadmap assumed in-race
overtake markets exist and merely "need Lane B's live feed to exist first." Nobody had checked the
markets themselves. This section is that check, run the same way `04` §2 ran the Phase A4
market-coverage check — by fetching real events and reading real rules text, not by assuming a
name implies a market.

**Headline: the answer splits in two, and the two halves point opposite ways.**

| Question | Answer |
|---|---|
| Does a **corner-level / overtake** market exist on either venue? | **No.** Neither venue, never, on any race. Evidenced negative (§10.1). |
| Do F1 markets **trade during the race**, with real volume? | **Yes on Kalshi — measured.** 48.5% of the Dutch GP winner market's entire lifetime volume traded inside the 2-hour race window (§10.3). |

So Lane B's stated trading rationale — *corner-level overtake probability, traded on Polymarket or
Kalshi* — is aimed at a market that does not exist. But the premise underneath it ("fast-moving
in-race information can outpace how quickly these markets reprice") turns out to be **more**
supported than the roadmap assumed, just against a different market. See §10.5.

### 10.1 No overtake market exists — the evidenced negative

Absence today would not prove absence (it is between race weekends: Dutch GP was 2026-08-23,
Monza is 2026-09-04/06), so both sweeps deliberately included closed/settled/historical markets.

**Polymarket (Gamma only, per `01` §6.2).** Swept `/events` by `tag_slug` across `f1`,
`formula-1`, `formula1`, `motorsports`, `sports`, each at `closed=true`, `closed=false`, and with
no `closed` filter, paginating on `offset`. Pagination was verified rather than assumed
(`offset=0` vs `offset=100` return disjoint id sets). **985 unique events pulled, 387 of them F1,
including 333 closed ones, spanning 2023–2026.** Market types found, by frequency:

> winner (49), safety-car (23), red-flag (20), driver/constructor pole (38), driver/constructor
> fastest lap (38), practice 1/2/3 fastest lap (43), h2h (30), constructor-scores-1st (22),
> driver podium (17), most-constructor-points (11), sprint winner (9), winning margin (1),
> championship//season props, plus novelty markets ("will it rain during the…").

No overtake, position-change, positions-gained, lead-change, or lap-level market at any point in
the corpus. A keyword scan over title + slug + description for `overtake / overtak / pass /
position change / lead change / lap 1 / first lap / opening lap / gain position / places gained /
most positions / corner / DRS / in-race` returned **zero** F1 hits (the `DRS` and `live` hits were
cricket — DRS is cricket's Decision Review System). Gamma `/public-search` for `overtake`,
`overtakes`, `f1 overtake`, `position change`, `lead change`, `first lap` likewise returned no
such F1 market; `f1 overtake` just fuzzy-matches championship and race-winner events.

**Kalshi (`external-api.kalshi.com`, per `01` §7.2).** Enumerated **all 13,545 series** via
`GET /series` and filtered titles/tickers for F1 — 51 candidates, of which ~30 are genuinely
Formula 1 (the rest are College Football `KXNCAAF1H*`, Turkish football `KXTFF1LIG*`, and the
`F1` movie's Rotten Tomatoes score). Then pulled `GET /markets` **across all statuses** for the
F1 series. A keyword scan over all 13,545 series titles for `overtake / overtaking / pass /
position / lead change / lap / corner / safety car / red flag / yellow flag / pit / margin /
in-play / live / in-game` returned, for motorsport, only pole-position and fastest-lap series.
Kalshi does list `corner` markets (22 series) — all soccer. **No F1 overtake market, and, unlike
Polymarket, no F1 safety-car or red-flag market either.**

This matches the prior the task flagged as worth testing: corner-level overtake trading resembles
in-play sportsbook micro-betting (bet365, DraftKings), and neither prediction-market venue lists
anything of that shape for F1.

### 10.2 The closest thing that does exist — `KXF1BIGGESTMOVER`, and it's a one-race pilot

Kalshi's `KXF1BIGGESTMOVER` ("F1 Biggest Mover") is the only position-change market found on
either venue. Rules text read in full, per the `KXF1RETIRE` discipline (`04` §2):

> "If {driver} has the largest positive differential between their starting grid position and
> their finishing position in the main race at the 2026 Dutch Grand Prix … then the market
> resolves to Yes."

That is **net grid-to-flag position change, settled after the race** — one number per driver per
race. It is not an overtake market, carries no corner-level or lap-level component, and a Lane B
live feed is not required to trade it. `rules_secondary` is careful (dead-heat $1/N payout,
pit-lane starts treated as one below the last gridded driver, DNF/DSQ resolves No unless
classified), so it is a well-formed market — just not the one Lane B was aimed at.

**It is a single-race experiment, not a standing series.** All 22 markets in the series are
`DUTGP26`; `created_time` is 2026-08-19, five days pre-race; and `GET /events?status=open` for
`KXF1BIGGESTMOVER` returns **no Monza event** while `KXF1RACE`, `KXF1RACEPODIUM`, `KXF1TOP10`,
`KXF1TOP5`, `KXF1FASTLAP`, and `KXF1TOPCONSTRUCTOR` all have `ITAGP26` open. Whether it ever
relists is **UNVERIFIED**. Liquidity was thin: 17,524 contracts lifetime across all 22 legs
(≈1% of the winner market's 1,703,263), and its four most-traded legs (`-VER` 2,959, `-HAD` 2,854,
`-ALO` 2,826, `-BEA` 2,786) together traded just 2,478 contracts in-race across 25 leg-minutes,
with `-HAD` recording **zero** in-race trades at all. Polymarket has no equivalent market of any kind.

### 10.3 Kalshi F1 markets trade *during* the race — measured, not inferred

The discriminating question for Lane B is not "is there a market about an in-race event" but
"is a book open and trading while the cars are running." Measured against the Dutch GP, whose
timestamps are final. Lights-out is **2026-08-23T13:00:00Z**, taken from this project's own frozen
snapshot (`data/snapshots/2026-12-race-20260823T031058Z.json`, `meta.race_start_utc`), not assumed.

Kalshi books did not close at lights-out. Across the Dutch GP, `close_time` sits **+5.05h** past
lights-out for every race-outcome series (`KXF1RACE`, `KXF1RACEPODIUM`, `KXF1TOP10`, `KXF1TOP5`,
`KXF1FASTLAP`, `KXF1BIGGESTMOVER`, `KXF1TOPCONSTRUCTOR`) — comfortably past the end of the race —
while the qualifying-dependent series closed the evening before (`KXF1POLE` at −17.58h).

Volume, per one-minute candle, `KXF1RACE-DUTGP26`, **all 22 driver legs**:

| | contracts |
|---|---|
| Lifetime volume (`volume_fp` on the market record) | 1,703,263 |
| Recovered by the candlestick sweep (open_time → close_time) | 1,703,816 (**100.0%** coverage) |
| Traded **inside the 13:00–15:00Z race window** | **826,229 = 48.5% of lifetime** |
| Race minutes with ≥1 trade | **120 of 120** |

Nearly half the market's entire life-of-contract volume traded while the race was running, and
there was not a single silent minute. Prices moved accordingly — `-NOR` went 0.37 → 0.32 → 0.16
→ 0.28 → 0.48 → 0.82 → 0.96 across the two hours, with 25,895 contracts in the 14:53Z minute
alone. Every other F1 market type traded in-race too. Stated as in-race contract counts rather than
shares, because only `KXF1RACE` was swept over its full `open_time → close_time` life — a
percentage against a shorter window would understate pre-race volume and is not comparable to the
48.5% above. Across each series' four most-traded Dutch GP legs, contracts traded inside
13:00–15:00Z: winner 649,575, podium 36,614, fastest lap 7,484, top-5 3,259, top-constructor
2,941, biggest-mover 2,478, top-10 2,194.

> **Scope note — this used an endpoint outside the locked decision.** `01`'s locked decision scopes
> Kalshi to `GET /markets`. The measurement above uses
> `GET /series/{series}/markets/{ticker}/candlesticks` (same host, read-only, credential-free,
> unauthenticated). It is what turned this finding from inference into measurement, so it is
> flagged here rather than left for a future reader to discover. The claim rests on **`volume_fp`
> per one-minute candle = executed contracts**; the `price` OHLC block is corroborating colour, not
> load-bearing. Whether to fold this endpoint into the locked scope is an open item (§10.6).

### 10.4 Polymarket: books stay open past lights-out, but in-race *execution* is UNVERIFIED

Split deliberately, because only the first half is measured.

**Verified — the book does not close at lights-out.** Across 4,132 F1 markets carrying both
`gameStartTime` and `closedTime`, the median `closedTime − gameStartTime` is **+6.88h**. The
Dutch GP safety-car market is the clean single case: `acceptingOrders: true`, `closed: false`,
bestBid 0.002 / bestAsk 0.008, **three days after** its `gameStartTime` of 2026-08-23T13:00Z. A
book cannot be pre-race-only while accepting orders 72 hours past race start. Every F1 market
carries `clearBookOnStart: true` and a `secondsDelay` of 1–3s — the latter being a live-trading
latency guard, which is only meaningful if a book trades live.

**UNVERIFIED — whether trades actually executed during the race window, and at what volume.**
Gamma exposes no timestamped series (only lifetime/24h/1wk aggregates), and Polymarket's
timestamped trade history lives on the CLOB/Data APIs, which `01` §6.2's locked decision scopes
out. Total `volumeNum` on a settled market does not say when the volume arrived, so it is not
substitutable. This is measurable, but only by reopening that scope — see §10.6.

For scale, Polymarket's Dutch GP winner event turned over **$566,791** (Gamma `volume` is USD;
Kalshi `volume_fp` is contract count at $1 notional, so the two venues' numbers are not directly
comparable without that conversion).

### 10.5 What this means for Lane B — flagged, not decided

Gate 4 does not come back as a clean pass or fail, and the reframing belongs to the owner:

- **The corner-level overtake premise has no market.** Nothing on either venue settles on
  overtakes, position at a corner, or lap-level position change. If Lane B's justification is
  trading, gate 3's "corner-level overtake model" has nothing to trade into, and no amount of
  Lane B feed quality changes that.
- **But the liquid in-race market is `race winner`,** and it is very liquid exactly when Lane B's
  feed would be live — 826k contracts inside two hours, repricing every single minute. Trading
  that needs a **live win-probability model** (given current positions/gaps/laps remaining, who
  wins?), which is a different and arguably simpler model than corner-level overtake probability.
  Lane B's *feed* is directly useful to it; Lane B's *specced model layer* is not.
- **Gate 2 (broadcast delay) gets sharper, not weaker.** Against a market repricing every minute
  for two hours, a delay of seconds vs. minutes is precisely the discriminator, and the Kalshi
  book stays open ~3h past the flag. Gate 2 was already next in the owner's order; this finding
  raises its value rather than changing its place.
- **The unresolved fork still decides this.** Lane B has been carrying two justifications at once
  — *it's for trading* and *it's for learning streaming architecture / a portfolio piece*. Gate 4
  answers them differently: it kills the corner-level-overtake **trading** rationale while
  strengthening a different trading rationale, and it gates nothing at all if Lane B is a learning
  goal. That fork has not been picked and is not picked here.

Consistent with `03` §4.3's interlock, none of the above authorizes Lane B output reaching a Lane C
component; that remains a separate dated decision.

### 10.6 Open items from gate 4

1. **Which Lane B justification governs — trading or learning?** Unpicked. Gate 4's result only
   bites under the first. Owner's call; see §10.5.
2. **Does the in-race *winner* market replace corner-level overtakes as Lane B's trading target?**
   If yes, gate 3 changes shape (live win-probability, not corner-level overtake) and `03`'s model
   layer needs rewriting. Not decided here.
3. **Fold Kalshi's `candlesticks` endpoint into the locked market-data scope?** It is
   unauthenticated, read-only, same host, and is the only way to measure *when* volume traded.
   Used once here under an explicit flag (§10.3).
4. **Reopen Polymarket CLOB/Data read-only, for measurement?** It is the only way to close the
   UNVERIFIED in §10.4. Distinct from §8 item 3, which is about CLOB for *execution*.
5. **Does `KXF1BIGGESTMOVER` relist?** UNVERIFIED — one race, no Monza event. Worth re-checking at
   Monza; if it becomes a standing series it is a genuine (if illiquid) position-change market that
   Lane A could price without any live feed at all.

---

## 11. Which markets are actually worth trading? — live structure survey (measured 2026-08-26)

Runs the question the owner asked directly: given this project's model and its roadmap, which
F1 market is the one to trade? Measured by pulling every open F1 market on both venues live —
Polymarket Gamma `/events?tag_slug=f1` (all nested markets, `outcomePrices` + `spread` +
`liquidityNum`), Kalshi `/markets?series_ticker=KXF1*&status=open` (`yes_bid_dollars` /
`yes_ask_dollars` / `volume_fp` / `open_interest_fp`). Not a recommendation to trade anything —
§2's edge blocker and `welcome.md`'s approval rule both still stand.

### 11.1 The market map, by venue

| Market type | Polymarket | Kalshi | Project has a model? |
|---|---|---|---|
| **Drivers' Champion** (season) | **$201M vol, $14.3M liq, 0.1–1.1% spreads** | *not listed* | **No** |
| Constructors' Champion (season) | $28.5M vol, ~0.1–1% spreads | $2.5M+ OI, MER 2¢ spread, FER/MCL 1¢ | No |
| **Per-race winner** | $48k/race, 1–2¢ on 5 contenders, $5–12k liq/leg | **1¢ on 5 contenders 10 days out**, OI 5k–50k contracts/leg | **Yes (A1)** |
| Per-race podium | $2.5k, spreads 6–86¢ (mostly absent) | listed, thin | Yes (A4 exact top-3) |
| Head-to-head (finish higher) | **$22 total vol**, spreads 43–93¢ — dead | *not listed* | Yes (A1 teammate-H2H feature) |
| Points / top-10 / top-5 | *not listed* | listed, thin | Yes (A4) |
| Fastest lap (race) | ~$45, near-zero | listed, thin | Yes (A4) |
| Pole / practice fastest lap | $100–600, near-zero | pole listed | No |
| Safety car / red flag | **real book** (~$5.5k Dutch SC market) | *not listed for F1* | No (A4 reliability feature is nearest) |
| Constructor-scores-1st | $470, near-zero | — | Partial (A4 points sim) |

### 11.2 Championship markets are deep — and that's the problem, not the opportunity

Polymarket's 2026 Drivers'-Champion market is the single most liquid F1 market on either venue by
two orders of magnitude: **$201M lifetime volume, $14.3M live liquidity, spreads of 0.1–1.1%** on
every real contender (NOR 0.0915, ANT 0.7495, RUS 0.0585, HAM 0.068, LEC 0.018). The YES legs sum
to ≈1.006 — a ~0.6% book, i.e. essentially no vig to fade. Constructors' is the same shape at
smaller size, and Kalshi's constructors market agrees with Polymarket's to within 1–2pp on every
team (MER 0.865 vs 0.87/0.89, FER 0.1105 vs 0.10/0.11, MCL 0.986 vs 0.97/0.98).

This **corrects §5's capacity ceiling** — "tens of dollars, learning exercise not income stream"
is wrong for *this* market type; you could put real money to work here. But the logic runs the
other way from how that reads:

- A $201M book with a 10bp spread and cross-venue agreement to 1pp is the textbook definition of
  an **efficient market**. There is no structural vig to harvest and no reason to expect a
  hand-set or lightly-fitted model to beat that crowd.
- **The project has no championship model at all.** A1/A3/A4 are per-race. The only bridge is a
  season simulation (see §11.5), which is a *derivative* of the per-race model and inherits its
  calibration — and A1's one measured per-race result was a **loss to the market mean** (§2).

So capacity and model-fit are in **disjoint markets**: the deep market is one we can't model and
probably couldn't beat anyway; the market we can model is thin.

### 11.3 Per-race winner is the only real overlap — and Kalshi is the better venue for it

Per-race winner is the one market where all three of {a model output exists, a real book exists,
the spread is tight enough for an edge to survive} are true at once.

- **Kalshi, Monza, measured 10 days out (2026-08-24 open):** NOR 0.31/0.32, RUS 0.27/0.28,
  ANT 0.08/0.09, HAM 0.11/0.12, LEC 0.09/0.10, VER 0.04/0.05, PIA 0.04/0.05 — **1¢ spreads** on
  all seven, with open interest already at NOR 12.7k / ANT 49.6k / HAM 8.5k / VER 7.7k / LEC 5.2k
  contracts. This **contradicts A4's "markets are only liquid close to lights-out"** — that
  observation was Polymarket-specific (`00-roadmap.md` Phase A4, Monza podium). Kalshi's per-race
  winner book is live and tight well before the weekend.
- **Polymarket, Monza:** $48k event volume, NOR/RUS 0.29 mid at 2¢ spread, ANT 0.08, HAM 0.115,
  $6–12k liquidity per contender leg. Tradeable at tens of dollars, as §5 says.
- **Cross-venue divergences exist but are inside the round-trip cost:** NOR is Polymarket 0.29
  vs Kalshi 0.315; RUS is Polymarket 0.29 vs Kalshi 0.275. A 2–3pp gap — but crossing both spreads
  plus Kalshi's mid-price fee (§4, ~1.75¢/contract each way) eats it. Worth *monitoring* as the
  most edge-shaped thing on the board; not an arb today.
- **Kalshi vig is concentrated exactly where §2 warned.** The Monza winner ask-side legs sum to
  ≈1.26 — ~26% overround — almost all of it in the 1¢ tail (17 no-hope drivers at 0.01 ask).
  Confirms `01` §7.7 and §2: only ever price the ~5 contenders, never the tail, and prefer prices
  away from 50¢ where the fee also bites less.

### 11.4 The wide-spread trap, now measured, not predicted

§2 predicted proportional de-vig would "manufacture systematic fake edges in the longshot tail."
Here is the same failure arriving through a **second door — the midpoint**:

Polymarket's podium and H2H markets report an `outcomePrices` midpoint even when the book is
empty. Monza podium examples, live: Gasly 0.368 (**spread 0.724**), Ocon 0.3685 (0.723),
Lindblad 0.431 (0.858), Hadjar 0.4035 (0.801) — every one a near-coin-flip midpoint sitting on a
book whose best bid is ~0.01. Computing `edge = p_algo − midpoint` on these would "find" a
~35pp edge on Gasly making the Monza podium, entirely a spread artifact, and precisely on the
midfield legs a naive bot most wants to bet.

**Rule for the §7 harness:** every edge number is computed against `bestBid`/`bestAsk` (Kalshi:
`yes_bid_dollars`/`yes_ask_dollars`), with the spread printed next to it, and any leg with spread
> ~5¢ is marked "no book" and excluded from strategy evaluation — not fed in with a midpoint.

H2H deserves a specific note: it is the **best structural fit for A1** (A1 already computes a
teammate head-to-head feature, and H2H removes the softmax-normalisation problem entirely), but
the Polymarket book is **dead** — $22 lifetime volume across all 24 pairs, spreads 43–93¢. Good
model fit, no market. "Watch at Monza," not a candidate.

### 11.5 Season simulator — the bridge, and why it isn't a shortcut

`lib/simulate`'s Plackett-Luce machinery (already built for A4 podium/points) could be run
forward over the remaining calendar to produce championship probabilities, which would put a
project number next to the one deep, liquid market (§11.2). Worth knowing the shape of, but:

- It is a **derivative of A1**. Each simulated race draws from A1's win-strength scores, so a
  season sim **compounds** A1's per-race calibration error rather than escaping it. It is
  downstream of fixing per-race calibration, not a route around it.
- If it is ever tried, the leg to test is the **tail** — NOR 0.0915, HAM 0.068, RUS 0.0585,
  LEC 0.018 — not the ANT favourite, and the check is whether the sim's tail probability differs
  from the market by **more than the ~1% spread**. Do not assume it does; there is no evidence
  yet either way.

### 11.6 Adjacency worth naming: safety-car / red-flag markets

Polymarket lists per-race **safety-car** and **red-flag** markets with a genuine (if small) book
— the Dutch GP safety-car market carried ~$5.5k. Kalshi lists neither for F1. The project has no
model for these; A4's DNF reliability feature is the nearest relative, and there is no second
venue to corroborate a price against. Named here as a modelling adjacency the roadmap doesn't
mention, not a recommendation.

### 11.7 Recommendation, layered

1. **If Lane C ever trades, the first and possibly only market is per-race winner, top ~5
   drivers, both venues.** It is the only place model + book + tight spread coincide. Kalshi's
   book is live earlier; Polymarket adds the cross-venue check. Still gated on everything in §2
   (no measured edge), §7 (harness first), §3 (jurisdiction), §6 (risk controls).
2. **Build the §7 edge-measurement harness against winner markets specifically**, pulling both
   venues' `bestBid`/`bestAsk` (not midpoints) at snapshot time, logging `p_a1` vs both books
   vs realised result, accumulating `n`. This is the missing evidence and it is free.
3. **Watch at Monza, don't trade:** per-race podium (Norris podium leg has a real 11¢-spread
   book; most legs don't), and whether `KXF1BIGGESTMOVER` relists (§10.2).
4. **Not candidates:** H2H (dead book), constructor/pole/practice/fastest-lap (near-zero
   volume), championships (efficient, and unmodelled).
5. **De-vig choice (§2, `01` §9.2) is now urgent for winner markets**, because Kalshi's 26%
   tail-heavy overround is exactly the pathology proportional de-vig mishandles. Decide it
   against harness data before any strategy number is quoted.

### 11.8 Erratum for `01` — Kalshi API field names have changed

`01` §7.6 documents Kalshi market fields as `volume`, `yes_bid`, `yes_ask`, `open_interest`,
`liquidity`. As of 2026-08-26 the `/markets` and `/markets/{ticker}` endpoints return those as
**`null`**; the live values are under `volume_fp`, `yes_bid_dollars`, `yes_ask_dollars`,
`open_interest_fp`, `liquidity_dollars` (and `last_price_dollars`). Any code written against
`01` §7.6's names will silently read nothing. `01` §7 should be re-verified and updated.

---

## 9. Sources

- Polymarket US / CFTC / QCEX: [Cryptobriefing](https://cryptobriefing.com/polymarket-us-regulatory-approval-cftc/),
  [dropstab: Is Polymarket Legal 2026](https://news.dropstab.com/research/is-polymarket-legal),
  [Polymarket KYC 2026](https://www.copytradeinsider.com/blog/polymarket-kyc-requirements/)
- Kalshi automated trading / ToS: [Kalshi trading bot API automation (2026)](https://tech-insider.org/prediction-markets/kalshi-trading-bot/),
  [botforkalshi: complete guide](https://www.botforkalshi.com/blog/kalshi-trading-bots-complete-guide)
- Kalshi fees: [Kalshi Help Center — Fees](https://help.kalshi.com/en/articles/13823805-fees),
  [Kalshi fee schedule PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf)
- Polymarket fees: [predictionhunt: Polymarket fees 2026](https://www.predictionhunt.com/blog/polymarket-fees-complete-guide),
  [zenhodl: maker/taker/CLOB](https://zenhodl.net/blog/polymarket-fees-explained-maker-taker-clob)
- Gate 4 (§10) is measured from the venues' own APIs, not from secondary sources: Polymarket
  Gamma `/events` + `/public-search`, Kalshi `/series`, `/events`, `/markets`, and
  `/series/{s}/markets/{t}/candlesticks`. Lights-out from
  `data/snapshots/2026-12-race-20260823T031058Z.json` `meta.race_start_utc`.
- Internal: `00-roadmap.md` (Lane C, Phase A3 §6.4, Phase A4), `01-data-pipeline.md` §6–§9,
  `03-live-telemetry-overtakes.md` §2.4/§5, `data/snapshots/2026-12-race-20260823T031058Z-postrace.json`
