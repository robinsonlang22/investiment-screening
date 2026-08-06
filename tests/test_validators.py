import unittest

from engine.validators import validate_price_history


class ValidatorTests(unittest.TestCase):
    def test_valid_price_history(self):
        rows = {
            "rows": [
                {"date": f"2026-01-{day:02d}", "close": 100 + day}
                for day in range(1, 6)
            ],
            "latest_closed_date": "2026-01-05",
        }
        result = validate_price_history(rows, minimum_observations=5)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(len(result["valid_rows"]), 5)

    def test_price_accepts_minimal_metadata(self):
        result = validate_price_history(
            {
                "rows": [{"date": "2026-01-01", "close": 100}],
            },
            minimum_observations=1,
        )
        self.assertEqual(result["status"], "VALID")


if __name__ == "__main__":
    unittest.main()
