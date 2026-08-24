#!/usr/bin/env python3
"""Race configs and circuit tables. Same discipline as the other test_*.py:
verify against real Jolpica data, not against assumptions.

These are the tables that used to be argparse defaults and one-entry dicts --
the two things that made "run the next race" a seven-flag invocation with a
KeyError waiting at the end of it. The point of the tests is that a race config
is checked against what Jolpica actually says about that round.
"""

import argparse
import glob
import json
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import snapshot
from lib import jolpica
from lib.circuits import OVERTAKING_MULTIPLIER

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RACE_CONFIGS = sorted(glob.glob(os.path.join(REPO_ROOT, "races", "*.json")))


class TestCircuitTables(unittest.TestCase):
    def test_every_scored_circuit_has_a_timezone(self):
        """A circuit you can score but can't resolve a timezone for is a
        snapshot that dies partway through -- which is exactly what Monza was
        before this, being in the multiplier table and not the timezone one."""
        missing = set(OVERTAKING_MULTIPLIER) - set(snapshot.CIRCUIT_TIMEZONE)
        self.assertEqual(missing, set(), f"circuits scored but not locatable in time: {missing}")

    def test_every_timezone_is_a_real_zoneinfo_key(self):
        for circuit_id, tz in snapshot.CIRCUIT_TIMEZONE.items():
            with self.subTest(circuit=circuit_id):
                ZoneInfo(tz)

    # Every circuitId Jolpica returns for 2014-2026, enumerated live on
    # 2026-08-24 (05-trained-model.md sec4.3/sec5.2). Hardcoded rather than
    # re-fetched so this stays an offline test; a new circuit joining the
    # calendar shows up as a race-config failure in TestRaceConfigs, which
    # does hit the network.
    A3_BACKFILL_CORPUS = {
        "albert_park", "americas", "bahrain", "baku", "catalunya", "hockenheimring",
        "hungaroring", "imola", "interlagos", "istanbul", "jeddah", "losail",
        "madring", "marina_bay", "miami", "monaco", "monza", "mugello",
        "nurburgring", "portimao", "red_bull_ring", "ricard", "rodriguez",
        "sepang", "shanghai", "silverstone", "sochi", "spa", "suzuka",
        "vegas", "villeneuve", "yas_marina", "zandvoort",
    }

    def test_timezone_table_covers_the_whole_a3_backfill_corpus(self):
        """CIRCUIT_TIMEZONE is indexed as a bare dict lookup in
        build_track_history and build_weather, so a circuit missing here is a
        KeyError that kills the backfill mid-run rather than degrading. It was
        15 of these 33 before 2026-08-24."""
        missing = self.A3_BACKFILL_CORPUS - set(snapshot.CIRCUIT_TIMEZONE)
        self.assertEqual(missing, set(), f"backfill would KeyError on: {sorted(missing)}")


class TestRaceConfigs(unittest.TestCase):
    def test_there_is_at_least_one(self):
        self.assertTrue(RACE_CONFIGS, "no races/*.json found")

    def test_each_parses_and_declares_only_known_fields(self):
        for path in RACE_CONFIGS:
            with self.subTest(config=os.path.basename(path)):
                cfg = snapshot.load_race_config(path)
                self.assertEqual(set(cfg), set(snapshot.RACE_CONFIG_FIELDS))

    def test_each_round_matches_jolpica_and_the_slug_date(self):
        """The failure this catches is a config copied from another race with
        the round left alone -- which would snapshot the right odds against the
        wrong grid, and nothing downstream would notice."""
        cache_dir = snapshot.DEFAULT_CACHE_DIR
        for path in RACE_CONFIGS:
            with self.subTest(config=os.path.basename(path)):
                cfg = snapshot.load_race_config(path)
                race, _ = jolpica.race_info(cfg["season"], cfg["round"], cache_dir)
                self.assertIn(race["Circuit"]["circuitId"], snapshot.CIRCUIT_TIMEZONE)
                # Polymarket slugs end in the race date; if they disagree with
                # Jolpica's schedule, one of the two is about the wrong race.
                self.assertTrue(
                    cfg["polymarket_slug"].endswith(race["date"]),
                    f"{cfg['polymarket_slug']} does not end in Jolpica's race date {race['date']}",
                )

    def test_dutch_config_reproduces_the_committed_snapshot_identifiers(self):
        """The A2 reference race is frozen on disk; its config must still name
        the same markets, or the reference run stops being reproducible."""
        frozen = sorted(glob.glob(os.path.join(REPO_ROOT, "data", "snapshots", "*-race-*[0-9Z].json")))
        self.assertTrue(frozen)
        with open(frozen[0]) as f:
            snap = json.load(f)
        cfg = snapshot.load_race_config(os.path.join(REPO_ROOT, "races", "2026-dutch.json"))
        self.assertEqual(cfg["season"], snap["meta"]["season"])
        self.assertEqual(cfg["round"], snap["meta"]["round"])
        self.assertEqual(cfg["polymarket_slug"], snap["markets"]["polymarket"]["slug"])
        self.assertEqual(cfg["kalshi_event_ticker"], snap["markets"]["kalshi"]["event_ticker"])


class TestConfigResolution(unittest.TestCase):
    def _args(self, **kw):
        base = {f: None for f in snapshot.RACE_CONFIG_FIELDS}
        base["race"] = None
        base.update(kw)
        return argparse.Namespace(**base)

    def test_cli_flag_overrides_the_config_file(self):
        cfg = snapshot.resolve_race_config(
            self._args(race=os.path.join(REPO_ROOT, "races", "2026-dutch.json"),
                       kalshi_event_ticker="KXF1RACE-OVERRIDE")
        )
        self.assertEqual(cfg["kalshi_event_ticker"], "KXF1RACE-OVERRIDE")
        self.assertEqual(cfg["round"], 12, "unoverridden fields must survive the merge")

    def test_missing_required_field_exits_loudly(self):
        with self.assertRaises(SystemExit) as cm:
            snapshot.resolve_race_config(
                self._args(race=os.path.join(REPO_ROOT, "races", "2026-monza.json"))
            )
        self.assertIn("kalshi_event_ticker", str(cm.exception))

    def test_unknown_field_is_rejected(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"season": 2026, "round": 1, "typo_field": "x"}, f)
            path = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                snapshot.load_race_config(path)
            self.assertIn("typo_field", str(cm.exception))
        finally:
            os.unlink(path)

    def test_no_race_and_no_flags_exits_rather_than_defaulting_to_dutch(self):
        with self.assertRaises(SystemExit):
            snapshot.resolve_race_config(self._args())


if __name__ == "__main__":
    unittest.main()
