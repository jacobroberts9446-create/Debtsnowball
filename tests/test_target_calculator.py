from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.forecast_engine import ForecastEngine
from app.models import BudgetSettings, Debt, DebtFreeTargetRequest, DebtFreeTargetStatus
from app.target_calculator import DebtFreeTargetCalculator


def make_config(
    paycheck=100,
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
        debts=debts if debts is not None else [debt("Card", 500)],
        scenarios=[],
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


def request(
    target_date=date(2026, 1, 15),
    maximum_extra=Decimal("10000.00"),
    precision=Decimal("0.01"),
    maximum_iterations=100,
):
    return DebtFreeTargetRequest(
        enabled=True,
        target_date=target_date,
        maximum_extra_per_paycheck=maximum_extra,
        precision=precision,
        maximum_iterations=maximum_iterations,
    )


def calculate(config, target_request):
    return DebtFreeTargetCalculator(config, target_request).calculate()


def test_baseline_already_meets_target_and_returns_zero_extra():
    config = make_config(paycheck=500, debts=[debt("Card", 100)])

    result = calculate(config, request(target_date=date(2026, 1, 1)))

    assert result.target_met is True
    assert result.required_extra_per_paycheck == Decimal("0.00")
    assert result.projected_debt_free_date == date(2026, 1, 1)
    assert result.calculation_status == DebtFreeTargetStatus.ALREADY_ON_TRACK
    assert result.iterations_used == 1


def test_exact_target_date_payoff_counts_as_success():
    config = make_config(paycheck=100, debts=[debt("Card", 200)])

    result = calculate(config, request(target_date=date(2026, 1, 15)))

    assert result.required_extra_per_paycheck == Decimal("0.00")
    assert result.projected_debt_free_date == date(2026, 1, 15)
    assert result.target_met is True


def test_calculator_finds_positive_required_payment():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(config, request(target_date=date(2026, 1, 15)))

    assert result.required_extra_per_paycheck == Decimal("150.00")
    assert result.projected_debt_free_date == date(2026, 1, 15)
    assert result.calculation_status == DebtFreeTargetStatus.TARGET_MET


def test_returned_payment_is_minimum_at_configured_precision():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(config, request(target_date=date(2026, 1, 15)))
    lower_result = calculate(
        config,
        request(
            target_date=date(2026, 1, 15),
            maximum_extra=result.required_extra_per_paycheck - Decimal("0.01"),
        ),
    )

    assert result.required_extra_per_paycheck == Decimal("150.00")
    assert result.lower_bound_tested == Decimal("149.99")
    assert lower_result.target_met is False


def test_maximum_extra_exactly_meets_target():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(
        config,
        request(
            target_date=date(2026, 1, 15),
            maximum_extra=Decimal("150.00"),
        ),
    )

    assert result.target_met is True
    assert result.required_extra_per_paycheck == Decimal("150.00")
    assert result.maximum_extra_tested == Decimal("150.00")


def test_target_unreachable_at_maximum_extra():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(
        config,
        request(
            target_date=date(2026, 1, 15),
            maximum_extra=Decimal("149.99"),
        ),
    )

    assert result.target_met is False
    assert result.required_extra_per_paycheck is None
    assert result.calculation_status == DebtFreeTargetStatus.UNREACHABLE
    assert result.maximum_extra_tested == Decimal("149.99")


def test_no_debts_returns_zero_required():
    config = make_config(debts=[])

    result = calculate(config, request(target_date=date(2026, 1, 1)))

    assert result.target_met is True
    assert result.required_extra_per_paycheck == Decimal("0.00")
    assert result.projected_debt_free_date == date(2026, 1, 1)
    assert result.calculation_status == DebtFreeTargetStatus.NO_DEBT


def test_target_before_start_date_is_rejected():
    config = make_config(first_paycheck=date(2026, 1, 2))

    with pytest.raises(ValueError, match="before"):
        calculate(config, request(target_date=date(2026, 1, 1)))


def test_target_equal_to_start_date_is_handled():
    config = make_config(paycheck=0, debts=[debt("Card", 100)])

    result = calculate(
        config,
        request(target_date=date(2026, 1, 1), maximum_extra=Decimal("100.00")),
    )

    assert result.target_met is True
    assert result.required_extra_per_paycheck == Decimal("99.99")
    assert result.projected_debt_free_date == date(2026, 1, 1)


@pytest.mark.parametrize(
    ("target_request", "message"),
    [
        (DebtFreeTargetRequest(enabled=True, target_date=None), "target_date"),
        (
            request(precision=Decimal("0.00")),
            "precision",
        ),
        (
            request(maximum_extra=Decimal("-0.01")),
            "negative",
        ),
        (
            request(maximum_extra=Decimal("NaN")),
            "finite",
        ),
        (
            request(precision=Decimal("Infinity")),
            "finite",
        ),
    ],
)
def test_invalid_target_request_values_are_rejected(target_request, message):
    with pytest.raises(ValueError, match=message):
        calculate(make_config(), target_request)


def test_zero_maximum_extra_works_correctly():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(
        config,
        request(
            target_date=date(2026, 1, 15),
            maximum_extra=Decimal("0.00"),
        ),
    )

    assert result.target_met is False
    assert result.required_extra_per_paycheck is None
    assert result.upper_bound_tested == Decimal("0.00")


def test_state_is_not_mutated():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])
    original = deepcopy(config)

    calculate(config, request(target_date=date(2026, 1, 15)))

    assert config.debts[0].balance == original.debts[0].balance
    assert config.debts[0].total_paid == original.debts[0].total_paid
    assert config.settings.starting_savings == original.settings.starting_savings
    assert config.settings.first_paycheck == original.settings.first_paycheck


def test_repeated_calls_are_deterministic():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])
    target_request = request(target_date=date(2026, 1, 15))

    first = calculate(config, target_request)
    second = calculate(config, target_request)

    assert first.required_extra_per_paycheck == second.required_extra_per_paycheck
    assert first.projected_debt_free_date == second.projected_debt_free_date
    assert first.iterations_used == second.iterations_used


def test_interest_bearing_debts_work():
    config = make_config(paycheck=100, debts=[debt("Card", 500, apr=26)])

    result = calculate(config, request(target_date=date(2026, 1, 15)))

    assert result.required_extra_per_paycheck == Decimal("153.75")
    assert result.total_interest == Decimal("7.51")


def test_minimum_payments_still_occur_before_snowball():
    config = make_config(
        paycheck=0,
        debts=[debt("Card", 150, minimum=50, due_day=10)],
    )

    result = calculate(
        config,
        request(target_date=date(2026, 1, 1), maximum_extra=Decimal("100.00")),
    )

    assert result.required_extra_per_paycheck == Decimal("99.99")
    assert result.total_snowball_paid == Decimal("99.99")


def test_savings_behavior_is_unchanged_by_extra_payment():
    config = make_config(
        paycheck=100,
        starting_savings=0,
        savings_goal=100,
        debts=[debt("Card", 100)],
    )

    result = calculate(
        config,
        request(target_date=date(2026, 1, 1), maximum_extra=Decimal("50.00")),
    )

    assert result.required_extra_per_paycheck == Decimal("49.99")
    assert result.total_snowball_paid == Decimal("99.99")


def test_binary_search_iteration_count_is_efficient():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(config, request(target_date=date(2026, 1, 15)))

    assert result.iterations_used < 40


def test_decimal_precision_is_preserved():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])

    result = calculate(
        config,
        request(target_date=date(2026, 1, 15), precision=Decimal("0.10")),
    )

    assert result.required_extra_per_paycheck == Decimal("150.00")
    assert result.precision == Decimal("0.10")


def test_payoff_order_remains_unchanged():
    debts = [
        debt("A", 500, order=1),
        debt("B", 100, order=2),
    ]
    config = make_config(paycheck=100, debts=debts)

    calculate(config, request(target_date=date(2026, 1, 15)))

    assert [debt.name for debt in config.debts] == ["A", "B"]
    assert [debt.snowball_order for debt in config.debts] == [1, 2]


def test_existing_forecast_results_remain_unchanged_after_calculation():
    config = make_config(paycheck=100, debts=[debt("Card", 500)])
    before = ForecastEngine(config).forecast()

    calculate(config, request(target_date=date(2026, 1, 15)))
    after = ForecastEngine(config).forecast()

    assert before.debt_free_date == after.debt_free_date
    assert before.total_interest_paid == after.total_interest_paid
    assert before.total_snowball_payments == after.total_snowball_payments
