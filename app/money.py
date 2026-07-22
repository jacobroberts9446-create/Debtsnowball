"""Centralized Decimal helpers for monetary values."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")


def to_decimal(value: object) -> Decimal:
    """Convert a supported value to a finite Decimal without float math."""
    if value is None:
        raise ValueError("money value is required.")
    if isinstance(value, bool):
        raise ValueError("money value must not be boolean.")

    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, float):
            result = Decimal(str(value))
        else:
            result = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid money value: {value!r}") from exc

    if not result.is_finite():
        raise ValueError("money value must be finite.")

    return result


def round_money(value: object) -> Decimal:
    """Round a value to cents using normal financial half-up rounding."""
    rounded = to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    return ZERO_MONEY if rounded == ZERO_MONEY else rounded


def money(value: object) -> Decimal:
    """Return a cents-rounded Decimal money value."""
    return round_money(value)


def excel_number(value: object) -> float:
    """Convert money to an Excel-friendly number at the presentation boundary."""
    return float(money(value))


def format_currency(value: object) -> str:
    """Format a money value for user-facing text output."""
    return f"${money(value):,.2f}"
