"""
models.py
----------
Core data models used throughout DebtSnowball.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import List


# --------------------------------------------------
# Bill
# --------------------------------------------------

@dataclass
class Bill:
    name: str
    amount: float
    due_day: int


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

    @property
    def active(self) -> bool:
        return self.balance > 0.01

    @property
    def rate_per_paycheck(self) -> float:
        return (self.apr / 100) / 26

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