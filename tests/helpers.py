from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_rule(rule_id: str):
    return json.loads(
        (PROJECT_ROOT / "rules" / f"{rule_id.lower()}.json").read_text()
    )


def trading_dates(count: int, start: date = date(2025, 1, 2)) -> list[str]:
    result: list[str] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result
