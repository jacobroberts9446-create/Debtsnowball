from datetime import date
from types import SimpleNamespace

from app.budget_engine import BudgetEngine
from app.models import Bill, BudgetSettings, Debt, PayPeriod


def make_config(
    paycheck=1000,
    starting_savings=0,
    savings_goal=1000,
    rent_per_paycheck=0,
    insurance_per_paycheck=0,
    personal_per_paycheck=0,
    bills=None,
    debts=None,
):
    return SimpleNamespace(
        settings=BudgetSettings(
            paycheck=paycheck,
            first_paycheck=date(2026, 1, 1),
            rent_per_paycheck=rent_per_paycheck,
            insurance_per_paycheck=insurance_per_paycheck,
            personal_per_paycheck=personal_per_paycheck,
            starting_savings=starting_savings,
            savings_goal=savings_goal,
            snowball_split=0.50,
        ),
        bills=bills or [],
        debts=debts or [],
    )


def test_savings_split_before_reaching_goal():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=0, due_day=15, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.savings_contribution == 500.0
    assert summary.savings_balance == 500.0
    assert summary.snowball_payment == 500.0
    assert summary.remaining_cash == 0.0


def test_savings_split_after_reaching_goal():
    config = make_config(
        paycheck=1000,
        starting_savings=1000,
        savings_goal=1000,
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=0, due_day=15, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.savings_contribution == 0.0
    assert summary.savings_balance == 1000.0
    assert summary.snowball_payment == 1000.0
    assert summary.remaining_cash == 0.0


def test_budget_engine_pay_period_calculations():
    config = make_config(
        paycheck=1000,
        starting_savings=200,
        savings_goal=500,
        bills=[Bill("Phone", 100, 10)],
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=100, due_day=12, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 100.0
    assert summary.debt_minimums == 100.0
    assert summary.savings_contribution == 300.0
    assert summary.savings_balance == 500.0
    assert summary.snowball_payment == 500.0
    assert summary.remaining_cash == 0.0
    assert summary.active_debt_balances[0].balance == 400.0


def test_budget_engine_handles_empty_bill_list():
    config = make_config(
        paycheck=500,
        starting_savings=500,
        savings_goal=500,
        debts=[
            Debt("Card", balance=500, apr=0, minimum=50, due_day=10, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 0.0
    assert summary.debt_minimums == 50.0
    assert summary.snowball_payment == 450.0
    assert summary.remaining_cash == 0.0


def test_budget_engine_handles_empty_debt_list():
    config = make_config(
        paycheck=500,
        starting_savings=0,
        savings_goal=100,
        bills=[Bill("Phone", 100, 10)],
        debts=[],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 100.0
    assert summary.debt_minimums == 0.0
    assert summary.savings_contribution == 100.0
    assert summary.snowball_payment == 0.0
    assert summary.remaining_cash == 300.0
    assert summary.active_debt_balances == []


def test_budget_engine_zero_paycheck():
    config = make_config(
        paycheck=0,
        starting_savings=0,
        savings_goal=100,
        bills=[Bill("Phone", 100, 10)],
        debts=[
            Debt("Card", balance=500, apr=0, minimum=50, due_day=10, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 100.0
    assert summary.debt_minimums == 50.0
    assert summary.savings_contribution == 0.0
    assert summary.snowball_payment == 0.0
    assert summary.remaining_cash == -150.0


def test_budget_engine_paycheck_smaller_than_minimum_payments():
    config = make_config(
        paycheck=75,
        starting_savings=0,
        savings_goal=100,
        debts=[
            Debt("Card", balance=500, apr=0, minimum=100, due_day=10, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.debt_minimums == 100.0
    assert summary.savings_contribution == 0.0
    assert summary.snowball_payment == 0.0
    assert summary.remaining_cash == -25.0


def test_savings_goal_reached_mid_pay_period_sends_rest_to_snowball():
    config = make_config(
        paycheck=1000,
        starting_savings=900,
        savings_goal=1000,
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.savings_contribution == 100.0
    assert summary.savings_balance == 1000.0
    assert summary.snowball_payment == 900.0
    assert summary.remaining_cash == 0.0


def test_budget_engine_build_plan_preserves_running_state_regression():
    config = make_config(
        paycheck=500,
        starting_savings=0,
        savings_goal=250,
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)
        ],
    )
    periods = [
        PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14)),
        PayPeriod(date(2026, 1, 15), date(2026, 1, 15), date(2026, 1, 28)),
    ]

    summaries = BudgetEngine(config).build_plan(periods)

    assert summaries[0].savings_contribution == 250.0
    assert summaries[0].snowball_payment == 250.0
    assert summaries[1].savings_contribution == 0.0
    assert summaries[1].snowball_payment == 500.0


def test_per_paycheck_expenses_reduce_surplus():
    config = make_config(
        paycheck=1000,
        starting_savings=1000,
        savings_goal=1000,
        rent_per_paycheck=100,
        insurance_per_paycheck=50,
        personal_per_paycheck=200,
        debts=[Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 350.0
    assert summary.snowball_payment == 650.0
    assert summary.remaining_cash == 0.0


def test_savings_contribution_uses_cash_after_per_paycheck_expenses():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        rent_per_paycheck=100,
        insurance_per_paycheck=50,
        personal_per_paycheck=200,
        debts=[Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.savings_contribution == 325.0
    assert summary.snowball_payment == 325.0


def test_snowball_uses_cash_after_per_paycheck_expenses_and_monthly_bills():
    config = make_config(
        paycheck=1000,
        starting_savings=1000,
        savings_goal=1000,
        rent_per_paycheck=100,
        insurance_per_paycheck=50,
        personal_per_paycheck=200,
        bills=[Bill("Phone", 100, 10)],
        debts=[Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 450.0
    assert summary.snowball_payment == 550.0


def test_zero_per_paycheck_expenses_preserve_existing_behavior():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        rent_per_paycheck=0,
        insurance_per_paycheck=0,
        personal_per_paycheck=0,
        debts=[Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.bills_paid == 0.0
    assert summary.savings_contribution == 500.0
    assert summary.snowball_payment == 500.0


def test_minimum_reservation_accounts_for_interest_before_payoff():
    config = make_config(
        paycheck=100,
        starting_savings=100,
        savings_goal=100,
        debts=[
            Debt(
                "Card",
                balance=23.22,
                apr=27.24,
                minimum=67,
                due_day=10,
                snowball_order=1,
            ),
            Debt("Loan", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=2),
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.debt_minimums == 23.46
    assert summary.snowball_payment == 76.54
    assert summary.remaining_cash == 0.0
