from datetime import date
from decimal import Decimal

from app.debt_engine import DebtEngine
from app.models import Debt, ScheduledPayment


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def test_debt_interest_calculation_uses_paycheck_rate():
    debt = Debt("Card", balance=2600, apr=26, minimum=100, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    interest = engine.accrue_interest()

    assert interest["Card"] == Decimal("26.00")
    assert debt.balance == Decimal("2626.00")
    assert debt.total_interest_paid == Decimal("26.00")


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


def test_remaining_penny_is_explicitly_treated_as_paid():
    debt = Debt(
        "Card",
        balance="1.01",
        apr="0",
        minimum="0.00",
        due_day=1,
        snowball_order=1,
    )
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(Decimal("1.00"))

    assert payments == {"Card": Decimal("1.00")}
    assert remaining == Decimal("0.00")
    assert debt.balance == Decimal("0.00")
    assert debt.active is False
    assert engine.active_debts == []
    assert engine.paid_off_summary()[0]["balance"] == Decimal("0.00")


def test_more_than_one_remaining_cent_is_not_forgiven():
    debt = Debt(
        "Card",
        balance="1.02",
        apr="0",
        minimum="0.00",
        due_day=1,
        snowball_order=1,
    )
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(Decimal("1.00"))

    assert payments == {"Card": Decimal("1.00")}
    assert remaining == Decimal("0.00")
    assert debt.balance == Decimal("0.02")
    assert debt.active is True
    assert [active.name for active in engine.active_debts] == ["Card"]


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


def test_snowball_targets_correct_debt_and_rolls_over_after_payoff_regression():
    debts = [
        Debt("Debt A", balance="100.00", apr="0", minimum="10.00", due_day=1, snowball_order=1),
        Debt("Debt B", balance="200.00", apr="0", minimum="20.00", due_day=1, snowball_order=2),
        Debt("Debt C", balance="300.00", apr="0", minimum="30.00", due_day=1, snowball_order=3),
    ]
    period_one_scheduled = [
        ScheduledPayment("Debt A", 10, date(2026, 1, 1), "debt"),
        ScheduledPayment("Debt B", 20, date(2026, 1, 1), "debt"),
        ScheduledPayment("Debt C", 30, date(2026, 1, 1), "debt"),
    ]
    engine = DebtEngine(debts)

    period_one = engine.process_pay_period(period_one_scheduled, snowball_amount=150)

    assert period_one["minimums"] == {"Debt A": 10.0, "Debt B": 20.0, "Debt C": 30.0}
    assert period_one["snowball"] == {"Debt A": 90.0, "Debt B": 60.0}
    assert "Debt C" not in period_one["snowball"]
    assert money(debts[0].balance) == Decimal("0.00")
    assert money(debts[1].balance) == Decimal("120.00")
    assert money(debts[2].balance) == Decimal("270.00")
    assert money(debts[0].total_paid) == Decimal("100.00")
    assert money(debts[1].total_paid) == Decimal("80.00")
    assert money(debts[2].total_paid) == Decimal("30.00")
    assert money(sum(period_one["minimums"].values())) == Decimal("60.00")
    assert money(sum(period_one["snowball"].values())) == Decimal("150.00")
    assert money(sum(debt.total_paid for debt in debts)) == Decimal("210.00")
    assert [debt["name"] for debt in period_one["paid_off_debts"]] == ["Debt A"]
    assert [debt.name for debt in engine.paid_off_debts] == ["Debt A"]

    period_two_scheduled = [
        ScheduledPayment("Debt B", 20, date(2026, 1, 15), "debt"),
        ScheduledPayment("Debt C", 30, date(2026, 1, 15), "debt"),
    ]

    period_two = engine.process_pay_period(period_two_scheduled, snowball_amount=40)

    assert period_two["minimums"] == {"Debt B": 20.0, "Debt C": 30.0}
    assert period_two["snowball"] == {"Debt B": 50.0}
    assert "Debt A" not in period_two["minimums"]
    assert "Debt A" not in period_two["snowball"]
    assert "Debt C" not in period_two["snowball"]
    assert money(debts[0].balance) == Decimal("0.00")
    assert money(debts[1].balance) == Decimal("50.00")
    assert money(debts[2].balance) == Decimal("240.00")
    assert [debt["name"] for debt in period_two["active_debts"]] == ["Debt B", "Debt C"]
    assert [debt["name"] for debt in period_two["paid_off_debts"]] == ["Debt A"]
    assert [debt.name for debt in engine.paid_off_debts] == ["Debt A"]


def test_interest_accrues_before_minimums_and_snowball_targeting_regression():
    debts = [
        Debt("Debt A", balance="100.00", apr="26", minimum="10.00", due_day=1, snowball_order=1),
        Debt("Debt B", balance="200.00", apr="26", minimum="20.00", due_day=1, snowball_order=2),
        Debt("Debt C", balance="300.00", apr="26", minimum="30.00", due_day=1, snowball_order=3),
    ]
    scheduled = [
        ScheduledPayment("Debt A", 10, date(2026, 1, 1), "debt"),
        ScheduledPayment("Debt B", 20, date(2026, 1, 1), "debt"),
        ScheduledPayment("Debt C", 30, date(2026, 1, 1), "debt"),
    ]
    engine = DebtEngine(debts)

    result = engine.process_pay_period(scheduled, snowball_amount=95)

    assert result["interest"] == {"Debt A": 1.0, "Debt B": 2.0, "Debt C": 3.0}
    assert result["minimums"] == {"Debt A": 10.0, "Debt B": 20.0, "Debt C": 30.0}
    assert result["snowball"] == {"Debt A": 91.0, "Debt B": 4.0}
    assert "Debt C" not in result["snowball"]
    assert money(debts[0].balance) == Decimal("0.00")
    assert money(debts[1].balance) == Decimal("178.00")
    assert money(debts[2].balance) == Decimal("273.00")
    assert [debt["name"] for debt in result["active_debts"]] == ["Debt B", "Debt C"]
    assert [debt["name"] for debt in result["paid_off_debts"]] == ["Debt A"]
    assert money(debts[1].total_interest_paid) == Decimal("2.00")
    assert money(debts[2].total_interest_paid) == Decimal("3.00")
