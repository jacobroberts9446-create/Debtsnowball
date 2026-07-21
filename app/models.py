"""
models.py
----------
Core data models used throughout DebtSnowball.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
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
    active_savings_goal_name: str | None = None
    active_savings_target: Decimal | None = None
    savings_balance_before_withdrawal: Decimal | None = None
    planned_withdrawal_amount: Decimal = Decimal("0.00")
    savings_balance_after_withdrawal: Decimal | None = None
    savings_contribution: Decimal = Decimal("0.00")
    ending_savings_balance: Decimal | None = None
    goal_progress_percentage: Decimal | None = None
    savings_stage_changed: bool = False
    required_fixed_expenses: Decimal = Decimal("0.00")
    normal_personal_allowance: Decimal = Decimal("0.00")
    actual_personal_allowance: Decimal = Decimal("0.00")
    available_after_required_payments: Decimal = Decimal("0.00")
    normal_savings_contribution: Decimal = Decimal("0.00")
    deadline_required_savings_contribution: Decimal = Decimal("0.00")
    snowball_before_savings_adjustment: Decimal = Decimal("0.00")
    snowball_reduction: Decimal = Decimal("0.00")
    personal_expense_reduction: Decimal = Decimal("0.00")
    projected_savings_shortfall: Decimal = Decimal("0.00")


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
    savings_stage_results: list["SavingsStageResult"] = field(default_factory=list)
    planned_withdrawal_results: list["PlannedWithdrawalResult"] = field(
        default_factory=list
    )


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


class SavingsGoalStatus(StrEnum):
    """Lifecycle status for a savings goal stage."""

    UPCOMING = "upcoming"
    ACTIVE = "active"
    ACHIEVED_EARLY = "achieved_early"
    ACHIEVED_ON_TIME = "achieved_on_time"
    ACHIEVED_LATE = "achieved_late"
    NOT_ACHIEVED = "not_achieved"


class SavingsFundingMode(StrEnum):
    """How an active savings goal receives paycheck surplus."""

    PERCENTAGE = "percentage"
    DEADLINE_PRIORITY = "deadline_priority"
    PRIORITY_UNTIL_FUNDED = "priority_until_funded"


@dataclass
class SavingsGoalStage:
    """A dated savings target in a multi-stage savings plan."""

    name: str
    target_amount: Decimal
    start_date: date
    target_date: date | None = None
    starting_balance_override: Decimal | None = None
    savings_percentage_override: Decimal | None = None
    funding_mode: SavingsFundingMode = SavingsFundingMode.PERCENTAGE


@dataclass
class PlannedSavingsWithdrawal:
    """A configured savings withdrawal event."""

    name: str
    withdrawal_date: date
    amount: Decimal | None = None
    drain_balance: bool = False


@dataclass
class SavingsPlan:
    """Ordered savings goals and planned withdrawals."""

    deadline_priority_enabled: bool = False
    goals: list[SavingsGoalStage] = field(default_factory=list)
    withdrawals: list[PlannedSavingsWithdrawal] = field(default_factory=list)


@dataclass
class SavingsStageResult:
    """Progress and deadline result for a savings goal stage."""

    goal_name: str
    start_date: date
    target_date: date | None
    target_amount: Decimal
    starting_balance: Decimal
    amount_needed: Decimal = Decimal("0.00")
    eligible_paychecks_remaining: int = 0
    projected_available_contributions: Decimal = Decimal("0.00")
    projected_balance_at_deadline: Decimal | None = None
    projected_shortfall: Decimal | None = None
    additional_funding_needed: Decimal = Decimal("0.00")
    feasible_under_current_plan: bool | None = None
    achieved_date: date | None = None
    amount_at_deadline: Decimal | None = None
    shortfall_at_deadline: Decimal | None = None
    ending_balance: Decimal = Decimal("0.00")
    status: SavingsGoalStatus = SavingsGoalStatus.UPCOMING
    days_early_or_late: int | None = None


@dataclass
class PlannedWithdrawalResult:
    """Application result for a planned savings withdrawal."""

    name: str
    scheduled_date: date
    requested_amount: Decimal | None
    drain_balance: bool
    actual_amount_withdrawn: Decimal
    balance_before: Decimal
    balance_after: Decimal
    applied_date: date | None
    shortfall: Decimal = Decimal("0.00")
    status: str = "pending"


class DebtFreeTargetStatus(StrEnum):
    """Status values returned by the debt-free target calculator."""

    NOT_CONFIGURED = "not_configured"
    NO_DEBT = "no_debt"
    ALREADY_ON_TRACK = "already_on_track"
    TARGET_MET = "target_met"
    UNREACHABLE = "unreachable"


@dataclass
class DebtFreeTargetRequest:
    """Configuration for a debt-free target calculation."""

    enabled: bool = False
    target_date: date | None = None
    maximum_extra_per_paycheck: Decimal = Decimal("10000.00")
    precision: Decimal = Decimal("0.01")
    maximum_iterations: int = 100


@dataclass
class DebtFreeTargetIteration:
    """One trial forecast evaluated by the target calculator."""

    extra_per_paycheck: Decimal
    projected_debt_free_date: date | None
    target_met: bool


@dataclass
class DebtFreeTargetResult:
    """Result of finding the minimum extra payment for a target date."""

    target_date: date
    required_extra_per_paycheck: Decimal | None
    projected_debt_free_date: date | None
    target_met: bool
    total_interest: Decimal
    total_snowball_paid: Decimal
    ending_debt: Decimal
    iterations_used: int
    lower_bound_tested: Decimal
    upper_bound_tested: Decimal
    maximum_extra_tested: Decimal
    precision: Decimal
    calculation_status: DebtFreeTargetStatus
    message: str | None = None
    baseline_debt_free_date: date | None = None
    baseline_total_interest: Decimal = Decimal("0.00")
    baseline_total_snowball_paid: Decimal = Decimal("0.00")
    baseline_ending_debt: Decimal = Decimal("0.00")
    iterations: list[DebtFreeTargetIteration] = field(default_factory=list)
