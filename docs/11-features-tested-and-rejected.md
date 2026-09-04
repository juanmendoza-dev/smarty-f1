# 11 — Features tested and rejected

A running register of candidate features that were **spiked against the real 264-race corpus
(`data/training/winner.csv`) and came back null**, so the next person does not re-run them without
a reason to. This is the project's standing rule made visible: a variable does not enter a spec on
the strength of a plausible mechanism — it enters after it is shown to move something real
(`welcome.md`, "prove it live, cheaply, before scaling it up"). Every entry here is reproducible
from the script named.

Rejection here is **not permanent**. It means "not on this corpus, at this resolution, against
these outcomes." An entry can be reopened by a different kind of evidence — finer time resolution,
a larger corpus, a better target variable — and each note says what that would take.

The pre-race feature set that survived is the eight in `02` §2. Nothing below is in it.

---

## Weather variables — see `06` §12 for the full numbers

Recorded in `06-weather-ensemble-signal.md` §12 rather than duplicated here, because they were
spiked as part of that spec's work on 2026-09-01. Summarised:

| Candidate | Result | Script |
|---|---|---|
| Wind — speed / gusts vs DNF, churn, favourite-win | null (gust vs DNF ρ = +0.012; calmest vs windiest quartile DNF 0.169 vs 0.168) | `wind_spike.py` |
| Temperature — race-window max vs DNF, churn, algo Brier | null, and the sign points the wrong way (max temp vs Brier +0.012; coldest-quartile Brier 0.602 vs hottest 0.624) | `temp_humidity_spike.py` |
| Humidity — race-window max vs same | null (vs DNF −0.007, vs Brier −0.064, vs churn +0.092) | `temp_humidity_spike.py` |
| Cross-model forecast disagreement, generalised beyond rain | null — the `p_spread` reliability signal that works for rain (`06` §5.3) does not carry to temperature or humidity (spread vs Brier −0.032 / −0.051, n = 60) | `temp_humidity_spike.py` |

A corner-level directional wind model (FastF1-telemetry-derived per-corner heading × wind vector
decomposition) was scoped in conversation as the follow-on if wind had shown a race-aggregate
effect. It did not, so that design is shelved — `06` §12.1. Reopen only with a different kind of
evidence than a race-aggregate correlation.

---

## Pit-crew execution quality (2026-09-03)

**Hypothesis.** A crew that executes fast, consistent pit stops should show up as its driver
gaining places (finishing better than they qualified) more often than a crew with slow or erratic
stops. Different mechanism from the ambient-condition spikes above — pit execution is a measured,
mechanical time loss, not weather.

**Feature.** Built exactly like `02` F4 (driver recent form): rolling mean pit-stop duration over
the driver's last 5 prior races with recorded pit data, never including the race being predicted —
no leakage. Data from Jolpica's `pitstops` endpoint (stop durations back to 2011, no gap against
the 2014+ corpus) plus `race_results` for the `driverId` → FIA code mapping.

**Result — null.** `n = 5,199` driver-race rows with the rolling feature; `n = 4,316` classified
finishers for the outcome tests (`pit_quality_spike.py`).

- Rolling pit speed vs grid-to-finish delta: **+0.064** — noise.
- Fastest-quartile crews (19.9–23.6 s) gained **+0.94** places on average; slowest-quartile crews
  (25.8–40.5 s) gained **+1.54**. If anything the *opposite* of the hypothesis — the slow-crew
  drivers are lower-midfield entries who start further back and have more room to climb, so the
  gain reflects grid position, not the pit stop.
- Rolling pit speed vs the algo's own Brier score: **−0.208**. This looks like a signal and is
  almost certainly confounded: fast pit crews belong to the front-running teams, and A1 predicts
  front-runner races more accurately regardless of anything happening in the pit lane. Team
  strength is upstream of both terms. Not evidence for a feature.

**Reopen if.** This is race-aggregate, and a pit stop's effect is a within-race, few-second event.
A live in-race layer (`09`) that models the pit cycle directly — `09` §13 item 2, measured there
as behind 71% of lead changes — is where pit-stop timing plausibly matters. That is a live-model
decision, not a pre-race feature, and this null does not bear on it.

---

## What this implies for "make the algo better"

Five candidates spiked, five null. The pre-race scorer's ceiling is its **feature set**, not its
weighting — Phase A3 established the same thing from the other direction (`05` §6.4.1: a fitted
model over the same 7 features lost to A1's hand-set weights on 48 held-out races). Adding more
pre-race variables is the path with the worst track record in this project so far.

The two directions with evidence behind them:

1. **The weather ensemble's disagreement flag** (`06` §5.3) — the one spike that hit. Not more
   data, a reliability signal on the data already used.
2. **The live layer** (`09`) — the pre-race number is a *prior*; the value a state estimator adds
   is tracking in-race mechanical change (retirements, pit cycles, closing pursuits, laps
   remaining) continuously rather than once. `09` §1.3 is honest that this cannot fix a weak
   prior — but it is the only place genuinely new information enters after lights-out.

---

**Update 2026-09-04 — the pit-execution entry's "reopen if" pointer now resolves.**
`docs/12-pit-strategy-model.md` is specced. It is **not** a reopening of the null recorded here,
and `12` §1 states why in terms: this register's entry tested *race-aggregate, pre-race crew
speed* as a feature of the Lane A scorer, and `12` specs an *in-race, per-stop* projection of track
position through a pit cycle. Different claim, different lane, different evidence. Crew speed does
not appear in `12` as a feature and is not proposed as one.
