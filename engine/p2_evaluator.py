"""Evaluate P2 from precomputed regression and moving-average features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


PASS = "PASS"
CONDITIONAL_PASS = "CONDITIONAL_PASS"
FAIL = "FAIL"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _value(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _thresholds(config: Mapping[str, Any]) -> dict[str, float]:
    raw = config.get("thresholds", {})
    raw = raw if isinstance(raw, Mapping) else {}

    def get(name: str, default: float) -> float:
        value = _number(raw.get(name))
        return default if value is None else value

    return {
        "r2": get("stable_r2_min_inclusive", 0.60),
        "flat_min": get("flat_g30_min_inclusive_pct", -0.15),
        "flat_max": get("flat_g30_max_inclusive_pct", 0.15),
        "fast_abs": get("fast_move_abs_threshold_pct", 0.50),
        "max_days": get("clock_one_max_trading_days_inclusive", 10),
        "max_return": get("clock_one_max_return_pct_inclusive", 15.0),
    }


def classify_clock_direction(
    g30: float,
    r2_30: float,
    config: Mapping[str, Any],
) -> int | None:
    """Classify the P2 clock direction with exact configured boundaries."""

    g = _number(g30)
    r2 = _number(r2_30)
    if g is None or r2 is None:
        return None
    limits = _thresholds(config)

    if r2 < limits["r2"]:
        return 3
    if g > limits["fast_abs"]:
        return 1
    if limits["flat_max"] < g <= limits["fast_abs"]:
        return 2
    if limits["flat_min"] <= g <= limits["flat_max"]:
        return 3
    if -limits["fast_abs"] <= g < limits["flat_min"]:
        return 4
    if g < -limits["fast_abs"]:
        return 5
    return None


def _opposite_sign(left: float, right: float) -> bool:
    return left * right < 0


def classify_short_term_state(
    g10: float | None,
    e10: float | None,
    r2_10: float | None,
    g30: float,
) -> dict[str, Any]:
    """Describe short-term speed and stability without changing the clock."""

    short_g = _number(g10)
    short_e = _number(e10)
    short_r2 = _number(r2_10)
    main_g = _number(g30)
    if None in (short_g, short_e, short_r2, main_g):
        return {
            "state": "INSUFFICIENT_INFORMATION",
            "direction_unstable": None,
            "labels": ["短期加速度信息不足"],
        }

    assert short_g is not None
    assert short_e is not None
    assert short_r2 is not None
    assert main_g is not None

    if _opposite_sign(short_g, main_g):
        state = "DIVERGING"
        label = "背离"
    elif abs(short_g) > abs(main_g):
        state = "ACCELERATING"
        label = "加速"
    elif abs(short_g) < abs(main_g):
        state = "DECELERATING"
        label = "减速"
    else:
        state = "SAME_SPEED"
        label = "速度一致"

    unstable = _opposite_sign(short_g, short_e) or short_r2 < 0.60
    labels = [label]
    if unstable:
        labels.append("短期方向不稳定")
    return {
        "state": state,
        "direction_unstable": unstable,
        "labels": labels,
    }


def _rolling_value(row: Mapping[str, Any], *keys: str) -> float | None:
    return _number(_value(row, *keys))


def _is_clock_one(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    g30 = _rolling_value(row, "g30_daily_pct", "g30", "G30")
    r2 = _rolling_value(row, "r_squared_30", "r2_30", "R2_30")
    return g30 is not None and r2 is not None and g30 > 0.50 and r2 >= 0.60


def find_clock_one_entry(
    rolling_clock_results: Sequence[Mapping[str, Any] | None],
    price_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Find the first trading day of the current continuous clock-one run."""

    if len(rolling_clock_results) != len(price_rows):
        raise ValueError("rolling_clock_results and price_rows must be aligned")
    if not rolling_clock_results or not _is_clock_one(rolling_clock_results[-1]):
        return None

    entry_index = len(rolling_clock_results) - 1
    while entry_index > 0 and _is_clock_one(
        rolling_clock_results[entry_index - 1]
    ):
        entry_index -= 1

    entry_row = price_rows[entry_index]
    current_row = price_rows[-1]
    if not isinstance(entry_row, Mapping) or not isinstance(current_row, Mapping):
        raise ValueError("price_rows must contain mappings")
    entry_date = _value(entry_row, "date", "trade_date", "交易日期", "日期")
    current_date = _value(current_row, "date", "trade_date", "交易日期", "日期")
    entry_close = _number(_value(entry_row, "close", "close_price", "收盘价"))
    current_close = _number(_value(current_row, "close", "close_price", "收盘价"))
    if entry_date is None or current_date is None or entry_close is None or current_close is None:
        return None

    return {
        "entry_index": entry_index,
        "entry_date": str(entry_date),
        "entry_close": entry_close,
        "current_date": str(current_date),
        "current_close": current_close,
        "trading_days_inclusive": len(price_rows) - entry_index,
    }


def evaluate_clock_one_window(
    entry_date: Any,
    current_date: Any,
    entry_close: float,
    current_close: float,
    *,
    trading_days_inclusive: int | None = None,
    max_trading_days: int = 10,
    max_return_pct: float = 15.0,
) -> dict[str, Any]:
    """Evaluate the two objective clock-one window conditions.

    Exact trading-day counting requires ``trading_days_inclusive`` from aligned
    price rows. Dates are retained for evidence and are not treated as a
    substitute for an exchange trading calendar.
    """

    start = _number(entry_close)
    end = _number(current_close)
    if start is None or end is None or entry_date is None or current_date is None:
        return {
            "status": INSUFFICIENT_INFORMATION,
            "trading_day_condition": None,
            "return_condition": None,
            "passes_any_condition": None,
            "return_since_entry_pct": None,
            "reasons": ["缺少1点钟进入日、当前日或相应收盘价"],
        }

    day_condition = (
        None
        if trading_days_inclusive is None
        else 1 <= trading_days_inclusive <= max_trading_days
    )
    return_pct = (end / start - 1.0) * 100.0
    return_condition = return_pct <= max_return_pct

    if day_condition is True or return_condition:
        status = PASS
        passes_any: bool | None = True
    elif day_condition is False and not return_condition:
        status = FAIL
        passes_any = False
    else:
        status = INSUFFICIENT_INFORMATION
        passes_any = None

    return {
        "status": status,
        "entry_date": str(entry_date),
        "current_date": str(current_date),
        "trading_days_inclusive": trading_days_inclusive,
        "trading_day_condition": day_condition,
        "return_since_entry_pct": return_pct,
        "return_condition": return_condition,
        "passes_any_condition": passes_any,
        "reasons": (
            [
                f"已超过{max_trading_days}个交易日且进入后累计涨幅"
                f"严格超过{max_return_pct:g}%"
            ]
            if status == FAIL
            else (
                ["缺少精确交易日计数，且累计涨幅条件未满足"]
                if status == INSUFFICIENT_INFORMATION
                else []
            )
        ),
    }


def evaluate_clock_three(p1_result: Mapping[str, Any]) -> dict[str, Any]:
    """Clock three passes only when P1 explicitly identifies a dense state."""

    if not isinstance(p1_result, Mapping):
        return {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [],
            "reasons": ["缺少P1评估结果"],
        }
    market_state = p1_result.get("market_state")
    if market_state == "DENSE":
        return {
            "status": PASS,
            "checks": [
                {"id": "p1_dense", "passed": True, "observed": market_state}
            ],
            "reasons": [],
        }
    if market_state in (None, INSUFFICIENT_INFORMATION):
        return {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [
                {"id": "p1_dense", "passed": None, "observed": market_state}
            ],
            "reasons": ["无法确认P1是否处于均线密集状态"],
        }
    return {
        "status": FAIL,
        "checks": [
            {"id": "p1_dense", "passed": False, "observed": market_state}
        ],
        "reasons": ["3点钟方向下，P1未处于均线密集状态"],
    }


def evaluate_clock_five(
    close: float,
    ma10: float,
    ma20: float,
) -> dict[str, Any]:
    """Evaluate clock-five close-versus-MA10/MA20 conditions."""

    close_value = _number(close)
    ma10_value = _number(ma10)
    ma20_value = _number(ma20)
    if None in (close_value, ma10_value, ma20_value):
        return {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [],
            "reasons": ["5点钟判断缺少收盘价、MA10或MA20"],
        }

    assert close_value is not None
    assert ma10_value is not None
    assert ma20_value is not None
    above_ma10 = close_value > ma10_value
    above_ma20 = close_value > ma20_value
    checks = [
        {"id": "close_above_ma10", "passed": above_ma10},
        {"id": "close_above_ma20", "passed": above_ma20},
    ]
    if not above_ma10:
        return {
            "status": FAIL,
            "checks": checks,
            "reasons": ["最新收盘价未严格站上MA10"],
        }
    if not above_ma20:
        return {
            "status": CONDITIONAL_PASS,
            "checks": checks,
            "reasons": ["已站上MA10，但尚未严格站上MA20"],
        }
    return {"status": PASS, "checks": checks, "reasons": []}


def _extract_price_features(price_features: Mapping[str, Any]) -> dict[str, Any]:
    regression = price_features.get("regression_features", price_features)
    if not isinstance(regression, Mapping):
        regression = price_features
    mas = price_features.get(
        "moving_averages",
        price_features.get("latest_moving_averages", price_features),
    )
    if not isinstance(mas, Mapping):
        mas = price_features
    return {
        "g30": _number(_value(regression, "g30_daily_pct", "g30", "G30")),
        "e30": _number(_value(regression, "e30_daily_pct", "e30", "E30")),
        "r2_30": _number(
            _value(regression, "r_squared_30", "r2_30", "R2_30")
        ),
        "g10": _number(_value(regression, "g10_daily_pct", "g10", "G10")),
        "e10": _number(_value(regression, "e10_daily_pct", "e10", "E10")),
        "r2_10": _number(
            _value(regression, "r_squared_10", "r2_10", "R2_10")
        ),
        "close": _number(_value(price_features, "close", "latest_close")),
        "ma10": _number(_value(mas, "ma10", "MA10")),
        "ma20": _number(_value(mas, "ma20", "MA20")),
        "rolling": price_features.get(
            "rolling_clock_results",
            price_features.get("rolling_clock_features"),
        ),
        "price_rows": price_features.get("price_rows"),
    }


def evaluate_p2(
    price_features: Mapping[str, Any],
    p1_result: Mapping[str, Any],
    rule_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate P2 without recalculating regressions or moving averages."""

    if not isinstance(price_features, Mapping):
        raise TypeError("price_features must be a mapping")
    if not isinstance(rule_config, Mapping):
        raise TypeError("rule_config must be a mapping")
    if str(rule_config.get("id", "")).upper() != "P2":
        raise ValueError("rule_config must be the P2 configuration")

    features = _extract_price_features(price_features)
    metrics = {
        key: features[key]
        for key in ("g30", "e30", "r2_30", "g10", "e10", "r2_10", "close", "ma10", "ma20")
    }
    missing_main = [
        name for name in ("g30", "e30", "r2_30") if features[name] is None
    ]
    short_state = classify_short_term_state(
        features["g10"],
        features["e10"],
        features["r2_10"],
        features["g30"],
    )
    if missing_main:
        return {
            "rule_id": "P2",
            "status": INSUFFICIENT_INFORMATION,
            "clock": None,
            "short_term_state": short_state,
            "checks": [],
            "metrics": metrics,
            "reasons": [f"缺少P2主方向指标：{', '.join(missing_main)}"],
        }

    g30 = features["g30"]
    e30 = features["e30"]
    r2_30 = features["r2_30"]
    assert g30 is not None and e30 is not None and r2_30 is not None
    if _opposite_sign(g30, e30):
        return {
            "rule_id": "P2",
            "status": INSUFFICIENT_INFORMATION,
            "clock": None,
            "short_term_state": short_state,
            "checks": [
                {
                    "id": "g30_e30_direction_consistent",
                    "passed": False,
                    "observed": {"g30": g30, "e30": e30},
                }
            ],
            "metrics": metrics,
            "reasons": ["G30与E30方向相反，主方向不稳定"],
        }

    clock = classify_clock_direction(g30, r2_30, rule_config)
    if clock is None:
        evaluation = {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [],
            "reasons": ["无法根据G30和R²30分类钟点方向"],
        }
    elif clock == 1:
        rolling = features["rolling"]
        price_rows = features["price_rows"]
        if not isinstance(rolling, Sequence) or not isinstance(price_rows, Sequence):
            evaluation = {
                "status": INSUFFICIENT_INFORMATION,
                "checks": [],
                "reasons": ["1点钟方向缺少滚动30日特征或价格行"],
            }
        else:
            entry = find_clock_one_entry(rolling, price_rows)
            if entry is None:
                evaluation = {
                    "status": INSUFFICIENT_INFORMATION,
                    "checks": [],
                    "reasons": ["无法定位当前连续1点钟区间的进入日"],
                }
            else:
                limits = _thresholds(rule_config)
                evaluation = evaluate_clock_one_window(
                    entry["entry_date"],
                    entry["current_date"],
                    entry["entry_close"],
                    entry["current_close"],
                    trading_days_inclusive=entry["trading_days_inclusive"],
                    max_trading_days=int(limits["max_days"]),
                    max_return_pct=limits["max_return"],
                )
                metrics["clock_one_entry"] = entry
    elif clock == 2:
        evaluation = {"status": PASS, "checks": [], "reasons": []}
    elif clock == 3:
        evaluation = evaluate_clock_three(p1_result)
    elif clock == 4:
        evaluation = {
            "status": FAIL,
            "checks": [{"id": "clock_4_hard_veto", "passed": False}],
            "reasons": ["4点钟缓慢下降触发P2硬性否决"],
        }
    else:
        evaluation = evaluate_clock_five(
            features["close"], features["ma10"], features["ma20"]
        )

    return {
        "rule_id": "P2",
        "status": evaluation["status"],
        "clock": clock,
        "short_term_state": short_state,
        "checks": evaluation.get("checks", []),
        "metrics": metrics,
        "reasons": evaluation.get("reasons", []),
        "hard_veto": clock == 4,
    }
