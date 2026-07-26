"""Serialization helpers for DebtPilot result objects."""

from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
import json
from typing import Any

from app.money import money


def decimal_to_json_string(value: Decimal) -> str:
    """Serialize Decimal money as a fixed two-decimal string."""
    return f"{money(value):.2f}"


def to_json_ready(value: Any) -> Any:
    """Convert supported nested objects to JSON-compatible values."""
    if is_dataclass(value):
        return to_json_ready(asdict(value))
    if isinstance(value, Decimal):
        return decimal_to_json_string(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): to_json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json_ready(item) for item in value]
    if isinstance(value, str | int | bool) or value is None:
        return value

    raise TypeError(f"Unsupported JSON serialization type: {type(value).__name__}")


def dumps_json(value: Any, **kwargs: Any) -> str:
    """Serialize supported DebtPilot objects to JSON."""
    return json.dumps(to_json_ready(value), **kwargs)
