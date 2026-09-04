# probes/

**Measurement scripts, not implementation.** Each one exists to produce a number quoted in a spec
under `docs/`, and each is named for the section it feeds. They read the archive and the existing
training matrix; they build nothing and they are not part of any model's code path.

`welcome.md`'s rule is that specs come before code. These are the other half of that: `08` §2
established that measurements come before specs, and this directory is where the measurements live
so a claim in a document can be re-derived instead of taken on trust.

**Not everything a spec quotes lives here.** `09` §10.1's results come from the layer itself, not
from a probe — `winprob_fit.py`, then `winprob_validate.py`, both from the repo root on `.venv312`,
writing to the gitignored `data/live/winprob/`. `09` §15 has the commands and the runtimes.

## Running them

Use `.venv312` — `fastf1` is not installed anywhere else (`08` §13.2). Run from the repo root. The
FastF1 cache at `data/cache/fastf1/` should be warm; a cold run downloads per-race data.

```bash
.venv312/bin/python probes/09_race_dynamics.py           # 09 sec2.2, sec2.3, sec2.6   ~2 min warm
.venv312/bin/python probes/09_leadchange_attribution.py  # 09 sec2.1, sec2.5           ~2 min warm
.venv312/bin/python probes/09_domain_bands.py            # 09 sec2.4 position bands    ~4 min
.venv312/bin/python probes/09_theta_front.py             # 09 sec2.4 theta_front       ~4 min
.venv312/bin/python probes/12_pit_loss.py                # docs/12 pit-strategy spec   ~3 min warm
.venv312/bin/python probes/12b_pit_projection.py         # docs/12, second pass        ~4 min warm
.venv312/bin/python probes/12c_q_refit.py                # docs/12 sec6 outcome 2      ~4 min warm
.venv312/bin/python probes/09b_dispersion.py             # 09 sec10.2                  ~3 min warm
```

The last two need `data/live/overtakes/training.csv`, which `overtake_build.py` produces and which
is gitignored (`03` §11.2 — it is F1 timing data and this repo is public).

## Expected output, so a regression is visible

| Script | Key numbers |
|---|---|
| `09_race_dynamics.py` | 48 lead changes over 12 races; leader converts 120/120 inside 10 laps to go; per-lap adjacent swap rate 0.0603 (P1–P3) to 0.0793 (P11–P15); 257 of 745 laps carry a pit stop |
| `09_leadchange_attribution.py` | 71% of lead changes pit-attributable, 2% retirement, 27% neither; `Status` counts Finished 120 / Lapped 87 / Retired 50 / DNS 7; 50 retirements, median race-fraction 0.440 |
| `09_domain_bands.py` | 264,049 test rows, 986 overtakes; P1–P3 holds 47 overtakes, 32 in-domain, 68.1% retained; front-band calibration worst ratio 2.33 |
| `09_theta_front.py` | θ_front mean 0.01046, range 0.00949–0.01160; keeps 77.4% of front-of-field overtakes in 41.0% of rows; 3/3 bins within 2×, worst 1.31 |
| `12_pit_loss.py` | pit δ pooled median **23.0s** (IQR 20.6–26.3), per-circuit 19–30s across 306 stops; eventual top-6 move a net 0 through the pit phase (\|move\|≥2 in 27%); of 32 pit-attributable P1 changes only **38%** stuck to the flag; undercut succeeds **15%** of 154 clean attempts |
| `12b_pit_projection.py` | **net displacement at 5 laps is 0.61× what the same pairs' per-lap swap rate compounded predicts, in every band and every quarter (0.41–0.78)** — a property of the raw rate, *not* of `09`'s simulator (see `09b` below); lead pair swaps 0.0055/lap in the final quarter against the P1–P3 band's 0.0351; 19% of lead changes revert within 5 laps; δ pooled median **22.8s**, MAD 3.7, per-circuit 19.4–28.3 over 286 stops on the tightened green filter; stint-age hazard 0.015–0.075 on a 0.037 base, so stint age barely says which lap a stop lands on; **undercut 14.9% against a matched background of 9.9%** |
| `09b_dispersion.py` | the real simulator's net displacement at 5 laps against the same pairs' archive outcome: **0.176 vs 0.178, ratio 0.99 pooled** — no general over-dispersion. The defect is one cell: the **lead pair in the closing quarter, 0.115 vs 0.012, ratio 9.9×** |

| `12c_q_refit.py` | 12 sec6's **outcome 2, and it failed.** Pit cycles are a far bigger share of `q` than `09` assumed — removing them takes the pooled adjacent-swap rate from **0.0667 to 0.0363**, so nearly half of every adjacent swap in the corpus happens inside a pit cycle. But the net displacement at 5 laps falls in almost exact proportion (0.1776 → 0.0997), so `12b`'s 0.61 goes to **0.59**, marginally *away* from the 1.0 the spec predicted. Pit-cycle swaps are not disproportionately transient. The refit is still mandatory (it is a double count either way, `12` §4) — it buys correct bookkeeping, not a better-behaved rate |

**A pre-registered prediction that failed, 2026-09-04.** The row above is `docs/12` §6 outcome 2,
and it is recorded here as a result rather than explained away, on `05` §6.4.1's precedent. The
prediction was reasonable and specific — pit cycles generate transient swaps, so taking them out of
`q` should move the net/compounded ratio toward 1 — and the corpus says no. Both the span rule and
the direction of the prediction were committed before the probe was run (`git log`), which is the
only reason the failure is readable as one.

**A third correction, 2026-09-04, and the one worth reading.** `12b`'s 0.61 was written up in `09`
§10.2 and `docs/12` §2.3 as *"the simulator over-disperses the field by 1.6×"*. It is not: 0.61 is
computed from raw archive swap counts, and `09`'s simulator consumes a shrunk, retirement-excluded,
circuit-scaled rate and then applies an asymmetric strength tilt. `09b_dispersion.py` exists because
of this — it runs the **real** `forward_simulate` and measures **0.99**. Both documents are
corrected in place. `09` §16.6 item 7 records the shape of the mistake: a plausible general
explanation displaced a correct narrow one (the lead-pair band cell, which `09b` measures at 9.9×),
and the general one got written up because it was more satisfying.

**A second correction, made 2026-09-03 while building B4.** `12_pit_loss.py`'s undercut line was
read as "barely above the background swap rate over the same span", comparing a 15% success rate
against the ~6%/lap adjacent-swap rate compounded over ~4.7 laps (≈25%). **That background is
wrong in the other direction:** compounding a per-lap swap rate counts *at least one swap*, and
swaps revert, so it is not "the car behind is ahead at the end". `12b_pit_projection.py` measures
the matched quantity — adjacent pairs over the same span with **no stop by either car** — at
**9.9%**, against the undercut's 14.9%. So the undercut carries a real ~1.5× lift, not a null.
It is still only 23 successes in 154 attempts clustered inside 12 races, so read it as "a modest
effect this corpus cannot pin down", not as a measured edge. The README line above is corrected in
place; the probe itself was not changed, only the comparison it is read against.

**One correction already made and recorded** (`09` §15): `Status == "Lapped"` is a **finish**.
Classifying it as a retirement inflates the count from 4.2/race to 6.6/race and flips the
distribution from front-loaded to back-loaded. `09_leadchange_attribution.py` has the correct
filter; the wrong one was caught by checking against `04` §5.1's measured 12.53% 2025 DNF rate.
