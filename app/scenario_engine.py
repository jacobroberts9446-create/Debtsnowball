"""
scenario_engine.py

Compares a baseline forecast with alternative extra-payment scenarios.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.forecast_engine import ForecastEngine
from app.models import (
    Debt,
    ForecastSummary,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioDelta,
    ScenarioResult,
)


MONEY = Decimal("0.01")


class ScenarioEngine:
    """Run isolated forecast scenarios and compare them with a baseline."""

    def __init__(
        self,
        config: object,
        scenarios: list[ScenarioDefinition],
        starting_debts: list[Debt] | None = None,
        starting_savings: Decimal | None = None,
        forecast_start_date: date | None = None,
        max_years: int = 30,
    ) -> None:
        self.config = config
        self.scenarios = scenarios
        self.starting_debts = starting_debts
        self.starting_savings = starting_savings
        self.forecast_start_date = forecast_start_date
        self.max_years = max_years

    def compare(self) -> ScenarioComparison:
        """Return the baseline forecast and every requested scenario forecast."""
        self._validate_scenarios()
        baseline_definition = ScenarioDefinition(name="Baseline")
        baseline = self._run_scenario(baseline_definition)
        scenarios = [self._run_scenario(scenario) for scenario in self.scenarios]

        return ScenarioComparison(baseline=baseline, scenarios=scenarios)

    def calculate_deltas(
        self,
        comparison: ScenarioComparison,
    ) -> list[ScenarioDelta]:
        """Calculate scenario changes relative to the baseline forecast."""
        return [
            self._delta(comparison.baseline.forecast, scenario)
            for scenario in comparison.scenarios
        ]

    def _run_scenario(self, scenario: ScenarioDefinition) -> ScenarioResult:
        scenario_config = self._scenario_config(scenario)
        forecast = ForecastEngine(
            scenario_config,
            max_years=self.max_years,
            extra_snowball_per_paycheck=scenario.extra_per_paycheck,
            savings_percentage_override=scenario.savings_percentage_override,
        ).forecast()

        return ScenarioResult(
            name=scenario.name,
            extra_per_paycheck=self._money(scenario.extra_per_paycheck),
            forecast=forecast,
            debt_payoffs=forecast.debt_payoffs,
            periods=forecast.periods,
        )

    def _scenario_config(self, scenario: ScenarioDefinition) -> object:
        scenario_config = deepcopy(self.config)

        if self.starting_debts is not None:
            scenario_config.debts = deepcopy(self.starting_debts)
        if self.starting_savings is not None:
            scenario_config.settings.starting_savings = float(
                self._money(self.starting_savings)
            )
        if self.forecast_start_date is not None:
            scenario_config.settings.first_paycheck = self.forecast_start_date

        if scenario.snowball_order_override is not None:
            scenario_config.debts = self._apply_snowball_order_override(
                scenario_config.debts,
                scenario.snowball_order_override,
            )

        return scenario_config

    def _apply_snowball_order_override(
        self,
        debts: list[Debt],
        override: list[str],
    ) -> list[Debt]:
        debts_by_name = {debt.name: debt for debt in debts}
        ordered_names = override + [
            debt.name for debt in sorted(debts, key=lambda debt: debt.snowball_order)
            if debt.name not in override
        ]

        ordered_debts = []
        for index, debt_name in enumerate(ordered_names, start=1):
            debt = deepcopy(debts_by_name[debt_name])
            debt.snowball_order = index
            ordered_debts.append(debt)

        return ordered_debts

    def _validate_scenarios(self) -> None:
        scenario_names = [scenario.name for scenario in self.scenarios]
        if len(scenario_names) != len(set(scenario_names)):
            raise ValueError("scenario names must be unique.")

        valid_debt_names = {debt.name for debt in self._base_debts()}
        for scenario in self.scenarios:
            self._validate_snowball_override(scenario, valid_debt_names)

    def _validate_snowball_override(
        self,
        scenario: ScenarioDefinition,
        valid_debt_names: set[str],
    ) -> None:
        if scenario.snowball_order_override is None:
            return

        unknown_names = set(scenario.snowball_order_override) - valid_debt_names
        if unknown_names:
            unknown = ", ".join(sorted(unknown_names))
            raise ValueError(f"unknown debt in snowball_order_override: {unknown}")

    def _base_debts(self) -> list[Debt]:
        if self.starting_debts is not None:
            return self.starting_debts

        return self.config.debts

    def _delta(
        self,
        baseline: ForecastSummary,
        scenario: ScenarioResult,
    ) -> ScenarioDelta:
        scenario_forecast = scenario.forecast

        return ScenarioDelta(
            scenario_name=scenario.name,
            debt_free_days_saved=self._days_saved(
                baseline.debt_free_date,
                scenario_forecast.debt_free_date,
            ),
            savings_goal_days_changed=self._days_saved(
                baseline.savings_goal_date,
                scenario_forecast.savings_goal_date,
            ),
            interest_saved=self._money(
                baseline.total_interest_paid - scenario_forecast.total_interest_paid
            ),
            additional_snowball_paid=self._money(
                scenario_forecast.total_snowball_payments
                - baseline.total_snowball_payments
            ),
            ending_debt_difference=self._money(
                scenario_forecast.remaining_debt - baseline.remaining_debt
            ),
            ending_savings_difference=self._money(
                scenario_forecast.ending_savings - baseline.ending_savings
            ),
        )

    def _days_saved(
        self,
        baseline_date: date | None,
        scenario_date: date | None,
    ) -> int | None:
        if baseline_date is None or scenario_date is None:
            return None

        return (baseline_date - scenario_date).days

    def _money(self, value) -> Decimal:
        return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
