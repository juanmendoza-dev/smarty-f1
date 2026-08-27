# quant/00 — Directional trading on per-race prediction markets

Status: **spec draft, 2026-08-26. Not approved, no implementation.** Follows `../07-lane-c-trading-feasibility.md`
(feasibility) and its §11 (market-structure survey). Per `welcome.md`: this authorizes nothing
by itself, and no real-money trading happens without a separate explicit go-ahead on top of an
approved spec, after risk controls exist and Phase Q1 has produced evidence of an edge.

Read first: `welcome.md`, `../07-lane-c-trading-feasibility.md` (all of it), `../01-data-pipeline.md`
§6–§8, `../02-winner-prediction-algo.md` §9–§10, `../04-outcome-expansion-algo.md` §2.

---

## 1. What this is

Take Lane A's probability output for a race (winner from `02`, podium/points/fastest-lap from
`04`), compare it against the Polymarket and Kalshi books for the same outcomes, and — where the
difference is large enough to survive spread and fees — take a position. Settle against the real
result. Accumulate a track record.

**This lane builds no new models.** It consumes `score.py` / `postrace.py` output. The new code
is: an edge-measurement harness, a paper-trading simulator, risk controls, and (gated, later)
order execution.

The realistic prize, from §3: **tens to low hundreds of dollars per race**, with variance large
enough that confirming the edge is real takes multiple seasons. This is a learning / track-record
exercise that *could* become a modest income stream, not an income stream on day one. If that
framing is wrong for the owner, stop here — the market sizes do not support more (`07` §5, §11.2).

---

## 2. The thesis, stated precisely

For a market on outcome `O` with the project's model probability `p_model` and the venue's book:

```
edge = p_model − p_execution
```

where `p_execution` is the price you would actually pay/receive (best ask if buying YES, best
bid if selling / buying NO), **not** the midpoint. A position is worth taking only if:

1. `|edge|` exceeds a pre-registered threshold (Q2 decides the number; start ~0.05),
2. the book is real: quoted spread ≤ ~0.03–0.05 and depth at/near the touch supports the intended
   size without moving price more than ~1–2¢,
3. the price is not in the extreme tail (fees and de-vig noise dominate below ~0.03 / above ~0.97
   — `07` §2, §4),
4. after applying the venue fee schedule (`07` §4) and expected slippage, expected value is still
   positive.

The thesis is only worth acting on if `p_model` is actually better calibrated than the book.
**It is not yet:** A1 lost to the market mean on the Dutch GP (`07` §2, `n=1`), A3 is a closed
negative result, and podium/points/fastest-lap have zero live market comparisons. Phase Q1
exists to produce that evidence before any capital — paper or real — is committed to a strategy.

---

## 3. Expected value — the honest math

Worked against the real Monza books pulled 2026-08-26, ~10 days pre-race (`07` §11.3, plus the
CLOB / Kalshi order-book depth below).

The governing relationship:

```
expected profit per race ≈ Σ over legs [ edge_leg × dollars_filled_leg ] − fees − slippage
```

`dollars_filled_leg` is capped by book depth, not by bankroll. Real depth at the touch, Norris
YES, Monza winner, 10 days out:

| Venue | Best bid | Best ask | Size at best ask | Fill $500 sweeps to | Fill $2,000 sweeps to |
|---|---|---|---|---|---|
| Polymarket (CLOB) | 0.28 (150 sh) | 0.30 | **125 sh ≈ $38** | ~0.315 | ~0.33+ (thin) |
| Kalshi | 0.31 (2,959 ct) | 0.32 | **869 ct ≈ $278** | ~0.32 | ~0.33 |

**Finding that contradicts earlier advice in this conversation:** Kalshi's per-race winner book
is roughly **7× deeper at the touch** than Polymarket's on the same leg, ten days out, and the
NO side is deeper still (Kalshi Norris: 2,148 + 2,505 contracts resting at 0.28/0.29). The owner's
stated preference is Polymarket; the measured data says **Kalshi is the better execution venue
for this specific strategy.** Polymarket's role is the second price for the cross-venue
comparison and (later) the liquidity-rewards / MM angle (§9). This should be revisited close to
a race — Polymarket depth builds toward lights-out (`07` §10.3 measured Kalshi at 826k contracts
in-race; Polymarket in-race volume is UNVERIFIED, `07` §10.4).

Scenario table, per race, assuming the model is genuinely better calibrated (which Q1 has not
shown):

| Model quality | Legs traded | Per race (EV) | Per 24-race season | Variance reality |
|---|---|---|---|---|
| Good (2–3pp edge after spread) | winner only | $20–70 | $500–1,700 | ±$1–2k swings per race; needs 2–3 seasons to distinguish from luck |
| Good | winner + 2–3 podium legs | $60–200 | $1,500–5,000 | requires the model to be good at podium, unproven |
| Strong (5–8pp edge) | winner + podium | $150–450 | $3,500–11,000 | edges this size rarely persist |

Slippage on a $6k-deep book eats 1–3pp of any edge on a $500–1,000 position. Size discipline
(§8) is not optional.

---

## 4. Market universe vs. tradeable subset

**Universe (defined here):** per-race Driver Winner, Driver Podium Finish, Points / Top-10 /
Top-5, Driver Fastest Lap — every outcome type Lane A models AND at least one venue lists.
Per `07` §11.1: winner/podium/fastest-lap on both venues; points/top-N on Kalshi only
(`KXF1TOP10` / `KXF1TOP5`); Polymarket has no per-driver points market.

**Tradeable subset (decided per-race at snapshot time by the §2 gate):** whatever passes the
spread ≤ ~0.03–0.05 and depth checks. On the books measured 2026-08-26 that is:

- **Winner:** ~5 contender legs on both venues (1–2¢ spreads). Tradeable.
- **Podium:** a handful of Polymarket legs (Norris 11¢, Antonelli 6¢); most legs 40–86¢
  spreads = "no book", excluded. Kalshi podium thinner still.
- **Points / top-N:** Kalshi-only, thin. Mostly excluded today.
- **Fastest lap:** near-zero volume both venues. Excluded.

This is not a contradiction of the "full scope" decision — the universe is broad on purpose so
Q1 measures the whole surface, but the strategy only ever trades what passes the gate. **Q1's
real job on podium/points is to measure whether those books tighten near lights-out** — `04` and
`07` §11 both flag this as unknown, and it determines whether the scope is genuinely three
market types or effectively just winner.

---

## 5. The winner markets are linked — `negRisk: True`

Every Polymarket Monza winner leg returned `negRisk: True`. The legs are a single
negative-risk market: the exchange enforces the relationship across outcomes, and a complete
set of NO shares converts to cash. Consequences the strategy layer must respect:

- **A1 already outputs a normalized distribution** over drivers (softmax sums to 1). Buying YES
  on five contenders independently on a negRisk market **double-counts the edge** — the legs are
  not independent bets. Edge must be computed against the market's *joint* implied distribution
  (de-vig the full set, compare distributions), not leg-by-leg.
- **The cheap expression of "my distribution differs" is often NO on overpriced legs**, not YES
  on underpriced ones — and on the measured books the NO side is where the depth sits (Antonelli
  YES: $266 bid / $10,795 ask; Russell: $935 / $7,896). The fill simulator (§8) needs both sides.
- **UNVERIFIED, resolve before the sizing section is finalized:** whether Polymarket's negRisk
  conversion changes the effective fee or collateral requirement on a multi-leg position. If it
  does, the per-race capital number in §8 changes. Check against Polymarket CLOB docs.
- Kalshi's winner series (`KXF1RACE`) is 22 separate binary markets, **not** linked this way —
  its YES legs summed to ≈1.26 on the ask side (`07` §11.3), a ~26% overround concentrated in
  the 1¢ tail. De-vig handles the two venues differently.

---

## 6. Fair value and edge construction

The de-vig method is **load-bearing here** in a way it is not for a calibration report
(`07` §2, `01` §8.4). Proportional de-vig over-penalizes longshots; Kalshi's overround is almost
all in its zero-bid tail. `edge = p_model − p_market_normalized` computed naively **manufactures
systematic fake edges in the longshot tail** — exactly where books are thinnest and a naive bot
most wants to bet.

Rules:

1. **Edge is computed against `bestBid` / `bestAsk`** (Polymarket CLOB `/book`; Kalshi
   `yes_bid_dollars` / `yes_ask_dollars` and the `orderbook_fp` arrays), never the Gamma
   `outcomePrices` midpoint. Gamma reports a midpoint even on an empty book — §11.4 measured
   Monza podium legs at "0.37" midpoints sitting on ~1¢ best bids.
2. **Any leg with quoted spread > ~0.05 is marked `no_book` and excluded** from strategy
   evaluation — not fed in with a midpoint.
3. **Polymarket (negRisk):** de-vig the full contender set jointly, compare the joint
   distribution to A1's, express the difference as NO-side or YES-side positions per §5.
4. **Kalshi:** de-vig per-market but never trade the 1¢ tail; only the ~5 contender legs.
5. Persist both raw and de-vigged market probabilities alongside `p_model`, same as `01` §8.4
   already does for A1's calibration record.
6. The de-vig choice itself (proportional vs. longshot-aware / Shin) is an **open decision** —
   decide it against Q1 harness data, not by argument (`01` §9 item 2).

---

## 7. Data model — what Q1 logs

Per race, per market, per venue, at snapshot time (and again at a pre-lights-out re-snapshot):

```
race_id, venue, market_type, outcome (driver/constructor code, canonical FIA 3-letter),
snapshot_ts,
p_model,                      # from score.py / the A4 sims
best_bid, best_ask, mid,
spread = best_ask − best_bid,
depth_bid_touch, depth_ask_touch,          # size at best quote, in $ and native units
depth_bid_within_2c, depth_ask_within_2c,  # cumulative depth within 2 cents
p_market_raw, p_market_devig_proportional, p_market_devig_alt,
book_flag ∈ {ok, wide_spread, no_book, tail},
gate_pass ∈ {true, false},   # did §2's four conditions all hold
edge = p_model − p_execution  # execution price given intended side; null if gate_pass=false
```

After the race, joined to the realized outcome (from `postrace.py` / Jolpica) and the settled
market price. Stored as CSV under `data/quant/` and **committed to git**, same reasoning as
`05` §5.1 for the training matrix — it must be diffable and auditable.

---

## 8. Phases

### Phase Q1 — Edge-measurement harness (buildable now, zero budget, no approval)

Extends `score.py` / `postrace.py`. For every race the pipeline snapshots:

- log the §7 record for every in-universe market on both venues,
- compute a **running calibration series**: is `p_model` better calibrated (Brier, log-loss)
  than each venue's de-vigged mid, and than the two-venue mean, across all races so far — not
  per race,
- **pre-register the success threshold** before accumulating data: e.g. "A1 beats the two-venue
  mean on pooled Brier over ≥ 10 snapshotted races, winner market." Q2 does not start until this
  is met. `07` §7 step 1 is this phase.

No positions, paper or real. This is the missing evidence from `07` §2.

### Phase Q2 — Paper-trading simulator (buildable now, after Q1 has ≥ a few races)

Given a snapshot's books and a strategy, simulate:

- **entry rule:** `gate_pass` AND `|edge| ≥ θ` AND price ∈ `[a, b]`,
- **sizing:** fractional Kelly (`f* = edge / odds`, take `¼ f*` or less), hard-capped per §Q3,
  and further capped so the simulated fill moves price ≤ ~1–2¢ against the real book,
- **fills:** walk the real `/book` / `orderbook_fp` ladder, both sides (§5), apply the venue
  fee schedule (`07` §4) and Polymarket negRisk fee/collateral treatment once §5's UNVERIFIED
  is resolved,
- **settlement:** against the actual result; realized P&L and a running equity curve.

Run over the accumulating race set. Exercises the Q3 risk controls before they gate anything
real. Tune `θ`, `[a,b]`, and the Kelly fraction here — on out-of-sample races only, season-forward,
never refit on races already scored (same discipline as `05` §6.1).

### Phase Q3 — Risk controls (decided and built before Q4)

`07` §6: "an auto-trading bot with no cap on it is the actual failure mode here, independent of
prediction quality." Required before any order-placement code runs, even on a demo host:

- position limit per market (absolute $ and % of bankroll),
- max loss per race and per session,
- max total exposure across open positions,
- a kill switch (one command halts all quoting/ordering and optionally flattens),
- a daily reconciliation check: local position ledger vs. venue-reported balances.

Bankroll size is an **open decision** — it sets every cap above.

### Phase Q4 — Live execution (gated: Q1 shows edge + Q3 built + separate real-money approval)

Forked by venue. Both `read` paths are already public and keyless (`01` §6.2, §7.2); only the
order path needs auth.

**Kalshi** (recommended first, per §3's depth finding):
- RSA-PSS request signing (`01` §7.3 documents the scheme),
- **`external-api.demo.kalshi.co`** — full order + fill flow built and tested with no money at
  risk (`07` §3). This is the single most valuable execution asset in the project.
- then a funded, KYC'd account for live.

**Polymarket** (CLOB v2):
- L1: one EIP-712 wallet signature → `POST /auth/api-key` → `(apiKey, secret, passphrase)`,
- L2: HMAC-SHA256 over `timestamp + METHOD + path + body` on every request, five `POLY_*`
  headers; order payloads are separately wallet-signed,
- USDC on Polygon, funded wallet, `py-clob-client-v2`,
- negRisk market mechanics (§5).

Order + fill confirmation + local ledger reconciliation on both. A mapping from `p_model` and
`edge` to order size and limit price (the Kelly logic from Q2, now live).

### Phase Q5 — Market-making extension (future, own decision required)

The owner's stated long-term interest: quote both sides, earn the spread / rewards rather than
bet direction.

Concrete hook: the Monza winner legs carry **`rewardsMinSize: 50`, `rewardsMaxSpread: 4.5`** —
Polymarket's F1 winner markets **are** in the liquidity-rewards program. Rewards are daily USDC,
**paid whether or not the resting orders fill**, scored quadratically on how close to mid and
how tight vs. other makers (min payout $1/day). Maker rebates (a share of taker fees on filled
resting orders) stack on top.

- **UNVERIFIED and required before rewards are treated as a revenue line:** the per-market
  reward pool for F1 markets. The documented examples are crypto at $300k/market; F1 is
  configured separately and is likely far smaller. Find the F1 allocation or mark it UNVERIFIED
  in the repo convention — do not estimate it.
- Needs CLOB (§Q4 Polymarket path), a fair-value model that updates continuously (Lane B
  territory — `03`), inventory-risk management, and a latency budget. It is a genuinely
  different build from Q1–Q4 and gets its own spec.
- The in-race version (quote the winner market *during* the race off a live win-probability
  model) is where this lane, Lane B, and `07` §10.3's "Kalshi winner market trades 826k
  contracts in-race" converge. Also its own spec.

---

## 9. Open decisions

1. **Jurisdiction.** Owner stated "both venues available" (2026-08-26). `07` §3 notes Kalshi is
   generally US-only and Polymarket Global geo-blocks US persons — close to mutually exclusive
   for one natural person. Recorded as given; **must be confirmed before any funded account
   exists.** Do not build the KYC/funding steps of Q4 on it until confirmed.
2. **Execution venue first.** §3's depth data says Kalshi. Owner prefers Polymarket. Decide with
   the number in front of you; recommendation is Kalshi-demo-first for Q4, Polymarket for the
   cross-venue price and Q5.
3. **De-vig method** (§6) — proportional vs. longshot-aware. Decide against Q1 data.
4. **Bankroll size** — sets every Q3 cap. Owner's call.
5. **Kelly fraction** — ¼ or less; tune in Q2, but the ceiling is a risk decision not a
   optimization one.
6. **Pre-registered Q1 success threshold** — the exact "beats the market by X over N races"
   bar. Write it down before Q1 accumulates data.
7. **negRisk fee/collateral treatment** (§5) — UNVERIFIED, blocks the Q2 sizing math for
   Polymarket.
8. **Fold Kalshi `candlesticks` and/or Polymarket CLOB `/book` into the locked data scope?**
   `01` §6.2 / §7.2 scope reads to Gamma + `GET /markets`. Q1 needs live `/book` depth (both
   venues) and, for in-race work, Kalshi `candlesticks`. `07` §10.6 already has this as an open
   item; Q1 forces it.
9. **F1 liquidity-rewards pool size** (§Q5) — UNVERIFIED.
10. **Does Q5 happen at all**, or does the lane stay directional-only? Depends on Q1's result
    and the owner's read of the §3 capacity ceiling.

---

## 10. Sources

- Market structure, book depth, prices: measured 2026-08-26 from Polymarket Gamma
  `/events?tag_slug=f1`, Polymarket CLOB `https://clob.polymarket.com/book?token_id=`, Kalshi
  `https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}` and `/orderbook`. Recorded in
  `../07-lane-c-trading-feasibility.md` §11.
- Polymarket CLOB auth: [Polymarket Docs — Authentication](https://docs.polymarket.com/developers/CLOB/authentication),
  [py-clob-client-v2](https://github.com/Polymarket/py-clob-client-v2).
- Polymarket liquidity rewards: [Polymarket Docs — Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards),
  [Polymarket Help Center — Liquidity Rewards](https://help.polymarket.com/en/articles/13364466-liquidity-rewards).
- Fees: `../07-lane-c-trading-feasibility.md` §4 (Kalshi `0.07·C·(1−C)`, Polymarket ~0.75% sports
  taker) — re-confirm at spec-approval time, both move.
- Internal: `../07` (all), `../01` §6–§9, `../02` §9–§10, `../04` §2, `../05` §5–§6, `../00-roadmap.md`
  Lane C.
