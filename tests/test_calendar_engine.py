"""Direct tests for CalendarEngine pay-period generation."""

from datetime import date
from types import SimpleNamespace

import pytest

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
    """Month-end schedules return to month end after a shorter February."""
    periods = CalendarEngine(settings(date(2026, 12, 31), "monthly")).generate(
        date(2027, 2, 28)
    )

    assert period_dates(periods) == [
        (date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 30)),
        (date(2027, 1, 31), date(2027, 1, 31), date(2027, 2, 27)),
        (date(2027, 2, 28), date(2027, 2, 28), date(2027, 3, 30)),
    ]


def test_monthly_generation_handles_leap_year_february() -> None:
    """Monthly periods preserve February 29 when the target month has it."""
    periods = CalendarEngine(settings(date(2028, 1, 31), "monthly")).generate(
        date(2028, 2, 29)
    )

    assert period_dates(periods) == [
        (date(2028, 1, 31), date(2028, 1, 31), date(2028, 2, 28)),
        (date(2028, 2, 29), date(2028, 2, 29), date(2028, 3, 30)),
    ]


def test_monthly_january_31_preserves_month_end_non_leap_year() -> None:
    """A January month-end anchor returns to month end after February."""
    periods = CalendarEngine(settings(date(2027, 1, 31), "monthly")).generate(
        date(2027, 4, 30)
    )

    assert [period.pay_date for period in periods] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


def test_monthly_january_31_preserves_month_end_leap_year() -> None:
    """A January month-end anchor uses leap day and later month ends."""
    periods = CalendarEngine(settings(date(2028, 1, 31), "monthly")).generate(
        date(2028, 4, 30)
    )

    assert [period.pay_date for period in periods] == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
        date(2028, 4, 30),
    ]


def test_monthly_january_30_retains_day_after_february_clamp() -> None:
    """A day-30 anchor returns to day 30 rather than drifting to day 28."""
    periods = CalendarEngine(settings(date(2027, 1, 30), "monthly")).generate(
        date(2027, 4, 30)
    )

    assert [period.pay_date for period in periods] == [
        date(2027, 1, 30),
        date(2027, 2, 28),
        date(2027, 3, 30),
        date(2027, 4, 30),
    ]


def test_monthly_january_29_retains_day_after_february_clamp() -> None:
    """A day-29 anchor returns to day 29 after a non-leap February."""
    periods = CalendarEngine(settings(date(2027, 1, 29), "monthly")).generate(
        date(2027, 4, 29)
    )

    assert [period.pay_date for period in periods] == [
        date(2027, 1, 29),
        date(2027, 2, 28),
        date(2027, 3, 29),
        date(2027, 4, 29),
    ]


@pytest.mark.parametrize(
    ("first_paycheck", "expected"),
    [
        (
            date(2027, 2, 28),
            [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)],
        ),
        (
            date(2028, 2, 29),
            [date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)],
        ),
        (
            date(2027, 11, 15),
            [date(2027, 11, 15), date(2027, 12, 15), date(2028, 1, 15)],
        ),
    ],
)
def test_monthly_anchors_cover_february_midmonth_and_year_boundary(
    first_paycheck,
    expected,
) -> None:
    """Monthly anchors remain deterministic across February and year boundaries."""
    periods = CalendarEngine(settings(first_paycheck, "monthly")).generate(expected[-1])

    assert [period.pay_date for period in periods] == expected


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
