"""Tests for in-memory plan generation."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.budget_setup import BudgetSetupResult
from app.models import Bill, Debt
from app.plan_generation import (
    generate_plan_from_setup,
    monthly_to_per_paycheck,
    setup_to_engine_config,
)
from app.plan_setup import PayFrequency


def setup_result() -> BudgetSetupResult:
    """Build a valid setup result for generation tests."""
    return BudgetSetupResult(
        plan_name="Plan",
        pay_frequency=PayFrequency.BIWEEKLY,
        first_paycheck_date=date(2026, 7, 17),
        net_paycheck_amount=Decimal("2500.00"),
        debts=[
            Debt("Visa", Decimal("100.00"), Decimal("0"), Decimal("10.00"), 1, 1)
        ],
        bills=[Bill("Rent", Decimal("100.00"), 1)],
        monthly_personal_spending=Decimal("260.00"),
        current_savings=Decimal("1000.00"),
        emergency_fund_target=Decimal("1000.00"),
    )


def test_setup_maps_to_existing_engine_config_shape() -> None:
    """Collected setup data maps to config attributes expected by engines."""
    config = setup_to_engine_config(setup_result())

    assert config.settings.paycheck == Decimal("2500.00")
    assert config.settings.first_paycheck == date(2026, 7, 17)
    assert config.settings.pay_frequency == "biweekly"
    assert config.settings.personal_per_paycheck == Decimal("120.00")
    assert config.settings.starting_savings == Decimal("1000.00")
    assert config.settings.savings_goal == Decimal("1000.00")
    assert config.bills[0].name == "Rent"
    assert config.debts[0].name == "Visa"
    assert config.scenarios == []
    assert not config.debt_free_target.enabled
    assert config.savings_plan is None


def test_monthly_personal_spending_converts_by_frequency() -> None:
    """Monthly personal spending maps into per-paycheck engine settings."""
    assert monthly_to_per_paycheck(Decimal("433.33"), PayFrequency.WEEKLY) == Decimal(
        "100.00"
    )
    assert monthly_to_per_paycheck(Decimal("260.00"), PayFrequency.BIWEEKLY) == Decimal(
        "120.00"
    )
    assert monthly_to_per_paycheck(
        Decimal("300.00"), PayFrequency.SEMIMONTHLY
    ) == Decimal("150.00")
    assert monthly_to_per_paycheck(Decimal("300.00"), PayFrequency.MONTHLY) == Decimal(
        "300.00"
    )


def test_valid_setup_generates_plan_in_memory(monkeypatch) -> None:
    """Generation calls ForecastEngine and returns real forecast values."""
    writes = []

    class FakeForecastEngine:
        def __init__(self, config) -> None:
            self.config = config

        def forecast(self):
            writes.append("forecasted")
            return SimpleNamespace(
                starting_debt=Decimal("100.00"),
                debt_free_date=date(2026, 7, 31),
                total_interest_paid=Decimal("0.00"),
                ending_savings=Decimal("1200.00"),
                periods=[SimpleNamespace(snowball_paid=Decimal("50.00"))],
            )

    monkeypatch.setattr("app.plan_generation.ForecastEngine", FakeForecastEngine)

    summary = generate_plan_from_setup(setup_result())

    assert writes == ["forecasted"]
    assert summary.plan_name == "Plan"
    assert summary.debt_count == 1
    assert summary.total_starting_debt == Decimal("100.00")
    assert summary.projected_debt_free_date == date(2026, 7, 31)
    assert summary.projected_payoff_duration_days == 14
    assert summary.first_period_snowball_amount == Decimal("50.00")
    assert summary.savings_goal_met is True


def test_no_database_or_workbook_generation_is_invoked(monkeypatch) -> None:
    """Plan generation stays entirely inside the forecast boundary."""
    forbidden = []

    def fail(*_args, **_kwargs):
        forbidden.append("called")
        raise AssertionError("file boundary should not be called")

    monkeypatch.setattr("app.database.Database", fail)
    monkeypatch.setattr("app.excel_writer.ExcelWriter", fail)

    summary = generate_plan_from_setup(setup_result())

    assert forbidden == []
    assert summary.plan_name == "Plan"


def test_generation_validation_errors_propagate() -> None:
    """Validation errors are left for the setup workflow to display and retry."""
    invalid = setup_result()
    invalid = BudgetSetupResult(
        plan_name=invalid.plan_name,
        pay_frequency=invalid.pay_frequency,
        first_paycheck_date=invalid.first_paycheck_date,
        net_paycheck_amount=Decimal("-1.00"),
        debts=invalid.debts,
        bills=invalid.bills,
        monthly_personal_spending=invalid.monthly_personal_spending,
        current_savings=invalid.current_savings,
        emergency_fund_target=invalid.emergency_fund_target,
    )

    with pytest.raises(ValueError):
        setup_to_engine_config(invalid)
