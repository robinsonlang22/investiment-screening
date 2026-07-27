"""Input validation for investment-screening rules.

This module validates data sufficiency, value legality and dataset conventions.
It deliberately does not calculate rule outcomes or investment conclusions.

Accepted dataset shapes
-----------------------
Functions accept either a list of row mappings or a mapping shaped like::

    {
        "rows": [...],
        "adjustment": "forward",
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
_MARGIN_BALANCE_KEYS = ("margin_balance", "financing_balance", "融资余额")
_NET_PURCHASE_KEYS = (
    "margin_net_purchase",
    "financing_net_purchase",
    "融资净买入额",
    "融资净买入",
)
_MARKET_CAP_KEYS = (
    "free_float_market_cap",
    "free_float_cap",
    "ffmc",
    "自由流通市值",
)
_MARKET_MARGIN_KEYS = (
    "market_margin_balance",
    "total_margin_balance",
    "两市融资融券余额",
    "全市场两融余额",
)
_FORWARD_ADJUSTMENT_VALUES = {
    "forward",
    "forward_adjusted",
    "qfq",
    "前复权",
}
_CNY_VALUES = {"cny", "rmb", "人民币", "元"}


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
    required_adjustment: str = "forward",
) -> dict[str, Any]:
    """Validate adjusted daily close-price history without judging a rule."""

    raw_rows, metadata, issues = _unpack_dataset(rows)
    valid_rows: list[dict[str, Any]] = []
    dated_rows: list[tuple[int, date]] = []

    if not isinstance(minimum_observations, int) or minimum_observations <= 0:
        issues.append(
            _issue("INVALID_MINIMUM", "minimum_observations 必须是正整数")
        )

    expected_adjustment = _normalise_text(required_adjustment)
    if expected_adjustment in _FORWARD_ADJUSTMENT_VALUES:
        accepted_adjustments = _FORWARD_ADJUSTMENT_VALUES
    else:
        accepted_adjustments = {expected_adjustment}

    for index, raw_row in enumerate(raw_rows):
        row_number = index + 1
        if not isinstance(raw_row, Mapping):
            issues.append(_issue("INVALID_ROW", "每一行必须是字典", row=row_number))
            continue

        raw_date, _ = _first_value(raw_row, _DATE_KEYS)
        trade_date = _parse_date(raw_date)
        raw_close, _ = _first_value(raw_row, _CLOSE_KEYS)
        close = _parse_positive_number(raw_close)
        adjustment = _resolve_metadata(
            raw_row,
            metadata,
            ("adjustment", "adjustment_type", "复权方式"),
        )

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

        if not adjustment:
            issues.append(
                _issue("MISSING_ADJUSTMENT", "必须明确声明复权方式", row=row_number)
            )
            row_valid = False
        elif _normalise_text(adjustment) not in accepted_adjustments:
            issues.append(
                _issue(
                    "WRONG_ADJUSTMENT",
                    f"复权方式必须为 {required_adjustment}",
                    row=row_number,
                )
            )
            row_valid = False

        if row_valid and trade_date is not None and close is not None:
            normalised = dict(raw_row)
            normalised["date"] = trade_date.isoformat()
            normalised["close"] = close
            normalised["adjustment"] = required_adjustment
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


def validate_margin_history(
    rows: Any, minimum_observations: int = 21
) -> dict[str, Any]:
    """Validate individual-security financing-balance history."""

    raw_rows, metadata, issues = _unpack_dataset(rows)
    valid_rows: list[dict[str, Any]] = []
    dated_rows: list[tuple[int, date]] = []

    for index, raw_row in enumerate(raw_rows):
        row_number = index + 1
        if not isinstance(raw_row, Mapping):
            issues.append(_issue("INVALID_ROW", "每一行必须是字典", row=row_number))
            continue

        raw_date, _ = _first_value(raw_row, _DATE_KEYS)
        trade_date = _parse_date(raw_date)
        balance_value, balance_key = _first_value(raw_row, _MARGIN_BALANCE_KEYS)
        _, net_key = _first_value(raw_row, _NET_PURCHASE_KEYS)
        metric = _resolve_metadata(
            raw_row, metadata, ("metric", "metric_type", "指标", "数据口径")
        )
        unit = _resolve_metadata(raw_row, metadata, ("unit", "currency", "单位", "币种"))
        balance = _parse_positive_number(balance_value)

        row_valid = True
        date_valid = True
        if trade_date is None:
            issues.append(
                _issue("INVALID_DATE", "融资数据日期缺失或非法", row=row_number)
            )
            row_valid = False
            date_valid = False
        if date_valid and trade_date is not None:
            dated_rows.append((row_number, trade_date))
        if net_key is not None and balance_key is None:
            issues.append(
                _issue(
                    "NET_PURCHASE_NOT_BALANCE",
                    "融资净买入额不得代替融资余额",
                    row=row_number,
                )
            )
            row_valid = False
        if balance is None:
            issues.append(
                _issue(
                    "INVALID_MARGIN_BALANCE",
                    "融资余额必须是非 NULL 的正数",
                    row=row_number,
                )
            )
            row_valid = False
        if metric and _normalise_text(metric) not in {
            "margin_balance",
            "financing_balance",
            "融资余额",
        }:
            issues.append(
                _issue(
                    "WRONG_MARGIN_METRIC",
                    "数据口径必须明确为融资余额",
                    row=row_number,
                )
            )
            row_valid = False
        if not metric and balance_key is None:
            issues.append(
                _issue("MISSING_MARGIN_METRIC", "无法确认数据是融资余额", row=row_number)
            )
            row_valid = False
        if _normalise_text(unit) not in _CNY_VALUES:
            issues.append(
                _issue("WRONG_OR_MISSING_UNIT", "融资余额单位必须统一为 CNY", row=row_number)
            )
            row_valid = False

        if row_valid and trade_date is not None and balance is not None:
            normalised = dict(raw_row)
            normalised.update(
                {
                    "date": trade_date.isoformat(),
                    "margin_balance": balance,
                    "unit": "CNY",
                    "metric": "margin_balance",
                }
            )
            valid_rows.append(normalised)

    _check_order_and_duplicates(dated_rows, issues)
    if len(valid_rows) < minimum_observations:
        issues.append(
            _issue(
                "INSUFFICIENT_OBSERVATIONS",
                f"至少需要 {minimum_observations} 个有效融资余额数据点，当前为 {len(valid_rows)}",
            )
        )
    return _finish(valid_rows, issues)

def validate_market_cap(rows: Any, target_date: Any) -> dict[str, Any]:
    """Select the closest valid FFMC observation not later than target_date."""

    raw_rows, metadata, issues = _unpack_dataset(rows)
    parsed_target = _parse_date(target_date)
    valid_rows: list[dict[str, Any]] = []

    if parsed_target is None:
        issues.append(_issue("INVALID_TARGET_DATE", "target_date 格式非法"))

    for index, raw_row in enumerate(raw_rows):
        row_number = index + 1
        if not isinstance(raw_row, Mapping):
            issues.append(_issue("INVALID_ROW", "每一行必须是字典", row=row_number))
            continue
        raw_date, _ = _first_value(raw_row, _DATE_KEYS)
        cap_date = _parse_date(raw_date)
        cap_value, _ = _first_value(raw_row, _MARKET_CAP_KEYS)
        cap = _parse_positive_number(cap_value)
        unit = _resolve_metadata(raw_row, metadata, ("unit", "currency", "单位", "币种"))
        row_valid = True
        if cap_date is None:
            issues.append(
                _issue("INVALID_DATE", "市值数据日期缺失或非法", row=row_number)
            )
            row_valid = False
        if cap is None:
            issues.append(
                _issue(
                    "INVALID_MARKET_CAP",
                    "自由流通市值必须是非 NULL 的正数",
                    row=row_number,
                )
            )
            row_valid = False
        if _normalise_text(unit) not in _CNY_VALUES:
            issues.append(
                _issue("WRONG_OR_MISSING_UNIT", "自由流通市值单位必须为 CNY", row=row_number)
            )
            row_valid = False
        if row_valid and cap_date is not None and cap is not None:
            normalised = dict(raw_row)
            normalised.update(
                {
                    "date": cap_date.isoformat(),
                    "free_float_market_cap": cap,
                    "unit": "CNY",
                }
            )
            valid_rows.append(normalised)

    selected_row = None
    if parsed_target is not None:
        eligible = [
            row for row in valid_rows if _parse_date(row["date"]) <= parsed_target
        ]
        if eligible:
            selected_row = max(eligible, key=lambda row: row["date"])
        else:
            issues.append(
                _issue(
                    "NO_ELIGIBLE_MARKET_CAP",
                    "没有不晚于融资余额日期的有效自由流通市值",
                )
            )

    return _finish(
        valid_rows,
        issues,
        target_date=parsed_target.isoformat() if parsed_target else None,
        selected_row=selected_row,
    )


def validate_market_margin_history(rows: Any) -> dict[str, Any]:
    """Validate market-wide margin-balance observations used for manual review."""

    raw_rows, metadata, issues = _unpack_dataset(rows)
    valid_rows: list[dict[str, Any]] = []
    dated_rows: list[tuple[int, date]] = []
    observed_conventions: set[tuple[str, str]] = set()

    for index, raw_row in enumerate(raw_rows):
        row_number = index + 1
        if not isinstance(raw_row, Mapping):
            issues.append(_issue("INVALID_ROW", "每一行必须是字典", row=row_number))
            continue
        raw_date, _ = _first_value(raw_row, _DATE_KEYS)
        trade_date = _parse_date(raw_date)
        value, _ = _first_value(raw_row, _MARKET_MARGIN_KEYS)
        balance = _parse_positive_number(value)
        unit = _resolve_metadata(raw_row, metadata, ("unit", "currency", "单位", "币种"))
        convention = _resolve_metadata(
            raw_row, metadata, ("scope", "convention", "统计口径")
        )
        row_valid = True
        date_valid = True
        if trade_date is None:
            issues.append(
                _issue("INVALID_DATE", "全市场两融日期缺失或非法", row=row_number)
            )
            row_valid = False
            date_valid = False
        if date_valid and trade_date is not None:
            dated_rows.append((row_number, trade_date))
        if balance is None:
            issues.append(
                _issue(
                    "INVALID_MARKET_MARGIN_BALANCE",
                    "全市场两融余额必须是非 NULL 的正数",
                    row=row_number,
                )
            )
            row_valid = False
        if _normalise_text(unit) not in _CNY_VALUES:
            issues.append(
                _issue("WRONG_OR_MISSING_UNIT", "全市场两融余额单位必须为 CNY", row=row_number)
            )
            row_valid = False
        if not convention:
            issues.append(
                _issue("MISSING_CONVENTION", "必须声明全市场两融余额统计口径", row=row_number)
            )
            row_valid = False
        if row_valid and trade_date is not None and balance is not None:
            normalised = dict(raw_row)
            normalised.update(
                {
                    "date": trade_date.isoformat(),
                    "market_margin_balance": balance,
                    "unit": "CNY",
                    "convention": str(convention),
                }
            )
            valid_rows.append(normalised)
            observed_conventions.add(("CNY", str(convention).strip()))

    _check_order_and_duplicates(dated_rows, issues)
    if len(observed_conventions) > 1:
        issues.append(
            _issue("INCONSISTENT_CONVENTION", "相邻全市场两融数据的单位或统计口径不一致")
        )
    if len(valid_rows) < 2:
        issues.append(
            _issue("INSUFFICIENT_OBSERVATIONS", "至少需要两个相邻的全市场两融余额数据点")
        )
    return _finish(valid_rows, issues)
