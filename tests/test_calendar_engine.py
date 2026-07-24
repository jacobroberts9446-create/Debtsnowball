"""Direct tests for CalendarEngine pay-period generation."""

from datetime import date
from types import SimpleNamespace

from app.calendar_engine import CalendarEngine


def period_dates(periods):
    """Return pay/start/end date tuples for exact calendar assertions."""
    return [
        (period.pay_date, period.start_date, period.end_date)
        for period in periods
    ]


def settings(first_paycheck: date, pay_frequency: str | None = None):
    """Build the minimal settings object CalendarEngine requires."""
    values = {"first_paycheck": first_paycheck}
    if pay_frequency is not None:
        values["pay_frequency"] = pay_frequency
    return SimpleNamespace(**values)


def test_biweekly_generation_defaults_when_frequency_is_missing() -> None:
    """Missing pay_frequency preserves the legacy biweekly behavior."""
    periods = CalendarEngine(settings(date(2026, 1, 2))).generate(date(2026, 1, 30))

    assert period_dates(periods) == [
        (date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 15)),
        (date(2026, 1, 16), date(2026, 1, 16), date(2026, 1, 29)),
        (date(2026, 1, 30), date(2026, 1, 30), date(2026, 2, 12)),
    ]


def test_unknown_frequency_uses_biweekly_fallback() -> None:
    """Unsupported frequencies fall back to biweekly intervals."""
    periods = CalendarEngine(
        settings(date(2026, 1, 2), "every-so-often")
    ).generate(date(2026, 1, 16))

    assert period_dates(periods) == [
        (date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 15)),
        (date(2026, 1, 16), date(2026, 1, 16), date(2026, 1, 29)),
    ]


def test_weekly_generation_spans_months() -> None:
    """Weekly periods advance by seven real calendar days across months."""
    periods = CalendarEngine(settings(date(2026, 1, 29), "weekly")).generate(
        date(2026, 2, 12)
    )

    assert period_dates(periods) == [
        (date(2026, 1, 29), date(2026, 1, 29), date(2026, 2, 4)),
        (date(2026, 2, 5), date(2026, 2, 5), date(2026, 2, 11)),
        (date(2026, 2, 12), date(2026, 2, 12), date(2026, 2, 18)),
    ]


def test_monthly_generation_clamps_to_shorter_months_and_spans_years() -> None:
    """Monthly periods use real month lengths and clamp missing days."""
    periods = CalendarEngine(settings(date(2026, 12, 31), "monthly")).generate(
        date(2027, 2, 28)
    )

    assert period_dates(periods) == [
        (date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 30)),
        (date(2027, 1, 31), date(2027, 1, 31), date(2027, 2, 27)),
        (date(2027, 2, 28), date(2027, 2, 28), date(2027, 3, 27)),
    ]


def test_monthly_generation_handles_leap_year_february() -> None:
    """Monthly periods preserve February 29 when the target month has it."""
    periods = CalendarEngine(settings(date(2028, 1, 31), "monthly")).generate(
        date(2028, 2, 29)
    )

    assert period_dates(periods) == [
        (date(2028, 1, 31), date(2028, 1, 31), date(2028, 2, 28)),
        (date(2028, 2, 29), date(2028, 2, 29), date(2028, 3, 28)),
    ]


def test_semimonthly_generation_uses_fifteenth_and_month_end() -> None:
    """Semimonthly periods advance to the 15th, then real month end."""
    periods = CalendarEngine(settings(date(2026, 2, 14), "semimonthly")).generate(
        date(2026, 3, 15)
    )

    assert period_dates(periods) == [
        (date(2026, 2, 14), date(2026, 2, 14), date(2026, 2, 14)),
        (date(2026, 2, 15), date(2026, 2, 15), date(2026, 2, 27)),
        (date(2026, 2, 28), date(2026, 2, 28), date(2026, 3, 14)),
        (date(2026, 3, 15), date(2026, 3, 15), date(2026, 3, 30)),
    ]


def test_semimonthly_generation_handles_leap_day_month_end() -> None:
    """Semimonthly month-end logic uses February 29 in leap years."""
    periods = CalendarEngine(settings(date(2028, 2, 15), "semimonthly")).generate(
        date(2028, 3, 15)
    )

    assert period_dates(periods) == [
        (date(2028, 2, 15), date(2028, 2, 15), date(2028, 2, 28)),
        (date(2028, 2, 29), date(2028, 2, 29), date(2028, 3, 14)),
        (date(2028, 3, 15), date(2028, 3, 15), date(2028, 3, 30)),
    ]


def test_end_date_before_first_paycheck_returns_empty_periods() -> None:
    """An end date before the first paycheck produces no periods."""
    periods = CalendarEngine(settings(date(2026, 1, 2), "biweekly")).generate(
        date(2026, 1, 1)
    )

    assert periods == []


def test_end_date_equal_to_first_paycheck_includes_same_day_period() -> None:
    """The forecast end date is inclusive of a paycheck on the same day."""
    periods = CalendarEngine(settings(date(2026, 1, 2), "biweekly")).generate(
        date(2026, 1, 2)
    )

    assert period_dates(periods) == [
        (date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 15)),
    ]


def test_generation_is_chronological_and_deterministic() -> None:
    """Repeated generation returns the same chronological pay periods."""
    engine = CalendarEngine(settings(date(2026, 7, 17), "biweekly"))

    first = engine.generate(date(2026, 8, 28))
    second = engine.generate(date(2026, 8, 28))

    assert period_dates(first) == period_dates(second)
    assert [period.pay_date for period in first] == sorted(
        period.pay_date for period in first
    )
    assert len({period.pay_date for period in first}) == len(first)
