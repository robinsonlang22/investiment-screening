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
_METRIC_KEYS = (
    "metric",
    "metric_name",
    "indicator",
    "name",
    "指标",
    "项目",
    "_metric",
    "_sheet_name",
)
_UNIT_KEYS = ("unit", "currency", "单位", "币种", "_original_unit")
_WRAPPER_KEYS = ("rows", "data", "records", "results", "items", "values")

_VALUE_ALIASES = {
    "price": ("close", "close_price", "收盘价"),
}

_METRIC_ALIASES = {
    "price": {"close", "close_price", "收盘价"},
}

# Fields commonly returned alongside the requested metric.  Their presence
# identifies a valid but irrelevant table/row, which should be ignored rather
# than treated as a malformed representation of the requested dataset.
_KNOWN_OTHER_VALUE_ALIASES = {
    "price": {
        "open",
        "open_price",
        "开盘价",
        "high",
        "high_price",
        "最高价",
        "low",
        "low_price",
        "最低价",
        "change",
        "change_pct",
        "pct_change",
        "区间涨跌幅",
        "涨跌幅",
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
    "万亿元": 1_000_000_000_000.0,
    "万亿": 1_000_000_000_000.0,
    "%": 1.0,
    "倍": 1.0,
    "次": 1.0,
}


def is_null_token(value: Any) -> bool:
    """Return whether a source value represents missing data."""

    return value is None or (
        isinstance(value, str) and value.strip().lower() in _NULL_TOKENS
    )


def normalize_date(value: Any) -> str | None:
    """Normalize common MCP date forms without raising on invalid input."""

    if is_null_token(value) or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    text = re.sub(r"\([^)]*\)$", "", text).strip()
    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )

    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None

    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    # Preserve support for ISO timestamps returned by some MCP tools.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def clean_date(value: Any) -> str | None:
    """Backward-compatible alias for the shared date normalizer."""

    return normalize_date(value)


def _numeric_warning(
    warnings: list[dict[str, Any]] | None,
    *,
    code: str,
    value: Any,
    message: str,
    context: Mapping[str, Any] | None,
) -> None:
    if warnings is None:
        return
    warning = {
        "code": code,
        "value": str(value),
        "message": message,
    }
    if context:
        warning.update(context)
    warnings.append(warning)


def normalize_number(
    value: Any,
    *,
    unit: str | None = None,
    warnings: list[dict[str, Any]] | None = None,
    context: Mapping[str, Any] | None = None,
) -> float | None:
    """Normalize a value without aborting the dataset on a bad cell."""

    if is_null_token(value):
        return None
    if isinstance(value, bool):
        _numeric_warning(
            warnings,
            code="UNPARSEABLE_VALUE",
            value=value,
            message="布尔值不能作为数值解析",
            context=context,
        )
        return None

    explicit_unit: str | None = None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        parenthesized = text.startswith("(") and text.endswith(")")
        if parenthesized:
            text = text[1:-1].strip()
        for suffix in (
            "万亿元",
            "亿元",
            "万元",
            "人民币",
            "CNY",
            "RMB",
            "万亿",
            "元",
            "亿",
            "万",
            "%",
            "倍",
            "次",
        ):
            if text.lower().endswith(suffix.lower()):
                explicit_unit = suffix
                text = text[: -len(suffix)].strip()
                break
        text = text.removeprefix("¥").removeprefix("￥").strip()
        try:
            number = float(text)
        except ValueError:
            _numeric_warning(
                warnings,
                code="UNPARSEABLE_VALUE",
                value=value,
                message="无法解析数值",
                context=context,
            )
            return None
        if parenthesized:
            number = -number
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            _numeric_warning(
                warnings,
                code="UNPARSEABLE_VALUE",
                value=value,
                message="无法解析数值",
                context=context,
            )
            return None

    if not isfinite(number):
        _numeric_warning(
            warnings,
            code="NON_FINITE_VALUE",
            value=value,
            message="数值必须为有限值",
            context=context,
        )
        return None
    source_unit = explicit_unit or unit or "CNY"
    factor = _UNIT_FACTORS.get(str(source_unit).strip().lower())
    if factor is None:
        _numeric_warning(
            warnings,
            code="UNSUPPORTED_UNIT",
            value=value,
            message=f"不支持的单位：{source_unit}",
            context=context,
        )
        return None
    return number * factor


def _first(row: Mapping[str, Any], keys: Sequence[str]) -> tuple[Any, str | None]:
    for key in keys:
        if key in row:
            return row[key], key
    return None, None


def expand_choice_tables(raw_data: Any) -> list[dict[str, Any]]:
    """Expand Choice-style ``columns``/``items`` tables into row mappings.

    A payload may wrap one or more sheets in ``{"data": [...]}``. Ordinary
    row mappings are preserved, so this function can safely run once at the
    common adapter boundary before dataset-specific normalization.
    """

    if isinstance(raw_data, dict) and isinstance(raw_data.get("data"), list):
        raw_data = raw_data["data"]

    if not isinstance(raw_data, list):
        return raw_data

    expanded: list[dict[str, Any]] = []

    for entry in raw_data:
        if not isinstance(entry, dict):
            expanded.append(entry)
            continue

        columns = entry.get("columns")
        items = entry.get("items")

        if not isinstance(columns, list) or not isinstance(items, list):
            expanded.append(entry)
            continue

        for item in items:
            if not isinstance(item, list):
                continue

            row = {
                str(column): item[index] if index < len(item) else None
                for index, column in enumerate(columns)
            }
            row["_sheet_name"] = entry.get("sheetName")
            expanded.append(row)

    return expanded


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
    return normalize_date(key) is not None


def _metric_name(row: Mapping[str, Any]) -> str | None:
    value, _ = _first(row, _METRIC_KEYS)
    return str(value).strip().lower() if value is not None else None


def _has_known_other_value(row: Mapping[str, Any], dataset_type: str) -> bool:
    return any(key in row for key in _KNOWN_OTHER_VALUE_ALIASES[dataset_type])


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
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows, metadata = _unwrap_payload(payload)
    aliases = _VALUE_ALIASES[dataset_type]
    metric_aliases = _METRIC_ALIASES[dataset_type]
    records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for row_index, row in enumerate(rows):
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
                elif _has_known_other_value(row, dataset_type):
                    continue
                else:
                    raise AdapterError(
                        f"long-table row is missing a {dataset_type} value"
                    )
            records.append(
                {
                    "date": clean_date(raw_date),
                    "value": normalize_number(
                        raw_value,
                        unit=source_unit,
                        warnings=warnings,
                        context={
                            "row": row_index + 1,
                            "date": clean_date(raw_date),
                            "dataset_type": dataset_type,
                        },
                    ),
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
                        warnings=warnings,
                        context={
                            "row": row_index + 1,
                            "date": clean_date(date_column),
                            "dataset_type": dataset_type,
                        },
                    ),
                }
            )

    records.sort(key=lambda row: (row["date"] is None, row["date"] or ""))
    return records, metadata, warnings


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
    latest_closed_date: Any = None,
) -> dict[str, Any]:
    """Adapt an MCP price table to ``validate_price_history`` input."""

    records, metadata, warnings = _standard_records(
        payload, dataset_type="price", unit="CNY"
    )
    rows = [
        {
            "date": record["date"],
            "close": record["value"],
        }
        for record in records
    ]
    result: dict[str, Any] = {
        "rows": rows,
        "warnings": warnings,
    }
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


def adapt_mcp_data(
    payload: Any,
    dataset_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch a raw MCP payload to the requested Engine dataset contract."""

    original_payload = payload
    payload = expand_choice_tables(payload)
    if (
        isinstance(original_payload, Mapping)
        and isinstance(original_payload.get("data"), list)
    ):
        payload = {
            "rows": payload,
            **{
                key: value
                for key, value in original_payload.items()
                if key != "data"
            },
        }

    normalized = str(dataset_type).strip().lower()
    adapters = {
        "price": adapt_price_history,
        "price_history": adapt_price_history,
    }
    adapter = adapters.get(normalized)
    if adapter is None:
        raise AdapterError(f"unsupported dataset_type: {dataset_type!r}")
    return adapter(payload, **kwargs)
