from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.forecast_engine import ForecastEngine
from app.models import BudgetSettings, Debt, ScenarioDefinition
from app.scenario_engine import ScenarioEngine


def make_config(
    paycheck=1000,
    first_paycheck=date(2026, 1, 1),
    starting_savings=0,
    savings_goal=0,
    debts=None,
):
    return SimpleNamespace(
        settings=BudgetSettings(
            paycheck=paycheck,
            first_paycheck=first_paycheck,
            rent_per_paycheck=0,
            insurance_per_paycheck=0,
            personal_per_paycheck=0,
            starting_savings=starting_savings,
            savings_goal=savings_goal,
            snowball_split=0.50,
        ),
        bills=[],
        debts=debts or [],
    )


def debt(name, balance, order=1, apr=0, minimum=0, due_day=10):
    return Debt(
        name=name,
        balance=balance,
        apr=apr,
        minimum=minimum,
        due_day=due_day,
        snowball_order=order,
    )


def test_baseline_is_always_generated():
    comparison = ScenarioEngine(make_config(), scenarios=[]).compare()

    assert comparison.baseline.name == "Baseline"
    assert comparison.baseline.extra_per_paycheck == Decimal("0.00")
    assert comparison.scenarios == []


def test_zero_extra_scenario_matches_baseline():
    config = make_config(paycheck=100, debts=[debt("Card", 300)])
    scenario = ScenarioDefinition(name="Same")

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].forecast.debt_free_date == (
        comparison.baseline.forecast.debt_free_date
    )
    assert comparison.scenarios[0].forecast.total_snowball_payments == (
        comparison.baseline.forecast.total_snowball_payments
    )


def test_positive_extra_payment_pays_debt_sooner():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].forecast.debt_free_date < (
        comparison.baseline.forecast.debt_free_date
    )


def test_positive_extra_payment_reduces_total_interest():
    config = make_config(paycheck=100, debts=[debt("Card", 1000, apr=26)])
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].forecast.total_interest_paid < (
        comparison.baseline.forecast.total_interest_paid
    )


def test_extra_payment_is_applied_every_paycheck():
    config = make_config(paycheck=100, debts=[debt("Card", 1000)])
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("50.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    first_two = comparison.scenarios[0].periods[:2]
    assert [period.snowball_paid for period in first_two] == [
        Decimal("150.00"),
        Decimal("150.00"),
    ]


def test_extra_payment_does_not_increase_savings():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        debts=[debt("Card", 5000)],
    )
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("500.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].periods[0].savings_balance == (
        comparison.baseline.periods[0].savings_balance
    )


def test_extra_payment_is_not_counted_twice():
    config = make_config(paycheck=100, debts=[debt("Card", 1000)])
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("50.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].periods[0].snowball_paid == Decimal("150.00")


def test_multiple_scenarios_remain_independent():
    config = make_config(paycheck=100, debts=[debt("Card", 1000)])
    scenarios = [
        ScenarioDefinition(name="Small", extra_per_paycheck=Decimal("25.00")),
        ScenarioDefinition(name="Large", extra_per_paycheck=Decimal("100.00")),
    ]

    comparison = ScenarioEngine(config, scenarios).compare()

    assert comparison.scenarios[0].forecast.debt_free_date > (
        comparison.scenarios[1].forecast.debt_free_date
    )
    assert comparison.baseline.forecast.debt_free_date > (
        comparison.scenarios[0].forecast.debt_free_date
    )


def test_original_debts_are_not_mutated():
    original_debt = debt("Card", 300)
    config = make_config(paycheck=100, debts=[original_debt])

    ScenarioEngine(
        config,
        [ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))],
    ).compare()

    assert original_debt.balance == 300.0
    assert original_debt.total_paid == 0.0


def test_original_savings_is_not_mutated():
    config = make_config(
        paycheck=100,
        starting_savings=0,
        savings_goal=100,
        debts=[debt("Card", 100)],
    )

    ScenarioEngine(config, [ScenarioDefinition(name="Extra")]).compare()

    assert config.settings.starting_savings == 0.0


def test_original_configuration_is_not_mutated_by_start_overrides():
    config = make_config(
        paycheck=100,
        first_paycheck=date(2026, 1, 1),
        starting_savings=0,
        debts=[debt("Card", 100)],
    )

    ScenarioEngine(
        config,
        [ScenarioDefinition(name="Extra")],
        starting_savings=Decimal("50.00"),
        forecast_start_date=date(2026, 2, 1),
    ).compare()

    assert config.settings.starting_savings == 0.0
    assert config.settings.first_paycheck == date(2026, 1, 1)


def test_duplicate_scenario_names_are_rejected():
    config = make_config()
    scenarios = [ScenarioDefinition(name="A"), ScenarioDefinition(name="A")]

    with pytest.raises(ValueError, match="unique"):
        ScenarioEngine(config, scenarios).compare()


def test_blank_scenario_name_is_rejected():
    with pytest.raises(ValueError, match="blank"):
        ScenarioDefinition(name=" ")


def test_negative_extra_payment_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        ScenarioDefinition(name="Bad", extra_per_paycheck=Decimal("-0.01"))


def test_invalid_savings_percentage_is_rejected():
    with pytest.raises(ValueError, match="between 0 and 1"):
        ScenarioDefinition(
            name="Bad",
            savings_percentage_override=Decimal("1.01"),
        )


def test_savings_percentage_override_changes_only_that_scenario():
    config = make_config(
        paycheck=1000,
        starting_savings=0,
        savings_goal=1000,
        debts=[debt("Card", 5000)],
    )
    scenario = ScenarioDefinition(
        name="Save Less",
        savings_percentage_override=Decimal("0.25"),
    )

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.baseline.periods[0].savings_balance == Decimal("500.00")
    assert comparison.scenarios[0].periods[0].savings_balance == Decimal("250.00")


def test_snowball_override_changes_payoff_order():
    config = make_config(
        paycheck=100,
        debts=[debt("A", 200, order=1), debt("B", 100, order=2)],
    )
    scenario = ScenarioDefinition(name="B First", snowball_order_override=["B"])

    comparison = ScenarioEngine(config, [scenario]).compare()

    scenario_payoffs = {p.debt_name: p.payoff_date for p in comparison.scenarios[0].debt_payoffs}
    assert scenario_payoffs["B"] < scenario_payoffs["A"]


def test_omitted_debts_are_appended_to_override_order():
    config = make_config(
        paycheck=100,
        debts=[
            debt("A", 100, order=1),
            debt("B", 100, order=2),
            debt("C", 100, order=3),
        ],
    )
    scenario = ScenarioDefinition(name="C First", snowball_order_override=["C"])

    comparison = ScenarioEngine(config, [scenario]).compare()

    payoff_order = [
        payoff.debt_name
        for payoff in sorted(
            comparison.scenarios[0].debt_payoffs,
            key=lambda payoff: payoff.payoff_date,
        )
    ]
    assert payoff_order == ["C", "A", "B"]


def test_unknown_override_debt_is_rejected():
    config = make_config(debts=[debt("A", 100)])
    scenario = ScenarioDefinition(name="Unknown", snowball_order_override=["Missing"])

    with pytest.raises(ValueError, match="unknown debt"):
        ScenarioEngine(config, [scenario]).compare()


def test_duplicate_override_debt_is_rejected():
    with pytest.raises(ValueError, match="duplicates"):
        ScenarioDefinition(name="Duplicate", snowball_order_override=["A", "A"])


def test_maximum_horizon_behavior():
    config = make_config(paycheck=0, debts=[debt("Card", 1000)])
    scenario = ScenarioDefinition(name="No Progress")

    comparison = ScenarioEngine(config, [scenario], max_years=1).compare()

    assert comparison.scenarios[0].forecast.debt_free_date is None
    assert comparison.scenarios[0].forecast.completed is False


def test_decimal_precision_and_rounding():
    config = make_config(paycheck=0, debts=[debt("Card", 100)])
    scenario = ScenarioDefinition(name="Rounded", extra_per_paycheck=Decimal("33.335"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].periods[0].snowball_paid == Decimal("33.34")


def test_scenario_delta_calculations():
    config = make_config(paycheck=100, debts=[debt("Card", 1000, apr=26)])
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))
    engine = ScenarioEngine(config, [scenario])

    comparison = engine.compare()
    delta = engine.calculate_deltas(comparison)[0]

    assert delta.scenario_name == "Extra"
    assert delta.debt_free_days_saved is not None
    assert delta.debt_free_days_saved > 0
    assert delta.interest_saved > Decimal("0.00")
    assert delta.additional_snowball_paid == (
        comparison.scenarios[0].forecast.total_snowball_payments
        - comparison.baseline.forecast.total_snowball_payments
    )
    assert delta.ending_debt_difference == Decimal("0.00")


def test_delta_dates_are_none_when_either_scenario_date_is_unavailable():
    config = make_config(paycheck=0, debts=[debt("Card", 1000)])
    engine = ScenarioEngine(
        config,
        [ScenarioDefinition(name="Still Blocked", extra_per_paycheck=Decimal("0.00"))],
        max_years=1,
    )

    delta = engine.calculate_deltas(engine.compare())[0]

    assert delta.debt_free_days_saved is None


def test_baseline_and_scenario_payoff_dates_use_actual_paycheck_dates():
    config = make_config(
        paycheck=50,
        first_paycheck=date(2026, 1, 2),
        debts=[debt("Card", 100)],
    )
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("50.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.baseline.forecast.debt_free_date == date(2026, 1, 16)
    assert comparison.scenarios[0].forecast.debt_free_date == date(2026, 1, 2)
    assert comparison.scenarios[0].forecast.debt_free_date in {
        period.paycheck_date for period in comparison.scenarios[0].periods
    }


def test_starting_debts_override_does_not_use_live_debts():
    config = make_config(paycheck=100, debts=[debt("Live", 1000)])
    starting_debts = [debt("Snapshot", 100)]

    comparison = ScenarioEngine(config, [], starting_debts=starting_debts).compare()

    assert comparison.baseline.debt_payoffs[0].debt_name == "Snapshot"
    assert config.debts[0].name == "Live"


def test_scenario_baseline_matches_forecast_engine():
    config = make_config(paycheck=100, debts=[debt("Card", 200)])

    comparison = ScenarioEngine(config, []).compare()
    forecast = ForecastEngine(config).forecast()

    assert comparison.baseline.forecast.debt_free_date == forecast.debt_free_date
    assert comparison.baseline.forecast.total_interest_paid == forecast.total_interest_paid
