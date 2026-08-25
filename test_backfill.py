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
import postrace
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


class TestReproducesTheDutchGPReferenceTable(unittest.TestCase):
    """sec9 assertion 10: the harness must reproduce 02-winner-prediction-algo.md
    sec9's reference sub-scores when pointed at the 2026 Dutch GP. It is the
    cheapest check that sec4.2's shared-code-path rule still holds -- if these
    drift, every row in the training set is computed by something other than
    the scorer that runs at inference.

    The assertion is not satisfiable as literally written, and that is a spec
    defect rather than a code one. 2026 R12 is a SPRINT weekend, and sec4.4
    item 4 records that a backfilled sprint weekend cannot see that weekend's
    own sprint points: F6 reads {season}/{round-1}/driverstandings.json, and no
    round-indexed endpoint answers "after round 11's race plus round 12's
    sprint". So F6 provably differs on the one race sec9 names as the fixed
    point. Six of the seven features reproduce sec9 exactly; F6 reproduces it
    exactly once R12's sprint points are added back, which is what this checks.
    """

    # 02 sec9, mapped onto score_all's sub_scores names.
    REFERENCE = {
        "NOR": {"grid": 1.000, "team": 0.733, "sprint": 0.565, "driver_form": 0.905,
                "track": 0.450, "teammate": 0.500, "champ": 0.598},
        "RUS": {"grid": 0.779, "team": 0.931, "sprint": 1.000, "driver_form": 0.943,
                "track": 0.412, "teammate": 0.375, "champ": 0.750},
        "ANT": {"grid": 0.607, "team": 0.931, "sprint": 0.424, "driver_form": 0.770,
                "track": 0.323, "teammate": 0.625, "champ": 1.000},
        "LEC": {"grid": 0.287, "team": 1.000, "sprint": 0.751, "driver_form": 0.838,
                "track": 0.248, "teammate": 0.556, "champ": 0.647},
        "HAM": {"grid": 0.368, "team": 1.000, "sprint": 0.180, "driver_form": 1.000,
                "track": 0.188, "teammate": 0.444, "champ": 0.763},
        "PIA": {"grid": 0.472, "team": 0.733, "sprint": 0.319, "driver_form": 0.508,
                "track": 0.785, "teammate": 0.500, "champ": 0.429},
        "VER": {"grid": 0.223, "team": 0.802, "sprint": 0.240, "driver_form": 0.916,
                "track": 1.000, "teammate": 1.000, "champ": 0.500},
    }
    STANDINGS_FREE = ["grid", "team", "sprint", "driver_form", "track", "teammate"]
    SPRINT_POINTS = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

    @classmethod
    def setUpClass(cls):
        cls.rows, _ = backfill.build_race_rows(2026, 12, CACHE)
        cls.by_code = {r["driver_code"]: r for r in cls.rows}

    def test_the_reference_race_is_a_sprint_weekend(self):
        """Load-bearing for this whole class: it is why F6 is exempted below,
        and it is what makes sec3.4's zero-the-column rule NOT apply here."""
        self.assertEqual(self.rows[0]["is_sprint_weekend"], 1)

    def test_six_features_reproduce_the_reference_table_exactly(self):
        for code, ref in self.REFERENCE.items():
            for f in self.STANDINGS_FREE:
                with self.subTest(driver=code, feature=f):
                    self.assertAlmostEqual(self.by_code[code][f], ref[f], places=3)

    def test_f6_differs_by_exactly_this_weekends_sprint_points(self):
        """sec4.4 item 4's accepted train-only skew, measured rather than
        asserted. Note it moves drivers in BOTH directions: the feature is
        normalised by the leader's points, and the leader scored in the sprint
        too, so a driver who scored nothing (HAM, 2 points) still shifts."""
        standings, _ = jolpica.driver_standings(2026, CACHE, round_=11)
        points = {s["Driver"]["code"]: float(s["points"]) for s in standings}
        sprint_results, _ = jolpica.sprint(2026, 12, CACHE)
        gained = {r["Driver"]["code"]: self.SPRINT_POINTS.get(int(r["position"]), 0)
                  for r in sprint_results}

        after = {c: points.get(c, 0) + gained.get(c, 0) for c in set(points) | set(gained)}
        leader_after = max(after.values())

        for code, ref in self.REFERENCE.items():
            with self.subTest(driver=code):
                self.assertAlmostEqual(after[code] / leader_after, ref["champ"], places=3)

    def test_the_skew_stays_inside_its_documented_bound(self):
        """sec4.4 bounds this at 8 points on a 0.08-weight feature. On the
        sub-score itself that is what the bound has to mean in practice."""
        worst = max(abs(self.by_code[c]["champ"] - r["champ"])
                    for c, r in self.REFERENCE.items())
        self.assertLess(worst, 8.0 / 219.0)

    def test_the_winner_label_is_norris(self):
        self.assertEqual([r["driver_code"] for r in self.rows if r["label"] == 1], ["NOR"])


class TestEmptyResultCacheIsNotBelievedBlindly(unittest.TestCase):
    """A results response fetched before a race ran caches "no result" forever.
    Seen live: 2026/12/results.json was cached at 04:17Z on race day, nine
    hours before lights out, so every local run afterwards concluded the Dutch
    GP had never happened -- including the backfill, which then died rather
    than skipped (SystemExit is not an Exception).

    sec5.4's staleness rule does not catch this: it compares fetch date against
    race date at DAY granularity, and here they are the same day.
    """

    def test_the_dutch_gp_has_a_result(self):
        rows, _ = postrace.find_full_result(2026, 12, CACHE)
        self.assertEqual(next(r["code"] for r in rows
                              if r["classified"] and r["position"] == 1), "NOR")

    def test_a_resultless_race_skips_one_race_instead_of_killing_the_run(self):
        """backfill.main() guards with `except Exception`, so a BaseException
        escaping here aborts a multi-hour run on its last race."""
        with self.assertRaises(Exception) as ctx:
            backfill.find_full_result_checked(2099, 1, CACHE)
        self.assertNotIsInstance(ctx.exception, SystemExit)


class TestDoubleHeaderSeasons(unittest.TestCase):
    """Regression: build_track_history selected the 3 most recent SEASONS but
    handed out weights from a 3-slot list, which is only safe if a season holds
    at most one race per circuit. COVID broke that -- 2020 ran two races at
    each of bahrain, silverstone and red_bull_ring, and 2021 two more at
    red_bull_ring -- so an affected driver got 4-5 rows for 3 slots and the
    race died with a bare IndexError.

    It failed as a *skip*, not a crash, so the only visible symptom was races
    quietly missing from the training set: 2021 R1/R8/R9/R10 and 2022 R1 had
    already been dropped before anyone diffed the round numbers, with six more
    to come in 2022-2024.
    """

    @classmethod
    def setUpClass(cls):
        # 2021 Bahrain: the lookback window is 2020 (TWO races), 2019, 2018.
        race, _ = jolpica.race_info(2021, 1, CACHE)
        grid, _, _ = snapshot.build_grid(2021, 1, CACHE)
        cls.th, _ = snapshot.build_track_history(
            race["Circuit"]["circuitId"],
            float(race["Circuit"]["Location"]["lat"]),
            float(race["Circuit"]["Location"]["long"]),
            grid, race["date"], CACHE,
        )

    def test_it_builds_at_all(self):
        self.assertTrue(self.th["by_driver"])

    def test_no_driver_exceeds_the_three_weight_slots(self):
        for code, rows in self.th["by_driver"].items():
            with self.subTest(driver=code):
                self.assertLessEqual(len(rows), 3)

    def test_weights_are_assigned_in_date_order(self):
        expected = [1.0, 0.7, 0.5]
        for code, rows in self.th["by_driver"].items():
            with self.subTest(driver=code):
                dates = [r["date"] for r in rows]
                self.assertEqual(dates, sorted(dates, reverse=True))
                self.assertEqual([r["recency_weight"] for r in rows],
                                 expected[:len(rows)])

    def test_both_2020_editions_are_kept_as_separate_editions(self):
        """The whole point: keyed by season, the Sakhir GP and the Bahrain GP
        collapse into one entry and one of them silently vanishes."""
        dates_2020 = sorted(d for d in self.th["editions_weather"] if d.startswith("2020"))
        self.assertEqual(len(dates_2020), 2, f"expected both 2020 editions, got {dates_2020}")

    def test_each_edition_carries_its_own_weather(self):
        """Keyed by season, whichever edition was seen first donated its `wet`
        flag to the other. F7's wet branch reads that flag."""
        for date, entry in self.th["editions_weather"].items():
            with self.subTest(date=date):
                self.assertEqual(entry["date"], date)

    def test_a_drivers_wet_flag_matches_his_own_editions_date(self):
        for code, rows in self.th["by_driver"].items():
            for r in rows:
                with self.subTest(driver=code, date=r["date"]):
                    self.assertEqual(r["wet"], self.th["editions_weather"][r["date"]]["wet"])


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
