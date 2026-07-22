from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.budget_engine import BudgetEngine
from app.models import Bill, BudgetSettings, Debt, PayPeriod, ScheduledPayment
from app.money import (
    CENT,
    ZERO_MONEY,
    excel_number,
    format_currency,
    from_cents,
    money,
    round_money,
    to_cents,
    to_decimal,
)


def test_money_constants_are_decimal_values():
    assert CENT == Decimal("0.01")
    assert ZERO_MONEY == Decimal("0.00")


def test_money_parses_strings_and_rounds_half_up_to_cents():
    assert to_decimal("12.345") == Decimal("12.345")
    assert round_money("12.345") == Decimal("12.35")
    assert money(12.3) == Decimal("12.30")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, Decimal("10.00")),
        ("10.01", Decimal("10.01")),
        (Decimal("10.019"), Decimal("10.02")),
        (10.2, Decimal("10.20")),
        (Decimal("-10.015"), Decimal("-10.02")),
        ("999999999999.995", Decimal("1000000000000.00")),
        ("0.004", Decimal("0.00")),
        ("-0.004", Decimal("0.00")),
    ],
)
def test_money_accepts_supported_inputs_and_normalizes_to_cents(value, expected):
    assert money(value) == expected


def test_negative_zero_is_normalized_for_reported_money_values():
    assert money(Decimal("-0.00")) == Decimal("0.00")
    assert round_money("-0.004") == Decimal("0.00")
    assert not money(Decimal("-0.00")).is_signed()
    assert not round_money("-0.004").is_signed()
    assert excel_number(Decimal("-0.00")) == 0.0


def test_format_currency_normalizes_reported_money():
    assert format_currency(Decimal("-0.00")) == "$0.00"
    assert format_currency(Decimal("215")) == "$215.00"
    assert format_currency(Decimal("568.5")) == "$568.50"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (Decimal("-0.00"), 0),
        (Decimal("0.01"), 1),
        (Decimal("0.02"), 2),
        (Decimal("215.00"), 21500),
        (Decimal("568.50"), 56850),
        (Decimal("-12.34"), -1234),
        (Decimal("999999999999.99"), 99999999999999),
        ("1.005", 101),
    ],
)
def test_to_cents_converts_money_without_float_math(value, expected):
    assert to_cents(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, Decimal("0.00")),
        ("0", Decimal("0.00")),
        (1, Decimal("0.01")),
        (2, Decimal("0.02")),
        (21500, Decimal("215.00")),
        (56850, Decimal("568.50")),
        (-1234, Decimal("-12.34")),
        (99999999999999, Decimal("999999999999.99")),
    ],
)
def test_from_cents_converts_integer_storage_to_money(value, expected):
    assert from_cents(value) == expected


@pytest.mark.parametrize("value", ["bad", "21500.5", None, True, 21500.5])
def test_from_cents_rejects_invalid_cent_storage(value):
    with pytest.raises(ValueError):
        from_cents(value)


def test_cent_storage_rejects_sqlite_integer_overflow():
    with pytest.raises(OverflowError):
        to_cents(Decimal("92233720368547758.08"))

    with pytest.raises(OverflowError):
        from_cents(9223372036854775808)


@pytest.mark.parametrize(
    "value",
    [None, True, Decimal("NaN"), Decimal("Infinity"), "not-money"],
)
def test_money_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        money(value)


def test_domain_and_budget_money_fields_are_decimals():
    settings = BudgetSettings(
        paycheck="1000.00",
        first_paycheck=date(2026, 1, 1),
        rent_per_paycheck="100.00",
        insurance_per_paycheck="50.00",
        personal_per_paycheck="25.00",
        starting_savings="0.00",
        savings_goal="500.00",
        snowball_split="0.50",
    )
    bill = Bill("Utility", "100.00", 5)
    debt = Debt("Card", "200.00", "0", "50.00", 5, 1)
    payment = ScheduledPayment("Utility", "100.00", settings.first_paycheck, "bill")
    config = SimpleNamespace(
        settings=settings,
        bills=[bill],
        debts=[debt],
        savings_plan=None,
    )
    period = PayPeriod(
        settings.first_paycheck,
        settings.first_paycheck,
        settings.first_paycheck.replace(day=14),
    )

    summary = BudgetEngine(config).process_pay_period(period)

    assert isinstance(settings.paycheck, Decimal)
    assert isinstance(bill.amount, Decimal)
    assert isinstance(debt.balance, Decimal)
    assert isinstance(payment.amount, Decimal)
    assert isinstance(summary.savings_contribution, Decimal)
    assert isinstance(summary.snowball_payment, Decimal)
