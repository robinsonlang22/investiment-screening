"""Deterministic FastAPI pipeline for the investment screening engine."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from engine.adapter import AdapterError, adapt_mcp_data
from engine.aggregator import (
    build_evaluation_bundle,
    validate_evaluation_completeness,
)
from engine.f1_evaluator import evaluate_f1
from engine.margin_features import build_f1_features
from engine.p1_evaluator import evaluate_p1
from engine.p2_evaluator import evaluate_p2
from engine.price_features import (
    calculate_density_series,
    calculate_ma_slope,
    calculate_price_regression_features,
    calculate_rolling_clock_features,
    latest_moving_averages,
    moving_average,
)
from engine.validators import (
    validate_margin_history,
    validate_market_cap,
    validate_market_margin_history,
    validate_price_history,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "rules"
REQUIRED_RULES = ("P1", "P2", "F1")

app = FastAPI(
    title="Investment Rule Engine",
    version="1.0.0",
    description=(
        "Two-stage deterministic service: /prepare adapts and validates MCP "
        "data; /evaluate calculates features, evaluates P1/P2/F1 and aggregates."
    ),
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawData(StrictModel):
    price_history: Any | None = None
    margin_history: Any | None = None
    market_cap: Any | None = None
    market_margin_history: Any | None = None


class PrepareRequest(StrictModel):
    symbol: str
    raw_data: RawData
    adapter_options: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EvaluateRequest(StrictModel):
    symbol: str
    normalized_data: dict[str, Any]
    data_quality: dict[str, Any] = Field(default_factory=dict)
    spread_expanding: bool | None = None


@lru_cache(maxsize=4)
def _load_json(name: str) -> dict[str, Any]:
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def _rule(rule_id: str) -> dict[str, Any]:
    return deepcopy(_load_json(f"{rule_id.lower()}.json"))


def _rule_version() -> str:
    registry = _load_json("registry.json")
    return str(registry.get("rule_version", registry.get("schema_version", "unknown")))


def _adapter_options(
    options: dict[str, dict[str, Any]], dataset: str
) -> dict[str, Any]:
    value = options.get(dataset, {})
    return value if isinstance(value, dict) else {}


def _missing_entry(
    missing_fields: list[str],
    recommended_queries: list[str],
    field: str,
    query: str,
) -> None:
    if field not in missing_fields:
        missing_fields.append(field)
    if query not in recommended_queries:
        recommended_queries.append(query)


def _issue_codes(result: dict[str, Any]) -> list[str]:
    return [str(issue.get("code")) for issue in result.get("issues", [])]


def _build_price_features(price_rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(row["close"]) for row in price_rows]
    ma_series = {
        f"ma{window}": moving_average(closes, window)
        for window in (5, 10, 20, 60)
    }
    densities = calculate_density_series(price_rows, ma_series)
    return {
        "close": closes[-1],
        "price_rows": price_rows,
        "moving_averages": latest_moving_averages(closes),
        "ma_series": ma_series,
        "slopes": {
            key: calculate_ma_slope(series, 10)
            for key, series in ma_series.items()
        },
        "density_series": densities,
        "density_last_3_days": densities[-3:],
        "regression_features": calculate_price_regression_features(closes),
        "rolling_clock_results": calculate_rolling_clock_features(closes),
    }


def _prepare_data(request: PrepareRequest) -> dict[str, Any]:
    missing_fields: list[str] = []
    recommended_queries: list[str] = []
    warnings: list[str] = []
    normalized_data: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    adapter_errors: dict[str, str] = {}

    raw = request.raw_data
    options = request.adapter_options

    if raw.price_history is None:
        _missing_entry(
            missing_fields,
            recommended_queries,
            "price_history",
            "forward_adjusted_daily_price_history_120d",
        )
    else:
        try:
            adapted = adapt_mcp_data(
                raw.price_history,
                "price_history",
                **_adapter_options(options, "price_history"),
            )
            result = validate_price_history(
                adapted,
                minimum_observations=120,
                required_adjustment="forward",
            )
            validation["price_history"] = result
            if result["status"] == "VALID":
                normalized_data["price_rows"] = result["valid_rows"]
            else:
                _missing_entry(
                    missing_fields,
                    recommended_queries,
                    "price_history",
                    "forward_adjusted_daily_price_history_120d",
                )
        except (AdapterError, ValueError) as exc:
            adapter_errors["price_history"] = str(exc)
            _missing_entry(
                missing_fields,
                recommended_queries,
                "price_history",
                "forward_adjusted_daily_price_history_120d",
            )

    if raw.margin_history is None:
        _missing_entry(
            missing_fields,
            recommended_queries,
            "margin_history",
            "daily_margin_balance_21d",
        )
    else:
        try:
            adapted = adapt_mcp_data(
                raw.margin_history,
                "margin_history",
                **_adapter_options(options, "margin_history"),
            )
            result = validate_margin_history(adapted, minimum_observations=21)
            validation["margin_history"] = result
            if result["status"] == "VALID":
                normalized_data["margin_rows"] = result["valid_rows"]
            else:
                _missing_entry(
                    missing_fields,
                    recommended_queries,
                    "margin_history",
                    "daily_margin_balance_21d",
                )
        except (AdapterError, ValueError) as exc:
            adapter_errors["margin_history"] = str(exc)
            _missing_entry(
                missing_fields,
                recommended_queries,
                "margin_history",
                "daily_margin_balance_21d",
            )

    margin_rows = normalized_data.get("margin_rows", [])
    target_date = margin_rows[-1]["date"] if margin_rows else None
    if raw.market_cap is None:
        _missing_entry(
            missing_fields,
            recommended_queries,
            "free_float_market_cap",
            "free_float_market_cap_near_margin_date",
        )
    elif target_date is None:
        _missing_entry(
            missing_fields,
            recommended_queries,
            "free_float_market_cap",
            "free_float_market_cap_near_margin_date",
        )
    else:
        try:
            adapted = adapt_mcp_data(
                raw.market_cap,
                "market_cap",
                **_adapter_options(options, "market_cap"),
            )
            result = validate_market_cap(adapted, target_date)
            validation["market_cap"] = result
            selected = result.get("selected_row")
            if result["status"] == "VALID" and selected:
                normalized_data["free_float_market_cap"] = selected[
                    "free_float_market_cap"
                ]
                normalized_data["free_float_market_cap_row"] = selected
            else:
                _missing_entry(
                    missing_fields,
                    recommended_queries,
                    "free_float_market_cap",
                    "free_float_market_cap_near_margin_date",
                )
        except (AdapterError, ValueError) as exc:
            adapter_errors["market_cap"] = str(exc)
            _missing_entry(
                missing_fields,
                recommended_queries,
                "free_float_market_cap",
                "free_float_market_cap_near_margin_date",
            )

    if raw.market_margin_history is None:
        warnings.append(
            "未提供全市场两融余额；若F1触发大幅流出否决，将无法判断是否需要系统性去杠杆人工复核"
        )
        normalized_data["market_margin_rows"] = None
    else:
        try:
            adapted = adapt_mcp_data(
                raw.market_margin_history,
                "market_margin_history",
                **_adapter_options(options, "market_margin_history"),
            )
            result = validate_market_margin_history(adapted)
            validation["market_margin_history"] = result
            if result["status"] == "VALID":
                normalized_data["market_margin_rows"] = result["valid_rows"]
            else:
                normalized_data["market_margin_rows"] = None
                warnings.append(
                    "全市场两融余额未通过验证，系统性去杠杆人工复核能力不可用"
                )
        except (AdapterError, ValueError) as exc:
            adapter_errors["market_margin_history"] = str(exc)
            normalized_data["market_margin_rows"] = None
            warnings.append(
                "全市场两融余额适配失败，系统性去杠杆人工复核能力不可用"
            )

    valid = not missing_fields
    data_quality = {
        "status": "VALID" if valid else "INVALID",
        "validation": validation,
        "adapter_errors": adapter_errors,
        "issue_codes": {
            name: _issue_codes(result)
            for name, result in validation.items()
        },
    }
    return {
        "symbol": request.symbol,
        "valid": valid,
        "retryable": not valid,
        "normalized_data": normalized_data,
        "missing_fields": missing_fields,
        "recommended_queries": recommended_queries,
        "warnings": warnings,
        "data_quality": data_quality,
    }


def _validate_normalized_data(data: dict[str, Any]) -> dict[str, Any]:
    missing = [
        field
        for field in ("price_rows", "margin_rows", "free_float_market_cap")
        if data.get(field) is None
    ]
    if missing:
        raise ValueError(
            "normalized_data is incomplete; call /prepare first. Missing: "
            + ", ".join(missing)
        )

    price_result = validate_price_history(
        {
            "rows": data["price_rows"],
            "adjustment": "forward",
        },
        minimum_observations=120,
        required_adjustment="forward",
    )
    margin_result = validate_margin_history(
        {
            "rows": data["margin_rows"],
            "unit": "CNY",
            "metric": "margin_balance",
        },
        minimum_observations=21,
    )
    if price_result["status"] != "VALID" or margin_result["status"] != "VALID":
        raise ValueError("normalized_data failed validation; call /prepare again")
    market_cap = float(data["free_float_market_cap"])
    if market_cap <= 0:
        raise ValueError("free_float_market_cap must be positive")
    return {
        "price_rows": price_result["valid_rows"],
        "margin_rows": margin_result["valid_rows"],
        "free_float_market_cap": market_cap,
        "market_margin_rows": data.get("market_margin_rows"),
    }


@app.exception_handler(AdapterError)
async def adapter_error_handler(_, exc: AdapterError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "ADAPTER_ERROR", "detail": str(exc)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "INVALID_ENGINE_INPUT", "detail": str(exc)},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "investment-rule-engine"}


@app.post("/prepare")
async def prepare(request: PrepareRequest) -> dict[str, Any]:
    """Adapter -> Validator. Returns gaps for the Research Agent to refill."""

    return _prepare_data(request)


@app.post("/evaluate")
async def evaluate(request: EvaluateRequest) -> dict[str, Any]:
    """Validated data -> Features -> P1/P2/F1 -> Aggregator."""

    data = _validate_normalized_data(request.normalized_data)
    price_features = _build_price_features(data["price_rows"])
    margin_features = build_f1_features(
        data["margin_rows"],
        data["free_float_market_cap"],
        data["market_margin_rows"],
    )

    p1 = evaluate_p1(
        price_features,
        spread_expanding=request.spread_expanding,
        rule_config=_rule("P1"),
    )
    p2 = evaluate_p2(price_features, p1, _rule("P2"))
    f1 = evaluate_f1(margin_features, _rule("F1"))
    rule_results = [p1, p2, f1]

    completeness = validate_evaluation_completeness(
        REQUIRED_RULES,
        rule_results,
    )
    if not completeness["complete"]:
        raise ValueError(f"internal evaluation incomplete: {completeness}")

    data_quality = deepcopy(request.data_quality)
    data_quality["evaluation_completeness"] = completeness
    bundle = build_evaluation_bundle(
        request.symbol,
        rule_results,
        data_quality,
    )
    decision_mapping = {
        "COMPLIANT": "pass",
        "CONDITIONAL": "conditional_pass",
        "NOT_COMPLIANT": "fail",
        "INFORMATION_INSUFFICIENT": "information_insufficient",
    }
    return {
        "symbol": request.symbol,
        "decision": decision_mapping[bundle["overall_status"]],
        "overall_status": bundle["overall_status"],
        "rule_version": _rule_version(),
        "results": {"p1": p1, "p2": p2, "f1": f1},
        "data_quality": bundle["data_quality"],
        "human_review": bundle["human_review"],
        "report_constraints": bundle["report_constraints"],
    }
