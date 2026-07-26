import unittest

from engine.adapter import (
    AdapterError,
    adapt_margin_history,
    adapt_market_cap,
    adapt_market_margin_history,
    adapt_mcp_data,
    adapt_price_history,
    clean_date,
    normalize_number,
    wide_to_long,
)
from engine.validators import (
    validate_margin_history,
    validate_market_cap,
    validate_market_margin_history,
    validate_price_history,
)
from tests.helpers import trading_dates


class AdapterTests(unittest.TestCase):
    def test_date_cleaning(self):
        self.assertEqual(clean_date("2026年7月1日"), "2026-07-01")
        self.assertEqual(clean_date("20260701"), "2026-07-01")
        self.assertEqual(clean_date("2026/07/01"), "2026-07-01")
        self.assertEqual(clean_date("2026-07-01T15:00:00+08:00"), "2026-07-01")
        self.assertIsNone(clean_date("-"))
        with self.assertRaises(AdapterError):
            clean_date("not-a-date")

    def test_number_and_unit_normalization(self):
        self.assertEqual(normalize_number("-"), None)
        self.assertEqual(normalize_number("1.25亿元"), 125_000_000)
        self.assertEqual(normalize_number("2.5", unit="万元"), 25_000)
        self.assertEqual(normalize_number("1,234", unit="CNY"), 1234)
        with self.assertRaises(AdapterError):
            normalize_number("abc", unit="CNY")

    def test_generic_wide_to_long(self):
        result = wide_to_long(
            [{"指标": "融资余额", "2026/07/01": "1", "2026/07/02": "-"}],
            id_columns=("指标",),
        )
        self.assertEqual(
            result,
            [
                {"指标": "融资余额", "date": "2026-07-01", "value": "1"},
                {"指标": "融资余额", "date": "2026-07-02", "value": None},
            ],
        )

    def test_wide_margin_to_engine_and_validator(self):
        dates = trading_dates(21)
        payload = {
            "rows": [
                {
                    "指标": "融资余额",
                    "单位": "亿元",
                    **{day: str(1 + index / 100) for index, day in enumerate(dates)},
                }
            ]
        }
        adapted = adapt_margin_history(payload)
        self.assertEqual(adapted["rows"][0]["margin_balance"], 100_000_000)
        self.assertEqual(adapted["rows"][-1]["unit"], "CNY")
        self.assertEqual(validate_margin_history(adapted)["status"], "VALID")

    def test_price_adapter_null_and_forward_adjustment(self):
        dates = trading_dates(120)
        payload = {
            "data": [
                {"日期": day, "收盘价": "-" if index == 0 else 10 + index}
                for index, day in enumerate(dates)
            ],
            "复权方式": "前复权",
            "latest_closed_date": dates[-1],
        }
        adapted = adapt_price_history(payload)
        self.assertIsNone(adapted["rows"][0]["close"])
        result = validate_price_history(adapted, minimum_observations=120)
        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(
            any(issue["code"] == "INVALID_CLOSE" for issue in result["issues"])
        )

    def test_market_cap_adapter(self):
        cap = adapt_market_cap(
            [{"日期": "2026-07-01", "自由流通市值": "2.2"}],
            unit="亿元",
        )
        selected = validate_market_cap(cap, "2026-07-02")
        self.assertAlmostEqual(
            selected["selected_row"]["free_float_market_cap"], 220_000_000
        )

    def test_unsupported_unit_and_dispatch(self):
        with self.assertRaises(AdapterError):
            adapt_market_margin_history(
                [{"日期": "2026-07-01", "全市场两融余额": "1"}],
                unit="万亿元",
            )
        market = adapt_mcp_data(
            [
                {"日期": "2026-07-01", "全市场两融余额": "100"},
                {"日期": "2026-07-02", "全市场两融余额": "97"},
            ],
            "market_margin",
            unit="亿元",
        )
        self.assertEqual(
            validate_market_margin_history(market)["status"], "VALID"
        )
        with self.assertRaises(AdapterError):
            adapt_mcp_data([], "unknown")


if __name__ == "__main__":
    unittest.main()
