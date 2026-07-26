"""Evaluate F1 thresholds, vetoes and tightly bounded human review."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isclose, isfinite
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


def _threshold_mapping(
    config: Mapping[str, Any],
    tier_id: str,
) -> dict[str, float]:
    tiers = config.get("leverage_tiers")
    if not isinstance(tiers, Sequence):
        raise ValueError("F1 config is missing leverage_tiers")
    for tier in tiers:
        if not isinstance(tier, Mapping) or tier.get("id") != tier_id:
            continue
        raw = tier.get("thresholds")
        if not isinstance(raw, Mapping):
            break
        names = {
            "c20_min_pct": "C20_min_inclusive_pct",
            "gb_min_daily_pct": "GB_min_inclusive_pct_per_day",
            "f1_min_exclusive_pct": "F1_min_strictly_greater_than_pct",
            "f3_min_exclusive_pct": "F3_min_strictly_greater_than_pct",
        }
        result: dict[str, float] = {}
        for output_name, config_name in names.items():
            value = _number(raw.get(config_name))
            if value is None:
                raise ValueError(f"F1 config threshold is missing: {config_name}")
            result[output_name] = value
        return result
    raise ValueError(f"F1 config tier not found: {tier_id}")


def choose_threshold_tier(
    leverage_ratio_pct: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose RELAXED for LR <= 5%; otherwise choose STRICT."""

    leverage = _number(leverage_ratio_pct)
    if leverage is None or leverage < 0:
        raise ValueError("leverage_ratio_pct must be a finite non-negative number")
    tier = "RELAXED" if leverage <= 5.0 else "STRICT"
    config_tier_id = "relaxed_low_leverage" if tier == "RELAXED" else "strict"
    return {
        "tier": tier,
        "high_crowding": leverage > 10.0,
        "leverage_ratio_pct": leverage,
        "thresholds": _threshold_mapping(config, config_tier_id),
    }


def _greater_than_or_equal(value: float, threshold: float) -> bool:
    return value > threshold or isclose(value, threshold, abs_tol=1e-12)


def _less_than_or_equal(value: float, threshold: float) -> bool:
    return value < threshold or isclose(value, threshold, abs_tol=1e-12)


def evaluate_trend_conditions(
    c20_pct: float,
    gb_daily_pct: float,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the two inclusive financing-balance trend conditions."""

    c20 = _number(c20_pct)
    gb = _number(gb_daily_pct)
    c20_min = _number(thresholds.get("c20_min_pct"))
    gb_min = _number(thresholds.get("gb_min_daily_pct"))
    if None in (c20, gb, c20_min, gb_min):
        return {
            "complete": False,
            "all_passed": None,
            "failed_count": None,
            "checks": [],
        }
    assert c20 is not None and gb is not None
    assert c20_min is not None and gb_min is not None
    checks = [
        {
            "id": "c20_stable",
            "passed": _greater_than_or_equal(c20, c20_min),
            "observed": c20,
            "threshold": c20_min,
            "operator": ">=",
        },
        {
            "id": "gb_stable",
            "passed": _greater_than_or_equal(gb, gb_min),
            "observed": gb,
            "threshold": gb_min,
            "operator": ">=",
        },
    ]
    failed_count = sum(check["passed"] is False for check in checks)
    return {
        "complete": True,
        "all_passed": failed_count == 0,
        "failed_count": failed_count,
        "checks": checks,
    }


def evaluate_outflow_vetoes(
    f1_min_pct: float,
    f3_min_pct: float,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate inclusive single-day and three-day outflow veto boundaries."""

    f1_min = _number(f1_min_pct)
    f3_min = _number(f3_min_pct)
    f1_limit = _number(thresholds.get("f1_min_exclusive_pct"))
    f3_limit = _number(thresholds.get("f3_min_exclusive_pct"))
    if None in (f1_min, f3_min, f1_limit, f3_limit):
        return {
            "complete": False,
            "any_veto": None,
            "triggered_vetoes": [],
            "checks": [],
        }
    assert f1_min is not None and f3_min is not None
    assert f1_limit is not None and f3_limit is not None
    checks = [
        {
            "id": "single_day_outflow",
            "veto_triggered": _less_than_or_equal(f1_min, f1_limit),
            "observed": f1_min,
            "threshold": f1_limit,
            "operator": "<=",
        },
        {
            "id": "three_day_outflow",
            "veto_triggered": _less_than_or_equal(f3_min, f3_limit),
            "observed": f3_min,
            "threshold": f3_limit,
            "operator": "<=",
        },
    ]
    triggered = [
        check["id"] for check in checks if check["veto_triggered"] is True
    ]
    return {
        "complete": True,
        "any_veto": bool(triggered),
        "triggered_vetoes": triggered,
        "checks": checks,
    }


def _veto_end_dates(veto_dates: Sequence[Any]) -> set[str]:
    result: set[str] = set()
    for item in veto_dates:
        if isinstance(item, Mapping):
            value = item.get("end_date", item.get("date"))
        else:
            value = item
        if value is not None:
            result.add(str(value))
    return result


def detect_human_review_trigger(
    f1_status: str,
    veto_dates: Sequence[Any],
    market_deleveraging_features: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Detect date overlap between an outflow FAIL and market deleveraging."""

    dates = _veto_end_dates(veto_dates)
    matched_events: list[dict[str, Any]] = []
    if f1_status == FAIL and dates and market_deleveraging_features:
        for event in market_deleveraging_features:
            if not isinstance(event, Mapping):
                continue
            end_date = event.get("end_date", event.get("date"))
            change = _number(event.get("change_pct"))
            flag = event.get("below_minus_2_5_pct")
            objectively_below = (
                change is not None
                and change < -2.5
                and not isclose(change, -2.5, abs_tol=1e-12)
            )
            is_systemic = flag is True or objectively_below
            if end_date is not None and str(end_date) in dates and is_systemic:
                matched_events.append(dict(event))
    return {
        "required": bool(matched_events),
        "veto_dates": sorted(dates),
        "matched_market_events": matched_events,
    }


def apply_human_review(
    f1_result: Mapping[str, Any],
    confirmed_passive_adjustment: bool,
    reviewer: str | None,
    rationale: str | None,
) -> dict[str, Any]:
    """Apply a documented review, capped at FAIL -> CONDITIONAL_PASS."""

    if not isinstance(f1_result, Mapping):
        raise TypeError("f1_result must be a mapping")
    if not isinstance(confirmed_passive_adjustment, bool):
        raise TypeError("confirmed_passive_adjustment must be a boolean")

    result = deepcopy(dict(f1_result))
    review = result.get("human_review", {})
    review_required = isinstance(review, Mapping) and review.get("required") is True
    original_status = result.get("status")

    if confirmed_passive_adjustment:
        if original_status != FAIL:
            raise ValueError("human review can only reclassify an F1 FAIL")
        if not review_required:
            raise ValueError("human review is not eligible for this F1 result")
        if not reviewer or not str(reviewer).strip():
            raise ValueError("reviewer is required for a passive-adjustment finding")
        if not rationale or not str(rationale).strip():
            raise ValueError("rationale is required for a passive-adjustment finding")
        result["status"] = CONDITIONAL_PASS

    result["human_review"] = {
        **(dict(review) if isinstance(review, Mapping) else {}),
        "completed": True,
        "confirmed_passive_adjustment": confirmed_passive_adjustment,
        "reviewer": reviewer,
        "rationale": rationale,
        "original_status": original_status,
        "resulting_status": result.get("status"),
        "maximum_allowed_status": CONDITIONAL_PASS,
    }
    if confirmed_passive_adjustment:
        result.setdefault("reasons", []).append(
            "人工复核确认主要属于市场或板块被动跟随调整；F1最多上调为有条件通过"
        )
        result["hard_veto"] = False
        result["manual_override_applied"] = True
    else:
        result["manual_override_applied"] = False
    return result


def _worst_feature(
    margin_features: Mapping[str, Any],
    key: str,
) -> tuple[float | None, Mapping[str, Any] | None]:
    value = margin_features.get(key)
    if not isinstance(value, Mapping):
        return None, None
    return _number(value.get("change_pct")), value


def evaluate_f1(
    margin_features: Mapping[str, Any],
    rule_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate F1 from the objective feature package."""

    if not isinstance(margin_features, Mapping):
        raise TypeError("margin_features must be a mapping")
    if not isinstance(rule_config, Mapping):
        raise TypeError("rule_config must be a mapping")
    if str(rule_config.get("id", "")).upper() != "F1":
        raise ValueError("rule_config must be the F1 configuration")

    leverage = _number(margin_features.get("leverage_ratio_pct"))
    c20 = _number(margin_features.get("c20_pct"))
    trend_feature = margin_features.get("margin_trend")
    gb = (
        _number(trend_feature.get("gb_daily_pct"))
        if isinstance(trend_feature, Mapping)
        else None
    )
    f1_min, worst_daily = _worst_feature(
        margin_features, "worst_daily_outflow"
    )
    f3_min, worst_three_day = _worst_feature(
        margin_features, "worst_three_day_outflow"
    )
    missing = [
        name
        for name, value in (
            ("leverage_ratio_pct", leverage),
            ("c20_pct", c20),
            ("gb_daily_pct", gb),
            ("f1_min_pct", f1_min),
            ("f3_min_pct", f3_min),
        )
        if value is None
    ]
    base_metrics = {
        "leverage_ratio_pct": leverage,
        "c20_pct": c20,
        "gb_daily_pct": gb,
        "f1_min_pct": f1_min,
        "f3_min_pct": f3_min,
        "worst_daily_outflow": worst_daily,
        "worst_three_day_outflow": worst_three_day,
    }
    if missing:
        return {
            "rule_id": "F1",
            "status": INSUFFICIENT_INFORMATION,
            "tier": None,
            "high_crowding": None,
            "checks": [],
            "metrics": base_metrics,
            "hard_veto": False,
            "triggered_vetoes": [],
            "human_review": {"required": False, "matched_market_events": []},
            "reasons": [f"缺少F1特征：{', '.join(missing)}"],
        }

    assert leverage is not None and c20 is not None and gb is not None
    assert f1_min is not None and f3_min is not None
    tier = choose_threshold_tier(leverage, rule_config)
    trend = evaluate_trend_conditions(c20, gb, tier["thresholds"])
    vetoes = evaluate_outflow_vetoes(f1_min, f3_min, tier["thresholds"])

    if vetoes["any_veto"]:
        status = FAIL
    elif trend["failed_count"] == 2:
        status = FAIL
    elif trend["failed_count"] == 1:
        status = CONDITIONAL_PASS
    else:
        status = PASS

    veto_dates: list[Any] = []
    if "single_day_outflow" in vetoes["triggered_vetoes"] and worst_daily:
        veto_dates.append(worst_daily)
    if "three_day_outflow" in vetoes["triggered_vetoes"] and worst_three_day:
        veto_dates.append(worst_three_day)
    market_features = margin_features.get("market_deleveraging")
    human_review = detect_human_review_trigger(
        status,
        veto_dates,
        market_features if isinstance(market_features, Sequence) else None,
    )

    reasons: list[str] = []
    if trend["failed_count"]:
        failed_ids = [
            check["id"] for check in trend["checks"] if not check["passed"]
        ]
        reasons.append(f"融资余额趋势条件未满足：{', '.join(failed_ids)}")
    if vetoes["triggered_vetoes"]:
        reasons.append(
            "触发大幅流出否决：" + ", ".join(vetoes["triggered_vetoes"])
        )
    if human_review["required"]:
        reasons.append("否决异常日与全市场系统性去杠杆日重合，需人工复核")

    return {
        "rule_id": "F1",
        "status": status,
        "tier": tier["tier"],
        "high_crowding": tier["high_crowding"],
        "thresholds": tier["thresholds"],
        "checks": trend["checks"] + vetoes["checks"],
        "metrics": base_metrics,
        "hard_veto": bool(vetoes["any_veto"]),
        "triggered_vetoes": vetoes["triggered_vetoes"],
        "human_review": human_review,
        "reasons": reasons,
    }
