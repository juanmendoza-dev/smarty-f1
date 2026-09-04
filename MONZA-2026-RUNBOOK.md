# Monza 2026 runbook (Italian GP, round 13)

Operational checklist for the three time-sensitive runs this weekend. Specs are authoritative
(`03` §13, `09` §10, the A2/A4 snapshot flow) — this is just the sequence and the commands.

Session times (confirmed 2026-09-03 against formula1.com), Monza is UTC+2:

| Session | Local | UTC |
|---|---|---|
| FP1 | Fri 4 Sep 12:30 | **10:30Z** |
| Quali | Sat 5 Sep 16:00 | 14:00Z |
| Race | Sun 6 Sep 15:00 | **13:00Z** |

---

## 1. Lane B acceptance run + B1 delay — FP1, Friday 4 Sep

**Gate on all of Lane B.** One practice session; the same capture serves `03` §13 and B1.
Needs the live wire — cannot be done before FP1 starts.

### Do
1. From ~10:00Z (T-30, no earlier than T-60):
   ```
   .venv312/bin/python livetiming_capture.py --session-start 2026-09-04T10:30:00Z
   ```
   Writes `data/live/raw/<slug>/*.jsonl`, `data/live/ticks/<slug>.jsonl`, `data/live/logs/<slug>.log`
   (all gitignored). Let it run the full hour; it disconnects on `SessionStatus` finished.
   A 401/403/429 is a hard stop by design — do not restart to evade.
2. **B1 side-by-side:** have the Apple TV F1 app playing the FP1 live feed on the Mac at the same
   time. Note wall-clock (UTC) of one unambiguous on-screen event — a purple-sector flash, a car
   entering the pit, the session clock hitting a round number. Later subtract that from the same
   event's `t_wall` in the tick file. Seconds → Lane B lives. Minutes → Lane B is dead regardless
   of feed quality.

### Check afterwards (from the raw capture)
- `livetiming_verify.py` needs `requests`/`websockets` → run it under `.venv312`, not system python.
- Walk `03` §13 items 1–8. The go/no-go ones:
  - item 1: six-step handshake completed **unauthenticated**, subscribe completion carried every
    `03` §6.3 channel. If not → `03` §6.4 gate, stop, file the open item.
  - item 2: `CarData.z` / `Position.z` decoded (base64 + raw DEFLATE) into `03` §7.2 shapes.
  - item 5: **was `Position.z` broadcast at all** — go/no-go for the live viewer's track map.
  - item 3: record what channel 45 actually carries this session.
- Then edit `03`: drop `UNVERIFIED` on what the run confirms; anything contradicted is a spec bug
  to fix in `03` before any more Lane B code. Update the roadmap's B0/B1 entries.

---

## 2. Lane A lights-out snapshot + winner/podium/FL prediction — Saturday eve / Sunday

`snapshot.py` needs the grid, so nothing real runs before quali (Sat 14:00Z). The market
comparison for **podium / points / fastest lap** is the first genuine one — earlier races
predated the market code — so snapshot **close to lights-out** for liquidity (`04` §10, roadmap).

Race config `races/2026-monza.json` is ready: all four Kalshi `-ITAGP26` event tickers filled
2026-09-03, Polymarket slugs verified 2026-08-23.

### Do (as late as practical before Sun 13:00Z, after a first Saturday-night dry run)
```
python3 snapshot.py --race races/2026-monza.json
python3 score.py data/snapshots/2026-13-race-<ISO>.json
```
`score.py` also prints the podium/points/FL market comparison from the extended-market block.
Commit the snapshot + `-score.json` (both are git-tracked per the roadmap's locked decision).

### After the race
```
python3 postrace.py data/snapshots/2026-13-race-<ISO>.json
```
Writes `<snapshot>-postrace.json` with the Brier comparison. Commit it. Update roadmap A4 with
the first real podium/points/FL market numbers.

### Open call: manual vs scheduled cloud routine
The Dutch GP lights-out routine failed on a network block; postrace only worked after moving to
the `smarty-f1` env. Recommendation: **run manual this weekend**, revisit automation after.

---

## 3. Offline `09` §10 ablation — no live session needed, but not built

Runnable now against the 12-race archive. **Not a quick fit** — it is Phase B4's offline layer:
the Monte Carlo forward-simulation state estimator, the replay harness over the 8 scoreable
races, four pre-registered baselines, block-bootstrap CIs. `09` is specced (1020 lines) but
**not approved**. `09` §13 item 3 recommends running it *before* B1: if `08`'s contribution to
P(win) ablates to zero, the live-connection risk stops being worth taking for this chain.

Needs an explicit go-ahead to build (Phase B4 offline, spec rule in `welcome.md`).
