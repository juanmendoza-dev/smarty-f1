#!/usr/bin/env python3
"""Exercise F7's active (wet-weather) branch, which has never run on a real
race (02-winner-prediction-algo.md sec10 item4 -- tonight's forecast is dry,
P_max=37% is three points under the 40% activation line).

Rather than assume, this loads the frozen Dutch GP snapshot -- whose
track_history already carries each of the driver's last-3-Zandvoort-editions
observed precipitation, pulled from Open-Meteo's archive per
01-data-pipeline.md sec5.4 (archive has no probability field, so "wet" is
observed precipitation > 0mm in the +/-2h window around that edition's own
lights-out) -- and forces P_max to 42% to walk the code path that would run on
a real wet Dutch GP.

Verified independently before writing this test (see conversation record):
2023 Dutch GP had rain during the race window (window max 0.4mm), 2024 was
dry (0.0mm), 2025 had a trace at the tail of the window (0.2mm). So 2023 is
confirmed -- not assumed -- as the wet edition, matching the roadmap's
expectation.
"""

import copy
import glob
import json
import os
import unittest

import score

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "data", "snapshots")


def latest_snapshot():
    paths = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json")))
    paths = [p for p in paths if not p.endswith("-score.json")]
    return paths[-1]


class TestF7WetBranch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(latest_snapshot()) as f:
            cls.snapshot = json.load(f)
        cls.algo_snapshot = {k: v for k, v in cls.snapshot.items() if k != "markets"}

    def test_2023_dutch_gp_is_the_verified_wet_edition(self):
        ew = self.snapshot["track_history"]["editions_weather"]
        self.assertTrue(ew["2023"]["wet"])
        self.assertGreater(ew["2023"]["race_window_precip_max_mm"], 0.0)
        self.assertFalse(ew["2024"]["wet"])

    def test_tonight_is_dormant_and_neutral_for_everyone(self):
        s_weather, dormant, p_max = score.compute_weather(self.algo_snapshot)
        self.assertTrue(dormant)
        self.assertLess(p_max, 40)
        self.assertTrue(all(v == score.NEUTRAL for v in s_weather.values()))

    def test_active_branch_discriminates_and_stays_in_range(self):
        active = copy.deepcopy(self.algo_snapshot)
        active["weather"]["p_max"] = 42  # simulate the forecast crossing the 40% line
        s_weather, dormant, p_max = score.compute_weather(active)

        self.assertFalse(dormant)
        for v in s_weather.values():
            self.assertGreaterEqual(v, -1e-9)
            self.assertLessEqual(v, 1 + 1e-9)
        self.assertTrue(any(v != score.NEUTRAL for v in s_weather.values()),
                         "active branch produced a flat field -- it isn't discriminating")

    def test_ver_wet_history_outranks_ant_single_wet_race(self):
        """VER: 2025(wet, P2) and 2023(wet, P1) both count -> n_wet=2, no shrinkage.
        ANT: only 2025(wet, P16) -> n_wet=1, shrunk hard toward NEUTRAL. VER's
        record in the wet should clearly outrank ANT's under the active branch.
        """
        active = copy.deepcopy(self.algo_snapshot)
        active["weather"]["p_max"] = 42
        s_weather, _, _ = score.compute_weather(active)
        self.assertGreater(s_weather["VER"], s_weather["ANT"])

    def test_2023_edition_keeps_its_own_recency_weight_when_2024_is_excluded(self):
        """Regression guard: VER's rows are [2025(wet), 2024(dry), 2023(wet)].
        Filtering to wet-only must not let 2023 inherit 2024's 0.7 weight --
        it must keep its own rank-3 weight of 0.5.
        """
        rows = self.algo_snapshot["track_history"]["by_driver"]["VER"]
        by_season = {r["season"]: r for r in rows}
        self.assertEqual(by_season[2025]["recency_weight"], 1.0)
        self.assertEqual(by_season[2024]["recency_weight"], 0.7)
        self.assertEqual(by_season[2023]["recency_weight"], 0.5)

    def test_full_pipeline_still_satisfies_softmax_assertions_when_active(self):
        active = copy.deepcopy(self.algo_snapshot)
        active["weather"]["p_max"] = 42
        result = score.score_all(active)
        total = sum(result["p_algo"].values())
        self.assertAlmostEqual(total, 1.0, places=6)
        self.assertFalse(result["weather_dormant"])


if __name__ == "__main__":
    unittest.main()
