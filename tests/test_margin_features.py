import unittest
from math import isclose

from engine.margin_features import (
    build_f1_features,
    calculate_c20,
    calculate_daily_margin_changes,
    calculate_leverage_ratio,
    calculate_margin_trend,
    calculate_market_deleveraging,
    calculate_three_day_margin_changes,
)
from tests.helpers import trading_dates


class MarginFeatureTests(unittest.TestCase):
    def setUp(self):
        dates = trading_dates(21)
        self.balances = [100 * 1.01**index for index in range(21)]
        self.rows = [
            {"date": day, "margin_balance": value}
            for day, value in zip(dates, self.balances)
        ]

    def test_lr_c20_and_trend(self):
        self.assertTrue(isclose(calculate_leverage_ratio(5, 100), 5))
        self.assertTrue(
            isclose(calculate_c20(self.balances), (1.01**20 - 1) * 100)
        )
        self.assertTrue(
            isclose(
                calculate_margin_trend(self.balances)["gb_daily_pct"],
                1,
                rel_tol=1e-12,
            )
        )

    def test_change_counts_and_feature_package(self):
        self.assertEqual(len(calculate_daily_margin_changes(self.rows)), 20)
        self.assertEqual(len(calculate_three_day_margin_changes(self.rows)), 18)
        package = build_f1_features(self.rows, 2_000)
        self.assertEqual(package["observation_count"], 21)
        self.assertNotIn("status", package)

    def test_market_deleveraging_strict_boundary(self):
        result = calculate_market_deleveraging(
            [
                {"date": "a", "market_margin_balance": 100},
                {"date": "b", "market_margin_balance": 97.5},
                {"date": "c", "market_margin_balance": 94},
            ]
        )
        self.assertFalse(result[0]["below_minus_2_5_pct"])
        self.assertTrue(result[1]["below_minus_2_5_pct"])


if __name__ == "__main__":
    unittest.main()
