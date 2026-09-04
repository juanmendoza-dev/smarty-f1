#!/usr/bin/env python3
"""Exercise F7's active (wet-weather) branch, which has still never run on a
real race, against the four-model ensemble path.

This is the shipping gate for 06-weather-ensemble-signal.md, not an
afterthought (06 sec7.1, sec10 item5). Two things changed together and neither
is safe alone: a wet edition is now >= 0.5mm rather than any trace at all
(sec6.1), and F7's gate reads p_mean rather than a blended p_max (sec6.2).
Changing the gate input is the act that first fires this branch, so it gets
exercised here against real pulled data rather than a stub.

Everything below runs on live Open-Meteo/Jolpica responses through the real
build_weather / build_track_history, cached to data/cache like every other
network-touching test in this suite. Numbers are asserted only where they were
independently reproduced by weather_backtest.py, which shares no code with the
snapshot path.

Monza carries the test's wet history: its 2024 edition observed 0.7mm, the one
edition across both configured circuits that clears 0.5mm. Zandvoort no longer
has any -- 0.4 / 0.0 / 0.2mm -- which is a real consequence of sec6.1 and is
asserted here rather than left to be discovered on a wet race day.
"""

import copy
import json
import os
import unittest
import unittest.mock

import score
import snapshot
from lib import jolpica
from lib.invariants import InvariantError

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "data", "snapshots")
CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")

# 2026 rounds 13 and 12. Lights-out in UTC, as Jolpica serves it.
MONZA = ("monza", "2026-09-06", "13:00:00Z")
ZANDVOORT = ("zandvoort", "2026-08-23", "13:00:00Z")


def _latlon(circuit_id):
    row, _ = jolpica.circuit(circuit_id, CACHE_DIR)
    return float(row["Location"]["lat"]), float(row["Location"]["long"])


class TestEnsembleWeatherBlock(unittest.TestCase):
    """build_weather's four-model output -- sec4.2's aggregates and sec4.4's raw."""

    @classmethod
    def setUpClass(cls):
        circuit, date, time = ZANDVOORT
        lat, lon = _latlon(circuit)
        cls.weather, _ = snapshot.build_weather(lat, lon, date, time, circuit, CACHE_DIR)

    def test_all_four_models_are_persisted_raw(self):
        """sec4.4: never persist only the aggregate."""
        self.assertEqual(sorted(self.weather["per_model"]),
                         sorted(snapshot.openmeteo.ENSEMBLE_MODELS))
        for model, series in self.weather["per_model"].items():
            self.assertEqual(len(series["local_time"]), 5, f"{model} window is not 5 hours")
            for field in snapshot.openmeteo.HOURLY_FIELDS:
                self.assertIn(field, series, f"{model} is missing {field}")
                self.assertEqual(len(series[field]), 5)

    def test_aggregates_are_in_range(self):
        for name in ("p_mean", "p_max", "p_spread"):
            self.assertGreaterEqual(self.weather[name], 0)
            self.assertLessEqual(self.weather[name], 100)

    def test_dutch_gp_matches_the_independent_backtest(self):
        """sec7.1's worked example, reproduced through the pipeline rather than quoted.

        weather_backtest.py pulls the same race off the historical-forecast
        endpoint and derives the aggregates with its own code, and lands on
        p_mean 32.50 / p_max 88 / p_spread 77.0. That the snapshot path agrees
        is the check that sec4.2's order of collapse was implemented, not just
        described -- mean-then-max and max-then-mean differ here by a lot.
        """
        self.assertAlmostEqual(self.weather["p_mean"], 32.50, places=2)
        self.assertEqual(self.weather["p_max"], 88)
        self.assertAlmostEqual(self.weather["p_spread"], 77.0, places=1)

    def test_the_two_aggregates_land_on_opposite_sides_of_the_gate(self):
        """Which is the whole reason sec6.2 had to be decided rather than assumed."""
        self.assertLess(self.weather["p_mean"], 40, "p_mean should leave F7 dormant here")
        self.assertGreaterEqual(self.weather["p_max"], 40, "p_max would have gone active")

    def test_spread_sets_the_agreement_flag(self):
        self.assertEqual(self.weather["agree"], self.weather["p_spread"] < 15)
        self.assertFalse(self.weather["agree"],
                         "77pp apart is the disagree case sec7.3 exists for")

    def test_hourly_window_declares_that_it_is_now_a_cross_model_mean(self):
        self.assertEqual(self.weather["hourly_window_source"], "ensemble_mean_over_4_models")
        self.assertEqual(len(self.weather["hourly_window"]), 5)


class TestWetHistoryUnderTheTighterRule(unittest.TestCase):
    """sec6.1: >= 0.5mm. Asserted against observed mm the archive actually returns."""

    @classmethod
    def setUpClass(cls):
        cls.editions = {}
        grid = [{"code": "VER", "driver_id": "max_verstappen"}]
        for circuit, date, _ in (MONZA, ZANDVOORT):
            lat, lon = _latlon(circuit)
            th, _ = snapshot.build_track_history(circuit, lat, lon, grid, date, CACHE_DIR)
            cls.editions[circuit] = th["editions_weather"]

    def test_monza_2024_is_wet_on_a_real_0_7mm(self):
        e = self.editions["monza"]["2024-09-01"]
        self.assertAlmostEqual(e["race_window_precip_max_mm"], 0.7, places=2)
        self.assertTrue(e["wet"])

    def test_a_trace_no_longer_counts_as_a_wet_race(self):
        """The 2023 and 2025 Dutch GPs -- 0.4mm and 0.2mm -- were wet under the
        old rule and are dry under this one. That flip is sec6.1's entire point:
        the same race used to be wet in the code and dry in the prose.
        """
        for date, mm in (("2023-08-27", 0.4), ("2025-08-31", 0.2)):
            e = self.editions["zandvoort"][date]
            self.assertAlmostEqual(e["race_window_precip_max_mm"], mm, places=2)
            self.assertGreater(e["race_window_precip_max_mm"], 0.0)
            self.assertFalse(e["wet"], f"{date} at {mm}mm should be dry under >= 0.5mm")

    def test_zandvoort_now_has_no_wet_edition_at_all(self):
        """Recorded because it is the cost of sec6.1 and the spec does not discuss it.

        Under >= 0.5mm none of Zandvoort's last three editions is wet, so F7's
        active branch there has nothing to score anyone on and returns a flat
        field (see TestF7ActiveBranch below). sec6.1 is decided and stands; this
        asserts the consequence stays visible instead of surfacing on a wet
        Sunday.
        """
        self.assertEqual([d for d, e in self.editions["zandvoort"].items() if e["wet"]], [])
        self.assertEqual([d for d, e in self.editions["monza"].items() if e["wet"]],
                         ["2024-09-01"])


class TestF7ActiveBranch(unittest.TestCase):
    """The branch itself, on a real grid against real Monza wet history."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SNAPSHOT_DIR, "2026-12-race-20260823T031058Z.json")) as f:
            frozen = json.load(f)
        cls.frozen = frozen
        cls.grid = frozen["grid"]

        lat, lon = _latlon(MONZA[0])
        th, _ = snapshot.build_track_history(MONZA[0], lat, lon, cls.grid, MONZA[1], CACHE_DIR)
        cls.monza_history = th
        # Only the wet history and the gate scalar matter to compute_weather.
        cls.monza = {"grid": cls.grid, "track_history": th, "weather": {"p_mean": 0}}

    def _score(self, p_mean, history=None):
        snap = copy.deepcopy(self.monza)
        if history is not None:
            snap["track_history"] = history
        snap["weather"]["p_mean"] = p_mean
        return score.compute_weather(snap)

    def test_dormant_below_the_gate(self):
        s_weather, dormant, p_mean = self._score(32.5)
        self.assertTrue(dormant)
        self.assertEqual(p_mean, 32.5)
        self.assertTrue(all(v == score.NEUTRAL for v in s_weather.values()))

    def test_active_branch_discriminates_and_stays_in_range(self):
        """First execution of this branch against >= 0.5mm history."""
        s_weather, dormant, _ = self._score(42)
        self.assertFalse(dormant)
        for code, v in s_weather.items():
            self.assertGreaterEqual(v, -1e-9, code)
            self.assertLessEqual(v, 1 + 1e-9, code)
        self.assertTrue(any(v != score.NEUTRAL for v in s_weather.values()),
                        "active branch produced a flat field -- it isn't discriminating")

    def test_a_driver_who_finished_monza_2024_well_beats_one_who_did_not(self):
        """Ordering, checked against the one wet edition's actual classification
        rather than against a remembered result.
        """
        rows = self.monza_history["by_driver"]
        wet = {}
        for code, driver_rows in rows.items():
            for r in driver_rows:
                if r["wet"] and r["classified"]:
                    wet[code] = r["position"]
        self.assertGreaterEqual(len(wet), 2, "need two classified finishers in the wet edition")
        best = min(wet, key=lambda c: wet[c])
        worst = max(wet, key=lambda c: wet[c])
        s_weather, _, _ = self._score(42)
        self.assertGreater(s_weather[best], s_weather[worst],
                           f"{best} (P{wet[best]}) should outrank {worst} (P{wet[worst]})")

    def test_drivers_with_no_wet_edition_are_neutral(self):
        s_weather, _, _ = self._score(42)
        rows = self.monza_history["by_driver"]
        for d in self.grid:
            code = d["code"]
            if not any(r["wet"] for r in rows.get(code, [])):
                self.assertEqual(s_weather[code], score.NEUTRAL,
                                 f"{code} has no wet Monza edition and should be NEUTRAL")

    def test_zandvoort_active_branch_is_flat_under_the_new_rule(self):
        """The consequence of sec6.1, asserted rather than assumed.

        Zandvoort has no >= 0.5mm edition left, so every driver takes the n_wet=0
        path and F7 contributes nothing even with the gate open. This is not a
        bug in the branch -- it is what a circuit with no wet history looks like.
        """
        lat, lon = _latlon(ZANDVOORT[0])
        th, _ = snapshot.build_track_history(ZANDVOORT[0], lat, lon, self.grid,
                                             ZANDVOORT[1], CACHE_DIR)
        s_weather, dormant, _ = self._score(42, history=th)
        self.assertFalse(dormant)
        self.assertTrue(all(v == score.NEUTRAL for v in s_weather.values()))

    def test_recency_weight_survives_the_wet_only_filter(self):
        """Regression guard: filtering to wet-only editions must not let an older
        edition inherit a newer one's weight slot. Weights are baked in at
        snapshot time by rank among all of a driver's editions, so a rank-2 wet
        row keeps 0.7 even when it is the only row F7 sees.
        """
        for code, rows in self.monza_history["by_driver"].items():
            expected = [1.0, 0.7, 0.5][:len(rows)]
            self.assertEqual([r["recency_weight"] for r in rows], expected, code)
            for r in rows:
                if r["date"] == "2024-09-01":
                    self.assertTrue(r["wet"])

    def test_a_pre_ensemble_snapshot_is_refused_not_silently_dormant(self):
        """sec6.2. The frozen Dutch snapshot carries a blended p_max of 37 and no
        p_mean. Scoring it against the current gate would be reading one quantity
        under another's name, so it raises instead.
        """
        legacy = {k: v for k, v in self.frozen.items() if k != "markets"}
        self.assertNotIn("p_mean", legacy["weather"])
        self.assertEqual(legacy["weather"]["p_max"], 37)
        with self.assertRaises(InvariantError) as cm:
            score.compute_weather(legacy)
        self.assertIn("p_mean", str(cm.exception))


class TestFullPipelineWithAnEnsembleBlock(unittest.TestCase):
    """score_all end to end on a snapshot carrying a real ensemble weather block."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SNAPSHOT_DIR, "2026-12-race-20260823T031058Z.json")) as f:
            frozen = json.load(f)
        circuit, date, time = ZANDVOORT
        lat, lon = _latlon(circuit)
        weather, _ = snapshot.build_weather(lat, lon, date, time, circuit, CACHE_DIR)
        # In memory only -- snapshots on disk are immutable (01 sec8.3).
        snap = {k: v for k, v in frozen.items() if k != "markets"}
        snap["weather"] = weather
        cls.snapshot = snap

    def test_dormant_run_still_satisfies_the_softmax_invariants(self):
        result = score.score_all(copy.deepcopy(self.snapshot))
        self.assertTrue(result["weather_dormant"])
        self.assertAlmostEqual(sum(result["p_algo"].values()), 1.0, places=6)
        self.assertAlmostEqual(result["p_mean"], 32.50, places=2)

    def test_active_run_still_satisfies_the_softmax_invariants(self):
        active = copy.deepcopy(self.snapshot)
        active["weather"]["p_mean"] = 42
        result = score.score_all(active)
        self.assertFalse(result["weather_dormant"])
        self.assertAlmostEqual(sum(result["p_algo"].values()), 1.0, places=6)


class TestAggregatesMatchTheIndependentBacktest(unittest.TestCase):
    """snapshot.ensemble_aggregates against weather_backtest.derive(), 44 races.

    The two implementations share no code and read different endpoints -- the
    backtest replays historical-forecast runs, the pipeline calls the live
    forecast API -- so agreeing on all three aggregates across every race in
    06 sec5's corpus is the real check that sec4.2's order of collapse shipped
    the way it is specified. One race would not distinguish max(mean(...)) from
    mean(max(...)); 44 do.

    Skipped rather than failed if the corpus JSON hasn't been generated, since
    building it is a few hundred cached HTTP reads:
        python3 weather_backtest.py --today 2026-08-23 --json <path>
    """

    CORPUS = os.environ.get(
        "F1_WEATHER_BACKTEST_JSON",
        os.path.join(REPO_ROOT, "data", "cache", "weather-backtest-rows.json"),
    )

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(cls.CORPUS):
            raise unittest.SkipTest(f"no backtest corpus at {cls.CORPUS}")
        with open(cls.CORPUS) as f:
            cls.rows = json.load(f)

    def test_every_race_agrees_on_all_three_aggregates(self):
        import weather_backtest

        self.assertGreaterEqual(len(self.rows), 44, "corpus is smaller than 06 sec5's 44 races")
        for row in self.rows:
            models = sorted(row["per_model"])
            self.assertEqual(models, sorted(snapshot.openmeteo.ENSEMBLE_MODELS))
            n_hours = len(row["per_model"][models[0]])
            by_hour = [[row["per_model"][m][h] for m in models] for h in range(n_hours)]

            mine = snapshot.ensemble_aggregates(by_hour)
            theirs = weather_backtest.derive(row["per_model"])
            for key in ("p_mean", "p_max", "p_spread"):
                self.assertAlmostEqual(
                    mine[key], theirs[key], places=9,
                    msg=f"{row['date']} {row['name']}: {key} "
                        f"{mine[key]} (pipeline) vs {theirs[key]} (backtest)",
                )

    def test_the_agreement_flag_splits_the_corpus_the_way_sec5_3_measured(self):
        """06 sec5.3's headline: >= 15pp flags 43% of races. Recomputed, not quoted."""
        flags = [snapshot.ensemble_aggregates(
            [[r["per_model"][m][h] for m in sorted(r["per_model"])]
             for h in range(len(r["per_model"]["ecmwf_ifs025"]))])["agree"]
            for r in self.rows]
        disagree = sum(1 for f in flags if not f)
        self.assertEqual(len(flags) - disagree, 25, "agree bucket should hold 25 races")
        self.assertEqual(disagree, 19, "disagree bucket should hold 19 races")


class TestWindowSpanningALocalDateBoundary(unittest.TestCase):
    """A race whose local window is not on its UTC date still gets a forecast.

    Jolpica's race date is UTC; the window is circuit-local. Las Vegas 2026 is
    the live case -- race_date 2026-11-22, local window 2026-11-21 18:00-22:00 --
    and a forecast pull bounded to the race date alone contained none of those
    hours, so F7 went dormant on an empty response rather than a dry forecast.
    Pre-existing, found while adding the aggregates' invariants.

    Vegas itself is past the forecast horizon until November, so this reproduces
    the same geometry at the same circuit on a date the endpoint serves: a UTC
    time late enough that the window sits on the previous local day and crosses
    midnight.
    """

    def test_all_five_window_hours_are_present_across_the_boundary(self):
        lat, lon = _latlon("vegas")
        weather, _ = snapshot.build_weather(lat, lon, "2026-09-10", "06:00:00Z",
                                            "vegas", CACHE_DIR)
        times = [r["local_time"] for r in weather["hourly_window"]]
        self.assertEqual(len(times), 5, f"window came back as {times}")
        self.assertEqual(times[0][:10], "2026-09-09")
        self.assertEqual(times[-1][:10], "2026-09-10")
        self.assertIsNotNone(weather["p_mean"])


class TestInvariantsFire(unittest.TestCase):
    """The guards are require(), not assert, so they survive python -O
    (lib/invariants.py). These check they actually trip -- an invariant nobody
    has seen fail is an invariant nobody knows is wired up.
    """

    def test_probability_outside_0_100_is_rejected(self):
        with self.assertRaises(InvariantError) as cm:
            snapshot.ensemble_aggregates([[10, 20, 30, 140]])
        self.assertIn("outside [0, 100]", str(cm.exception))

    def test_ragged_hours_are_rejected(self):
        with self.assertRaises(InvariantError):
            snapshot.ensemble_aggregates([[10, 20, 30, 40], [10, 20, 30]])

    def test_an_empty_window_yields_no_aggregates_rather_than_a_zero(self):
        self.assertEqual(
            snapshot.ensemble_aggregates([]),
            {"p_mean": None, "p_max": None, "p_spread": None, "agree": None},
        )

    def test_a_model_missing_from_the_response_is_named_in_the_failure(self):
        """Open-Meteo 400s on a model name it doesn't know, so this is the guard
        for a model silently dropping out of an otherwise-200 response -- stub
        the transport rather than try to provoke it live.
        """
        from lib import openmeteo
        body = {"hourly": {"time": ["2026-09-06T13:00"]}, "hourly_units": {}}
        for field in openmeteo.HOURLY_FIELDS:
            body["hourly"][f"{field}_ecmwf_ifs025"] = [10]
        with unittest.mock.patch.object(openmeteo.httpcache, "cached_get_json",
                                        return_value=(body, {"status": 200})):
            with self.assertRaises(InvariantError) as cm:
                openmeteo.forecast_ensemble(0, 0, "2026-09-06", "2026-09-06", "UTC",
                                            CACHE_DIR, models=["ecmwf_ifs025", "gfs_seamless"])
        self.assertIn("gfs_seamless", str(cm.exception))

    def test_a_null_inside_the_window_is_not_read_as_a_dry_forecast(self):
        """06 sec3.3's silent-null gap: per-model keys full of nulls under a 200."""
        from lib import openmeteo
        times = [f"2026-09-06T{h:02d}:00" for h in range(11, 20)]
        body = {"hourly": {"time": times}, "hourly_units": {}}
        for m in openmeteo.ENSEMBLE_MODELS:
            for field in openmeteo.HOURLY_FIELDS:
                body["hourly"][f"{field}_{m}"] = [0] * len(times)
        body["hourly"]["precipitation_probability_gem_seamless"] = [None] * len(times)
        lat, lon = _latlon(MONZA[0])
        with unittest.mock.patch.object(openmeteo.httpcache, "cached_get_json",
                                        return_value=(body, {"status": 200})):
            with self.assertRaises(InvariantError) as cm:
                snapshot.build_weather(lat, lon, MONZA[1], MONZA[2], MONZA[0], CACHE_DIR)
        self.assertIn("gem_seamless", str(cm.exception))

    def test_per_model_carries_exactly_the_four_models(self):
        lat, lon = _latlon(MONZA[0])
        weather, _ = snapshot.build_weather(lat, lon, MONZA[1], MONZA[2], MONZA[0], CACHE_DIR)
        self.assertEqual(len(weather["per_model"]), 4)
        self.assertEqual(sorted(weather["per_model"]),
                         sorted(snapshot.openmeteo.ENSEMBLE_MODELS))


if __name__ == "__main__":
    unittest.main()
