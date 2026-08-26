#!/usr/bin/env python3
"""lib.circuits.tier_for and tier_interaction_backtest.add_tier_columns.

05-trained-model.md sec3.5 / sec10 item 2. The backtest script itself
(tier_interaction_backtest.py) is verification tooling, same status as
weather_backtest.py -- these tests cover the two pieces of logic in it that
would silently produce a wrong design matrix rather than a crash: the tier
lookup, and the column-append arithmetic.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fit
from lib.circuits import OVERTAKING_MULTIPLIER, tier_for
from tier_interaction_backtest import TIER_FEATURES, add_tier_columns


class TestTierFor(unittest.TestCase):

    def test_hard_tier_circuits(self):
        for cid, m in OVERTAKING_MULTIPLIER.items():
            if m == 1.15:
                self.assertEqual(tier_for(cid), "hard")

    def test_easy_tier_circuits(self):
        for cid, m in OVERTAKING_MULTIPLIER.items():
            if m == 0.85:
                self.assertEqual(tier_for(cid), "easy")

    def test_explicit_default_tier_circuits(self):
        for cid, m in OVERTAKING_MULTIPLIER.items():
            if m == 1.00:
                self.assertEqual(tier_for(cid), "default")

    def test_unassigned_circuit_is_default_not_guessed(self):
        # sec10 item 2: a circuit the backfill added but no one hand-tiered
        # must land in the same bucket as the explicitly-1.00 circuits, not
        # get a guessed tier.
        self.assertNotIn("bahrain", OVERTAKING_MULTIPLIER)
        self.assertEqual(tier_for("bahrain"), "default")

    def test_every_corpus_circuit_resolves(self):
        # Never raises, whatever circuit_id it sees -- multiplier_for's own
        # dict.get(..., default) contract, exercised through tier_for.
        for cid in ("nonexistent_circuit", "", "zandvoort", "monza"):
            self.assertIn(tier_for(cid), ("hard", "default", "easy"))


def _race(circuit_id, grid_values, win_index=0):
    r = fit.Race(2023, 1, "2023-01-01", circuit_id)
    r.codes = [f"D{i}" for i in range(len(grid_values))]
    r.win_index = win_index
    r.n_wins = 1
    r.p_a1 = [1.0 / len(grid_values)] * len(grid_values)
    r.x = [[g, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5] for g in grid_values]
    return r


class TestAddTierColumns(unittest.TestCase):

    def test_hard_circuit_gets_grid_in_the_hard_column_only(self):
        hard_cid = next(c for c, m in OVERTAKING_MULTIPLIER.items() if m == 1.15)
        r = _race(hard_cid, [0.9, 0.3])
        [out] = add_tier_columns([r])
        self.assertEqual(len(out.x[0]), fit.K + 2)
        self.assertAlmostEqual(out.x[0][fit.K], 0.9)   # grid_x_hard
        self.assertAlmostEqual(out.x[0][fit.K + 1], 0.0)  # grid_x_easy
        self.assertAlmostEqual(out.x[1][fit.K], 0.3)
        self.assertAlmostEqual(out.x[1][fit.K + 1], 0.0)

    def test_easy_circuit_gets_grid_in_the_easy_column_only(self):
        easy_cid = next(c for c, m in OVERTAKING_MULTIPLIER.items() if m == 0.85)
        r = _race(easy_cid, [0.7])
        [out] = add_tier_columns([r])
        self.assertAlmostEqual(out.x[0][fit.K], 0.0)
        self.assertAlmostEqual(out.x[0][fit.K + 1], 0.7)

    def test_unassigned_circuit_gets_zero_on_both_new_columns(self):
        r = _race("bahrain", [0.6])
        [out] = add_tier_columns([r])
        self.assertAlmostEqual(out.x[0][fit.K], 0.0)
        self.assertAlmostEqual(out.x[0][fit.K + 1], 0.0)

    def test_original_seven_columns_are_untouched(self):
        r = _race("monaco", [0.8])
        [out] = add_tier_columns([r])
        self.assertEqual(out.x[0][:fit.K], r.x[0])

    def test_source_race_is_not_mutated(self):
        r = _race("monaco", [0.8])
        before = [row[:] for row in r.x]
        add_tier_columns([r])
        self.assertEqual(r.x, before)

    def test_race_metadata_carried_over(self):
        r = _race("monza", [0.5, 0.5], win_index=1)
        r.season, r.round = 2019, 7
        [out] = add_tier_columns([r])
        self.assertEqual(out.season, 2019)
        self.assertEqual(out.round, 7)
        self.assertEqual(out.win_index, 1)
        self.assertEqual(out.codes, r.codes)
        self.assertEqual(out.p_a1, r.p_a1)

    def test_feature_names_match_appended_columns(self):
        self.assertEqual(TIER_FEATURES, ["grid_x_hard", "grid_x_easy"])


if __name__ == "__main__":
    unittest.main()
