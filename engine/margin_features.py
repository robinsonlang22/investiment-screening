"""Pure financing-balance feature calculations.

Inputs are expected to have passed ``engine.validators``. This module computes
objective F1 inputs only; it never assigns an F1 status or changes a decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, isclose, isfinite, log
from typing import Any


_MARGIN_KEYS = ("margin_balance", "financing_balance", "融资余额")
_MARKET_MARGIN_KEYS = (
    "market_margin_balance",
    "total_margin_balance",
    "两市融资融券余额",
    "全市场两融余额",
)
_DATE_KEYS = ("date", "trade_date", "交易日期", "日期")


def _positive_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return number


def _row_value(row: Mapping[str, Any], keys: Sequence[str], *, name: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    raise ValueError(f"{name} is missing; accepted keys: {', '.join(keys)}")


def _normalise_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    value_keys: Sequence[str],
    value_name: str,
) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"rows[{index}] must be a mapping")
        raw_date = _row_value(row, _DATE_KEYS, name=f"rows[{index}].date")
        if raw_date is None or str(raw_date).strip() == "":
            raise ValueError(f"rows[{index}].date must not be empty")
        value = _positive_number(
            _row_value(row, value_keys, name=f"rows[{index}].{value_name}"),
            name=f"rows[{index}].{value_name}",
        )
        normalised.append({"date": str(raw_date), "value": value})
    return normalised


def _validated_balances(balance_values: Sequence[float]) -> list[float]:
    return [
        _positive_number(value, name=f"balance_values[{index}]")
        for index, value in enumerate(balance_values)
    ]


def calculate_leverage_ratio(
    latest_margin_balance: float,
    free_float_market_cap: float,
) -> float:
    """Return LR = financing balance / free-float market cap * 100%."""

    balance = _positive_number(
        latest_margin_balance, name="latest_margin_balance"
    )
    market_cap = _positive_number(
        free_float_market_cap, name="free_float_market_cap"
    )
    return balance / market_cap * 100.0


def calculate_c20(balance_values: Sequence[float]) -> float:
    """Return C20 from the most recent 21 financing-balance observations."""

    balances = _validated_balances(balance_values)
    if len(balances) < 21:
        raise ValueError("at least 21 financing-balance observations are required")
    window = balances[-21:]
    return (window[-1] / window[0] - 1.0) * 100.0


def calculate_margin_trend(
    balance_values: Sequence[float],
) -> dict[str, float]:
    """Run the F1 log-linear regression over the latest 21 observations."""

    balances = _validated_balances(balance_values)
    if len(balances) < 21:
        raise ValueError("at least 21 financing-balance observations are required")
    window = balances[-21:]
    count = len(window)
    x_mean = (count - 1) / 2.0
    log_balances = [log(value) for value in window]
    y_mean = sum(log_balances) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = (
        sum(
            (index - x_mean) * (value - y_mean)
            for index, value in enumerate(log_balances)
        )
        / denominator
    )
    return {
        "gb_daily_pct": (exp(slope) - 1.0) * 100.0,
        "slope_log": slope,
    }


def calculate_daily_margin_changes(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return adjacent daily financing-balance changes with their dates."""

    normalised = _normalise_rows(
        rows, value_keys=_MARGIN_KEYS, value_name="margin_balance"
    )
    changes: list[dict[str, Any]] = []
    for index in range(1, len(normalised)):
        previous = normalised[index - 1]
        current = normalised[index]
        changes.append(
            {
                "change_pct": (
                    current["value"] / previous["value"] - 1.0
                )
                * 100.0,
                "start_date": previous["date"],
                "end_date": current["date"],
            }
        )
    return changes


def calculate_three_day_margin_changes(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return all rolling three-trading-day financing-balance changes."""

    normalised = _normalise_rows(
        rows, value_keys=_MARGIN_KEYS, value_name="margin_balance"
    )
    changes: list[dict[str, Any]] = []
    for index in range(3, len(normalised)):
        start = normalised[index - 3]
        end = normalised[index]
        changes.append(
            {
                "change_pct": (end["value"] / start["value"] - 1.0) * 100.0,
                "start_date": start["date"],
                "end_date": end["date"],
            }
        )
    return changes


def find_worst_outflow(
    changes: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the most negative change, or ``None`` for an empty sequence."""

    if not changes:
        return None

    validated: list[dict[str, Any]] = []
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping):
            raise ValueError(f"changes[{index}] must be a mapping")
        try:
            number = float(change["change_pct"])
            start_date = change["start_date"]
            end_date = change["end_date"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"changes[{index}] must contain change_pct, start_date and end_date"
            ) from exc
        if not isfinite(number):
            raise ValueError(f"changes[{index}].change_pct must be finite")
        validated.append(
            {
                "change_pct": number,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
    return min(validated, key=lambda item: item["change_pct"])


def calculate_market_deleveraging(
    market_margin_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return daily market margin changes and objective <-2.5% flags."""

    normalised = _normalise_rows(
        market_margin_rows,
        value_keys=_MARKET_MARGIN_KEYS,
        value_name="market_margin_balance",
    )
    changes: list[dict[str, Any]] = []
    for index in range(1, len(normalised)):
        previous = normalised[index - 1]
        current = normalised[index]
        change_pct = (current["value"] / previous["value"] - 1.0) * 100.0
        changes.append(
            {
                "change_pct": change_pct,
                "start_date": previous["date"],
                "end_date": current["date"],
                "below_minus_2_5_pct": (
                    change_pct < -2.5
                    and not isclose(change_pct, -2.5, abs_tol=1e-12)
                ),
            }
        )
    return changes


def build_f1_features(
    margin_rows: Sequence[Mapping[str, Any]],
    free_float_market_cap: float,
    market_margin_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the complete objective feature package consumed by F1.

    When more than 21 security rows are supplied, only the latest 21 are used
    so daily and three-day outputs contain exactly 20 and 18 changes.
    """

    if len(margin_rows) < 21:
        raise ValueError("at least 21 financing-balance rows are required")
    window_rows = list(margin_rows[-21:])
    normalised = _normalise_rows(
        window_rows, value_keys=_MARGIN_KEYS, value_name="margin_balance"
    )
    balances = [row["value"] for row in normalised]
    daily_changes = calculate_daily_margin_changes(window_rows)
    three_day_changes = calculate_three_day_margin_changes(window_rows)

    features: dict[str, Any] = {
        "window_start_date": normalised[0]["date"],
        "window_end_date": normalised[-1]["date"],
        "observation_count": len(normalised),
        "leverage_ratio_pct": calculate_leverage_ratio(
            balances[-1], free_float_market_cap
        ),
        "c20_pct": calculate_c20(balances),
        "margin_trend": calculate_margin_trend(balances),
        "daily_changes": daily_changes,
        "three_day_changes": three_day_changes,
        "worst_daily_outflow": find_worst_outflow(daily_changes),
        "worst_three_day_outflow": find_worst_outflow(three_day_changes),
        "market_deleveraging": None,
    }
    if market_margin_rows is not None:
        features["market_deleveraging"] = calculate_market_deleveraging(
            market_margin_rows
        )
    return features
