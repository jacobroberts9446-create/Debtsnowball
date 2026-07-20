from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.forecast_engine import ForecastEngine
from app.models import Bill, BudgetSettings, Debt


def make_config(
    paycheck=1000,
    first_paycheck=date(2026, 1, 1),
    starting_savings=0,
    savings_goal=0,
    rent_per_paycheck=0,
    insurance_per_paycheck=0,
    personal_per_paycheck=0,
    bills=None,
    debts=None,
):
    return SimpleNamespace(
        settings=BudgetSettings(
            paycheck=paycheck,
            first_paycheck=first_paycheck,
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


def test_forecast_handles_no_debts():
    config = make_config(starting_savings=100, savings_goal=100, debts=[])

    forecast = ForecastEngine(config).forecast()

    assert forecast.debt_free_date == date(2026, 1, 1)
    assert forecast.savings_goal_date == date(2026, 1, 1)
    assert forecast.completed is True
    assert forecast.remaining_debt == Decimal("0.00")


def test_forecast_marks_savings_goal_already_reached():
    config = make_config(
        starting_savings=500,
        savings_goal=500,
        debts=[Debt("Card", balance=100, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.savings_goal_date == date(2026, 1, 1)
    assert forecast.debt_free_date == date(2026, 1, 1)


def test_forecast_one_debt_paid_successfully():
    config = make_config(
        paycheck=200,
        starting_savings=0,
        savings_goal=0,
        debts=[Debt("Card", balance=200, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.completed is True
    assert forecast.debt_free_date == date(2026, 1, 1)
    assert forecast.total_snowball_payments == Decimal("200.00")
    assert forecast.debt_payoffs[0].payoff_date == date(2026, 1, 1)


def test_forecast_multiple_debts_paid_using_snowball_rollover():
    config = make_config(
        paycheck=500,
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("A", balance=100, apr=0, minimum=0, due_day=10, snowball_order=1),
            Debt("B", balance=250, apr=0, minimum=0, due_day=10, snowball_order=2),
        ],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.debt_free_date == date(2026, 1, 1)
    assert forecast.remaining_debt == Decimal("0.00")
    assert [payoff.payoff_date for payoff in forecast.debt_payoffs] == [
        date(2026, 1, 1),
        date(2026, 1, 1),
    ]


def test_forecast_tracks_interest_accrual():
    config = make_config(
        paycheck=3000,
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("Card", balance=2600, apr=26, minimum=0, due_day=10, snowball_order=1)
        ],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.total_interest_paid == Decimal("26.00")
    assert forecast.debt_payoffs[0].total_interest_paid == Decimal("26.00")


def test_debt_free_date_uses_paycheck_date():
    config = make_config(
        first_paycheck=date(2026, 1, 2),
        paycheck=100,
        starting_savings=0,
        savings_goal=0,
        debts=[Debt("Card", balance=100, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.debt_free_date == date(2026, 1, 2)
    assert forecast.debt_free_date in {period.paycheck_date for period in forecast.periods}


def test_savings_goal_reached_during_forecast():
    config = make_config(
        paycheck=1000,
        starting_savings=900,
        savings_goal=1000,
        debts=[],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.savings_goal_date == date(2026, 1, 1)
    assert forecast.ending_savings == Decimal("1000.00")


def test_forecast_does_not_mutate_original_debt_objects():
    debt = Debt("Card", balance=100, apr=0, minimum=0, due_day=10, snowball_order=1)
    config = make_config(paycheck=100, starting_savings=0, savings_goal=0, debts=[debt])

    ForecastEngine(config).forecast()

    assert debt.balance == 100.0
    assert debt.total_paid == 0.0


def test_forecast_does_not_mutate_original_savings_state():
    config = make_config(
        paycheck=100,
        starting_savings=0,
        savings_goal=100,
        debts=[],
    )

    ForecastEngine(config).forecast()

    assert config.settings.starting_savings == 0.0


def test_maximum_horizon_reached_with_remaining_debt():
    config = make_config(
        paycheck=0,
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)
        ],
    )

    forecast = ForecastEngine(config, max_years=1).forecast()

    assert forecast.completed is False
    assert forecast.debt_free_date is None
    assert forecast.remaining_debt == Decimal("1000.00")


def test_decimal_values_remain_rounded():
    config = make_config(
        paycheck=100,
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("Card", balance=33.335, apr=0, minimum=0, due_day=10, snowball_order=1)
        ],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.starting_debt == Decimal("33.34")
    assert forecast.remaining_debt == Decimal("0.00")
    assert all(period.total_debt_balance.as_tuple().exponent == -2 for period in forecast.periods)


def test_leap_year_and_month_boundary_behavior():
    config = make_config(
        paycheck=100,
        first_paycheck=date(2028, 2, 16),
        starting_savings=0,
        savings_goal=0,
        bills=[Bill("Leap Bill", 10, 29)],
        debts=[Debt("Card", balance=90, apr=0, minimum=0, due_day=31, snowball_order=1)],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.periods[0].paycheck_date == date(2028, 2, 16)
    assert forecast.debt_free_date == date(2028, 2, 16)
    assert forecast.periods[0].total_debt_balance == Decimal("0.00")


def test_forecast_uses_corrected_budget_engine_fixed_expense_calculations():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        rent_per_paycheck=100,
        insurance_per_paycheck=50,
        personal_per_paycheck=200,
        debts=[Debt("Card", balance=1000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.periods[0].savings_balance == Decimal("325.00")
    assert forecast.periods[0].snowball_paid == Decimal("325.00")
    assert forecast.periods[0].total_debt_balance == Decimal("675.00")


def test_forecast_total_debt_trends_downward_when_payments_exceed_interest():
    config = make_config(
        paycheck=500,
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("Card", balance=1000, apr=26, minimum=0, due_day=10, snowball_order=1)
        ],
    )

    forecast = ForecastEngine(config).forecast()

    balances = [period.total_debt_balance for period in forecast.periods]
    assert balances[1] < balances[0]
    assert forecast.total_snowball_payments > forecast.total_interest_paid


def test_individual_debt_can_temporarily_increase_when_minimum_is_not_due():
    """Interest can raise an individual balance before its scheduled minimum is due."""
    config = make_config(
        paycheck=200,
        first_paycheck=date(2026, 1, 1),
        starting_savings=0,
        savings_goal=0,
        debts=[
            Debt("A", balance=200, apr=0, minimum=0, due_day=10, snowball_order=1),
            Debt("B", balance=1000, apr=26, minimum=100, due_day=20, snowball_order=2),
        ],
    )

    forecast = ForecastEngine(config).forecast()

    assert forecast.periods[0].total_debt_balance == Decimal("1010.00")
    b_payoff = next(payoff for payoff in forecast.debt_payoffs if payoff.debt_name == "B")
    assert b_payoff.total_interest_paid > Decimal("0.00")
