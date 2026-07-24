"""In-memory plan generation for interactive setup results."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.forecast_engine import ForecastEngine
from app.models import BudgetSettings, ForecastSummary
from app.money import ZERO_MONEY, money
from app.plan_setup import PayFrequency


PAYCHECKS_PER_MONTH = {
    PayFrequency.WEEKLY: Decimal("52") / Decimal("12"),
    PayFrequency.BIWEEKLY: Decimal("26") / Decimal("12"),
    PayFrequency.SEMIMONTHLY: Decimal("2"),
    PayFrequency.MONTHLY: Decimal("1"),
}


@dataclass(frozen=True)
class GeneratedPlanSummary:
    """Concise generated-plan summary for console display."""

    plan_name: str
    debt_count: int
    total_starting_debt: Decimal
    projected_debt_free_date: date | None
    projected_payoff_duration_days: int | None
    total_projected_interest: Decimal
    first_period_snowball_amount: Decimal
    ending_savings: Decimal
    savings_goal_met: bool
    forecast: ForecastSummary


def generate_plan_from_setup(setup) -> GeneratedPlanSummary:
    """Generate an in-memory payoff plan from collected setup inputs."""
    config = setup_to_engine_config(setup)
    forecast = ForecastEngine(config).forecast()
    first_period_snowball = (
        money(forecast.periods[0].snowball_paid) if forecast.periods else ZERO_MONEY
    )
    duration = None
    if forecast.debt_free_date is not None:
        duration = (forecast.debt_free_date - setup.first_paycheck_date).days

    return GeneratedPlanSummary(
        plan_name=setup.plan_name,
        debt_count=len(setup.debts),
        total_starting_debt=forecast.starting_debt,
        projected_debt_free_date=forecast.debt_free_date,
        projected_payoff_duration_days=duration,
        total_projected_interest=forecast.total_interest_paid,
        first_period_snowball_amount=first_period_snowball,
        ending_savings=forecast.ending_savings,
        savings_goal_met=forecast.ending_savings >= setup.emergency_fund_target,
        forecast=forecast,
    )


def setup_to_engine_config(setup):
    """Translate interactive setup data to the shape expected by existing engines."""
    settings = BudgetSettings(
        paycheck=setup.net_paycheck_amount,
        first_paycheck=setup.first_paycheck_date,
        rent_per_paycheck=ZERO_MONEY,
        insurance_per_paycheck=ZERO_MONEY,
        personal_per_paycheck=monthly_to_per_paycheck(
            setup.monthly_personal_spending,
            setup.pay_frequency,
        ),
        starting_savings=setup.current_savings,
        savings_goal=setup.emergency_fund_target,
        snowball_split=Decimal("0.5"),
    )
    settings.pay_frequency = setup.pay_frequency.value
    return SimpleNamespace(
        settings=settings,
        bills=list(setup.bills),
        debts=list(setup.debts),
        scenarios=[],
        debt_free_target=SimpleNamespace(enabled=False),
        savings_plan=None,
    )


def monthly_to_per_paycheck(amount: Decimal, pay_frequency: PayFrequency) -> Decimal:
    """Convert a monthly allowance into the current engine's per-paycheck field."""
    return money(money(amount) / PAYCHECKS_PER_MONTH[pay_frequency])
