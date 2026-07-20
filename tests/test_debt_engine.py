from datetime import date

import pytest

from app.debt_engine import DebtEngine
from app.models import Debt, ScheduledPayment


def test_debt_interest_calculation_uses_paycheck_rate():
    debt = Debt("Card", balance=2600, apr=26, minimum=100, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    interest = engine.accrue_interest()

    assert interest["Card"] == pytest.approx(26.0)
    assert debt.balance == pytest.approx(2626.0)
    assert debt.total_interest_paid == pytest.approx(26.0)


def test_minimum_payments_are_applied_for_scheduled_debts_only():
    card = Debt("Card", balance=500, apr=0, minimum=50, due_day=1, snowball_order=1)
    loan = Debt("Loan", balance=800, apr=0, minimum=80, due_day=1, snowball_order=2)
    scheduled = [ScheduledPayment("Card", 50, date(2026, 7, 20), "debt")]

    payments = DebtEngine([card, loan]).pay_minimums_due(scheduled)

    assert payments == {"Card": 50.0}
    assert card.balance == 450.0
    assert loan.balance == 800.0


def test_snowball_rolls_over_across_multiple_debts():
    debts = [
        Debt("A", balance=50, apr=0, minimum=10, due_day=1, snowball_order=1),
        Debt("B", balance=75, apr=0, minimum=20, due_day=1, snowball_order=2),
        Debt("C", balance=200, apr=0, minimum=30, due_day=1, snowball_order=3),
    ]
    engine = DebtEngine(debts)

    payments, remaining = engine.apply_snowball(150)

    assert payments == {"A": 50.0, "B": 75.0, "C": 25.0}
    assert remaining == 0.0
    assert [debt.name for debt in engine.active_debts] == ["C"]
    assert engine.freed_minimum_payment == 30.0


def test_empty_debt_list_handles_all_operations():
    engine = DebtEngine([])

    assert engine.accrue_interest() == {}
    assert engine.pay_minimums_due([]) == {}
    assert engine.apply_snowball(100) == ({}, 100.0)
    assert engine.process_pay_period([], 100)["active_debts"] == []


def test_zero_snowball_payment_does_not_change_debt():
    debt = Debt("Card", balance=500, apr=0, minimum=50, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(0)

    assert payments == {}
    assert remaining == 0.0
    assert debt.balance == 500.0


def test_exact_debt_payoff_removes_debt_and_frees_minimum():
    debt = Debt("Card", balance=100, apr=0, minimum=25, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(100)

    assert payments == {"Card": 100.0}
    assert remaining == 0.0
    assert engine.active_debts == []
    assert engine.paid_off_summary()[0]["name"] == "Card"
    assert engine.freed_minimum_payment == 25.0


def test_multiple_debts_paid_off_in_one_snowball_payment():
    debts = [
        Debt("A", balance=40, apr=0, minimum=10, due_day=1, snowball_order=1),
        Debt("B", balance=60, apr=0, minimum=20, due_day=1, snowball_order=2),
        Debt("C", balance=80, apr=0, minimum=30, due_day=1, snowball_order=3),
    ]
    engine = DebtEngine(debts)

    payments, remaining = engine.apply_snowball(100)

    assert payments == {"A": 40.0, "B": 60.0}
    assert remaining == 0.0
    assert [debt.name for debt in engine.active_debts] == ["C"]
    assert engine.freed_minimum_payment == 30.0


def test_very_large_snowball_payment_pays_all_debts_and_returns_leftover():
    debts = [
        Debt("A", balance=40, apr=0, minimum=10, due_day=1, snowball_order=1),
        Debt("B", balance=60, apr=0, minimum=20, due_day=1, snowball_order=2),
    ]
    engine = DebtEngine(debts)

    payments, remaining = engine.apply_snowball(1000)

    assert payments == {"A": 40.0, "B": 60.0}
    assert remaining == 900.0
    assert engine.active_debts == []


def test_freed_minimum_is_not_double_counted_in_process_pay_period_regression():
    debts = [
        Debt("A", balance=50, apr=0, minimum=50, due_day=1, snowball_order=1),
        Debt("B", balance=500, apr=0, minimum=25, due_day=1, snowball_order=2),
    ]
    scheduled = [ScheduledPayment("A", 50, date(2026, 1, 1), "debt")]
    engine = DebtEngine(debts)

    result = engine.process_pay_period(scheduled, 100)

    assert result["minimums"] == {"A": 50.0}
    assert result["snowball"] == {"B": 100.0}
    assert result["freed_minimum_payment"] == 50.0
