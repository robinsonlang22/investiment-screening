"""Input validation for investment-screening rules.

This module validates data sufficiency, value legality and dataset conventions.
It deliberately does not calculate rule outcomes or investment conclusions.

Accepted dataset shapes
-----------------------
Functions accept either a list of row mappings or a mapping shaped like::

    {
        "rows": [...],
        "unit": "CNY",
        "metric": "margin_balance",
        "latest_closed_date": "2026-07-24",
    }

Metadata may also be repeated on individual rows. Dataset-level metadata wins
only when the corresponding row value is absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from typing import Any


_NULL_TOKENS = {"", "-", "--", "null", "none", "nan", "n/a", "na"}
_DATE_KEYS = ("date", "trade_date", "交易日期", "日期")
_CLOSE_KEYS = ("close", "close_price", "收盘价")


def _issue(code: str, message: str, *, row: int | None = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "message": message}
    if row is not None:
        issue["row"] = row
    return issue


def _unpack_dataset(data: Any) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    if isinstance(data, Mapping):
        metadata = {key: value for key, value in data.items() if key != "rows"}
        raw_rows = data.get("rows")
    else:
        raw_rows = data

    if isinstance(raw_rows, Sequence) and not isinstance(
        raw_rows, (str, bytes, bytearray)
    ):
        return list(raw_rows), metadata, issues

    issues.append(_issue("INVALID_ROWS", "rows 必须是字典列表"))
    return [], metadata, issues


def _first_value(row: Mapping[str, Any], keys: Sequence[str]) -> tuple[Any, str | None]:
    for key in keys:
        if key in row:
            return row[key], key
    return None, None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _parse_positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _NULL_TOKENS:
            return None
        text = text.replace(",", "")
    else:
        text = value
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0 else None


def _normalise_text(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _resolve_metadata(
    row: Mapping[str, Any], metadata: Mapping[str, Any], keys: Sequence[str]
) -> Any:
    value, _ = _first_value(row, keys)
    if value is not None:
        return value
    value, _ = _first_value(metadata, keys)
    return value


def _finish(
    valid_rows: list[dict[str, Any]], issues: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    return {
        "status": "VALID" if not issues else "INVALID",
        "valid_rows": valid_rows,
        "observation_count": len(valid_rows),
        "issues": issues,
        **extra,
    }


def _check_order_and_duplicates(
    dated_rows: list[tuple[int, date]], issues: list[dict[str, Any]]
) -> None:
    dates = [item[1] for item in dated_rows]
    if len(dates) != len(set(dates)):
        duplicates = sorted({item.isoformat() for item in dates if dates.count(item) > 1})
        issues.append(
            _issue("DUPLICATE_DATE", f"存在重复交易日期：{', '.join(duplicates)}")
        )
    if dates != sorted(dates):
        issues.append(_issue("UNSORTED_DATES", "数据必须按交易日期升序排列"))


def validate_price_history(
    rows: Any,
    *,
    minimum_observations: int,
) -> dict[str, Any]:
    """Validate daily close-price history without judging a rule."""

    raw_rows, metadata, issues = _unpack_dataset(rows)
    valid_rows: list[dict[str, Any]] = []
    dated_rows: list[tuple[int, date]] = []

    if not isinstance(minimum_observations, int) or minimum_observations <= 0:
        issues.append(
            _issue("INVALID_MINIMUM", "minimum_observations 必须是正整数")
        )

    for index, raw_row in enumerate(raw_rows):
        row_number = index + 1
        if not isinstance(raw_row, Mapping):
            issues.append(_issue("INVALID_ROW", "每一行必须是字典", row=row_number))
            continue

        raw_date, _ = _first_value(raw_row, _DATE_KEYS)
        trade_date = _parse_date(raw_date)
        raw_close, _ = _first_value(raw_row, _CLOSE_KEYS)
        close = _parse_positive_number(raw_close)
        row_valid = True
        date_valid = True
        if trade_date is None:
            issues.append(
                _issue("INVALID_DATE", "交易日期缺失或格式非法", row=row_number)
            )
            row_valid = False
            date_valid = False
        elif trade_date > date.today():
            issues.append(
                _issue("FUTURE_DATE", "交易日期不能晚于今天", row=row_number)
            )
            row_valid = False
            date_valid = False
        if date_valid and trade_date is not None:
            dated_rows.append((row_number, trade_date))

        if close is None:
            issues.append(
                _issue(
                    "INVALID_CLOSE",
                    "收盘价必须是非 NULL、非 '-' 的正数",
                    row=row_number,
                )
            )
            row_valid = False

        if row_valid and trade_date is not None and close is not None:
            normalised = dict(raw_row)
            normalised["date"] = trade_date.isoformat()
            normalised["close"] = close
            valid_rows.append(normalised)

    _check_order_and_duplicates(dated_rows, issues)

    if isinstance(minimum_observations, int) and minimum_observations > 0:
        if len(valid_rows) < minimum_observations:
            issues.append(
                _issue(
                    "INSUFFICIENT_OBSERVATIONS",
                    f"至少需要 {minimum_observations} 个有效观测，当前为 {len(valid_rows)}",
                )
            )

    latest_date = max((item[1] for item in dated_rows), default=None)
    expected_latest = _parse_date(
        metadata.get("latest_closed_date")
        or metadata.get("latest_closed_trading_date")
        or metadata.get("as_of_date")
    )
    if expected_latest is not None and latest_date != expected_latest:
        issues.append(
            _issue(
                "STALE_OR_MISMATCHED_LATEST_DATE",
                "最新价格日期与声明的最近已收盘交易日不一致",
            )
        )

    return _finish(
        valid_rows,
        issues,
        latest_date=latest_date.isoformat() if latest_date else None,
        expected_latest_closed_date=(
            expected_latest.isoformat() if expected_latest else None
        ),
    )
