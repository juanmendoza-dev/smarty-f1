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
| Can we trade **in-race** markets (overtakes, corner-level)? | **Blocked behind Lane B, which is itself blocked.** `03` §2.4: no live data source is simultaneously zero-budget, genuinely live, ToS-clean, and stable. Lane C cannot reach this until that resolves. |

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
5. **In-race markets are downstream of Lane B (`03` §5).** No separate Lane C decision needed
   until Lane B's data-source question resolves.
6. **Both venues' current ToS on automated trading, read directly** — Kalshi's looks permissive,
   Polymarket US's is unwritten here; confirm at spec time, both move.

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
- Internal: `00-roadmap.md` (Lane C, Phase A3 §6.4, Phase A4), `01-data-pipeline.md` §6–§9,
  `03-live-telemetry-overtakes.md` §2.4/§5, `data/snapshots/2026-12-race-20260823T031058Z-postrace.json`
