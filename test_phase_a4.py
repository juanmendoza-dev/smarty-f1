#!/usr/bin/env python3
"""Validate Phase A4 (podium/points/DNF/fastest lap) against two real,
fully-resolved data points, same discipline as test_f7_wet_branch.py/
test_postrace.py. 04-outcome-expansion-algo.md sec3/sec9.

1. The already-frozen 2026 Dutch GP snapshot (real grid/form/track-history,
   pulled 2026-08-22 pre-race) scored against the real 2026-08-23 result,
   pulled fresh from Jolpica. Outcome-only, per sec3: this snapshot predates
   Phase A4 and never captured podium/points/fastest-lap market data, so no
   market comparison should be attempted. Also where sec7.4's fastest-lap
   ingest-lag gotcha actually reproduced live while this spec was being
   written (Jolpica's FastestLap field was null for the whole field right
   after the race) -- it has since resolved (LEC), which the fastest-lap
   test below validates against rather than the transient gap, so it stays a
   meaningful check for anyone running this later rather than a flaky one.

2. A fresh snapshot built for the archived 2023 Dutch GP (round looked up
   live from Jolpica's 2023 schedule, same technique test_postrace.py
   already uses) -- fully resolved including fastest lap, giving a second,
   independent fastest-lap answer (Alonso, P2 finish, fastest lap anyway --
   confirms fastest lap is independent of finishing position, same as the
   2026 result above where the winner, NOR, did not set it).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score
from postrace import find_full_result, find_fastest_lap
from snapshot import build_grid, build_form, build_track_history
from lib import jolpica
from lib.circuits import multiplier_for

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "data", "snapshots")
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")


class TestDutchGP2026OutcomeOnly(unittest.TestCase):
    """sec3: real pre-race snapshot + real post-race result, no market data
    for the new outcome types -- this snapshot predates Phase A4."""

    @classmethod
    def setUpClass(cls):
        with open(score.load_latest_snapshot(SNAPSHOT_DIR)) as f:
            cls.snapshot = json.load(f)
        cls.algo_snapshot = {k: v for k, v in cls.snapshot.items() if k != "markets"}
        season = cls.snapshot["meta"]["season"]
        round_ = cls.snapshot["meta"]["round"]
        # The project's persistent cache (data/cache) has a stale entry for
        # this exact results.json URL from before the race happened --
        # lib/jolpica.py's race_results() has no force_refresh option, and
        # httpcache caches a 200 response even when its Races list is empty
        # (that emptiness is the normal "hasn't happened yet" signal, sec4.4
        # of 01-data-pipeline.md, not a cache-miss). A fresh cache dir gets
        # the real, now-available result instead of replaying that entry.
        fresh_cache_dir = tempfile.mkdtemp(prefix="f1-a4-test-cache-")
        cls.rows, _ = find_full_result(season, round_, fresh_cache_dir)
        cls.base = score.score_all(cls.algo_snapshot)

    def test_real_podium_was_nor_ant_rus(self):
        podium = {r["code"] for r in self.rows if r["classified"] and r["position"] <= 3}
        self.assertEqual(podium, {"NOR", "ANT", "RUS"})

    def test_verstappen_actually_dnfd(self):
        ver = next(r for r in self.rows if r["code"] == "VER")
        self.assertFalse(ver["classified"])
        self.assertEqual(ver["status"], "Retired")

    def test_fastest_lap_leclerc_and_scores_against_it(self):
        """sec7.4's ingest lag was real and reproduced live earlier in this
        round's data (every FastestLap was null right after the race), which
        is exactly why find_fastest_lap() has a dedicated error for it rather
        than crashing or guessing -- see FastestLapNotIngestedError's own
        docstring. It has since resolved (confirmed live: Jolpica now reports
        LEC, rank 1, lap 60), so this test validates the resolved state
        rather than asserting a transient gap that would make the test flaky
        for anyone running it later."""
        fastest_lap_winner = find_fastest_lap(self.rows)
        self.assertEqual(fastest_lap_winner, "LEC")

        fastlap = score.compute_fastest_lap(self.algo_snapshot, self.base["sub_scores"])
        outcome = {code: (1.0 if code == fastest_lap_winner else 0.0) for code in fastlap["p_fastlap"]}
        post = score.compute_post_race_kofn(fastlap["p_fastlap"], outcome, market_block=None)
        self.assertGreaterEqual(post["brier_algo"], 0.0)
        self.assertLessEqual(post["brier_algo"], 1.0)

    def test_podium_points_dnf_probabilities_are_well_formed(self):
        p_dnf, dnf_n, field_dnf_rate = score.compute_dnf(self.algo_snapshot)
        p_podium, p_points, sim_meta = score.compute_podium_points(
            self.base["raw_scores"], self.base["p_algo"]
        )
        self.assertGreater(field_dnf_rate, 0.0)
        self.assertLess(field_dnf_rate, 1.0)
        for code in self.base["p_algo"]:
            self.assertGreaterEqual(p_dnf[code], -1e-9)
            self.assertLessEqual(p_dnf[code], 1 + 1e-9)
            # sec10 assertion 1: p_win <= p_podium <= p_points
            self.assertLessEqual(self.base["p_algo"][code], p_podium[code] + 0.01)
            self.assertLessEqual(p_podium[code], p_points[code] + 1e-9)

    def test_algo_gave_antonelli_real_podium_credit_despite_a_weak_win_probability(self):
        """Sanity check, not a hard pass/fail on prediction quality -- one
        race proves nothing (02 sec7). NOR/RUS ranking highest on podium
        probability would hold by construction (podium probability is
        monotone in win strength, sec6.1) regardless of what actually
        happened, so it isn't a real check. ANT is: the algo's win
        probability for ANT was only 11.3% (02 sec9's story -- the market
        backed the championship leader more than the algo did), but ANT
        actually did podium (P2). This checks the algo's *podium* number for
        ANT specifically credits that possibility rather than writing ANT off
        the way a pure win-probability read would suggest."""
        p_podium, _, _ = score.compute_podium_points(self.base["raw_scores"], self.base["p_algo"])
        ranked = sorted(p_podium.items(), key=lambda kv: -kv[1])
        top5_codes = {c for c, _ in ranked[:5]}
        self.assertIn("ANT", top5_codes)
        self.assertGreater(p_podium["ANT"], 0.3)

    def test_no_market_comparison_for_this_snapshot(self):
        """sec3: this snapshot predates Phase A4's market pulls -- podium/
        points comparison must come back empty, not fabricated from the
        now-settled prices sitting live on Polymarket/Kalshi today."""
        podium_comparison = score.compute_comparison_kofn(
            {"NOR": 0.5}, self.snapshot["markets"].get("podium")
        )
        self.assertIsNone(podium_comparison["NOR"]["p_polymarket"])
        self.assertIsNone(podium_comparison["NOR"]["p_kalshi"])
        self.assertIsNone(podium_comparison["NOR"]["edge"])

    def test_post_race_brier_computed_against_real_outcome(self):
        podium_outcome = {
            r["code"]: (1.0 if r["classified"] and r["position"] <= 3 else 0.0) for r in self.rows
        }
        p_podium, _, _ = score.compute_podium_points(self.base["raw_scores"], self.base["p_algo"])
        post = score.compute_post_race_kofn(p_podium, podium_outcome, market_block=None)
        self.assertIn("brier_algo", post)
        self.assertNotIn("brier_market_mean", post)  # no market data -> no market columns
        self.assertGreaterEqual(post["brier_algo"], 0.0)
        self.assertLessEqual(post["brier_algo"], 1.0)


class TestArchived2023DutchGP(unittest.TestCase):
    """Second real, fully-resolved data point -- built fresh via snapshot.py's
    own network-calling functions pointed at the archived 2023 race, same
    approach test_postrace.py uses to find the round. Exercises fastest lap
    end to end, which the 2026 snapshot above cannot (ingest lag)."""

    @classmethod
    def setUpClass(cls):
        cls.cache_dir = DEFAULT_CACHE_DIR
        races, _ = jolpica.schedule(2023, cls.cache_dir)
        zandvoort = [r for r in races if r["Circuit"]["circuitId"] == "zandvoort"]
        assert len(zandvoort) == 1, "expected exactly one 2023 Zandvoort round on the schedule"
        cls.season = 2023
        cls.round = int(zandvoort[0]["round"])
        cls.race_date = zandvoort[0]["date"]

        grid, is_sprint_weekend, _ = build_grid(cls.season, cls.round, cls.cache_dir)
        form, _ = build_form(cls.season, cls.round, grid, cls.cache_dir, race_has_run=True)
        circuit_row, _ = jolpica.circuit("zandvoort", cls.cache_dir)
        lat = float(circuit_row["Location"]["lat"])
        lon = float(circuit_row["Location"]["long"])
        track_history, _ = build_track_history(
            "zandvoort", lat, lon, grid, cls.race_date, cls.cache_dir
        )

        # Weather is irrelevant to what this test validates (podium/points/
        # DNF/fastest-lap, not F7) and Open-Meteo's forecast API doesn't serve
        # a date this far in the past -- stub a dormant/dry block rather than
        # pull the archive API and build a real weather feature test doesn't need.
        # p_mean, not p_max: 06 sec6.2 moved F7's gate onto it.
        weather = {"p_mean": 0}

        snapshot = {
            "meta": {
                "season": cls.season, "round": cls.round, "race_name": zandvoort[0]["raceName"],
                "circuit_id": "zandvoort", "is_sprint_weekend": is_sprint_weekend,
                "track_overtaking_multiplier": multiplier_for("zandvoort"),
            },
            "grid": grid, "form": form, "track_history": track_history, "weather": weather,
        }
        # Round-trip through JSON so this behaves exactly like a file-loaded
        # snapshot (dict keys like results_by_round's round numbers become
        # strings on the way through json.dump, same as every real snapshot
        # on disk -- score.py's functions index by str(rnd), not int).
        cls.algo_snapshot = json.loads(json.dumps(snapshot, default=str))

        cls.rows, _ = find_full_result(cls.season, cls.round, cls.cache_dir)
        cls.base = score.score_all(cls.algo_snapshot)

    def test_verstappen_won_alonso_2nd_gasly_3rd(self):
        """Real podium, verified live against Jolpica (2023/13/results.json),
        not assumed from memory -- this is his home race and a famously wet
        one, but the actual podium is VER/ALO/GAS, not the "Piastri and
        Norris" claim test_postrace.py's docstring made without ever
        checking it against an assertion (fixed there too, same session)."""
        podium_by_position = {r["position"]: r["code"] for r in self.rows if r["classified"] and r["position"] <= 3}
        self.assertEqual(podium_by_position, {1: "VER", 2: "ALO", 3: "GAS"})

    def test_alonso_set_the_fastest_lap_despite_finishing_second(self):
        """Confirms fastest lap is independent of finishing position -- ALO
        was classified 2nd but holds fastest_lap_rank == '1' (verified live
        against the raw Jolpica response, 04 sec7.4)."""
        fastest_lap_winner = find_fastest_lap(self.rows)
        self.assertEqual(fastest_lap_winner, "ALO")
        alo = next(r for r in self.rows if r["code"] == "ALO")
        self.assertEqual(alo["position"], 2)

    def test_fastest_lap_probabilities_and_brier_against_real_outcome(self):
        fastlap = score.compute_fastest_lap(self.algo_snapshot, self.base["sub_scores"])
        p_fastlap = fastlap["p_fastlap"]
        total = sum(p_fastlap.values())
        self.assertAlmostEqual(total, 1.0, places=6)

        fastest_lap_winner = find_fastest_lap(self.rows)
        outcome = {code: (1.0 if code == fastest_lap_winner else 0.0) for code in p_fastlap}
        post = score.compute_post_race_kofn(p_fastlap, outcome, market_block=None)
        self.assertGreaterEqual(post["brier_algo"], 0.0)

    def test_podium_points_dnf_full_pipeline(self):
        p_dnf, dnf_n, field_dnf_rate = score.compute_dnf(self.algo_snapshot)
        p_podium, p_points, sim_meta = score.compute_podium_points(
            self.base["raw_scores"], self.base["p_algo"]
        )
        for code in self.base["p_algo"]:
            self.assertLessEqual(self.base["p_algo"][code], p_podium[code] + 0.01)
            self.assertLessEqual(p_podium[code], p_points[code] + 1e-9)

        podium_outcome = {
            r["code"]: (1.0 if r["classified"] and r["position"] <= 3 else 0.0) for r in self.rows
        }
        dnf_outcome = {r["code"]: (0.0 if r["classified"] else 1.0) for r in self.rows}
        podium_post = score.compute_post_race_kofn(p_podium, podium_outcome, market_block=None)
        dnf_post = score.compute_post_race_kofn(p_dnf, dnf_outcome, market_block=None)
        self.assertGreaterEqual(podium_post["brier_algo"], 0.0)
        self.assertGreaterEqual(dnf_post["brier_algo"], 0.0)

    def test_verstappen_won_from_pole_algo_should_rate_him_highly(self):
        """Sanity check on the fresh 2023 snapshot's own pipeline, independent
        of anything the 2026 snapshot already validates."""
        ranked = sorted(self.base["p_algo"].items(), key=lambda kv: -kv[1])
        self.assertEqual(ranked[0][0], "VER")


if __name__ == "__main__":
    unittest.main()
