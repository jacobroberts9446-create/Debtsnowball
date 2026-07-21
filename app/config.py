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
    ScenarioDefinition,
)


class Config:
    """Loaded application configuration."""

    def __init__(self) -> None:

        self.settings = None
        self.bills = []
        self.debts = []
        self.scenarios: list[ScenarioDefinition] = []
        self.debt_free_target = DebtFreeTargetRequest(enabled=False)

    def load(self: Self, filename: str | Path = "config.json") -> Self:
        """Load budget settings, bills, and debts from a JSON file."""

        path = Path(filename)

        if not path.exists():
            raise FileNotFoundError(f"{filename} not found.")

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
        )

        self.bills = [Bill(**bill) for bill in data["bills"]]

        self.debts = sorted(
            [Debt(**debt) for debt in data["debts"]], key=lambda d: d.snowball_order
        )
        self.scenarios = self._load_scenarios(data.get("scenarios"))
        self.debt_free_target = self._load_debt_free_target(
            data.get("debt_free_target")
        )

        return self

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
