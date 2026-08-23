#!/usr/bin/env python3
"""Dry-run postrace.py's logic before it can be run for real: this weekend's
race hasn't finished, so Jolpica has no result to auto-pull yet.

find_winner() is tested against a real, already-decided race instead --
the 2023 Dutch Grand Prix, round looked up live from Jolpica's 2023 schedule
rather than hardcoded (Jolpica paginates a season's races in order, so the
round number isn't a fact worth assuming). Verstappen won his home race, in
the wet -- the only thing checked below, and it is checked against the
network response. (An earlier version of this docstring claimed Piastri and
Norris rounded out the podium; that was never actually asserted by a test
here and turned out to be wrong -- the real podium is VER/ALO/GAS, verified
and asserted in test_phase_a4.py's TestArchived2023DutchGP, which needed the
full result for podium/points/DNF/fastest-lap validation anyway.)

compute_post_race() is exercised separately with hand-built inputs small
enough to check the Brier arithmetic by sight, so a bug in the real snapshot's
numbers can't hide a bug in the formula.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import jolpica
from score import compute_post_race
from postrace import find_winner, DEFAULT_CACHE_DIR


class TestFindWinner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        races, _ = jolpica.schedule(2023, DEFAULT_CACHE_DIR)
        zandvoort = [r for r in races if r["Circuit"]["circuitId"] == "zandvoort"]
        assert len(zandvoort) == 1, "expected exactly one 2023 Zandvoort round on the schedule"
        cls.round_2023_dutch_gp = int(zandvoort[0]["round"])

    def test_finds_verstappen_as_2023_dutch_gp_winner(self):
        winner, meta = find_winner(2023, self.round_2023_dutch_gp, DEFAULT_CACHE_DIR)
        self.assertEqual(winner, "VER")
        self.assertIn("url", meta)

    def test_raises_when_jolpica_has_no_result_yet(self):
        with self.assertRaises(SystemExit):
            find_winner(2099, 1, DEFAULT_CACHE_DIR)


class TestComputePostRace(unittest.TestCase):
    def test_brier_arithmetic_and_top_pick(self):
        p_algo = {"A": 0.6, "B": 0.3, "C": 0.1}
        markets = {
            "market_mean": {"A": 0.5, "B": 0.3, "C": 0.2},
            "polymarket": {"by_code": {
                "A": {"normalized": 0.5}, "B": {"normalized": 0.3}, "C": {"normalized": 0.2},
            }},
            "kalshi": {"by_code": {
                "A": {"normalized": 0.4}, "B": {"normalized": 0.4}, "C": {"normalized": 0.2},
            }},
        }
        result = compute_post_race(p_algo, markets, winner="A")

        self.assertAlmostEqual(result["brier_algo"], 0.4**2 + 0.3**2 + 0.1**2)
        self.assertAlmostEqual(result["brier_market_mean"], 0.5**2 + 0.3**2 + 0.2**2)
        self.assertEqual(result["top_pick"], "A")
        self.assertTrue(result["top_pick_correct"])
        self.assertTrue(result["beat_market_mean_on_brier"])

    def test_wrong_top_pick_is_recorded_as_incorrect(self):
        p_algo = {"A": 0.6, "B": 0.4}
        markets = {"market_mean": {}, "polymarket": {"by_code": {}}, "kalshi": {"by_code": {}}}
        result = compute_post_race(p_algo, markets, winner="B")
        self.assertEqual(result["top_pick"], "A")
        self.assertFalse(result["top_pick_correct"])

    def test_unknown_winner_code_fails_loudly(self):
        p_algo = {"A": 1.0}
        markets = {"market_mean": {}, "polymarket": {"by_code": {}}, "kalshi": {"by_code": {}}}
        with self.assertRaises(AssertionError):
            compute_post_race(p_algo, markets, winner="ZZZ")


if __name__ == "__main__":
    unittest.main()
