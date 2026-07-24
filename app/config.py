"""
config.py
Loads and validates config.json
"""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Self

from app.models import (
    Bill,
    BudgetSettings,
    Debt,
    DebtFreeTargetRequest,
    PlannedSavingsWithdrawal,
    SavingsFundingMode,
    SavingsGoalStage,
    SavingsPlan,
    ScenarioDefinition,
)
from app.paths import default_config_path


class Config:
    """Loaded application configuration."""

    def __init__(self) -> None:

        self.settings = None
        self.bills = []
        self.debts = []
        self.scenarios: list[ScenarioDefinition] = []
        self.debt_free_target = DebtFreeTargetRequest(enabled=False)
        self.savings_plan: SavingsPlan | None = None

    def load(self: Self, filename: str | Path | None = None) -> Self:
        """Load budget settings, bills, and debts from a JSON file."""

        path = default_config_path() if filename is None else Path(filename)

        if not path.exists():
            raise FileNotFoundError(f"{path} not found.")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(
                f,
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=lambda value: value,
            )

        budget = data["budget"]

        self.settings = BudgetSettings(
            paycheck=budget["paycheck"],
            first_paycheck=datetime.strptime(
                budget["first_paycheck"],
                "%Y-%m-%d",
            ).date(),
            rent_per_paycheck=budget["rent_per_paycheck"],
            insurance_per_paycheck=budget["insurance_per_paycheck"],
            personal_per_paycheck=budget["personal_per_paycheck"],
            starting_savings=budget["starting_savings"],
            savings_goal=budget["savings_goal"],
            snowball_split=budget["snowball_split"],
            savings_percentage_override=budget.get("savings_percentage_override"),
        )

        self.bills = [Bill(**bill) for bill in data["bills"]]

        self.debts = sorted(
            [Debt(**debt) for debt in data["debts"]], key=lambda d: d.snowball_order
        )
        self.scenarios = self._load_scenarios(data.get("scenarios"))
        self.debt_free_target = self._load_debt_free_target(
            data.get("debt_free_target")
        )
        self.savings_plan = self._load_savings_plan(data.get("savings_plan"))

        return self

    def _load_savings_plan(self, raw_plan) -> SavingsPlan | None:
        """Parse optional dated savings goal and withdrawal configuration."""
        if raw_plan is None:
            return None
        if not isinstance(raw_plan, dict):
            raise ValueError("savings_plan must be an object.")

        raw_goals = raw_plan.get("goals", [])
        raw_withdrawals = raw_plan.get("withdrawals", [])
        deadline_priority_enabled = raw_plan.get("deadline_priority_enabled", False)
        if not isinstance(deadline_priority_enabled, bool):
            raise ValueError("savings_plan deadline_priority_enabled must be boolean.")
        if not isinstance(raw_goals, list):
            raise ValueError("savings_plan goals must be a list.")
        if not isinstance(raw_withdrawals, list):
            raise ValueError("savings_plan withdrawals must be a list.")

        goals = self._load_savings_goals(raw_goals)
        withdrawals = self._load_savings_withdrawals(raw_withdrawals)
        self._validate_savings_plan_order(goals)
        return SavingsPlan(
            deadline_priority_enabled=deadline_priority_enabled,
            goals=goals,
            withdrawals=withdrawals,
        )

    def _load_savings_goals(self, raw_goals: list) -> list[SavingsGoalStage]:
        goals = []
        names = set()
        for index, raw_goal in enumerate(raw_goals, start=1):
            if not isinstance(raw_goal, dict):
                raise ValueError(f"savings goal {index} must be an object.")
            name = str(raw_goal.get("name", "")).strip()
            if not name:
                raise ValueError(f"savings goal {index} name is required.")
            normalized_name = name.casefold()
            if normalized_name in names:
                raise ValueError("savings goal names must be unique.")
            names.add(normalized_name)

            target_amount = self._decimal_config_value(
                raw_goal.get("target_amount"),
                f"savings goal {index} target_amount",
            )
            if target_amount < Decimal("0.00"):
                raise ValueError("savings goal target_amount cannot be negative.")
            start_date = self._iso_date(
                raw_goal.get("start_date"),
                f"savings goal {index} start_date",
            )
            target_date = None
            if raw_goal.get("target_date") is not None:
                target_date = self._iso_date(
                    raw_goal["target_date"],
                    f"savings goal {index} target_date",
                )
                if target_date < start_date:
                    raise ValueError("savings goal target_date cannot be before start_date.")

            starting_balance = self._optional_decimal_config_value(
                raw_goal.get("starting_balance"),
                f"savings goal {index} starting_balance",
            )
            savings_percentage = self._optional_decimal_config_value(
                raw_goal.get("savings_percentage"),
                f"savings goal {index} savings_percentage",
            )
            if savings_percentage is not None and not (
                Decimal("0") <= savings_percentage <= Decimal("1")
            ):
                raise ValueError("savings goal savings_percentage must be between 0 and 1.")
            funding_mode = self._savings_funding_mode(
                raw_goal.get("funding_mode", SavingsFundingMode.PERCENTAGE.value),
                index,
            )
            if (
                funding_mode == SavingsFundingMode.DEADLINE_PRIORITY
                and target_date is None
            ):
                raise ValueError("deadline_priority savings goals require a target_date.")

            goals.append(
                SavingsGoalStage(
                    name=name,
                    target_amount=target_amount,
                    start_date=start_date,
                    target_date=target_date,
                    starting_balance_override=starting_balance,
                    savings_percentage_override=savings_percentage,
                    funding_mode=funding_mode,
                )
            )

        return goals

    def _savings_funding_mode(self, value, index: int) -> SavingsFundingMode:
        """Parse and validate a savings-goal funding mode."""
        try:
            return SavingsFundingMode(str(value))
        except ValueError as exc:
            supported = ", ".join(mode.value for mode in SavingsFundingMode)
            raise ValueError(
                f"savings goal {index} funding_mode must be one of: {supported}."
            ) from exc

    def _load_savings_withdrawals(
        self,
        raw_withdrawals: list,
    ) -> list[PlannedSavingsWithdrawal]:
        withdrawals = []
        for index, raw_withdrawal in enumerate(raw_withdrawals, start=1):
            if not isinstance(raw_withdrawal, dict):
                raise ValueError(f"savings withdrawal {index} must be an object.")
            name = str(raw_withdrawal.get("name", "")).strip()
            if not name:
                raise ValueError(f"savings withdrawal {index} name is required.")
            withdrawal_date = self._iso_date(
                raw_withdrawal.get("date"),
                f"savings withdrawal {index} date",
            )
            if withdrawal_date < self.settings.first_paycheck:
                raise ValueError("savings withdrawal date cannot be before forecast start.")

            has_amount = "amount" in raw_withdrawal and raw_withdrawal["amount"] is not None
            drain_balance = raw_withdrawal.get("drain_balance", False)
            if not isinstance(drain_balance, bool):
                raise ValueError("savings withdrawal drain_balance must be boolean.")
            if has_amount == drain_balance:
                raise ValueError(
                    "savings withdrawal must configure exactly one of amount or drain_balance."
                )

            amount = None
            if has_amount:
                amount = self._decimal_config_value(
                    raw_withdrawal["amount"],
                    f"savings withdrawal {index} amount",
                )
                if amount < Decimal("0.00"):
                    raise ValueError("savings withdrawal amount cannot be negative.")

            withdrawals.append(
                PlannedSavingsWithdrawal(
                    name=name,
                    withdrawal_date=withdrawal_date,
                    amount=amount,
                    drain_balance=drain_balance,
                )
            )

        return sorted(withdrawals, key=lambda withdrawal: withdrawal.withdrawal_date)

    def _validate_savings_plan_order(self, goals: list[SavingsGoalStage]) -> None:
        previous = None
        seen_starts = set()
        for goal in goals:
            if goal.start_date in seen_starts:
                raise ValueError("savings goal duplicate start dates are not supported.")
            seen_starts.add(goal.start_date)
            if previous is not None:
                if goal.start_date <= previous.start_date:
                    raise ValueError("savings goals must be in chronological order.")
                if (
                    previous.target_date is not None
                    and goal.start_date <= previous.target_date
                ):
                    raise ValueError(
                        "a savings goal cannot start before the prior goal is evaluated."
                    )
            previous = goal

    def _load_debt_free_target(self, raw_target) -> DebtFreeTargetRequest:
        """Parse optional debt-free target calculator configuration."""
        if raw_target is None:
            return DebtFreeTargetRequest(enabled=False)
        if not isinstance(raw_target, dict):
            raise ValueError("debt_free_target must be an object.")

        enabled = raw_target.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("debt_free_target enabled must be boolean.")
        if not enabled:
            return DebtFreeTargetRequest(enabled=False)

        if "target_date" not in raw_target:
            raise ValueError("debt_free_target target_date is required when enabled.")

        target_date = self._iso_date(
            raw_target["target_date"],
            "debt_free_target target_date",
        )
        maximum_extra = self._decimal_config_value(
            raw_target.get("maximum_extra_per_paycheck", Decimal("10000.00")),
            "debt_free_target maximum_extra_per_paycheck",
        )
        if maximum_extra < Decimal("0.00"):
            raise ValueError(
                "debt_free_target maximum_extra_per_paycheck cannot be negative."
            )

        precision = self._decimal_config_value(
            raw_target.get("precision", Decimal("0.01")),
            "debt_free_target precision",
        )
        if precision <= Decimal("0.00"):
            raise ValueError("debt_free_target precision must be greater than zero.")

        maximum_iterations = self._positive_int_config_value(
            raw_target.get("maximum_iterations", 100),
            "debt_free_target maximum_iterations",
        )
        if maximum_iterations <= 0:
            raise ValueError("debt_free_target maximum_iterations must be positive.")

        return DebtFreeTargetRequest(
            enabled=True,
            target_date=target_date,
            maximum_extra_per_paycheck=maximum_extra,
            precision=precision,
            maximum_iterations=maximum_iterations,
        )

    def _positive_int_config_value(self, value, label: str) -> int:
        """Parse a positive integer config value from JSON Decimal/int values."""
        if isinstance(value, bool):
            raise ValueError(f"{label} must be an integer.")
        if isinstance(value, Decimal):
            if value != value.to_integral_value():
                raise ValueError(f"{label} must be an integer.")
            return int(value)
        if isinstance(value, int):
            return value

        raise ValueError(f"{label} must be an integer.")

    def _iso_date(self, value, label: str):
        """Parse an ISO date from configuration."""
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"{label} must use YYYY-MM-DD format.") from exc

    def _load_scenarios(self, raw_scenarios) -> list[ScenarioDefinition]:
        """Parse optional scenario definitions from raw configuration data."""
        if raw_scenarios is None:
            return []
        if not isinstance(raw_scenarios, list):
            raise ValueError("scenarios must be a list.")

        scenario_names = set()
        debt_names = {debt.name for debt in self.debts}
        scenarios = []
        for index, raw_scenario in enumerate(raw_scenarios, start=1):
            if not isinstance(raw_scenario, dict):
                raise ValueError(f"scenario {index} must be an object.")

            scenario = self._scenario_from_mapping(
                raw_scenario=raw_scenario,
                index=index,
                debt_names=debt_names,
            )
            normalized_name = scenario.name.casefold()
            if normalized_name in scenario_names:
                raise ValueError("scenario names must be unique.")

            scenario_names.add(normalized_name)
            scenarios.append(scenario)

        return scenarios

    def _scenario_from_mapping(
        self,
        raw_scenario: dict,
        index: int,
        debt_names: set[str],
    ) -> ScenarioDefinition:
        """Convert one scenario config object into a ScenarioDefinition."""
        if "name" not in raw_scenario:
            raise ValueError(f"scenario {index} is missing name.")

        name = str(raw_scenario["name"])
        extra_per_paycheck = self._decimal_config_value(
            raw_scenario.get("extra_per_paycheck", Decimal("0.00")),
            f"scenario {index} extra_per_paycheck",
        )
        savings_percentage = self._optional_decimal_config_value(
            raw_scenario.get("savings_percentage"),
            f"scenario {index} savings_percentage",
        )
        snowball_order = self._scenario_snowball_order(
            raw_scenario.get("snowball_order"),
            index=index,
            debt_names=debt_names,
        )

        return ScenarioDefinition(
            name=name,
            extra_per_paycheck=extra_per_paycheck,
            savings_percentage_override=savings_percentage,
            snowball_order_override=snowball_order,
        )

    def _optional_decimal_config_value(self, value, label: str) -> Decimal | None:
        """Parse an optional Decimal config value."""
        if value is None:
            return None

        return self._decimal_config_value(value, label)

    def _decimal_config_value(self, value, label: str) -> Decimal:
        """Parse a finite Decimal from config without converting through float."""
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{label} must be a valid decimal value.") from exc

        if not decimal_value.is_finite():
            raise ValueError(f"{label} must be a finite decimal value.")

        return decimal_value

    def _scenario_snowball_order(
        self,
        raw_order,
        index: int,
        debt_names: set[str],
    ) -> list[str] | None:
        """Validate optional scenario debt ordering."""
        if raw_order is None:
            return None
        if not isinstance(raw_order, list):
            raise ValueError(f"scenario {index} snowball_order must be a list.")

        seen = set()
        order = []
        for debt_name in raw_order:
            name = str(debt_name).strip()
            if not name:
                raise ValueError(
                    f"scenario {index} snowball_order cannot contain blank names."
                )
            if name in seen:
                raise ValueError(
                    f"scenario {index} snowball_order cannot contain duplicates."
                )
            if name not in debt_names:
                raise ValueError(f"scenario {index} references unknown debt: {name}")

            seen.add(name)
            order.append(name)

        return order
