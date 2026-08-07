import unittest
from math import isclose

from engine.price_features import (
    calculate_density_series,
    calculate_log_regression,
    calculate_ma_density,
    calculate_ma_slope,
    calculate_price_regression_features,
    calculate_rolling_clock_features,
    latest_moving_averages,
    moving_average,
)


class PriceFeatureTests(unittest.TestCase):
    def test_moving_average_and_latest(self):
        self.assertEqual(
            moving_average([1, 2, 3, 4, 5], 3),
            [None, None, 2.0, 3.0, 4.0],
        )
        self.assertEqual(
            latest_moving_averages(list(range(1, 61))),
            {"ma5": 58, "ma10": 55.5, "ma20": 50.5, "ma60": 30.5},
        )

    def test_slope_density_and_direction(self):
        self.assertTrue(
            isclose(calculate_ma_slope([100.0] * 10 + [110.0]), 1.0)
        )
        density = calculate_ma_density(99, 100, 101)
        self.assertTrue(isclose(density["mean"], 100))
        self.assertTrue(isclose(density["range"], 2))
        self.assertTrue(isclose(density["relative_range_pct"], 2))
    def test_log_regression_and_rolling_alignment(self):
        closes = [100 * 1.01**index for index in range(40)]
        regression = calculate_log_regression(closes[-30:])
        self.assertTrue(isclose(regression["g_daily_pct"], 1.0, rel_tol=1e-12))
        self.assertTrue(isclose(regression["r_squared"], 1.0, rel_tol=1e-12))
        features = calculate_price_regression_features(closes)
        self.assertTrue(isclose(features["g10_daily_pct"], 1.0, rel_tol=1e-12))
        rolling = calculate_rolling_clock_features(closes)
        self.assertEqual(rolling[:29], [None] * 29)
        self.assertEqual(rolling[29]["index"], 29)

    def test_density_series_alignment(self):
        closes = [100.0] * 60
        series = {window: moving_average(closes, window) for window in (5, 10, 20, 60)}
        result = calculate_density_series(closes, series)
        self.assertEqual(result[:19], [None] * 19)
        self.assertEqual(
            result[-1],
            {"mean": 100.0, "range": 0.0, "relative_range_pct": 0.0},
        )


if __name__ == "__main__":
    unittest.main()
