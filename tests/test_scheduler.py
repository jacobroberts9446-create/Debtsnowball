from datetime import date
from types import SimpleNamespace

from app.models import Bill, Debt, PayPeriod, ScheduledPayment
from app.scheduler import Scheduler
import pytest


def test_scheduler_assigns_bills_to_last_paycheck_before_due_date():
    config = SimpleNamespace(
        bills=[
            Bill("Phone", 100, 12),
            Bill("Payday Bill", 50, 17),
            Bill("New Year Bill", 75, 1),
        ],
        debts=[],
    )
    periods = [
        PayPeriod(date(2026, 12, 4), date(2026, 12, 4), date(2026, 12, 17)),
        PayPeriod(date(2026, 12, 18), date(2026, 12, 18), date(2026, 12, 31)),
    ]

    schedule = Scheduler(config).schedule_for_periods(periods)

    first_paycheck = [
        (payment.name, payment.due_date) for payment in schedule[date(2026, 12, 4)]
    ]
    second_paycheck = [
        (payment.name, payment.due_date) for payment in schedule[date(2026, 12, 18)]
    ]

    assert ("Payday Bill", date(2026, 12, 17)) in first_paycheck
    assert ("New Year Bill", date(2027, 1, 1)) in second_paycheck
    assert all(
        payment.due_date > date(2026, 12, 18)
        for payment in schedule[date(2026, 12, 18)]
    )


def test_scheduler_handles_month_end_due_dates():
    config = SimpleNamespace(bills=[Bill("Month End", 20, 31)], debts=[])
    payments = Scheduler(config).payments_for_paycheck(
        date(2027, 2, 14), date(2027, 2, 28)
    )

    assert len(payments) == 1
    assert payments[0].due_date == date(2027, 2, 28)


def test_scheduler_handles_leap_year_dates():
    config = SimpleNamespace(bills=[Bill("Leap Bill", 29, 29)], debts=[])
    payments = Scheduler(config).payments_for_paycheck(
        date(2028, 2, 16), date(2028, 3, 1)
    )

    assert [(payment.name, payment.due_date) for payment in payments] == [
        ("Leap Bill", date(2028, 2, 29))
    ]


def test_scheduler_december_to_january_transition():
    config = SimpleNamespace(bills=[Bill("January Rent", 1200, 1)], debts=[])
    payments = Scheduler(config).payments_for_paycheck(
        date(2026, 12, 18), date(2027, 1, 1)
    )

    assert len(payments) == 1
    assert payments[0].due_date == date(2027, 1, 1)


def test_scheduler_preserves_duplicate_due_dates_for_bills_and_debts():
    config = SimpleNamespace(
        bills=[
            Bill("Phone", 100, 10),
            Bill("Internet", 80, 10),
        ],
        debts=[
            Debt("Card", balance=500, apr=0, minimum=50, due_day=10, snowball_order=1),
        ],
    )
    payments = Scheduler(config).payments_for_paycheck(
        date(2026, 1, 1), date(2026, 1, 15)
    )

    assert [
        (payment.name, payment.payment_type, payment.due_date) for payment in payments
    ] == [
        ("Internet", "bill", date(2026, 1, 10)),
        ("Phone", "bill", date(2026, 1, 10)),
        ("Card", "debt", date(2026, 1, 10)),
    ]


def test_scheduler_assigns_bills_due_on_paycheck_date_to_previous_paycheck():
    config = SimpleNamespace(bills=[Bill("Payday Bill", 50, 15)], debts=[])

    previous_paycheck = Scheduler(config).payments_for_paycheck(
        date(2026, 1, 1), date(2026, 1, 15)
    )
    current_paycheck = Scheduler(config).payments_for_paycheck(
        date(2026, 1, 15), date(2026, 1, 29)
    )

    assert [payment.due_date for payment in previous_paycheck] == [date(2026, 1, 15)]
    assert current_paycheck == []


def test_scheduler_validation_reports_missing_payment_regression():
    config = SimpleNamespace(bills=[Bill("Phone", 100, 12)], debts=[])
    periods = [PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))]

    issues = Scheduler(config).validate_schedule(
        periods, schedule={date(2026, 1, 1): []}
    )

    assert issues
    assert "Phone" in issues[0]


def test_scheduler_rejects_non_forward_paycheck_window():
    config = SimpleNamespace(bills=[], debts=[])

    with pytest.raises(ValueError):
        Scheduler(config).payments_for_paycheck(date(2026, 1, 1), date(2026, 1, 1))


def test_scheduler_payments_for_dates_includes_end_date():
    config = SimpleNamespace(bills=[Bill("Phone", 100, 14)], debts=[])
    payments = Scheduler(config).payments_for_dates(date(2026, 1, 1), date(2026, 1, 14))

    assert len(payments) == 1
    assert payments[0].due_date == date(2026, 1, 14)


def test_scheduler_validation_reports_unexpected_and_wrong_paycheck_regressions():
    config = SimpleNamespace(bills=[Bill("Phone", 100, 12)], debts=[])
    periods = [
        PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14)),
        PayPeriod(date(2026, 1, 15), date(2026, 1, 15), date(2026, 1, 28)),
    ]
    valid_payment = Scheduler(config).payments_for_paycheck(
        date(2026, 1, 1), date(2026, 1, 15)
    )[0]

    wrong_paycheck_issues = Scheduler(config).validate_schedule(
        periods,
        schedule={date(2026, 1, 15): [valid_payment]},
    )
    unexpected_issues = Scheduler(config).validate_schedule(
        periods,
        schedule={
            date(2026, 1, 1): [
                valid_payment,
                ScheduledPayment("Extra", 1, date(2026, 1, 13), "bill"),
            ]
        },
    )

    assert any("instead of" in issue for issue in wrong_paycheck_issues)
    assert any("unexpectedly scheduled" in issue for issue in unexpected_issues)
