"""
models.py
----------
Core data models used throughout DebtSnowball.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List


# --------------------------------------------------
# Bill
# --------------------------------------------------


@dataclass
class Bill:
    name: str
    amount: float
    due_day: int

    def __post_init__(self):
        self.amount = round(float(self.amount), 2)
        if not 1 <= int(self.due_day) <= 31:
            raise ValueError(f"{self.name} due_day must be between 1 and 31.")
        self.due_day = int(self.due_day)


# --------------------------------------------------
# Debt
# --------------------------------------------------


@dataclass
class Debt:
    name: str
    balance: float
    apr: float
    minimum: float
    due_day: int
    snowball_order: int

    total_interest_paid: float = 0.0
    total_paid: float = 0.0

    def __post_init__(self):
        self.balance = round(float(self.balance), 2)
        self.apr = float(self.apr)
        self.minimum = round(float(self.minimum), 2)
        self.due_day = int(self.due_day)
        self.snowball_order = int(self.snowball_order)

        if self.balance < 0:
            raise ValueError(f"{self.name} balance cannot be negative.")
        if self.apr < 0:
            raise ValueError(f"{self.name} apr cannot be negative.")
        if self.minimum < 0:
            raise ValueError(f"{self.name} minimum cannot be negative.")
        if not 1 <= self.due_day <= 31:
            raise ValueError(f"{self.name} due_day must be between 1 and 31.")

    @property
    def active(self) -> bool:
        return self.balance > 0.01

    @property
    def rate_per_paycheck(self) -> float:
        return (self.apr / 100) / 26

    @property
    def payoff_status(self) -> str:
        return "paid" if not self.active else "active"

    def add_interest(self) -> float:
        if not self.active:
            return 0.0

        interest = self.balance * self.rate_per_paycheck
        self.balance += interest
        self.total_interest_paid += interest

        return round(interest, 2)

    def make_payment(self, amount: float) -> float:
        if amount <= 0:
            return 0.0

        payment = min(amount, self.balance)

        self.balance -= payment
        self.total_paid += payment

        if self.balance < 0.01:
            self.balance = 0.0

        self.balance = round(self.balance, 2)
        return round(payment, 2)


# --------------------------------------------------
# Budget Settings
# --------------------------------------------------


@dataclass
class BudgetSettings:
    paycheck: float
    first_paycheck: date

    rent_per_paycheck: float
    insurance_per_paycheck: float
    personal_per_paycheck: float

    starting_savings: float
    savings_goal: float

    snowball_split: float

    def __post_init__(self):
        self.paycheck = round(float(self.paycheck), 2)
        self.rent_per_paycheck = round(float(self.rent_per_paycheck), 2)
        self.insurance_per_paycheck = round(float(self.insurance_per_paycheck), 2)
        self.personal_per_paycheck = round(float(self.personal_per_paycheck), 2)
        self.starting_savings = round(float(self.starting_savings), 2)
        self.savings_goal = round(float(self.savings_goal), 2)
        self.snowball_split = float(self.snowball_split)

        if self.paycheck < 0:
            raise ValueError("paycheck cannot be negative.")
        if not 0 <= self.snowball_split <= 1:
            raise ValueError("snowball_split must be between 0 and 1.")


# --------------------------------------------------
# Scheduled Payment
# --------------------------------------------------


@dataclass
class ScheduledPayment:
    name: str
    amount: float
    due_date: date
    payment_type: str  # "bill" or "debt"


# --------------------------------------------------
# Pay Period
# --------------------------------------------------


@dataclass
class PayPeriod:
    pay_date: date
    start_date: date
    end_date: date


# --------------------------------------------------
# Paycheck
# --------------------------------------------------


@dataclass
class Paycheck:
    pay_date: date
    income: float

    bills_paid: float = 0.0
    debt_minimums: float = 0.0
    snowball_payment: float = 0.0
    savings_added: float = 0.0
    checking_remaining: float = 0.0

    notes: List[str] = field(default_factory=list)


# --------------------------------------------------
# Savings
# --------------------------------------------------


@dataclass
class Savings:
    current_balance: float
    goal: float

    def add(self, amount: float):
        self.current_balance += amount

    @property
    def goal_met(self) -> bool:
        return self.current_balance >= self.goal


@dataclass
class DebtPayoffForecast:
    """Forecasted payoff details for a single debt."""

    debt_name: str
    starting_balance: Decimal
    payoff_date: date | None
    total_interest_paid: Decimal
    total_paid: Decimal


@dataclass
class ForecastPeriod:
    """Forecasted account state after one paycheck."""

    paycheck_date: date
    total_debt_balance: Decimal
    savings_balance: Decimal
    interest_paid: Decimal
    minimums_paid: Decimal
    snowball_paid: Decimal


@dataclass
class ForecastSummary:
    """Forecast result for a full simulated payoff horizon."""

    forecast_start_date: date
    forecast_end_date: date
    debt_free_date: date | None
    savings_goal_date: date | None
    starting_debt: Decimal
    total_interest_paid: Decimal
    total_minimum_payments: Decimal
    total_snowball_payments: Decimal
    ending_savings: Decimal
    remaining_debt: Decimal
    completed: bool
    debt_payoffs: list[DebtPayoffForecast] = field(default_factory=list)
    periods: list[ForecastPeriod] = field(default_factory=list)


@dataclass
class ScenarioDefinition:
    """User-defined forecast adjustment for scenario comparison."""

    name: str
    extra_per_paycheck: Decimal = Decimal("0.00")
    savings_percentage_override: Decimal | None = None
    snowball_order_override: list[str] | None = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.extra_per_paycheck = Decimal(str(self.extra_per_paycheck))

        if not self.name:
            raise ValueError("scenario name must not be blank.")
        if not self.extra_per_paycheck.is_finite():
            raise ValueError("extra_per_paycheck must be finite.")
        if self.extra_per_paycheck < Decimal("0.00"):
            raise ValueError("extra_per_paycheck cannot be negative.")

        if self.savings_percentage_override is not None:
            self.savings_percentage_override = Decimal(
                str(self.savings_percentage_override)
            )
            if not self.savings_percentage_override.is_finite():
                raise ValueError("savings_percentage_override must be finite.")
            if not Decimal("0") <= self.savings_percentage_override <= Decimal("1"):
                raise ValueError(
                    "savings_percentage_override must be between 0 and 1."
                )

        if self.snowball_order_override is not None:
            seen = set()
            for debt_name in self.snowball_order_override:
                if debt_name in seen:
                    raise ValueError("snowball_order_override cannot contain duplicates.")
                seen.add(debt_name)


@dataclass
class ScenarioResult:
    """Forecast result for one baseline or alternative scenario."""

    name: str
    extra_per_paycheck: Decimal
    forecast: ForecastSummary
    debt_payoffs: list[DebtPayoffForecast]
    periods: list[ForecastPeriod]


@dataclass
class ScenarioComparison:
    """Baseline forecast plus all requested scenario forecasts."""

    baseline: ScenarioResult
    scenarios: list[ScenarioResult]


@dataclass
class ScenarioDelta:
    """Scenario metrics measured relative to the baseline forecast."""

    scenario_name: str
    debt_free_days_saved: int | None
    savings_goal_days_changed: int | None
    interest_saved: Decimal
    additional_snowball_paid: Decimal
    ending_debt_difference: Decimal
    ending_savings_difference: Decimal
