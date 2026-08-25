#!/usr/bin/env python3
"""The A3 fitter, against the real training matrix. 05-trained-model.md sec6/sec7.

Same discipline as the other test_*.py: verify against races that actually
happened, not against assumptions. Hamilton really did win the 2015 Chinese GP
from pole, and if the loader disagrees the loader is wrong.

Unlike test_backfill.py these make **no network calls at all** -- fit.py reads
data/training/winner.csv and nothing else. `import backfill` below is for the
FEATURES drift check only; snapshot.py and lib/jolpica.py have no module-level
side effects, so importing them fetches nothing.

The one place synthetic data appears is TestRecoversAKnownOptimum, and it is
deliberate: a single-feature conditional logit over two-driver races has a
closed-form MLE, so that test checks the optimizer against arithmetic rather
than against a second opinion. Everything else runs on real races.
"""

import csv
import math
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backfill
import fit
import score
from lib.invariants import InvariantError

MATRIX = fit.DEFAULT_MATRIX


def load():
    return fit.load_matrix(MATRIX, quiet=True)


class TestTheFeatureContract(unittest.TestCase):
    """sec3.3/sec4.2: seven columns, in backfill's order, no weather, no market."""

    def test_features_match_the_backfill_exactly(self):
        """A silent reorder here would train each coefficient on the wrong
        column and produce entirely plausible numbers -- the same invisible
        train/serve skew sec4.2 makes backfill.py share score.py's code path to
        avoid. Order matters, so this is an equality, not a set comparison."""
        self.assertEqual(fit.FEATURES, backfill.FEATURES)

    def test_seven_features_not_eight(self):
        self.assertEqual(len(fit.FEATURES), 7)
        self.assertEqual(fit.K, 7)

    def test_no_weather_column(self):
        """sec3.3: F7 is a within-race constant in every backfilled race, so
        beta_weather is unidentified -- not merely imprecise. A constant 0.5
        column would report an artifact of the regularizer as an estimate."""
        self.assertNotIn("weather", fit.FEATURES)

    def test_the_matrix_header_carries_no_market_field(self):
        """sec9 assertion 7. p_a1 is not a market field -- it is A1's own
        market-blind probability, and it is baseline 1 of sec6.3."""
        with open(MATRIX, newline="") as f:
            header = next(csv.reader(f))
        for col in header:
            for token in fit.MARKET_TOKENS:
                self.assertNotIn(token, col.lower())
        self.assertIn("p_a1", header)

    def test_a_market_column_is_rejected_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "winner.csv")
            with open(MATRIX, newline="") as src, open(path, "w", newline="") as dst:
                for i, line in enumerate(src):
                    dst.write(line.rstrip("\n") + (",polymarket_p" if i == 0 else ",0.5") + "\n")
                    if i > 40:
                        break
            with self.assertRaises(InvariantError):
                fit.load_matrix(path, quiet=True)


class TestTheMatrixLoads(unittest.TestCase):
    """sec9 assertions 1, 2 and the A1 half of 9, on every race on disk."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()

    def test_there_are_races(self):
        self.assertGreater(len(self.races), 100)

    def test_exactly_one_winner_per_race(self):
        for r in self.races:
            with self.subTest(race=(r.season, r.round)):
                self.assertEqual(r.n_wins, 1)
                self.assertIsNotNone(r.win_index)

    def test_every_feature_is_in_the_unit_interval(self):
        for r in self.races:
            for row in r.x:
                for f, v in zip(fit.FEATURES, row):
                    with self.subTest(race=(r.season, r.round), feature=f):
                        self.assertGreaterEqual(v, 0.0)
                        self.assertLessEqual(v, 1.0)
                        self.assertEqual(v, v)  # NaN fails this and only this

    def test_a1s_own_probabilities_sum_to_one_per_race(self):
        """sec9 assertion 9 for baseline 1. p_a1 is rounded to 8dp per driver in
        the CSV, so ~20 drivers can drift at most ~1e-7 -- well inside 1e-6."""
        for r in self.races:
            with self.subTest(race=(r.season, r.round)):
                self.assertAlmostEqual(sum(r.p_a1), 1.0, delta=1e-6)

    def test_every_race_has_a_full_grid(self):
        for r in self.races:
            self.assertGreaterEqual(len(r), 2)

    def test_group_keys_are_unique(self):
        keys = [r.key for r in self.races]
        self.assertEqual(len(keys), len(set(keys)))


class TestKnownRaces(unittest.TestCase):
    """Races whose result is a matter of record, checked against the matrix.

    2015 Chinese GP (R3): Hamilton won from pole. Non-sprint weekend -- sprints
    did not exist until 2021 -- so sec3.4's whole-field zero applies.
    """

    @classmethod
    def setUpClass(cls):
        cls.by_key = {r.key: r for r in load()}

    def test_hamilton_won_the_2015_chinese_gp(self):
        r = self.by_key[(2015, 3)]
        self.assertEqual(r.codes[r.win_index], "HAM")

    def test_he_started_from_pole_so_his_grid_score_is_one(self):
        """pos_score(1, K_GRID) == 1.0 always (02 sec3.1), so the pole-sitter's
        grid column pins to exactly 1.0 and nobody else's does."""
        r = self.by_key[(2015, 3)]
        g = fit.FEATURES.index("grid")
        self.assertEqual(r.x[r.win_index][g], 1.0)
        self.assertEqual(sum(1 for row in r.x if row[g] == 1.0), 1)

    def test_the_sprint_column_is_zero_for_the_whole_field(self):
        """sec3.4 / sec9 assertion 4: zero for everyone or nobody."""
        r = self.by_key[(2015, 3)]
        s = fit.FEATURES.index("sprint")
        self.assertTrue(all(row[s] == 0.0 for row in r.x))

    def test_a_sprint_weekend_has_a_live_sprint_column(self):
        """2021 R10 is the British GP, F1's first-ever sprint. If every sprint
        race also came through as zeros, beta_sprint would be unidentified and
        the model would silently have six features."""
        sprint_races = [r for r in self.by_key.values()
                        if any(row[fit.FEATURES.index("sprint")] != 0.0 for row in r.x)]
        self.assertTrue(sprint_races, "no race in the matrix has a non-zero sprint column")
        self.assertTrue(all(r.season >= 2021 for r in sprint_races),
                        "sprints did not exist before 2021")


class TestTheAOnePrior(unittest.TestCase):
    """sec3.1: beta_f = w_f_eff / T, as the pooled model would write it."""

    def test_it_is_the_base_weights_divided_by_T(self):
        beta = fit.a1_implied_beta()
        for name, b in zip(fit.FEATURES, beta):
            self.assertAlmostEqual(b, score.BASE_WEIGHTS[name] / score.T, places=12)

    def test_grid_comes_out_at_the_documented_value(self):
        self.assertAlmostEqual(fit.a1_implied_beta()[fit.FEATURES.index("grid")],
                               0.35 / 0.1168, places=10)

    def test_the_seven_are_not_renormalised_to_sum_to_one(self):
        """Dropping F7 removes 0.05 of A1's weight budget. Rescaling the other
        seven by 1/0.95 would change every coefficient to compensate for a term
        that by sec3.2 contributes nothing to the likelihood anyway."""
        implied_weights = [b * score.T for b in fit.a1_implied_beta()]
        self.assertAlmostEqual(sum(implied_weights), 0.95, places=12)

    def test_it_has_no_weather_entry(self):
        self.assertEqual(len(fit.a1_implied_beta()), 7)


class TestTheLikelihood(unittest.TestCase):
    """The hand-written gradient and Hessian, against finite differences.

    This is the test the hand-rolled decision (sec7) most needs: scipy would
    have checked the derivatives for us, and nothing else here would notice a
    sign error in the gradient -- a wrong gradient still converges, just to the
    wrong place, and every downstream number stays plausible.
    """

    @classmethod
    def setUpClass(cls):
        cls.races = load()[:60]
        cls.beta = [0.30, -0.20, 0.50, 0.10, 0.40, -0.10, 0.25]

    def test_the_two_accumulators_agree(self):
        nll1, g1 = fit.nll_and_gradient(self.beta, self.races)
        nll2, g2, _ = fit.nll_gradient_hessian(self.beta, self.races)
        self.assertAlmostEqual(nll1, nll2, places=12)
        for a, b in zip(g1, g2):
            self.assertAlmostEqual(a, b, places=12)

    def test_gradient_matches_central_differences(self):
        _, grad = fit.nll_and_gradient(self.beta, self.races)
        eps = 1e-6
        for f in range(fit.K):
            up = list(self.beta)
            dn = list(self.beta)
            up[f] += eps
            dn[f] -= eps
            fd = (fit.nll_and_gradient(up, self.races)[0]
                  - fit.nll_and_gradient(dn, self.races)[0]) / (2 * eps)
            with self.subTest(feature=fit.FEATURES[f]):
                self.assertAlmostEqual(grad[f], fd, delta=1e-7)

    def test_hessian_matches_central_differences_of_the_gradient(self):
        _, _, hess = fit.nll_gradient_hessian(self.beta, self.races)
        eps = 1e-6
        for f in range(fit.K):
            up = list(self.beta)
            dn = list(self.beta)
            up[f] += eps
            dn[f] -= eps
            gu = fit.nll_and_gradient(up, self.races)[1]
            gd = fit.nll_and_gradient(dn, self.races)[1]
            for g in range(fit.K):
                with self.subTest(f=fit.FEATURES[f], g=fit.FEATURES[g]):
                    self.assertAlmostEqual(hess[f][g], (gu[g] - gd[g]) / (2 * eps),
                                           delta=1e-7)

    def test_the_hessian_is_symmetric(self):
        _, _, hess = fit.nll_gradient_hessian(self.beta, self.races)
        for f in range(fit.K):
            for g in range(fit.K):
                self.assertEqual(hess[f][g], hess[g][f])

    def test_the_hessian_is_positive_semidefinite(self):
        """It is a sum of within-race covariance matrices, so v'Hv >= 0 for
        every v. That is what makes the problem convex and Newton safe."""
        _, _, hess = fit.nll_gradient_hessian(self.beta, self.races)
        for seed in range(1, 25):
            v = [math.sin(seed * (f + 1.7)) for f in range(fit.K)]
            q = sum(v[f] * hess[f][g] * v[g] for f in range(fit.K) for g in range(fit.K))
            self.assertGreaterEqual(q, -1e-12)


class TestWithinRaceConstantsCancel(unittest.TestCase):
    """sec3.2, the fact the whole pooled design rests on.

    Any feature that takes the same value for every driver in a race
    contributes exactly nothing to that race's likelihood -- the exp(beta*c)
    factor divides out of numerator and denominator. This is not an
    approximation, so the test is an exact-to-floating-point one.
    """

    @classmethod
    def setUpClass(cls):
        cls.race = load()[0]
        cls.beta = [0.4, 1.1, 0.3, 0.9, 0.6, 0.7, 0.2]

    def test_adding_a_constant_to_a_column_leaves_probabilities_unchanged(self):
        base = fit.probabilities(self.beta, self.race.x)
        for f in range(fit.K):
            shifted = [[v + (0.37 if i == f else 0.0) for i, v in enumerate(row)]
                       for row in self.race.x]
            for a, b in zip(base, fit.probabilities(self.beta, shifted)):
                with self.subTest(feature=fit.FEATURES[f]):
                    self.assertAlmostEqual(a, b, places=12)

    def test_an_intercept_would_cancel_too(self):
        """sec3.6's reason for fitting no intercept: a global or per-race one is
        a within-race constant by definition."""
        base = fit.probabilities(self.beta, self.race.x)
        with_intercept = fit.probabilities(self.beta + [2.5],
                                           [row + [1.0] for row in self.race.x])
        for a, b in zip(base, with_intercept):
            self.assertAlmostEqual(a, b, places=12)

    def test_an_all_zero_sprint_column_contributes_no_gradient(self):
        """The consequence for sec3.4: on pre-2021 races the sprint column is
        zero for the whole field, so those races cannot move beta_sprint. If
        this ever became non-zero, beta_sprint would be fitted on races that
        had no sprint."""
        pre_sprint = [r for r in load() if r.season < 2021]
        self.assertTrue(pre_sprint)
        _, grad = fit.nll_and_gradient(self.beta, pre_sprint)
        self.assertEqual(grad[fit.FEATURES.index("sprint")], 0.0)


class TestTheOptimizer(unittest.TestCase):
    """Newton-Raphson on the penalized objective, on the real matrix."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()
        cls.mu = fit.a1_implied_beta()

    def test_it_converges(self):
        for lam, mu in [(0.0, [0.0] * 7), (0.01, [0.0] * 7), (0.1, self.mu)]:
            beta, info = fit.fit(self.races, lam, mu)
            with self.subTest(lam=lam):
                self.assertTrue(info["converged"])
                self.assertLess(info["iterations"], 50)

    def test_the_first_order_condition_holds_at_the_optimum(self):
        """The real check on a fitted answer: grad(NLL)/n + lam*(beta-mu) == 0,
        with the NLL half taken by finite differences so a shared bug in the
        analytic gradient could not hide it."""
        lam = 0.03
        beta, _ = fit.fit(self.races, lam, self.mu)
        eps = 1e-6
        for f in range(fit.K):
            up = list(beta)
            dn = list(beta)
            up[f] += eps
            dn[f] -= eps
            fd = (fit.nll_and_gradient(up, self.races)[0]
                  - fit.nll_and_gradient(dn, self.races)[0]) / (2 * eps)
            with self.subTest(feature=fit.FEATURES[f]):
                self.assertAlmostEqual(fd + lam * (beta[f] - self.mu[f]), 0.0, delta=1e-6)

    def test_the_objective_actually_decreased(self):
        lam, mu = 0.01, [0.0] * 7
        beta, info = fit.fit(self.races, lam, mu)
        start = fit.penalized_objective([0.0] * 7, self.races, lam, mu)
        self.assertLess(info["objective"], start)
        self.assertAlmostEqual(info["objective"],
                               fit.penalized_objective(beta, self.races, lam, mu),
                               places=10)

    def test_infinite_shrinkage_pins_beta_to_the_prior(self):
        """The identifiability sanity check on the penalty: as lambda grows the
        fit must walk to mu, which is also why a selection at the top of
        LAMBDA_GRID means 'A3 chose A1's coefficients', not 'A3 diverged'."""
        beta, _ = fit.fit(self.races, 1e6, self.mu)
        for b, m in zip(beta, self.mu):
            self.assertAlmostEqual(b, m, places=4)

    def test_grid_position_earns_a_positive_coefficient(self):
        """A checkable claim about F1 rather than about the code: a better grid
        slot scores closer to 1.0 (02 sec3.1) and starting further forward
        helps. A negative beta_grid would mean the matrix or the sign of the
        gradient is inverted."""
        beta, _ = fit.fit(self.races, 0.01, [0.0] * 7)
        self.assertGreater(beta[fit.FEATURES.index("grid")], 1.0)

    def test_solve_handles_a_known_system(self):
        a = [[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]]
        b = [8.0, -11.0, -3.0]
        for got, want in zip(fit.solve(a, b), [2.0, 3.0, -1.0]):
            self.assertAlmostEqual(got, want, places=10)


class TestRecoversAKnownOptimum(unittest.TestCase):
    """The one synthetic test, and the reason it earns its place.

    For a single-feature conditional logit over two-driver races where driver A
    has x=1 and driver B has x=0, p(A wins) = e^b / (e^b + 1) = logistic(b).
    The likelihood over n such races with k won by A is a binomial one, so the
    unpenalized MLE is available in closed form:

        b_hat = log(k / (n - k))

    No F1 knowledge is involved -- this is arithmetic, and it is the only way
    to check that the optimizer lands on the *right* answer rather than merely
    on a stationary point of its own gradient.
    """

    @staticmethod
    def two_driver_races(n, k):
        races = []
        for i in range(n):
            r = fit.Race(2000, i + 1, "2000-01-01", "synthetic")
            r.codes = ["AAA", "BBB"]
            r.x = [[1.0], [0.0]]
            r.win_index = 0 if i < k else 1
            r.n_wins = 1
            r.p_a1 = [0.5, 0.5]
            races.append(r)
        return races

    def test_it_finds_the_closed_form_mle(self):
        for n, k in [(100, 60), (200, 150), (80, 20)]:
            beta, info = fit.fit(self.two_driver_races(n, k), 0.0, [0.0], k=1)
            with self.subTest(n=n, k=k):
                self.assertTrue(info["converged"])
                self.assertAlmostEqual(beta[0], math.log(k / (n - k)), places=6)

    def test_an_l2_penalty_shrinks_that_answer_toward_the_prior(self):
        unpen, _ = fit.fit(self.two_driver_races(100, 60), 0.0, [0.0], k=1)
        pen, _ = fit.fit(self.two_driver_races(100, 60), 0.5, [0.0], k=1)
        self.assertLess(abs(pen[0]), abs(unpen[0]))


class TestMetrics(unittest.TestCase):
    """sec6.2, against 02 sec7's Brier definition computed by hand."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()[:40]

    def test_brier_matches_the_definition_in_02_sec7(self):
        """sum over drivers of (p_d - outcome_d)^2, summed within a race and
        averaged across races -- the same shape score.compute_post_race uses,
        so A3, A1 and the market numbers are directly comparable."""
        res = fit.evaluate(self.races, lambda r: list(r.p_a1))
        by_hand = 0.0
        for r in self.races:
            for j, p in enumerate(r.p_a1):
                by_hand += (p - (1.0 if j == r.win_index else 0.0)) ** 2
        self.assertAlmostEqual(res["brier"], by_hand / len(self.races), places=12)

    def test_logloss_is_the_winners_own_probability(self):
        res = fit.evaluate(self.races, lambda r: list(r.p_a1))
        by_hand = sum(-math.log(r.p_a1[r.win_index]) for r in self.races) / len(self.races)
        self.assertAlmostEqual(res["logloss"], by_hand, places=12)

    def test_top1_counts_races_where_the_favourite_won(self):
        res = fit.evaluate(self.races, lambda r: list(r.p_a1))
        hits = sum(1 for r in self.races
                   if max(range(len(r.p_a1)), key=lambda j: r.p_a1[j]) == r.win_index)
        self.assertAlmostEqual(res["top1"], hits / len(self.races), places=12)

    def test_a_perfect_predictor_scores_zero_and_a_uniform_one_does_not(self):
        perfect = fit.evaluate(self.races, lambda r: [
            1.0 if j == r.win_index else 0.0 for j in range(len(r))])
        self.assertAlmostEqual(perfect["brier"], 0.0, places=12)
        self.assertEqual(perfect["top1"], 1.0)
        uniform = fit.evaluate(self.races, lambda r: [1.0 / len(r)] * len(r))
        self.assertGreater(uniform["brier"], perfect["brier"])

    def test_probabilities_that_do_not_sum_to_one_are_rejected(self):
        """sec9 assertion 9, enforced on every predictor rather than trusted."""
        with self.assertRaises(InvariantError):
            fit.evaluate(self.races, lambda r: [0.5 / len(r)] * len(r))

    def test_fitted_probabilities_sum_to_one_within_each_race(self):
        beta, _ = fit.fit(self.races, 0.01, [0.0] * 7)
        for r in self.races:
            with self.subTest(race=(r.season, r.round)):
                self.assertAlmostEqual(sum(fit.probabilities(beta, r.x)), 1.0, delta=1e-9)

    def test_the_calibration_curve_covers_every_row_once(self):
        res = fit.evaluate(self.races, lambda r: list(r.p_a1))
        binned = sum(b["n"] for b in fit.calibration_curve(res["pairs"]))
        self.assertEqual(binned, sum(len(r) for r in self.races))


class TestSplits(unittest.TestCase):
    """sec6.1 season-forward validation, and sec9 assertion 8."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()
        cls.seasons = fit.seasons_of(cls.races)

    def test_folds_never_train_on_the_season_they_evaluate_or_later(self):
        for y, train, test in fit.season_forward_folds(self.races, self.seasons):
            with self.subTest(season=y):
                self.assertTrue(all(r.season < y for r in train))
                self.assertTrue(all(r.season == y for r in test))

    def test_train_and_test_share_no_group_key(self):
        for y, train, test in fit.season_forward_folds(self.races, self.seasons):
            with self.subTest(season=y):
                self.assertFalse({r.key for r in train} & {r.key for r in test})

    def test_a_leaking_split_is_caught(self):
        with self.assertRaises(InvariantError):
            fit.require_disjoint(self.races, self.races[:1])

    def test_no_fold_trains_on_fewer_than_the_minimum_seasons(self):
        for y, train, _ in fit.season_forward_folds(self.races, self.seasons):
            with self.subTest(season=y):
                self.assertGreaterEqual(len({r.season for r in train}),
                                        fit.MIN_TRAIN_SEASONS)

    def test_the_holdout_is_a_fixed_season_set_not_a_count(self):
        """sec6.1's 'touched exactly once' is structural here: with rows still
        arriving from a running backfill, 'the last N seasons' would silently
        name a different experiment on every run."""
        self.assertEqual(fit.HOLDOUT_SEASONS, (2024, 2025, 2026))
        dev = [s for s in self.seasons if s not in fit.HOLDOUT_SEASONS]
        self.assertFalse(set(dev) & set(fit.HOLDOUT_SEASONS))

    def test_dev_folds_never_see_a_holdout_season(self):
        dev_seasons = [s for s in self.seasons if s not in fit.HOLDOUT_SEASONS]
        dev_races = [r for r in self.races if r.season in set(dev_seasons)]
        for y, train, test in fit.season_forward_folds(dev_races, dev_seasons):
            with self.subTest(season=y):
                self.assertNotIn(y, fit.HOLDOUT_SEASONS)
                for r in train + test:
                    self.assertNotIn(r.season, fit.HOLDOUT_SEASONS)

    def test_pooling_weights_by_race_count(self):
        pooled = fit.pooled([{"n_races": 10, "brier": 1.0, "logloss": 2.0, "top1": 0.5},
                             {"n_races": 30, "brier": 2.0, "logloss": 4.0, "top1": 0.1}])
        self.assertEqual(pooled["n_races"], 40)
        self.assertAlmostEqual(pooled["brier"], (10 * 1.0 + 30 * 2.0) / 40)


class TestBaselines(unittest.TestCase):
    """sec6.3: A1's own p_a1 column, and a grid-only floor with a fitted scale."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()

    def test_the_grid_only_baseline_is_one_column(self):
        proj = fit.project(self.races, [fit.GRID_INDEX])
        for a, b in zip(proj, self.races):
            self.assertEqual(len(a.x[0]), 1)
            self.assertEqual(a.x[0][0], b.x[0][fit.GRID_INDEX])
            self.assertEqual(a.win_index, b.win_index)

    def test_its_coefficient_is_fitted_and_positive(self):
        """sec6.3 forbids pushing s_grid through a softmax at a fixed
        temperature -- that would smuggle T back in as an arbitrary constant.
        The floor gets a real fitted scale or it is a strawman."""
        beta, info = fit.fit(fit.project(self.races, [fit.GRID_INDEX]),
                             fit.GRID_ONLY_LAMBDA, [0.0], k=1)
        self.assertTrue(info["converged"])
        self.assertGreater(beta[0], 0.0)

    def test_a3_clears_the_grid_only_floor_in_sample(self):
        """Weak by design -- the real comparison is out of sample in fit.py --
        but if the seven-feature model could not beat one column on the data it
        was fitted to, something is wrong with the fit, not with the features."""
        beta7, _ = fit.fit(self.races, 0.01, [0.0] * 7)
        beta1, _ = fit.fit(fit.project(self.races, [fit.GRID_INDEX]),
                           fit.GRID_ONLY_LAMBDA, [0.0], k=1)
        a3 = fit.evaluate(self.races, lambda r: fit.probabilities(beta7, r.x))
        floor = fit.evaluate(fit.project(self.races, [fit.GRID_INDEX]),
                             lambda r: fit.probabilities(beta1, r.x))
        self.assertLess(a3["brier"], floor["brier"])

    def test_all_three_predictors_see_the_same_races(self):
        """A race dropped for one predictor must be dropped for all three, or
        the pooled comparison is over different denominators."""
        train = [r for r in self.races if r.season <= 2016]
        test = [r for r in self.races if r.season == 2017]
        res = fit.run_fold(train, test, 0.01, [0.0] * 7)
        self.assertEqual(res["a3"]["n_races"], res["a1"]["n_races"])
        self.assertEqual(res["a3"]["n_races"], res["grid_only"]["n_races"])
        self.assertEqual(res["a3"]["n_races"], len(test))


class TestSeparation(unittest.TestCase):
    """sec3.6: a perfectly separating feature makes its coefficient diverge."""

    @classmethod
    def setUpClass(cls):
        cls.races = load()

    def test_no_feature_separates_on_the_real_corpus(self):
        """The claim sec3.6 makes and says must be checked rather than assumed:
        poles lose regularly, so s_grid does not separate. Verified, not
        asserted -- the failure mode is a coefficient that grows until the
        optimizer stops, not an error."""
        sep = fit.separation_check(self.races)
        for name in fit.FEATURES:
            with self.subTest(feature=name):
                self.assertFalse(sep[name]["separates"])

    def test_pole_sitters_do_lose(self):
        sep = fit.separation_check(self.races)
        won = sep["grid"]["races_won_by_argmax"]
        self.assertGreater(won, 0)
        self.assertLess(won, len(self.races))

    def test_it_would_flag_a_separating_feature(self):
        rigged = []
        for r in self.races[:20]:
            c = fit.Race(r.season, r.round, r.date, r.circuit_id)
            c.codes = list(r.codes)
            c.win_index = r.win_index
            c.n_wins = 1
            c.p_a1 = list(r.p_a1)
            c.x = [[1.0 if j == r.win_index else 0.0] + row[1:]
                   for j, row in enumerate(r.x)]
            rigged.append(c)
        self.assertTrue(fit.separation_check(rigged)["grid"]["separates"])


class TestTolerationOfALiveBackfill(unittest.TestCase):
    """backfill.py appends to winner.csv while this runs, so a read can catch a
    truncated final line or a half-written trailing race. That is a property of
    the producer, not a corrupt file. Anything malformed that is NOT last is a
    real problem and must still raise."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "winner.csv")
        shutil.copy(MATRIX, self.path)
        self.baseline = len(fit.load_matrix(self.path, quiet=True))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _truncate_last_line(self, keep_chars):
        with open(self.path) as f:
            lines = f.read().splitlines()
        lines[-1] = lines[-1][:keep_chars]
        with open(self.path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def test_a_torn_final_line_is_dropped_not_fatal(self):
        self._truncate_last_line(20)
        races = fit.load_matrix(self.path, quiet=True)
        self.assertGreaterEqual(len(races), self.baseline - 1)

    def test_a_final_race_missing_its_winner_is_dropped(self):
        """The winner's row can legitimately be the one not yet flushed."""
        with open(self.path, newline="") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames)
            rows = list(reader)
        last_key = (rows[-1]["season"], rows[-1]["round"])
        kept = [r for r in rows
                if (r["season"], r["round"]) != last_key or r["label"] != "1"]
        with open(self.path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(kept)
        races = fit.load_matrix(self.path, quiet=True)
        self.assertEqual(len(races), self.baseline - 1)
        self.assertNotIn((int(last_key[0]), int(last_key[1])), {r.key for r in races})

    def test_a_broken_row_that_is_not_last_still_raises(self):
        with open(self.path) as f:
            lines = f.read().splitlines()
        lines[1] = lines[1].replace(",1.0,", ",7.5,", 1)
        with open(self.path, "w") as f:
            f.write("\n".join(lines) + "\n")
        with self.assertRaises(InvariantError):
            fit.load_matrix(self.path, quiet=True)


class TestCorpusGuard(unittest.TestCase):
    """sec6.4 verdicts need the whole corpus, and process-exit is not the test.

    00-roadmap.md's Phase A3 entry records ~11 races lost across 2021-2024 to
    the track-history IndexError, refilled only by a *second* backfill pass. A
    finished process therefore does not mean a finished corpus, and a holed one
    is not randomly holed -- it removes three specific circuits.
    """

    @classmethod
    def setUpClass(cls):
        cls.races = load()

    def test_the_report_names_missing_rounds(self):
        report = fit.corpus_report(self.races)
        self.assertTrue(report)
        for c in report:
            rounds = {r.round for r in self.races if r.season == c["season"]}
            self.assertEqual(c["n_races"], len(rounds))
            self.assertEqual(set(c["missing_rounds"]),
                             set(range(1, c["max_round"] + 1)) - rounds)

    def test_final_mode_refuses_a_holed_corpus(self):
        holed = [r for r in self.races if not (r.season == 2016 and r.round == 5)]
        with self.assertRaises(InvariantError):
            fit.require_complete_corpus(holed)

    def test_it_also_refuses_a_short_one(self):
        with self.assertRaises(InvariantError):
            fit.require_complete_corpus(self.races[:50])


class TestSelection(unittest.TestCase):
    """sec10.1: the prior is chosen on validation, not by argument."""

    def test_lowest_brier_wins(self):
        chosen = fit.select([
            {"prior": "zero", "lam": 0.1, "brier": 0.60, "logloss": 1.3, "top1": 0.5},
            {"prior": "a1", "lam": 0.3, "brier": 0.58, "logloss": 1.2, "top1": 0.4},
        ])
        self.assertEqual((chosen["prior"], chosen["lam"]), ("a1", 0.3))

    def test_it_selects_on_brier_not_top1(self):
        """sec6.2 says top-1 is reported and never selected on -- it ignores
        calibration entirely, which is the thing the project cares about."""
        chosen = fit.select([
            {"prior": "zero", "lam": 0.1, "brier": 0.58, "logloss": 1.2, "top1": 0.1},
            {"prior": "a1", "lam": 0.3, "brier": 0.60, "logloss": 1.3, "top1": 0.9},
        ])
        self.assertEqual(chosen["prior"], "zero")

    def test_a_tie_at_lambda_zero_resolves_to_the_uninformative_prior(self):
        """The two arms are identical at lambda=0, so reporting 'the A1 prior
        won' off such a row would be a finding about nothing."""
        chosen = fit.select([
            {"prior": "zero", "lam": 0.0, "brier": 0.6, "logloss": 1.3, "top1": 0.5},
            {"prior": "a1", "lam": 0.0, "brier": 0.6, "logloss": 1.3, "top1": 0.5},
        ])
        self.assertEqual(chosen["prior"], "zero")

    def test_both_arms_agree_at_lambda_zero_on_real_folds(self):
        races = [r for r in load() if r.season <= 2018]
        seasons = fit.seasons_of(races)
        results = {r["prior"]: r for r in fit.sweep(races, seasons[fit.MIN_TRAIN_SEASONS:])
                   if r["lam"] == 0.0}
        self.assertAlmostEqual(results["zero"]["brier"], results["a1"]["brier"], places=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
