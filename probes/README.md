# probes/

**Measurement scripts, not implementation.** Each one exists to produce a number quoted in a spec
under `docs/`, and each is named for the section it feeds. They read the archive and the existing
training matrix; they build nothing and they are not part of any model's code path.

`welcome.md`'s rule is that specs come before code. These are the other half of that: `08` §2
established that measurements come before specs, and this directory is where the measurements live
so a claim in a document can be re-derived instead of taken on trust.

## Running them

Use `.venv312` — `fastf1` is not installed anywhere else (`08` §13.2). Run from the repo root. The
FastF1 cache at `data/cache/fastf1/` should be warm; a cold run downloads per-race data.

```bash
.venv312/bin/python probes/09_race_dynamics.py           # 09 sec2.2, sec2.3, sec2.6   ~2 min warm
.venv312/bin/python probes/09_leadchange_attribution.py  # 09 sec2.1, sec2.5           ~2 min warm
.venv312/bin/python probes/09_domain_bands.py            # 09 sec2.4 position bands    ~4 min
.venv312/bin/python probes/09_theta_front.py             # 09 sec2.4 theta_front       ~4 min
.venv312/bin/python probes/12_pit_loss.py                # docs/12 pit-strategy spec   ~3 min warm
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
| `12_pit_loss.py` | pit δ pooled median **23.0s** (IQR 20.6–26.3), per-circuit 19–30s across 306 stops; eventual top-6 move a net 0 through the pit phase (\|move\|≥2 in 27%); of 32 pit-attributable P1 changes only **38%** stuck to the flag; undercut succeeds **15%** of 154 clean attempts, barely above the background swap rate over the same span |

**One correction already made and recorded** (`09` §15): `Status == "Lapped"` is a **finish**.
Classifying it as a retirement inflates the count from 4.2/race to 6.6/race and flips the
distribution from front-loaded to back-loaded. `09_leadchange_attribution.py` has the correct
filter; the wrong one was caught by checking against `04` §5.1's measured 12.53% 2025 DNF rate.
