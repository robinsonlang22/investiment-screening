"""Aggregate completed rule evaluations without calculating any feature."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


COMPLIANT = "COMPLIANT"
CONDITIONAL = "CONDITIONAL"
NOT_COMPLIANT = "NOT_COMPLIANT"
INFORMATION_INSUFFICIENT = "INFORMATION_INSUFFICIENT"

_PASS_STATUSES = {"PASS", "pass"}
_CONDITIONAL_STATUSES = {
    "CONDITIONAL",
    "CONDITIONAL_PASS",
    "conditional",
    "conditional_pass",
}
_FAIL_STATUSES = {"FAIL", "fail"}
_INSUFFICIENT_STATUSES = {
    "INSUFFICIENT",
    "INSUFFICIENT_INFORMATION",
    "INFORMATION_INSUFFICIENT",
    "insufficient_information",
}


def _as_results(rule_results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not isinstance(rule_results, Sequence) or isinstance(
        rule_results, (str, bytes, bytearray)
    ):
        raise TypeError("rule_results must be a sequence of mappings")
    results = list(rule_results)
    if not results:
        raise ValueError("rule_results must not be empty")
    if any(not isinstance(result, Mapping) for result in results):
        raise TypeError("each rule result must be a mapping")

    ids = [str(result.get("rule_id", "")).strip() for result in results]
    if any(not rule_id for rule_id in ids):
        raise ValueError("each rule result must contain rule_id")
    if len(ids) != len(set(ids)):
        raise ValueError("rule_results contains duplicate rule IDs")
    return results


def _normalised_status(result: Mapping[str, Any]) -> str:
    raw = result.get("status")
    if raw in _PASS_STATUSES:
        return "PASS"
    if raw in _CONDITIONAL_STATUSES:
        return "CONDITIONAL"
    if raw in _FAIL_STATUSES:
        return "FAIL"
    if raw in _INSUFFICIENT_STATUSES:
        return "INSUFFICIENT"
    raise ValueError(
        f"unsupported status for {result.get('rule_id', '<unknown>')}: {raw!r}"
    )


def aggregate_rule_results(
    rule_results: Sequence[Mapping[str, Any]],
) -> str:
    """Aggregate results using veto, failure, insufficiency, conditional order."""

    results = _as_results(rule_results)
    if any(result.get("hard_veto") is True for result in results):
        return NOT_COMPLIANT

    required_results = [
        result for result in results if result.get("required", True) is not False
    ]
    if any(_normalised_status(result) == "FAIL" for result in required_results):
        return NOT_COMPLIANT
    if any(
        _normalised_status(result) == "INSUFFICIENT"
        for result in required_results
    ):
        return INFORMATION_INSUFFICIENT
    if any(_normalised_status(result) == "CONDITIONAL" for result in results):
        return CONDITIONAL
    return COMPLIANT


def _rule_id(item: Any) -> str | None:
    if isinstance(item, Mapping):
        value = item.get("rule_id", item.get("id"))
    else:
        value = item
    if value is None or not str(value).strip():
        return None
    return str(value).strip().upper()


def validate_evaluation_completeness(
    applicable_rules: Sequence[Any],
    completed_results: Sequence[Any],
) -> dict[str, Any]:
    """Report missing, duplicate and unexpected rule evaluations."""

    applicable_ids = [_rule_id(item) for item in applicable_rules]
    completed_ids = [_rule_id(item) for item in completed_results]
    if any(rule_id is None for rule_id in applicable_ids):
        raise ValueError("every applicable rule must have an ID")
    if any(rule_id is None for rule_id in completed_ids):
        raise ValueError("every completed result must have a rule ID")

    applicable = [rule_id for rule_id in applicable_ids if rule_id is not None]
    completed = [rule_id for rule_id in completed_ids if rule_id is not None]
    duplicates = sorted(
        {rule_id for rule_id in completed if completed.count(rule_id) > 1}
    )
    missing = [rule_id for rule_id in applicable if rule_id not in completed]
    unexpected = sorted(
        {rule_id for rule_id in completed if rule_id not in applicable}
    )
    complete = not missing and not duplicates
    return {
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "complete": complete,
        "applicable_rules": applicable,
        "completed_rules": completed,
        "missing_rules": missing,
        "duplicate_rules": duplicates,
        "unexpected_rules": unexpected,
        "may_generate_complete_conclusion": complete,
    }


def _f1_exception_used(result: Mapping[str, Any]) -> bool:
    if str(result.get("rule_id", "")).upper() != "F1":
        return False
    if result.get("manual_override_applied") is True:
        return True
    review = result.get("human_review")
    return (
        isinstance(review, Mapping)
        and review.get("completed") is True
        and review.get("confirmed_passive_adjustment") is True
    )


def enforce_exception_cap(
    rule_results: Sequence[Mapping[str, Any]],
    overall_status: str,
) -> str:
    """Cap an F1 human-exception outcome at CONDITIONAL."""

    results = _as_results(rule_results)
    allowed = {
        COMPLIANT,
        CONDITIONAL,
        NOT_COMPLIANT,
        INFORMATION_INSUFFICIENT,
    }
    if overall_status not in allowed:
        raise ValueError(f"unsupported overall_status: {overall_status!r}")
    if any(_f1_exception_used(result) for result in results):
        if overall_status == COMPLIANT:
            return CONDITIONAL
    return overall_status


def _collect_human_review(
    rule_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for result in rule_results:
        review = result.get("human_review")
        if isinstance(review, Mapping) and (
            review.get("required")
            or review.get("completed")
            or review.get("matched_market_events")
        ):
            reviews[str(result["rule_id"])] = deepcopy(dict(review))
    return {
        "required": any(
            isinstance(review, Mapping) and review.get("required") is True
            for review in reviews.values()
        ),
        "reviews_by_rule": reviews,
    }


def build_evaluation_bundle(
    symbol: str,
    rule_results: Sequence[Mapping[str, Any]],
    data_quality: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the immutable-status input contract for the report LLM."""

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(data_quality, Mapping):
        raise TypeError("data_quality must be a mapping")

    results = _as_results(rule_results)
    overall_status = aggregate_rule_results(results)
    overall_status = enforce_exception_cap(results, overall_status)
    return {
        "symbol": symbol.strip(),
        "overall_status": overall_status,
        "rule_results": deepcopy([dict(result) for result in results]),
        "data_quality": deepcopy(dict(data_quality)),
        "human_review": _collect_human_review(results),
        "report_constraints": {
            "may_recalculate": False,
            "may_override_status": False,
        },
    }
