import unittest

from engine.adapter import (
    AdapterError,
    adapt_margin_history,
    adapt_market_cap,
    adapt_market_margin_history,
    adapt_mcp_data,
    adapt_price_history,
    clean_date,
    expand_choice_tables,
    normalize_date,
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
    def test_expand_choice_tables(self):
        expanded = expand_choice_tables(
            {
                "data": [
                    {
                        "sheetName": "日线",
                        "columns": ["日期", "收盘价", "备注"],
                        "items": [
                            ["2026-07-01", "10.5", "正常"],
                            ["2026-07-02", "10.8"],
                            "ignored",
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            expanded,
            [
                {
                    "日期": "2026-07-01",
                    "收盘价": "10.5",
                    "备注": "正常",
                    "_sheet_name": "日线",
                },
                {
                    "日期": "2026-07-02",
                    "收盘价": "10.8",
                    "备注": None,
                    "_sheet_name": "日线",
                },
            ],
        )

    def test_choice_table_flows_through_common_adapter_entry(self):
        payload = {
            "data": [
                {
                    "sheetName": "前复权日线",
                    "columns": ["日期", "收盘价"],
                    "items": [
                        ["2026/07/01", "10.5"],
                        ["2026/07/02", "-"],
                    ],
                }
            ],
            "复权方式": "前复权",
            "latest_closed_date": "2026/07/02",
        }
        adapted = adapt_mcp_data(payload, "price_history")
        self.assertEqual(adapted["rows"][0]["date"], "2026-07-01")
        self.assertEqual(adapted["rows"][0]["close"], 10.5)
        self.assertIsNone(adapted["rows"][1]["close"])
        self.assertEqual(adapted["latest_closed_date"], "2026-07-02")

    def test_choice_price_adapter_skips_unrelated_tables(self):
        payload = {
            "data": [
                {
                    "sheetName": "区间涨跌幅",
                    "columns": ["日期", "数值"],
                    "items": [["2026-07-01", "1.2%"]],
                },
                {
                    "sheetName": "开盘价",
                    "columns": ["日期", "数值"],
                    "items": [["2026-07-01", "10.2"]],
                },
                {
                    "sheetName": "收盘价",
                    "columns": ["日期", "数值"],
                    "items": [
                        ["2026-07-01", "10.5"],
                        ["2026-07-02", "10.8"],
                    ],
                },
            ],
            "复权方式": "前复权",
        }

        adapted = adapt_mcp_data(payload, "price")

        self.assertEqual(
            adapted["rows"],
            [
                {
                    "date": "2026-07-01",
                    "close": 10.5,
                    "adjustment": "forward",
                },
                {
                    "date": "2026-07-02",
                    "close": 10.8,
                    "adjustment": "forward",
                },
            ],
        )

    def test_price_adapter_skips_rows_with_unrelated_price_columns(self):
        adapted = adapt_price_history(
            {
                "rows": [
                    {"日期": "2026-07-01", "区间涨跌幅": "1.2%"},
                    {"日期": "2026-07-01", "开盘价": "10.2"},
                    {"日期": "2026-07-01", "收盘价": "10.5"},
                ],
                "复权方式": "前复权",
            }
        )

        self.assertEqual(len(adapted["rows"]), 1)
        self.assertEqual(adapted["rows"][0]["close"], 10.5)

    def test_date_cleaning(self):
        self.assertEqual(normalize_date("2026-07-24"), "2026-07-24")
        self.assertEqual(normalize_date("2026-07-24(日)"), "2026-07-24")
        self.assertEqual(normalize_date("2026/07/24"), "2026-07-24")
        self.assertEqual(normalize_date("2026年07月24日"), "2026-07-24")
        self.assertEqual(clean_date("20260701"), "2026-07-01")
        self.assertEqual(clean_date("2026-07-01T15:00:00+08:00"), "2026-07-01")
        self.assertIsNone(clean_date("-"))
        self.assertIsNone(clean_date("not-a-date"))

    def test_number_and_unit_normalization(self):
        self.assertEqual(normalize_number("-"), None)
        self.assertEqual(normalize_number("1元"), 1)
        self.assertEqual(normalize_number("1.25亿元"), 125_000_000)
        self.assertEqual(normalize_number("2.5", unit="万元"), 25_000)
        self.assertEqual(normalize_number("1.2万亿"), 1_200_000_000_000)
        self.assertEqual(normalize_number("3.5%"), 3.5)
        self.assertEqual(normalize_number("2倍"), 2)
        self.assertEqual(normalize_number("8次"), 8)
        self.assertEqual(normalize_number("1,234", unit="CNY"), 1234)
        warnings = []
        self.assertIsNone(
            normalize_number("abc", unit="CNY", warnings=warnings)
        )
        self.assertEqual(warnings[0]["code"], "UNPARSEABLE_VALUE")

    def test_bad_value_becomes_null_with_warning_and_other_rows_continue(self):
        adapted = adapt_margin_history(
            {
                "rows": [
                    {"日期": "2026-07-01", "融资余额": "1.2"},
                    {"日期": "2026-07-02", "融资余额": "坏值"},
                    {"日期": "2026-07-03", "融资余额": "1.3"},
                ],
                "单位": "亿元",
            }
        )
        self.assertEqual(len(adapted["rows"]), 3)
        self.assertEqual(adapted["rows"][0]["margin_balance"], 120_000_000)
        self.assertIsNone(adapted["rows"][1]["margin_balance"])
        self.assertEqual(adapted["rows"][2]["margin_balance"], 130_000_000)
        self.assertEqual(len(adapted["warnings"]), 1)
        self.assertEqual(adapted["warnings"][0]["row"], 2)

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
        unsupported = adapt_market_margin_history(
            [{"日期": "2026-07-01", "全市场两融余额": "1"}],
            unit="美元",
        )
        self.assertIsNone(
            unsupported["rows"][0]["market_margin_balance"]
        )
        self.assertEqual(
            unsupported["warnings"][0]["code"], "UNSUPPORTED_UNIT"
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
