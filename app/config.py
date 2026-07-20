"""
config.py
Loads and validates config.json
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Self

from app.models import (
    Bill,
    BudgetSettings,
    Debt,
)


class Config:
    """Loaded application configuration."""

    def __init__(self) -> None:

        self.settings = None
        self.bills = []
        self.debts = []

    def load(self: Self, filename: str | Path = "config.json") -> Self:
        """Load budget settings, bills, and debts from a JSON file."""

        path = Path(filename)

        if not path.exists():
            raise FileNotFoundError(f"{filename} not found.")

        with open(path, "r") as f:
            data = json.load(f)

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

        return self
