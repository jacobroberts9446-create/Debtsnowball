"""
budget_engine.py

Coordinates scheduled bills, debt payments, savings, and snowball payments
for each pay period.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.debt_engine import DebtEngine
from app.scheduler import Scheduler


MONEY = Decimal("0.01")


@dataclass
class DebtBalance:
    """Snapshot of a debt after a pay period is processed."""

    name: str
    balance: float
    minimum: float
    status: str


@dataclass
class PayPeriodSummary:
    """Budget result for one pay period."""

    pay_date: date
    start_date: date
    end_date: date
    income: float
    bills_paid: float
    debt_minimums: float
    snowball_payment: float
    savings_contribution: float
    savings_balance: float
    savings_goal: float
    remaining_cash: float
    active_debt_balances: list[DebtBalance]
    paid_off_debts: list[DebtBalance]


class BudgetEngine:
    """Builds pay-period budget summaries from project configuration."""

    def __init__(
        self,
        config,
        extra_snowball_per_paycheck: Decimal = Decimal("0.00"),
        savings_percentage_override: Decimal | None = None,
    ):
        self.config = config
        self.settings = config.settings
        self.scheduler = Scheduler(config)
        self.debt_engine = DebtEngine(config.debts)
        self.savings_balance = round(float(self.settings.starting_savings), 2)
        self.extra_snowball_per_paycheck = self._money_float(
            extra_snowball_per_paycheck
        )
        self.savings_percentage_override = savings_percentage_override

    def build_plan(self, periods) -> list[PayPeriodSummary]:
        """Process each pay period and return budget summaries."""
        return [self.process_pay_period(period) for period in periods]

    def process_pay_period(self, period) -> PayPeriodSummary:
        """Process scheduled payments, savings, and debt snowball for one period."""
        scheduled_payments = self.scheduler.payments_for_period(period)
        bills_paid = self._scheduled_bill_total(scheduled_payments)
        reserved_minimums = self._scheduled_debt_minimum_total(scheduled_payments)
        surplus = self._surplus_after_required_payments(
            bills_paid=bills_paid,
            debt_minimums=reserved_minimums,
        )

        savings_contribution, snowball_amount = self._split_surplus(surplus)
        snowball_amount = round(snowball_amount + self.extra_snowball_per_paycheck, 2)
        debt_engine_snowball = self._debt_engine_snowball_amount(snowball_amount)
        debt_result = self.debt_engine.process_pay_period(
            scheduled_payments=scheduled_payments,
            snowball_amount=debt_engine_snowball,
        )

        debt_minimums_paid = round(sum(debt_result["minimums"].values()), 2)
        snowball_paid = round(sum(debt_result["snowball"].values()), 2)
        remaining_cash = self._remaining_cash(
            bills_paid=bills_paid,
            debt_minimums=debt_minimums_paid,
            savings_contribution=savings_contribution,
            snowball_payment=snowball_paid,
        )

        return PayPeriodSummary(
            pay_date=period.pay_date,
            start_date=period.start_date,
            end_date=period.end_date,
            income=self.settings.paycheck,
            bills_paid=bills_paid,
            debt_minimums=debt_minimums_paid,
            snowball_payment=snowball_paid,
            savings_contribution=savings_contribution,
            savings_balance=self.savings_balance,
            savings_goal=self.settings.savings_goal,
            remaining_cash=remaining_cash,
            active_debt_balances=self._debt_balances(debt_result["active_debts"]),
            paid_off_debts=self._debt_balances(debt_result["paid_off_debts"]),
        )

    def _scheduled_bill_total(self, scheduled_payments) -> float:
        """Return fixed expenses plus scheduled non-debt bills for the pay period."""
        return round(
            self._fixed_expense_total()
            +
            sum(
                payment.amount
                for payment in scheduled_payments
                if payment.payment_type != "debt"
            ),
            2,
        )

    def _fixed_expense_total(self) -> float:
        """Return configured expenses that are reserved every paycheck."""
        return round(
            self.settings.rent_per_paycheck
            + self.settings.insurance_per_paycheck
            + self.settings.personal_per_paycheck,
            2,
        )

    def _scheduled_debt_minimum_total(self, scheduled_payments) -> float:
        """Return scheduled debt minimum total for the pay period."""
        return round(
            sum(
                self._debt_minimum_after_interest(payment.name)
                for payment in scheduled_payments
                if payment.payment_type == "debt"
            ),
            2,
        )

    def _debt_minimum_after_interest(self, debt_name: str) -> float:
        """Return the expected minimum payment after this period's interest."""
        debt = next(
            debt for debt in self.debt_engine.debts if debt.name == debt_name
        )
        balance_after_interest = debt.balance + (debt.balance * debt.rate_per_paycheck)
        return round(min(debt.minimum, balance_after_interest), 2)

    def _surplus_after_required_payments(
        self,
        bills_paid: float,
        debt_minimums: float,
    ) -> float:
        """Return cash left after bills and reserved debt minimums."""
        return round(max(self.settings.paycheck - bills_paid - debt_minimums, 0.0), 2)

    def _split_surplus(self, surplus: float) -> tuple[float, float]:
        """Apply the savings rule and return savings and snowball amounts."""
        if surplus <= 0:
            return 0.0, 0.0

        if self.savings_balance >= self.settings.savings_goal:
            return 0.0, round(surplus, 2)

        savings_needed = round(self.settings.savings_goal - self.savings_balance, 2)
        savings_contribution = min(
            round(surplus * self._savings_percentage(), 2),
            savings_needed,
        )
        self.savings_balance = round(self.savings_balance + savings_contribution, 2)
        snowball_amount = round(surplus - savings_contribution, 2)

        return savings_contribution, snowball_amount

    def _savings_percentage(self) -> float:
        """Return the scenario-specific savings percentage, or the default rule."""
        if self.savings_percentage_override is None:
            return 0.50

        return float(self.savings_percentage_override)

    def _debt_engine_snowball_amount(self, snowball_amount: float) -> float:
        """Return the snowball amount to pass before debt-engine rollover is added."""
        return round(
            max(snowball_amount - self.debt_engine.freed_minimum_payment, 0.0),
            2,
        )

    def _remaining_cash(
        self,
        bills_paid: float,
        debt_minimums: float,
        savings_contribution: float,
        snowball_payment: float,
    ) -> float:
        """Return cash left after all budgeted outflows."""
        return round(
            self.settings.paycheck
            - bills_paid
            - debt_minimums
            - savings_contribution
            - snowball_payment,
            2,
        )

    def _debt_balances(self, debts) -> list[DebtBalance]:
        """Convert debt engine snapshots to debt balance dataclasses."""
        return [
            DebtBalance(
                name=debt["name"],
                balance=round(debt["balance"], 2),
                minimum=round(debt["minimum"], 2),
                status=debt["status"],
            )
            for debt in debts
        ]

    def _money_float(self, value) -> float:
        """Convert Decimal-compatible money values to a rounded float."""
        return float(Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP))
