#!/usr/bin/env python3
"""The A3 backfill harness, against real Jolpica data. 05-trained-model.md sec4/sec5.

Same discipline as the other test_*.py: verify against races that actually
happened, not against assumptions. Every race used here is fully resolved and
its result is a matter of record, so the expected values are checkable by
anyone who remembers the season.

These hit the network on a cold cache and are near-instant on a warm one.
"""

import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backfill
import snapshot
from lib import jolpica

CACHE = snapshot.DEFAULT_CACHE_DIR


class TestStandingsWithoutAPosition(unittest.TestCase):
    """Regression: Jolpica omits `position` entirely for drivers tied on zero
    points, sending positionText '-' instead. int(s["position"]) raised a
    KeyError on every early-season round, which no run had ever reached --
    2026 snapshots are mid-season and the 2023 backfill is round 13."""

    @classmethod
    def setUpClass(cls):
        grid, _, _ = snapshot.build_grid(2015, 2, CACHE)
        cls.form, _ = snapshot.build_form(2015, 2, grid, CACHE, race_has_run=True)

    def test_build_form_survives_unranked_drivers(self):
        self.assertTrue(self.form["driver_standings"])

    def test_unranked_drivers_are_none_not_invented(self):
        unranked = [s for s in self.form["driver_standings"] if s["position"] is None]
        self.assertTrue(unranked, "2015 R1 standings should have drivers tied on zero points")
        for s in unranked:
            self.assertEqual(s["points"], 0.0)
            self.assertEqual(s["position_text"], "-")

    def test_ranked_drivers_still_get_a_real_position(self):
        ranked = [s for s in self.form["driver_standings"] if s["position"] is not None]
        self.assertTrue(ranked)
        self.assertEqual(min(s["position"] for s in ranked), 1)

    def test_f6_is_unaffected_because_it_scores_off_points(self):
        """The field is diagnostic only -- compute_champ divides points by
        leader points and never reads position. Hamilton won the opener, so he
        is the leader going into round 2."""
        by_code = {s["code"]: s["points"] for s in self.form["driver_standings"]}
        self.assertEqual(max(by_code.values()), by_code["HAM"])


class TestRaceRows(unittest.TestCase):
    """2015 Chinese GP, round 3: Hamilton won from pole, Rosberg 2nd,
    Vettel 3rd. Non-sprint weekend (sprints did not exist until 2021)."""

    @classmethod
    def setUpClass(cls):
        cls.rows, cls.stale = backfill.build_race_rows(2015, 3, CACHE)
        cls.by_code = {r["driver_code"]: r for r in cls.rows}

    def test_exactly_one_winner_and_it_is_hamilton(self):
        winners = [r["driver_code"] for r in self.rows if r["label"] == 1]
        self.assertEqual(winners, ["HAM"])

    def test_label_agrees_with_the_finishing_position(self):
        for r in self.rows:
            if r["label"] == 1:
                self.assertEqual(r["finish_position"], 1)
                self.assertEqual(r["classified"], 1)

    def test_every_feature_is_in_the_unit_interval(self):
        for r in self.rows:
            for f in backfill.FEATURES:
                with self.subTest(driver=r["driver_code"], feature=f):
                    self.assertGreaterEqual(r[f], 0.0)
                    self.assertLessEqual(r[f], 1.0)

    def test_no_weather_column(self):
        """sec3.3: F7 is dormant for every backfilled row, which makes it a
        within-race constant, which makes its coefficient unidentified. It is
        dropped from the matrix rather than carried as a constant 0.5."""
        self.assertNotIn("weather", backfill.COLUMNS)
        for r in self.rows:
            self.assertNotIn("weather", r)

    def test_sprint_is_zero_for_the_whole_field_on_a_non_sprint_race(self):
        """sec3.4. score.compute_sprint returns NEUTRAL here, which is correct
        for A1 (it drops the feature and renormalizes) but would put a 0.5
        constant in a pooled design matrix."""
        self.assertEqual({r["sprint"] for r in self.rows}, {0.0})

    def test_pole_sitter_scores_one_on_grid(self):
        pole = min(self.rows, key=lambda r: r["quali_position"])
        self.assertEqual(pole["quali_position"], 1)
        self.assertEqual(pole["grid"], 1.0)

    def test_a1_probabilities_sum_to_one(self):
        self.assertAlmostEqual(sum(r["p_a1"] for r in self.rows), 1.0, places=6)

    def test_no_stale_track_history_for_a_decade_old_race(self):
        self.assertEqual(self.stale, [])


class TestRoundOneIsFeaturePoorButUsable(unittest.TestCase):
    """sec4.3: F2/F4/F6/F8 all read *this season's* completed rounds, of which
    round 1 has none. Those columns come back field-constant, which by sec3.2
    cancels out of a conditional logit's likelihood exactly -- so the race
    informs grid and track history and cannot corrupt anything else. The spec
    says keep these races; this is the check that they build at all."""

    @classmethod
    def setUpClass(cls):
        cls.rows, _ = backfill.build_race_rows(2015, 1, CACHE)

    def test_it_builds_and_hamilton_won_the_2015_opener(self):
        self.assertEqual([r["driver_code"] for r in self.rows if r["label"] == 1], ["HAM"])

    def test_the_four_in_season_features_are_field_constant(self):
        for f in ("team", "driver_form", "champ", "teammate"):
            with self.subTest(feature=f):
                self.assertEqual(len({r[f] for r in self.rows}), 1)

    def test_grid_and_track_history_still_vary(self):
        """If these collapsed too, the race would carry no signal at all and
        keeping it would be pointless."""
        for f in ("grid", "track"):
            with self.subTest(feature=f):
                self.assertGreater(len({r[f] for r in self.rows}), 1)


class TestResumability(unittest.TestCase):
    """sec5.3: a rate-limit stall should cost the remaining races, not the
    finished ones."""

    def test_already_done_reads_back_written_races(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            w = csv.DictWriter(f, fieldnames=backfill.COLUMNS)
            w.writeheader()
            for season, round_ in ((2015, 1), (2015, 2), (2016, 7)):
                row = {c: "" for c in backfill.COLUMNS}
                row.update({"season": season, "round": round_})
                w.writerow(row)
            path = f.name
        try:
            self.assertEqual(backfill.already_done(path), {(2015, 1), (2015, 2), (2016, 7)})
        finally:
            os.unlink(path)

    def test_missing_file_is_an_empty_set_not_an_error(self):
        self.assertEqual(backfill.already_done("/nonexistent/nope.csv"), set())


if __name__ == "__main__":
    unittest.main()
