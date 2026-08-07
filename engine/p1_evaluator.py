"""Evaluate P1 from precomputed core moving-average features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


PASS = "PASS"
CONDITIONAL_PASS = "CONDITIONAL_PASS"
FAIL = "FAIL"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"

DENSE = "DENSE"
CORE_BULLISH_ALIGNMENT = "CORE_BULLISH_ALIGNMENT"
CORE_UPWARD = "CORE_UPWARD"
CORE_BEARISH = "CORE_BEARISH"
TRANSITION_ENTANGLED = "TRANSITION_ENTANGLED"


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


def _density_threshold(config: Mapping[str, Any]) -> float:
    definitions = config.get("state_definitions", {})
    dense = definitions.get("dense", {}) if isinstance(definitions, Mapping) else {}
    value = _number(dense.get("threshold_pct")) if isinstance(dense, Mapping) else None
    return 2.0 if value is None else value


def _extract_features(price_features: Mapping[str, Any]) -> dict[str, Any]:
    mas = price_features.get("moving_averages", price_features)
    slopes = price_features.get("slopes", price_features)
    if not isinstance(mas, Mapping):
        mas = price_features
    if not isinstance(slopes, Mapping):
        slopes = price_features
    density_series = price_features.get("density_series")
    latest_density = None
    if isinstance(density_series, Sequence) and not isinstance(
        density_series, (str, bytes, bytearray)
    ):
        valid = [row for row in density_series if isinstance(row, Mapping)]
        latest_density = valid[-1] if valid else None
    if latest_density is None and isinstance(price_features.get("density"), Mapping):
        latest_density = price_features["density"]
    return {
        "close": _number(_value(price_features, "close", "latest_close")),
        **{
            f"ma{window}": _number(_value(mas, f"ma{window}", f"MA{window}"))
            for window in (5, 10, 20, 60)
        },
        **{
            f"s10_ma{window}": _number(
                _value(slopes, f"ma{window}", f"MA{window}", f"s10_ma{window}")
            )
            for window in (5, 10, 20, 60)
        },
        "density": latest_density,
    }


def classify_core_state(features: Mapping[str, Any]) -> str:
    slopes = [features.get(f"s10_ma{window}") for window in (5, 10, 20)]
    ma5, ma10, ma20 = (features.get(f"ma{window}") for window in (5, 10, 20))
    if any(_number(value) is None for value in (*slopes, ma5, ma10, ma20)):
        return INSUFFICIENT_INFORMATION
    all_up = all(float(value) > 0 for value in slopes)
    all_down = all(float(value) < 0 for value in slopes)
    if all_up and float(ma5) > float(ma10) > float(ma20):
        return CORE_BULLISH_ALIGNMENT
    if all_up and float(ma10) > float(ma20):
        return CORE_UPWARD
    if all_down or float(ma10) <= float(ma20):
        return CORE_BEARISH
    return TRANSITION_ENTANGLED


def evaluate_p1(
    price_features: Mapping[str, Any],
    *,
    rule_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate P1 without any client-supplied derived-state flag."""

    if not isinstance(price_features, Mapping):
        raise TypeError("price_features must be a mapping")
    if not isinstance(rule_config, Mapping) or str(rule_config.get("id", "")).upper() != "P1":
        raise ValueError("rule_config must be the P1 configuration")

    features = _extract_features(price_features)
    density = features["density"]
    mean = _number(density.get("mean")) if isinstance(density, Mapping) else None
    value_range = _number(density.get("range")) if isinstance(density, Mapping) else None
    relative_range = (
        _number(density.get("relative_range_pct"))
        if isinstance(density, Mapping)
        else None
    )
    threshold = _density_threshold(rule_config)
    is_dense = None if relative_range is None else relative_range <= threshold
    core_state = classify_core_state(features)

    required_up = None if core_state == INSUFFICIENT_INFORMATION else core_state in {
        CORE_BULLISH_ALIGNMENT,
        CORE_UPWARD,
    }
    bullish_alignment = (
        None if core_state == INSUFFICIENT_INFORMATION else core_state == CORE_BULLISH_ALIGNMENT
    )
    checks = [
        {
            "id": "core_upward",
            "type": "REQUIRED",
            "passed": required_up,
            "expression": "S10_MA5 > 0 and S10_MA10 > 0 and S10_MA20 > 0 and MA10 > MA20",
        },
        {
            "id": "core_bullish_alignment",
            "type": "PREFERENCE",
            "passed": bullish_alignment,
            "expression": "MA5 > MA10 > MA20 and all core slopes > 0",
        },
    ]
    if required_up is None:
        status = INSUFFICIENT_INFORMATION
        reasons = ["缺少核心均线或其10日斜率，无法判断P1"]
    elif not required_up:
        status = FAIL
        reasons = ["未满足核心向上状态"]
    elif not bullish_alignment:
        status = CONDITIONAL_PASS
        reasons = ["核心均线向上，但尚未形成MA5 > MA10 > MA20的完整多头排列"]
    else:
        status = PASS
        reasons = []

    ma20 = features["ma20"]
    ma60 = features["ma60"]
    slope60 = features["s10_ma60"]
    background_synchronized = (
        None
        if None in (ma20, ma60, slope60)
        else ma20 > ma60 and slope60 > 0
    )
    background_label = (
        "中长期背景同步向上" if background_synchronized else "中长期背景尚未同步"
    ) if background_synchronized is not None else "中长期背景信息不足"

    return {
        "rule_id": "P1",
        "status": status,
        "market_state": DENSE if is_dense is True else core_state,
        "core_state": core_state,
        "dense": is_dense,
        "checks": checks,
        "metrics": {
            **{key: features[key] for key in (
                "close", "ma5", "ma10", "ma20", "ma60",
                "s10_ma5", "s10_ma10", "s10_ma20", "s10_ma60",
            )},
            "core_ma_mean": mean,
            "core_ma_range": value_range,
            "core_relative_range_pct": relative_range,
            "density_threshold_pct": threshold,
        },
        "ma60_background": {
            "synchronized": background_synchronized,
            "label": background_label,
        },
        "reasons": reasons,
    }
