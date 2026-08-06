"""Versioned deterministic investment-rule API."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.aggregator import build_evaluation_bundle, validate_evaluation_completeness
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
from engine.validators import validate_price_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "rules"

app = FastAPI(
    title="Investment Rule Engine",
    version="3.0.0",
    description="Evaluate P1, P2, or the complete P1/P2 rule set.",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PricePoint(StrictModel):
    date: str
    close: float = Field(gt=0)


class EvaluateRequest(StrictModel):
    symbol: str = Field(min_length=1)
    price_history: list[PricePoint] = Field(min_length=1)
    spread_expanding: bool | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized


@lru_cache(maxsize=4)
def _load_json(name: str) -> dict[str, Any]:
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def _rule(rule_id: str) -> dict[str, Any]:
    return deepcopy(_load_json(f"{rule_id.lower()}.json"))


def _rule_version() -> str:
    registry = _load_json("registry.json")
    return str(registry.get("rule_version", registry.get("schema_version", "unknown")))


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


def _prepare_price_features(
    request: EvaluateRequest,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = [point.model_dump() for point in request.price_history]
    validation = validate_price_history(
        {"rows": rows},
        minimum_observations=120,
    )
    if validation["status"] != "VALID":
        raise ValueError(
            f"price_history validation failed: {validation.get('issues', [])}"
        )
    data_quality = {
        "status": "VALID",
        "validation_issues": {},
        "unavailable_inputs": [],
        "price_history": {
            "observations": len(validation["valid_rows"]),
            "latest_date": validation.get("latest_date"),
        },
    }
    return _build_price_features(validation["valid_rows"]), data_quality


def _decision(overall_status: str) -> str:
    return {
        "COMPLIANT": "pass",
        "CONDITIONAL": "conditional_pass",
        "NOT_COMPLIANT": "fail",
        "INFORMATION_INSUFFICIENT": "information_insufficient",
    }[overall_status]


def _evaluate(
    request: EvaluateRequest,
    analysis_type: Literal["p1", "p2", "full"],
) -> dict[str, Any]:
    features, data_quality = _prepare_price_features(request)

    # P2's three-o'clock classification depends on P1's market state, so P1 is
    # always calculated internally for P2 while remaining hidden in /p2 output.
    p1 = evaluate_p1(
        features,
        spread_expanding=request.spread_expanding,
        rule_config=_rule("P1"),
    )
    p2 = None
    if analysis_type in {"p2", "full"}:
        p2 = evaluate_p2(features, p1, _rule("P2"))

    if analysis_type == "p1":
        results = {"p1": p1}
    elif analysis_type == "p2":
        assert p2 is not None
        results = {"p2": p2}
    else:
        assert p2 is not None
        results = {"p1": p1, "p2": p2}

    applicable_rules = [rule_id.upper() for rule_id in results]
    completeness = validate_evaluation_completeness(
        applicable_rules,
        list(results.values()),
    )
    if not completeness["complete"]:
        raise ValueError(f"internal evaluation incomplete: {completeness}")

    data_quality["evaluation_completeness"] = completeness
    bundle = build_evaluation_bundle(
        request.symbol,
        list(results.values()),
        data_quality,
    )
    return {
        "symbol": request.symbol,
        "analysis_type": analysis_type,
        "decision": _decision(bundle["overall_status"]),
        "overall_status": bundle["overall_status"],
        "rule_version": _rule_version(),
        "results": results,
        "data_quality": bundle["data_quality"],
        "human_review": bundle["human_review"],
        "report_constraints": bundle["report_constraints"],
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


@app.post("/v1/evaluate/p1")
async def evaluate_p1_endpoint(request: EvaluateRequest) -> dict[str, Any]:
    return _evaluate(request, "p1")


@app.post("/v1/evaluate/p2")
async def evaluate_p2_endpoint(request: EvaluateRequest) -> dict[str, Any]:
    return _evaluate(request, "p2")


@app.post("/v1/evaluate/full")
async def evaluate_full_endpoint(request: EvaluateRequest) -> dict[str, Any]:
    return _evaluate(request, "full")
