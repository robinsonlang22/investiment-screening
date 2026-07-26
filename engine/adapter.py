"""Adapters from common MCP table responses to Engine input contracts.

The adapter layer performs representation changes only:

* clean and normalize dates;
* convert currency units to CNY;
* convert null tokens such as ``"-"`` to ``None``;
* reshape date-column wide tables into date-row long tables;
* rename source fields to the Engine's canonical field names.

It does not validate sufficiency, calculate features or evaluate rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isfinite
import re
from typing import Any


class AdapterError(ValueError):
    """Raised when an MCP payload cannot be represented safely."""


_NULL_TOKENS = {"", "-", "--", "—", "null", "none", "nan", "n/a", "na"}
_DATE_KEYS = ("date", "trade_date", "日期", "交易日期", "报告日期")
_METRIC_KEYS = ("metric", "metric_name", "indicator", "name", "指标", "项目")
_UNIT_KEYS = ("unit", "currency", "单位", "币种")
_WRAPPER_KEYS = ("rows", "data", "records", "results", "items", "values")

_VALUE_ALIASES = {
    "price": ("close", "close_price", "收盘价", "前复权收盘价"),
    "margin": ("margin_balance", "financing_balance", "融资余额"),
    "market_cap": (
        "free_float_market_cap",
        "free_float_cap",
        "ffmc",
        "自由流通市值",
    ),
    "market_margin": (
        "market_margin_balance",
        "total_margin_balance",
        "两市融资融券余额",
        "全市场两融余额",
    ),
}

_METRIC_ALIASES = {
    "price": {"close", "close_price", "收盘价", "前复权收盘价"},
    "margin": {"margin_balance", "financing_balance", "融资余额"},
    "market_cap": {
        "free_float_market_cap",
        "free_float_cap",
        "ffmc",
        "自由流通市值",
    },
    "market_margin": {
        "market_margin_balance",
        "total_margin_balance",
        "两市融资融券余额",
        "全市场两融余额",
    },
}

_UNIT_FACTORS = {
    "cny": 1.0,
    "rmb": 1.0,
    "人民币": 1.0,
    "元": 1.0,
    "万元": 10_000.0,
    "万": 10_000.0,
    "亿元": 100_000_000.0,
    "亿": 100_000_000.0,
}


def is_null_token(value: Any) -> bool:
    """Return whether a source value represents missing data."""

    return value is None or (
        isinstance(value, str) and value.strip().lower() in _NULL_TOKENS
    )


def clean_date(value: Any) -> str | None:
    """Convert common MCP date representations to ISO ``YYYY-MM-DD``."""

    if is_null_token(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        raise AdapterError(f"invalid date value: {value!r}")

    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        formats = ("%Y%m%d",)
    else:
        text = (
            text.replace("年", "-")
            .replace("月", "-")
            .replace("日", "")
            .replace("/", "-")
            .replace(".", "-")
        )
        # ISO timestamps are frequent in JSON MCP responses.
        iso_candidate = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso_candidate).date().isoformat()
        except ValueError:
            formats = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise AdapterError(f"invalid date value: {value!r}")


def normalize_number(value: Any, *, unit: str | None = None) -> float | None:
    """Normalize a numeric MCP value, converting 万元/亿元 to CNY."""

    if is_null_token(value):
        return None
    if isinstance(value, bool):
        raise AdapterError(f"invalid numeric value: {value!r}")

    explicit_unit: str | None = None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        parenthesized = text.startswith("(") and text.endswith(")")
        if parenthesized:
            text = text[1:-1].strip()
        for suffix in ("亿元", "万元", "人民币", "CNY", "RMB", "元", "亿", "万"):
            if text.lower().endswith(suffix.lower()):
                explicit_unit = suffix
                text = text[: -len(suffix)].strip()
                break
        text = text.removeprefix("¥").removeprefix("￥").strip()
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise AdapterError(f"invalid numeric value: {value!r}") from exc
        if parenthesized:
            number = -number
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"invalid numeric value: {value!r}") from exc

    if not isfinite(number):
        raise AdapterError(f"numeric value must be finite: {value!r}")
    source_unit = explicit_unit or unit or "CNY"
    factor = _UNIT_FACTORS.get(str(source_unit).strip().lower())
    if factor is None:
        raise AdapterError(f"unsupported unit: {source_unit!r}")
    return number * factor


def _first(row: Mapping[str, Any], keys: Sequence[str]) -> tuple[Any, str | None]:
    for key in keys:
        if key in row:
            return row[key], key
    return None, None


def _unwrap_payload(payload: Any) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    raw_rows = payload
    if isinstance(payload, Mapping):
        wrapper_key = next(
            (
                key
                for key in _WRAPPER_KEYS
                if key in payload
                and isinstance(payload[key], Sequence)
                and not isinstance(payload[key], (str, bytes, bytearray))
            ),
            None,
        )
        if wrapper_key is not None:
            raw_rows = payload[wrapper_key]
            metadata = {
                key: value for key, value in payload.items() if key != wrapper_key
            }
        else:
            raw_rows = [payload]

    if not isinstance(raw_rows, Sequence) or isinstance(
        raw_rows, (str, bytes, bytearray)
    ):
        raise AdapterError("MCP payload must contain a row sequence")
    rows = list(raw_rows)
    if any(not isinstance(row, Mapping) for row in rows):
        raise AdapterError("every MCP row must be a mapping")
    return rows, metadata


def _is_date_column(key: Any) -> bool:
    try:
        return clean_date(key) is not None
    except AdapterError:
        return False


def wide_to_long(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_columns: Sequence[str] = (),
    variable_name: str = "date",
    value_name: str = "value",
) -> list[dict[str, Any]]:
    """Unpivot columns whose names are parseable dates."""

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AdapterError("every wide-table row must be a mapping")
        identifiers = {key: row[key] for key in id_columns if key in row}
        for key, value in row.items():
            if key in id_columns or not _is_date_column(key):
                continue
            result.append(
                {
                    **identifiers,
                    variable_name: clean_date(key),
                    value_name: None if is_null_token(value) else value,
                }
            )
    return result


def _metric_name(row: Mapping[str, Any]) -> str | None:
    value, _ = _first(row, _METRIC_KEYS)
    return str(value).strip().lower() if value is not None else None


def _row_unit(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    value, _ = _first(row, _UNIT_KEYS)
    if value is None:
        value, _ = _first(metadata, _UNIT_KEYS)
    return str(value).strip() if value is not None else None


def _standard_records(
    payload: Any,
    *,
    dataset_type: str,
    unit: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, metadata = _unwrap_payload(payload)
    aliases = _VALUE_ALIASES[dataset_type]
    metric_aliases = _METRIC_ALIASES[dataset_type]
    records: list[dict[str, Any]] = []

    for row in rows:
        raw_date, _ = _first(row, _DATE_KEYS)
        raw_value, value_key = _first(row, aliases)
        source_unit = unit or _row_unit(row, metadata)

        if raw_date is not None:
            if value_key is None:
                metric = _metric_name(row)
                generic_value, _ = _first(row, ("value", "数值"))
                if metric in metric_aliases and generic_value is not None:
                    raw_value = generic_value
                elif metric is not None and metric not in metric_aliases:
                    continue
                else:
                    raise AdapterError(
                        f"long-table row is missing a {dataset_type} value"
                    )
            records.append(
                {
                    "date": clean_date(raw_date),
                    "value": normalize_number(raw_value, unit=source_unit),
                }
            )
            continue
        if value_key is not None:
            raise AdapterError("long-table row is missing a date")

        metric = _metric_name(row)
        if metric is not None and metric not in metric_aliases:
            continue
        date_columns = [key for key in row if _is_date_column(key)]
        if not date_columns:
            raise AdapterError("row has neither a date field nor date columns")
        for date_column in date_columns:
            records.append(
                {
                    "date": clean_date(date_column),
                    "value": normalize_number(
                        row[date_column],
                        unit=source_unit,
                    ),
                }
            )

    records.sort(key=lambda row: (row["date"] is None, row["date"] or ""))
    return records, metadata


def _metadata_date(
    metadata: Mapping[str, Any],
    explicit: Any,
    *keys: str,
) -> str | None:
    value = explicit
    if value is None:
        value, _ = _first(metadata, keys)
    return clean_date(value) if value is not None else None


def adapt_price_history(
    payload: Any,
    *,
    adjustment: str | None = None,
    latest_closed_date: Any = None,
) -> dict[str, Any]:
    """Adapt an MCP price table to ``validate_price_history`` input."""

    records, metadata = _standard_records(
        payload, dataset_type="price", unit="CNY"
    )
    source_adjustment = (
        adjustment
        or metadata.get("adjustment")
        or metadata.get("adjustment_type")
        or metadata.get("复权方式")
    )
    adjustment_aliases = {
        "forward": "forward",
        "forward_adjusted": "forward",
        "qfq": "forward",
        "前复权": "forward",
    }
    normalized_adjustment = adjustment_aliases.get(
        str(source_adjustment).strip().lower()
        if source_adjustment is not None
        else ""
    )
    if normalized_adjustment is None:
        raise AdapterError("price adjustment must be explicitly forward/前复权")

    rows = [
        {
            "date": record["date"],
            "close": record["value"],
            "adjustment": normalized_adjustment,
        }
        for record in records
    ]
    result: dict[str, Any] = {"rows": rows, "adjustment": normalized_adjustment}
    as_of = _metadata_date(
        metadata,
        latest_closed_date,
        "latest_closed_date",
        "latest_closed_trading_date",
        "as_of_date",
    )
    if as_of is not None:
        result["latest_closed_date"] = as_of
    return result


def adapt_margin_history(
    payload: Any,
    *,
    unit: str | None = None,
) -> dict[str, Any]:
    """Adapt security financing balances, converting all values to CNY."""

    records, _ = _standard_records(payload, dataset_type="margin", unit=unit)
    return {
        "rows": [
            {
                "date": record["date"],
                "margin_balance": record["value"],
                "unit": "CNY",
                "metric": "margin_balance",
            }
            for record in records
        ],
        "unit": "CNY",
        "metric": "margin_balance",
    }


def adapt_market_cap(
    payload: Any,
    *,
    unit: str | None = None,
) -> dict[str, Any]:
    """Adapt free-float market-cap observations to CNY."""

    records, _ = _standard_records(payload, dataset_type="market_cap", unit=unit)
    return {
        "rows": [
            {
                "date": record["date"],
                "free_float_market_cap": record["value"],
                "unit": "CNY",
            }
            for record in records
        ],
        "unit": "CNY",
    }


def adapt_market_margin_history(
    payload: Any,
    *,
    unit: str | None = None,
    convention: str = "沪深两市融资融券余额合计",
) -> dict[str, Any]:
    """Adapt market-wide margin balances to the manual-review input."""

    if not convention.strip():
        raise AdapterError("market margin convention must be explicit")
    records, _ = _standard_records(
        payload, dataset_type="market_margin", unit=unit
    )
    return {
        "rows": [
            {
                "date": record["date"],
                "market_margin_balance": record["value"],
                "unit": "CNY",
                "convention": convention,
            }
            for record in records
        ],
        "unit": "CNY",
        "convention": convention,
    }


def adapt_mcp_data(
    payload: Any,
    dataset_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch a raw MCP payload to the requested Engine dataset contract."""

    normalized = str(dataset_type).strip().lower()
    adapters = {
        "price": adapt_price_history,
        "price_history": adapt_price_history,
        "margin": adapt_margin_history,
        "margin_history": adapt_margin_history,
        "market_cap": adapt_market_cap,
        "free_float_market_cap": adapt_market_cap,
        "market_margin": adapt_market_margin_history,
        "market_margin_history": adapt_market_margin_history,
    }
    adapter = adapters.get(normalized)
    if adapter is None:
        raise AdapterError(f"unsupported dataset_type: {dataset_type!r}")
    return adapter(payload, **kwargs)
