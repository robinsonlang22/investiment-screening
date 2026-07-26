import unittest

from engine.validators import (
    validate_margin_history,
    validate_market_cap,
    validate_price_history,
    validate_rule_inputs,
)
from tests.helpers import trading_dates


class ValidatorTests(unittest.TestCase):
    def test_valid_price_history_and_p1_contract(self):
        dates = trading_dates(120)
        dataset = {
            "rows": [
                {"date": day, "close": 10 + index, "adjustment": "forward"}
                for index, day in enumerate(dates)
            ],
            "adjustment": "forward",
            "latest_closed_date": dates[-1],
        }
        self.assertEqual(
            validate_price_history(dataset, minimum_observations=120)["status"],
            "VALID",
        )
        self.assertEqual(
            validate_rule_inputs("P1", {"price_history": dataset})["status"],
            "VALID",
        )

    def test_price_duplicates_order_null_and_adjustment(self):
        rows = [
            {"date": "2025-01-03", "close": 10, "adjustment": "none"},
            {"date": "2025-01-02", "close": "-", "adjustment": "none"},
            {"date": "2025-01-03", "close": 12, "adjustment": "none"},
        ]
        result = validate_price_history(rows, minimum_observations=3)
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("DUPLICATE_DATE", codes)
        self.assertIn("UNSORTED_DATES", codes)
        self.assertIn("INVALID_CLOSE", codes)
        self.assertIn("WRONG_ADJUSTMENT", codes)

    def test_margin_rejects_net_purchase_and_wrong_unit(self):
        result = validate_margin_history(
            [
                {
                    "date": "2025-01-02",
                    "margin_net_purchase": 10,
                    "unit": "亿元",
                }
            ],
            minimum_observations=1,
        )
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("NET_PURCHASE_NOT_BALANCE", codes)
        self.assertIn("WRONG_OR_MISSING_UNIT", codes)

    def test_market_cap_selects_closest_not_later_date(self):
        result = validate_market_cap(
            [
                {"date": "2025-01-01", "ffmc": 100, "unit": "CNY"},
                {"date": "2025-01-03", "ffmc": 300, "unit": "CNY"},
            ],
            "2025-01-02",
        )
        self.assertEqual(result["selected_row"]["date"], "2025-01-01")


if __name__ == "__main__":
    unittest.main()
