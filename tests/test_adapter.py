import unittest

from engine.adapter import (
    AdapterError,
    adapt_mcp_data,
    adapt_price_history,
    expand_choice_tables,
    normalize_date,
    normalize_number,
)


class AdapterTests(unittest.TestCase):
    def test_expand_choice_tables(self):
        payload = {
            "columns": ["security", "2026-01-01", "2026-01-02"],
            "items": [["closing_price", 10, 11]],
        }
        rows = expand_choice_tables(payload)
        self.assertEqual(len(rows), 2)

    def test_price_adapter(self):
        adapted = adapt_price_history(
            [{"date": "2026-01-01", "close": "10.5"}],
        )
        self.assertEqual(adapted["rows"][0]["close"], 10.5)

    def test_common_dispatch_rejects_removed_dataset_types(self):
        with self.assertRaises(AdapterError):
            adapt_mcp_data([], "removed_dataset")

    def test_normalizers(self):
        self.assertEqual(normalize_date("20260102"), "2026-01-02")
        self.assertEqual(normalize_number("1.2ten_thousand_cny"), 12_000)


if __name__ == "__main__":
    unittest.main()
