import unittest

from engine.p1_evaluator import (
    CONDITIONAL_PASS,
    DENSE,
    DIVERGING,
    FAIL,
    INSUFFICIENT_INFORMATION,
    PASS,
    TRANSITION_ENTANGLED,
    classify_p1_market_state,
    evaluate_p1,
)
from tests.helpers import load_rule


class P1EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_rule("p1")
        cls.base = {
            "close": 110,
            "moving_averages": {
                "ma5": 108,
                "ma10": 105,
                "ma20": 100,
                "ma60": 90,
            },
            "slopes": {"ma5": 0.4, "ma10": 0.3, "ma20": 0.2, "ma60": 0.1},
        }

    def test_market_state_classification(self):
        self.assertEqual(classify_p1_market_state([1, 2, 3], None), DENSE)
        self.assertEqual(
            classify_p1_market_state([3.1, 3.2, 3.3], True), DIVERGING
        )
        self.assertEqual(
            classify_p1_market_state([3.1, 3.2, 3.3], False),
            TRANSITION_ENTANGLED,
        )

    def test_dense_pass_and_strict_boundary_fail(self):
        result = evaluate_p1(
            {**self.base, "density_last_3_days": [2.8, 2.9, 3.0]},
            spread_expanding=None,
            rule_config=self.config,
        )
        self.assertEqual(result["status"], PASS)
        boundary = evaluate_p1(
            {
                **self.base,
                "close": 98.25,
                "density_last_3_days": [1, 1, 1],
            },
            spread_expanding=None,
            rule_config=self.config,
        )
        self.assertEqual(boundary["status"], FAIL)

    def test_diverging_required_and_preferences(self):
        passed = evaluate_p1(
            {**self.base, "density_last_3_days": [4, 4, 4]},
            spread_expanding=True,
            rule_config=self.config,
        )
        self.assertEqual(passed["status"], PASS)
        conditional = evaluate_p1(
            {
                **self.base,
                "close": 100,
                "moving_averages": {
                    "ma5": 104,
                    "ma10": 105,
                    "ma20": 100,
                    "ma60": 90,
                },
                "density_last_3_days": [4, 4, 4],
            },
            spread_expanding=True,
            rule_config=self.config,
        )
        self.assertEqual(conditional["status"], CONDITIONAL_PASS)
        failed = evaluate_p1(
            {
                **self.base,
                "moving_averages": {
                    "ma5": 108,
                    "ma10": 105,
                    "ma20": 80,
                    "ma60": 90,
                },
                "density_last_3_days": [4, 4, 4],
            },
            spread_expanding=True,
            rule_config=self.config,
        )
        self.assertEqual(failed["status"], FAIL)

    def test_transition_and_missing_density_are_insufficient(self):
        transition = evaluate_p1(
            {**self.base, "density_last_3_days": [4, 4, 4]},
            spread_expanding=None,
            rule_config=self.config,
        )
        self.assertEqual(transition["status"], INSUFFICIENT_INFORMATION)
        self.assertEqual(transition["market_state"], TRANSITION_ENTANGLED)
        missing = evaluate_p1(
            self.base,
            spread_expanding=None,
            rule_config=self.config,
        )
        self.assertEqual(missing["status"], INSUFFICIENT_INFORMATION)


if __name__ == "__main__":
    unittest.main()
