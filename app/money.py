"""Centralized Decimal helpers for monetary values."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")
SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
SQLITE_MIN_INTEGER = -9_223_372_036_854_775_808


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


def to_cents(value: object) -> int:
    """Convert a money-compatible value to exact integer cents."""
    cents = money(value) * Decimal("100")
    cents_int = int(cents)
    if cents_int < SQLITE_MIN_INTEGER or cents_int > SQLITE_MAX_INTEGER:
        raise OverflowError("money value exceeds SQLite signed 64-bit cent storage.")
    if cents_int == 0:
        return 0
    return cents_int


def from_cents(value: object) -> Decimal:
    """Convert integer cents from SQLite storage to Decimal money."""
    if value is None:
        raise ValueError("cent value is required.")
    if isinstance(value, bool):
        raise ValueError("cent value must not be boolean.")
    if isinstance(value, float):
        raise ValueError("cent value must be an integer, not a float.")

    try:
        cents = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid cent value: {value!r}") from exc

    if not cents.is_finite():
        raise ValueError("cent value must be finite.")
    if cents != cents.to_integral_value():
        raise ValueError("cent value must not contain fractional cents.")

    cents_int = int(cents)
    if cents_int < SQLITE_MIN_INTEGER or cents_int > SQLITE_MAX_INTEGER:
        raise OverflowError("cent value exceeds SQLite signed 64-bit storage.")
    if cents_int == 0:
        return ZERO_MONEY
    return money(Decimal(cents_int) / Decimal("100"))
