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

    assert payments == {"Card": Decimal("50.00")}
    assert card.balance == Decimal("450.00")
    assert loan.balance == Decimal("800.00")


def test_snowball_rolls_over_across_multiple_debts():
    debts = [
        Debt("A", balance=50, apr=0, minimum=10, due_day=1, snowball_order=1),
        Debt("B", balance=75, apr=0, minimum=20, due_day=1, snowball_order=2),
        Debt("C", balance=200, apr=0, minimum=30, due_day=1, snowball_order=3),
    ]
    engine = DebtEngine(debts)

    payments, remaining = engine.apply_snowball(150)

    assert payments == {"A": Decimal("50.00"), "B": Decimal("75.00"), "C": Decimal("25.00")}
    assert remaining == Decimal("0.00")
    assert [debt.name for debt in engine.active_debts] == ["C"]
    assert engine.freed_minimum_payment == Decimal("30.00")


def test_empty_debt_list_handles_all_operations():
    engine = DebtEngine([])

    assert engine.accrue_interest() == {}
    assert engine.pay_minimums_due([]) == {}
    assert engine.apply_snowball(100) == ({}, Decimal("100.00"))
    assert engine.process_pay_period([], 100)["active_debts"] == []


def test_zero_snowball_payment_does_not_change_debt():
    debt = Debt("Card", balance=500, apr=0, minimum=50, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(0)

    assert payments == {}
    assert remaining == Decimal("0.00")
    assert debt.balance == Decimal("500.00")


def test_exact_debt_payoff_removes_debt_and_frees_minimum():
    debt = Debt("Card", balance=100, apr=0, minimum=25, due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    payments, remaining = engine.apply_snowball(100)

    assert payments == {"Card": Decimal("100.00")}
    assert remaining == Decimal("0.00")
    assert engine.active_debts == []
    assert engine.paid_off_summary()[0]["name"] == "Card"
    assert engine.freed_minimum_payment == Decimal("25.00")


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

    assert payments == {"A": Decimal("40.00"), "B": Decimal("60.00")}
    assert remaining == Decimal("0.00")
    assert [debt.name for debt in engine.active_debts] == ["C"]
    assert engine.freed_minimum_payment == Decimal("30.00")


def test_very_large_snowball_payment_pays_all_debts_and_returns_leftover():
    debts = [
        Debt("A", balance=40, apr=0, minimum=10, due_day=1, snowball_order=1),
        Debt("B", balance=60, apr=0, minimum=20, due_day=1, snowball_order=2),
    ]
    engine = DebtEngine(debts)

    payments, remaining = engine.apply_snowball(1000)

    assert payments == {"A": Decimal("40.00"), "B": Decimal("60.00")}
    assert remaining == Decimal("900.00")
    assert engine.active_debts == []


def test_freed_minimum_is_not_double_counted_in_process_pay_period_regression():
    debts = [
        Debt("A", balance=50, apr=0, minimum=50, due_day=1, snowball_order=1),
        Debt("B", balance=500, apr=0, minimum=25, due_day=1, snowball_order=2),
    ]
    scheduled = [ScheduledPayment("A", 50, date(2026, 1, 1), "debt")]
    engine = DebtEngine(debts)

    result = engine.process_pay_period(scheduled, 100)

    assert result["minimums"] == {"A": Decimal("50.00")}
    assert result["snowball"] == {"B": Decimal("100.00")}
    assert result["freed_minimum_payment"] == Decimal("50.00")


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

    assert period_one["minimums"] == {
        "Debt A": Decimal("10.00"),
        "Debt B": Decimal("20.00"),
        "Debt C": Decimal("30.00"),
    }
    assert period_one["snowball"] == {
        "Debt A": Decimal("90.00"),
        "Debt B": Decimal("60.00"),
    }
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

    assert period_two["minimums"] == {
        "Debt B": Decimal("20.00"),
        "Debt C": Decimal("30.00"),
    }
    assert period_two["snowball"] == {"Debt B": Decimal("50.00")}
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

    assert result["interest"] == {
        "Debt A": Decimal("1.00"),
        "Debt B": Decimal("2.00"),
        "Debt C": Decimal("3.00"),
    }
    assert result["minimums"] == {
        "Debt A": Decimal("10.00"),
        "Debt B": Decimal("20.00"),
        "Debt C": Decimal("30.00"),
    }
    assert result["snowball"] == {
        "Debt A": Decimal("91.00"),
        "Debt B": Decimal("4.00"),
    }
    assert "Debt C" not in result["snowball"]
    assert money(debts[0].balance) == Decimal("0.00")
    assert money(debts[1].balance) == Decimal("178.00")
    assert money(debts[2].balance) == Decimal("273.00")
    assert [debt["name"] for debt in result["active_debts"]] == ["Debt B", "Debt C"]
    assert [debt["name"] for debt in result["paid_off_debts"]] == ["Debt A"]
    assert money(debts[1].total_interest_paid) == Decimal("2.00")
    assert money(debts[2].total_interest_paid) == Decimal("3.00")


def test_repeating_apr_interest_is_reported_with_half_up_cents():
    debt = Debt("Card", balance="1000.00", apr="17.99", minimum="0.00", due_day=1, snowball_order=1)

    interest = debt.add_interest()

    assert interest == Decimal("6.92")
    assert money(debt.total_interest_paid) == Decimal("6.92")


def test_half_cent_interest_rounds_up_for_reported_interest():
    debt = Debt("Card", balance="1.00", apr="13.00", minimum="0.00", due_day=1, snowball_order=1)

    interest = debt.add_interest()

    assert interest == Decimal("0.01")


def test_payment_equal_to_balance_plus_interest_pays_off_without_negative_balance():
    debt = Debt("Card", balance="100.00", apr="26.00", minimum="0.00", due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    result = engine.process_pay_period([], Decimal("101.00"))

    assert result["interest"] == {"Card": Decimal("1.00")}
    assert result["snowball"] == {"Card": Decimal("101.00")}
    assert debt.balance == Decimal("0.00")
    assert debt.total_paid == Decimal("101.00")


def test_payment_exceeding_balance_plus_interest_returns_remaining_cash():
    debt = Debt("Card", balance="100.00", apr="26.00", minimum="0.00", due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    result = engine.process_pay_period([], Decimal("150.00"))

    assert result["snowball"] == {"Card": Decimal("101.00")}
    assert result["snowball_remaining"] == Decimal("49.00")
    assert debt.balance == Decimal("0.00")


def test_zero_percent_debt_has_no_interest_and_no_negative_zero():
    debt = Debt("Card", balance="100.00", apr="0.00", minimum="0.00", due_day=1, snowball_order=1)
    engine = DebtEngine([debt])

    result = engine.process_pay_period([], Decimal("100.00"))

    assert result["interest"] == {"Card": Decimal("0.00")}
    assert debt.balance == Decimal("0.00")
    assert not debt.balance.is_signed()


def test_very_small_and_large_balances_remain_exact_after_payment():
    small = Debt("Small", balance="0.02", apr="0", minimum="0", due_day=1, snowball_order=1)
    large = Debt(
        "Large",
        balance="999999999.99",
        apr="0",
        minimum="0",
        due_day=1,
        snowball_order=2,
    )
    engine = DebtEngine([small, large])

    payments, remaining = engine.apply_snowball(Decimal("1000000000.00"))

    assert payments == {
        "Small": Decimal("0.02"),
        "Large": Decimal("999999999.98"),
    }
    assert remaining == Decimal("0.00")
    assert small.balance == Decimal("0.00")
    assert large.balance == Decimal("0.00")
    assert large.active is False
