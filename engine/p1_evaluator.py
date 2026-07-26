"""Evaluate P1 from precomputed price features.

This module consumes moving averages, slopes and densities produced elsewhere.
It does not calculate or rebuild moving-average series.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


PASS = "PASS"
CONDITIONAL_PASS = "CONDITIONAL_PASS"
FAIL = "FAIL"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"

DENSE = "DENSE"
DIVERGING = "DIVERGING"
TRANSITION_ENTANGLED = "TRANSITION_ENTANGLED"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _mapping(features: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        value = features.get(key)
        if isinstance(value, Mapping):
            return value
    return features


def _metric(
    source: Mapping[str, Any],
    *keys: str,
) -> float | None:
    for key in keys:
        if key in source:
            return _number(source[key])
    return None


def _extract_features(price_features: Mapping[str, Any]) -> dict[str, Any]:
    ma_source = _mapping(
        price_features, "moving_averages", "latest_moving_averages", "mas"
    )
    slope_source = _mapping(price_features, "slopes", "ma_slopes")

    density_values = price_features.get("density_last_3_days")
    if density_values is None:
        density_values = price_features.get("density_series")
        if isinstance(density_values, Sequence) and not isinstance(
            density_values, (str, bytes, bytearray)
        ):
            density_values = list(density_values)[-3:]

    return {
        "close": _metric(price_features, "close", "latest_close"),
        "ma5": _metric(ma_source, "ma5", "MA5"),
        "ma10": _metric(ma_source, "ma10", "MA10"),
        "ma20": _metric(ma_source, "ma20", "MA20"),
        "ma60": _metric(ma_source, "ma60", "MA60"),
        "s10_ma5": _metric(slope_source, "ma5", "MA5", "s10_ma5", "S10_MA5"),
        "s10_ma10": _metric(
            slope_source, "ma10", "MA10", "s10_ma10", "S10_MA10"
        ),
        "s10_ma20": _metric(
            slope_source, "ma20", "MA20", "s10_ma20", "S10_MA20"
        ),
        "s10_ma60": _metric(
            slope_source, "ma60", "MA60", "s10_ma60", "S10_MA60"
        ),
        "density_last_3_days": density_values,
    }


def _density_threshold(config: Mapping[str, Any]) -> float:
    state_definitions = config.get("state_definitions", {})
    if isinstance(state_definitions, Mapping):
        dense_config = state_definitions.get("dense", {})
        if isinstance(dense_config, Mapping):
            threshold = _number(dense_config.get("threshold_pct"))
            if threshold is not None:
                return threshold
    return 3.0


def _normalise_densities(values: Any) -> list[float] | None:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        return None
    tail = list(values)[-3:]
    if len(tail) != 3:
        return None
    densities = [_number(value) for value in tail]
    if any(value is None or value < 0 for value in densities):
        return None
    return [value for value in densities if value is not None]


def classify_p1_market_state(
    density_last_3_days: Sequence[float | None],
    spread_expanding: bool | None,
) -> str:
    """Classify P1's objective market state without evaluating conditions."""

    densities = _normalise_densities(density_last_3_days)
    if densities is None:
        return INSUFFICIENT_INFORMATION
    if all(value <= 3.0 for value in densities):
        return DENSE
    if spread_expanding is True:
        return DIVERGING
    return TRANSITION_ENTANGLED


def _check(
    check_id: str,
    kind: str,
    passed: bool | None,
    *,
    expression: str,
    observed: Any,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "type": kind,
        "passed": passed,
        "expression": expression,
        "observed": observed,
    }


def evaluate_dense_state(
    features: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the dense-state close-versus-MA-average condition."""

    del config  # The comparison itself has no configurable numeric threshold.
    close = _number(features.get("close"))
    averages = [
        _number(features.get(key)) for key in ("ma5", "ma10", "ma20", "ma60")
    ]
    if close is None or any(value is None for value in averages):
        check = _check(
            "close_above_ma_average",
            "REQUIRED",
            None,
            expression="close > (ma5 + ma10 + ma20 + ma60) / 4",
            observed={"close": close, "ma_average": None},
        )
        return {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [check],
            "reasons": ["缺少收盘价或最新四条均线，无法判断密集状态条件"],
            "derived_metrics": {"ma_average": None},
        }

    ma_average = sum(value for value in averages if value is not None) / 4.0
    passed = close > ma_average
    check = _check(
        "close_above_ma_average",
        "REQUIRED",
        passed,
        expression="close > (ma5 + ma10 + ma20 + ma60) / 4",
        observed={"close": close, "ma_average": ma_average},
    )
    return {
        "status": PASS if passed else FAIL,
        "checks": [check],
        "reasons": [] if passed else ["最新收盘价未严格高于四条均线的均价"],
        "derived_metrics": {"ma_average": ma_average},
    }


def evaluate_diverging_state(
    features: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate P1 required conditions and preferences in a diverging state."""

    del config
    close = _number(features.get("close"))
    ma5 = _number(features.get("ma5"))
    ma10 = _number(features.get("ma10"))
    ma20 = _number(features.get("ma20"))
    ma60 = _number(features.get("ma60"))
    slope20 = _number(features.get("s10_ma20"))
    slope60 = _number(features.get("s10_ma60"))

    order_passed = None if ma20 is None or ma60 is None else ma20 > ma60
    direction_passed = (
        None
        if slope20 is None or slope60 is None
        else not (slope20 < 0 and slope60 < 0)
    )
    short_order_passed = None if ma5 is None or ma10 is None else ma5 > ma10
    close_above_ma10 = None if close is None or ma10 is None else close > ma10

    checks = [
        _check(
            "mid_long_order",
            "REQUIRED",
            order_passed,
            expression="ma20 > ma60",
            observed={"ma20": ma20, "ma60": ma60},
        ),
        _check(
            "mid_long_not_clearly_bearish",
            "REQUIRED",
            direction_passed,
            expression="not (s10_ma20 < 0 and s10_ma60 < 0)",
            observed={"s10_ma20": slope20, "s10_ma60": slope60},
        ),
        _check(
            "short_order",
            "PREFERENCE",
            short_order_passed,
            expression="ma5 > ma10",
            observed={"ma5": ma5, "ma10": ma10},
        ),
        _check(
            "close_above_ma10",
            "PREFERENCE",
            close_above_ma10,
            expression="close > ma10",
            observed={"close": close, "ma10": ma10},
        ),
    ]

    required = [check for check in checks if check["type"] == "REQUIRED"]
    preferences = [check for check in checks if check["type"] == "PREFERENCE"]
    if any(check["passed"] is None for check in checks):
        status = INSUFFICIENT_INFORMATION
    elif any(check["passed"] is False for check in required):
        status = FAIL
    elif any(check["passed"] is False for check in preferences):
        status = CONDITIONAL_PASS
    else:
        status = PASS

    reasons: list[str] = []
    if order_passed is False:
        reasons.append("MA20 未严格高于 MA60")
    if direction_passed is False:
        reasons.append("MA20 与 MA60 的最近10日斜率均明确向下")
    if short_order_passed is False:
        reasons.append("偏好项未满足：MA5 未严格高于 MA10")
    if close_above_ma10 is False:
        reasons.append("偏好项未满足：最新收盘价未严格高于 MA10")
    if any(check["passed"] is None for check in checks):
        reasons.append("缺少发散状态判定所需的均线、斜率或收盘价")

    return {
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "derived_metrics": {},
    }


def evaluate_p1(
    price_features: Mapping[str, Any],
    *,
    spread_expanding: bool | None,
    rule_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate P1 from precomputed features and the P1 rule configuration."""

    if not isinstance(price_features, Mapping):
        raise TypeError("price_features must be a mapping")
    if not isinstance(rule_config, Mapping):
        raise TypeError("rule_config must be a mapping")
    if str(rule_config.get("id", "")).upper() != "P1":
        raise ValueError("rule_config must be the P1 configuration")
    if spread_expanding is not None and not isinstance(spread_expanding, bool):
        raise TypeError("spread_expanding must be True, False or None")

    features = _extract_features(price_features)
    densities = _normalise_densities(features["density_last_3_days"])
    threshold = _density_threshold(rule_config)

    if densities is None:
        market_state = INSUFFICIENT_INFORMATION
    elif all(value <= threshold for value in densities):
        market_state = DENSE
    elif spread_expanding is True:
        market_state = DIVERGING
    else:
        market_state = TRANSITION_ENTANGLED

    if market_state == DENSE:
        evaluation = evaluate_dense_state(features, rule_config)
    elif market_state == DIVERGING:
        evaluation = evaluate_diverging_state(features, rule_config)
    elif market_state == TRANSITION_ENTANGLED:
        evaluation = {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [],
            "reasons": ["均线既不密集，间距也未明确扩大，属于过渡/纠缠状态"],
            "derived_metrics": {},
        }
    else:
        evaluation = {
            "status": INSUFFICIENT_INFORMATION,
            "checks": [],
            "reasons": ["缺少最近连续3个交易日的有效均线密集度"],
            "derived_metrics": {},
        }

    metrics = {
        "close": features["close"],
        "ma5": features["ma5"],
        "ma10": features["ma10"],
        "ma20": features["ma20"],
        "ma60": features["ma60"],
        "s10_ma5": features["s10_ma5"],
        "s10_ma10": features["s10_ma10"],
        "s10_ma20": features["s10_ma20"],
        "s10_ma60": features["s10_ma60"],
        "density_last_3_days": densities,
        "density_threshold_pct": threshold,
        **evaluation["derived_metrics"],
    }
    return {
        "rule_id": "P1",
        "status": evaluation["status"],
        "market_state": market_state,
        "checks": evaluation["checks"],
        "metrics": metrics,
        "reasons": evaluation["reasons"],
    }
