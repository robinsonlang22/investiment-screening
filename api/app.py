"""Single-endpoint deterministic investment rule engine."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Callable

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
    version="2.0.0",
    description="Parse available market data and immediately evaluate P1/P2/F1.",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawData(StrictModel):
    price_history: Any | None = None
    margin_history: Any | None = None
    market_cap: Any | None = None
    market_margin_history: Any | None = None


class EvaluateRequest(StrictModel):
    symbol: str
    raw_data: RawData
    adapter_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    spread_expanding: bool | None = None


@lru_cache(maxsize=4)
def _load_json(name: str) -> dict[str, Any]:
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def _rule(rule_id: str) -> dict[str, Any]:
    return deepcopy(_load_json(f"{rule_id.lower()}.json"))


def _rule_version() -> str:
    registry = _load_json("registry.json")
    return str(registry.get("rule_version", registry.get("schema_version", "unknown")))


def _options(request: EvaluateRequest, dataset: str) -> dict[str, Any]:
    value = request.adapter_options.get(dataset, {})
    return value if isinstance(value, dict) else {}


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


def _parse_raw_data(request: EvaluateRequest) -> dict[str, Any]:
    """Adapt and minimally validate each independent rule input."""

    parsed: dict[str, Any] = {
        "price_rows": None,
        "margin_rows": None,
        "free_float_market_cap": None,
        "market_margin_rows": None,
    }
    diagnostics: dict[str, Any] = {
        "adapter_errors": {},
        "validation_issues": {},
        "adapter_warnings": {},
    }

    def parse_dataset(
        name: str,
        payload: Any,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any] | None:
        if payload is None:
            return None
        try:
            adapted = adapt_mcp_data(
                payload,
                name,
                **_options(request, name),
            )
            if adapted.get("warnings"):
                diagnostics["adapter_warnings"][name] = adapted["warnings"]
            validation = validator(adapted)
            if validation["status"] != "VALID":
                diagnostics["validation_issues"][name] = validation.get(
                    "issues", []
                )
                return None
            return validation
        except (AdapterError, ValueError) as exc:
            diagnostics["adapter_errors"][name] = str(exc)
            return None

    price = parse_dataset(
        "price_history",
        request.raw_data.price_history,
        lambda adapted: validate_price_history(
            adapted,
            minimum_observations=120,
            required_adjustment="forward",
        ),
    )
    if price:
        parsed["price_rows"] = price["valid_rows"]

    margin = parse_dataset(
        "margin_history",
        request.raw_data.margin_history,
        lambda adapted: validate_margin_history(
            adapted,
            minimum_observations=21,
        ),
    )
    if margin:
        parsed["margin_rows"] = margin["valid_rows"]

    if parsed["margin_rows"] and request.raw_data.market_cap is not None:
        target_date = parsed["margin_rows"][-1]["date"]
        market_cap = parse_dataset(
            "market_cap",
            request.raw_data.market_cap,
            lambda adapted: validate_market_cap(adapted, target_date),
        )
        if market_cap and market_cap.get("selected_row"):
            parsed["free_float_market_cap"] = market_cap["selected_row"][
                "free_float_market_cap"
            ]

    market_margin = parse_dataset(
        "market_margin_history",
        request.raw_data.market_margin_history,
        validate_market_margin_history,
    )
    if market_margin:
        parsed["market_margin_rows"] = market_margin["valid_rows"]

    unavailable = []
    if parsed["price_rows"] is None:
        unavailable.append("price_history")
    if parsed["margin_rows"] is None:
        unavailable.append("margin_history")
    if parsed["free_float_market_cap"] is None:
        unavailable.append("free_float_market_cap")
    if parsed["market_margin_rows"] is None:
        unavailable.append("market_margin_history")
    diagnostics["unavailable_inputs"] = unavailable
    diagnostics["status"] = "VALID" if not unavailable else "PARTIAL"
    return {**parsed, "diagnostics": diagnostics}


def _insufficient_rule(
    rule_id: str,
    missing_inputs: list[str],
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "required": True,
        "status": "INSUFFICIENT_INFORMATION",
        "hard_veto": False,
        "checks": [],
        "derived_metrics": {},
        "missing_inputs": missing_inputs,
        "reasons": ["缺少可安全计算该原则的数据；其他原则已继续计算"],
    }


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "INVALID_ENGINE_INPUT", "detail": str(exc)},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "investment-rule-engine"}


@app.post("/evaluate")
async def evaluate(request: EvaluateRequest) -> dict[str, Any]:
    """Parse raw data and calculate every principle that has enough input."""

    data = _parse_raw_data(request)

    if data["price_rows"] is not None:
        price_features = _build_price_features(data["price_rows"])
        p1 = evaluate_p1(
            price_features,
            spread_expanding=request.spread_expanding,
            rule_config=_rule("P1"),
        )
        p2 = evaluate_p2(price_features, p1, _rule("P2"))
    else:
        p1 = _insufficient_rule("P1", ["price_history"])
        p2 = _insufficient_rule("P2", ["price_history"])

    if (
        data["margin_rows"] is not None
        and data["free_float_market_cap"] is not None
    ):
        margin_features = build_f1_features(
            data["margin_rows"],
            data["free_float_market_cap"],
            data["market_margin_rows"],
        )
        f1 = evaluate_f1(margin_features, _rule("F1"))
    else:
        missing_f1_inputs = []
        if data["margin_rows"] is None:
            missing_f1_inputs.append("margin_history")
        if data["free_float_market_cap"] is None:
            missing_f1_inputs.append("free_float_market_cap")
        f1 = _insufficient_rule("F1", missing_f1_inputs)

    rule_results = [p1, p2, f1]
    completeness = validate_evaluation_completeness(REQUIRED_RULES, rule_results)
    if not completeness["complete"]:
        raise ValueError(f"internal evaluation incomplete: {completeness}")

    data_quality = data["diagnostics"]
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
