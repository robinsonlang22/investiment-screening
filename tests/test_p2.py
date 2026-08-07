import unittest

from engine.p2_evaluator import (
    CONDITIONAL_PASS,
    FAIL,
    INSUFFICIENT_INFORMATION,
    PASS,
    classify_clock_direction,
    classify_short_term_state,
    evaluate_clock_five,
    evaluate_clock_one_window,
    evaluate_p2,
    find_clock_one_entry,
)
from tests.helpers import load_rule


class P2EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_rule("p2")
        cls.base = {
            "regression_features": {
                "g30_daily_pct": 0.3,
                "e30_daily_pct": 0.29,
                "r_squared_30": 0.8,
                "g10_daily_pct": 0.4,
                "e10_daily_pct": 0.39,
                "r_squared_10": 0.9,
            },
            "close": 100,
            "moving_averages": {"ma10": 95, "ma20": 90},
        }

    def test_all_clock_boundaries(self):
        cases = [
            (0.50001, 0.60, 1),
            (0.50, 0.60, 2),
            (0.15001, 0.60, 2),
            (0.15, 0.60, 3),
            (-0.15, 0.60, 3),
            (-0.15001, 0.60, 4),
            (-0.50, 0.60, 4),
            (-0.50001, 0.60, 5),
            (2.0, 0.5999, 3),
        ]
        for g30, r2, expected in cases:
            with self.subTest(g30=g30, r2=r2):
                self.assertEqual(
                    classify_clock_direction(g30, r2, self.config), expected
                )

    def test_evaluation_includes_chinese_direction_name(self):
        result = evaluate_p2(self.base, {"market_state": "DENSE"}, self.config)
        self.assertEqual(result["clock"], 2)
        self.assertEqual(result["direction_name"], "稳步上升")

    def test_short_term_can_be_accelerating_and_unstable(self):
        result = classify_short_term_state(0.6, -0.2, 0.5, 0.3)
        self.assertEqual(result["state"], "ACCELERATING")
        self.assertTrue(result["direction_unstable"])

    def test_clock_one_entry_and_or_window(self):
        rows = [{"date": f"d{i}", "close": 100 + i} for i in range(40)]
        rolling = [None] * 35 + [
            {"g30_daily_pct": 0.6, "r_squared_30": 0.8}
            for _ in range(5)
        ]
        entry = find_clock_one_entry(rolling, rows)
        self.assertEqual(entry["entry_index"], 35)
        self.assertEqual(entry["trading_days_inclusive"], 5)
        self.assertEqual(
            evaluate_clock_one_window(
                "a", "b", 100, 116, trading_days_inclusive=11
            )["status"],
            FAIL,
        )
        self.assertEqual(
            evaluate_clock_one_window(
                "a", "b", 100, 115, trading_days_inclusive=11
            )["status"],
            PASS,
        )

    def test_clock_three_four_and_five(self):
        clock_three = {
            **self.base,
            "regression_features": {
                **self.base["regression_features"],
                "g30_daily_pct": 0.1,
                "e30_daily_pct": 0.09,
            },
        }
        self.assertEqual(
            evaluate_p2(clock_three, {"market_state": "DENSE"}, self.config)[
                "status"
            ],
            PASS,
        )
        self.assertEqual(
            evaluate_p2(clock_three, {"market_state": "DIVERGING"}, self.config)[
                "status"
            ],
            FAIL,
        )
        clock_four = {
            **self.base,
            "regression_features": {
                **self.base["regression_features"],
                "g30_daily_pct": -0.3,
                "e30_daily_pct": -0.2,
            },
        }
        result = evaluate_p2(clock_four, {"market_state": "DENSE"}, self.config)
        self.assertEqual(result["status"], FAIL)
        self.assertTrue(result["hard_veto"])
        self.assertEqual(evaluate_clock_five(9, 10, 11)["status"], FAIL)
        self.assertEqual(
            evaluate_clock_five(11, 10, 12)["status"], CONDITIONAL_PASS
        )
        self.assertEqual(evaluate_clock_five(13, 10, 12)["status"], PASS)

    def test_g30_e30_direction_conflict_is_insufficient(self):
        features = {
            **self.base,
            "regression_features": {
                **self.base["regression_features"],
                "g30_daily_pct": 0.3,
                "e30_daily_pct": -0.1,
            },
        }
        result = evaluate_p2(features, {"market_state": "DENSE"}, self.config)
        self.assertEqual(result["status"], INSUFFICIENT_INFORMATION)
        self.assertIsNone(result["direction_name"])


if __name__ == "__main__":
    unittest.main()
