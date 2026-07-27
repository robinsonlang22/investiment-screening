"""Pure technical-feature calculations for validated price data.

No function in this module returns PASS/FAIL or an investment conclusion.
Series-producing functions preserve input alignment by using ``None`` until
there are enough observations for a value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, isfinite, log
from typing import Any


def _validated_values(values: Sequence[float], *, name: str) -> list[float]:
    result: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool):
            raise ValueError(f"{name}[{index}] must be a finite positive number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name}[{index}] must be a finite positive number"
            ) from exc
        if not isfinite(number) or number <= 0:
            raise ValueError(f"{name}[{index}] must be a finite positive number")
        result.append(number)
    return result


def moving_average(
    values: list[float],
    window: int,
) -> list[float | None]:
    """Return a simple-moving-average series aligned to ``values``."""

    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("window must be a positive integer")
    numbers = _validated_values(values, name="values")
    result: list[float | None] = [None] * len(numbers)
    running_sum = 0.0

    for index, number in enumerate(numbers):
        running_sum += number
        if index >= window:
            running_sum -= numbers[index - window]
        if index >= window - 1:
            result[index] = running_sum / window
    return result


def latest_moving_averages(
    closes: Sequence[float],
    windows: Sequence[int] = (5, 10, 20, 60),
) -> dict[str, float]:
    """Return the latest available simple moving average for each window."""

    numbers = _validated_values(closes, name="closes")
    result: dict[str, float] = {}
    for window in windows:
        series = moving_average(numbers, window)
        latest = series[-1] if series else None
        if latest is None:
            raise ValueError(
                f"at least {window} close observations are required for MA{window}"
            )
        result[f"ma{window}"] = latest
    return result


def calculate_ma_slope(
    ma_series: Sequence[float | None],
    lookback: int = 10,
) -> float | None:
    """Calculate S_lookback(MA) in percentage points per trading day.

    The current and ``lookback``-prior MA values are used, so the input needs
    at least ``lookback + 1`` calculable tail observations.
    """

    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback <= 0:
        raise ValueError("lookback must be a positive integer")
    if len(ma_series) <= lookback:
        return None

    current = ma_series[-1]
    previous = ma_series[-1 - lookback]
    if current is None or previous is None:
        return None
    current_number, previous_number = _validated_values(
        [current, previous], name="ma_series"
    )
    return (current_number / previous_number - 1.0) / lookback * 100.0


def calculate_ma_density(
    close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    ma60: float,
) -> float:
    """Calculate MA density as a percentage of the same-day close."""

    close_number, *averages = _validated_values(
        [close, ma5, ma10, ma20, ma60], name="density_inputs"
    )
    return (max(averages) - min(averages)) / close_number * 100.0


def _close_value(row: Any, index: int) -> float:
    if isinstance(row, Mapping):
        if "close" not in row:
            raise ValueError(f"close_rows[{index}] is missing 'close'")
        return _validated_values([row["close"]], name=f"close_rows[{index}]")[0]
    return _validated_values([row], name=f"close_rows[{index}]")[0]


def _ma_series_for_window(
    ma_series: Mapping[Any, Sequence[float | None]], window: int
) -> Sequence[float | None]:
    for key in (window, str(window), f"ma{window}", f"MA{window}"):
        if key in ma_series:
            return ma_series[key]
    raise ValueError(f"ma_series is missing MA{window}")


def calculate_density_series(
    close_rows: Sequence[Any],
    ma_series: Mapping[Any, Sequence[float | None]],
) -> list[float | None]:
    """Return the aligned MA-density series for MA5/10/20/60.

    ``close_rows`` may contain close numbers or validated mappings with a
    ``close`` key. ``ma_series`` accepts keys such as ``ma5`` or ``5``.
    """

    series_by_window = {
        window: _ma_series_for_window(ma_series, window)
        for window in (5, 10, 20, 60)
    }
    length = len(close_rows)
    if any(len(series) != length for series in series_by_window.values()):
        raise ValueError("close_rows and every MA series must have equal lengths")

    result: list[float | None] = []
    for index, row in enumerate(close_rows):
        averages = [series_by_window[window][index] for window in (5, 10, 20, 60)]
        if any(value is None for value in averages):
            result.append(None)
            continue
        close = _close_value(row, index)
        result.append(calculate_ma_density(close, *averages))  # type: ignore[arg-type]
    return result


def calculate_log_regression(closes: Sequence[float]) -> dict[str, float]:
    """Regress log closes on ``0..n-1`` and return G, E and R-squared."""

    numbers = _validated_values(closes, name="closes")
    count = len(numbers)
    if count < 2:
        raise ValueError("at least two close observations are required")

    x_mean = (count - 1) / 2.0
    log_closes = [log(value) for value in numbers]
    y_mean = sum(log_closes) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = (
        sum(
            (index - x_mean) * (value - y_mean)
            for index, value in enumerate(log_closes)
        )
        / denominator
    )
    intercept = y_mean - slope * x_mean
    residual_sum_squares = sum(
        (
            value
            - (intercept + slope * index)
        )
        ** 2
        for index, value in enumerate(log_closes)
    )
    total_sum_squares = sum((value - y_mean) ** 2 for value in log_closes)
    if total_sum_squares == 0:
        r_squared = 1.0
    else:
        r_squared = 1.0 - residual_sum_squares / total_sum_squares
        # Protect against tiny floating-point excursions outside [0, 1].
        r_squared = min(1.0, max(0.0, r_squared))

    return {
        "g_daily_pct": (exp(slope) - 1.0) * 100.0,
        "e_daily_pct": (numbers[-1] / numbers[0] - 1.0) / (count - 1) * 100.0,
        "r_squared": r_squared,
    }


def calculate_price_regression_features(
    closes: Sequence[float],
    main_window: int = 30,
    short_window: int = 10,
) -> dict[str, float]:
    """Calculate the latest main- and short-window regression features."""

    if not isinstance(main_window, int) or main_window < 2:
        raise ValueError("main_window must be an integer of at least 2")
    if not isinstance(short_window, int) or short_window < 2:
        raise ValueError("short_window must be an integer of at least 2")
    numbers = _validated_values(closes, name="closes")
    required = max(main_window, short_window)
    if len(numbers) < required:
        raise ValueError(f"at least {required} close observations are required")

    main = calculate_log_regression(numbers[-main_window:])
    short = calculate_log_regression(numbers[-short_window:])
    return {
        "g30_daily_pct": main["g_daily_pct"],
        "e30_daily_pct": main["e_daily_pct"],
        "r_squared_30": main["r_squared"],
        "g10_daily_pct": short["g_daily_pct"],
        "e10_daily_pct": short["e_daily_pct"],
        "r_squared_10": short["r_squared"],
    }


def calculate_rolling_clock_features(
    closes: Sequence[float],
) -> list[dict[str, float | int] | None]:
    """Return aligned rolling 30-day regression features.

    The first 29 elements are ``None``. Each later element contains objective
    regression features and its zero-based source index; no clock category or
    P2 decision is assigned here.
    """

    numbers = _validated_values(closes, name="closes")
    window = 30
    result: list[dict[str, float | int] | None] = [None] * len(numbers)
    for end_index in range(window - 1, len(numbers)):
        features = calculate_log_regression(
            numbers[end_index - window + 1 : end_index + 1]
        )
        result[end_index] = {
            "index": end_index,
            "g30_daily_pct": features["g_daily_pct"],
            "e30_daily_pct": features["e_daily_pct"],
            "r_squared_30": features["r_squared"],
        }
    return result
