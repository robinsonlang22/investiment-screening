import unittest

from engine.p1_evaluator import (
    CONDITIONAL_PASS,
    CORE_BEARISH,
    CORE_BULLISH_ALIGNMENT,
    CORE_UPWARD,
    DENSE,
    FAIL,
    INSUFFICIENT_INFORMATION,
    PASS,
    classify_core_state,
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
            "density_series": [
                {"mean": 104.333, "range": 8, "relative_range_pct": 1.9}
            ],
        }

    def test_core_state_classification(self):
        features = {
            "ma5": 108, "ma10": 105, "ma20": 100,
            "s10_ma5": 0.4, "s10_ma10": 0.3, "s10_ma20": 0.2,
        }
        self.assertEqual(classify_core_state(features), CORE_BULLISH_ALIGNMENT)
        self.assertEqual(classify_core_state({**features, "ma5": 104}), CORE_UPWARD)
        self.assertEqual(classify_core_state({**features, "ma10": 99}), CORE_BEARISH)

    def test_dense_is_latest_day_core_relative_range(self):
        result = evaluate_p1(self.base, rule_config=self.config)
        self.assertEqual(result["status"], PASS)
        self.assertEqual(result["market_state"], DENSE)
        self.assertTrue(result["dense"])
        self.assertEqual(result["metrics"]["density_threshold_pct"], 2)

    def test_core_upward_without_full_alignment_is_conditional(self):
        features = {
            **self.base,
            "moving_averages": {**self.base["moving_averages"], "ma5": 104},
        }
        result = evaluate_p1(features, rule_config=self.config)
        self.assertEqual(result["status"], CONDITIONAL_PASS)
        self.assertEqual(result["core_state"], CORE_UPWARD)

    def test_bearish_fails_and_ma60_cannot_change_status(self):
        features = {
            **self.base,
            "moving_averages": {**self.base["moving_averages"], "ma60": 200},
            "slopes": {**self.base["slopes"], "ma60": -1},
        }
        result = evaluate_p1(features, rule_config=self.config)
        self.assertEqual(result["status"], PASS)
        self.assertFalse(result["ma60_background"]["synchronized"])

        bearish = {
            **self.base,
            "moving_averages": {**self.base["moving_averages"], "ma10": 99},
        }
        self.assertEqual(evaluate_p1(bearish, rule_config=self.config)["status"], FAIL)

    def test_missing_core_feature_is_insufficient(self):
        missing = {**self.base, "slopes": {"ma5": 0.4, "ma10": 0.3}}
        self.assertEqual(
            evaluate_p1(missing, rule_config=self.config)["status"],
            INSUFFICIENT_INFORMATION,
        )


if __name__ == "__main__":
    unittest.main()
