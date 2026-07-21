"""
debt_engine.py

Calculates debt interest, minimum payments, and snowball payments.
"""

from decimal import Decimal

from app.money import ZERO_MONEY, money
from app.models import Debt, ScheduledPayment


class DebtEngine:
    """Apply interest, minimum payments, and snowball payments to debts."""

    def __init__(self, debts: list[Debt]) -> None:
        self.debts = sorted(debts, key=lambda debt: debt.snowball_order)
        self.paid_off_debts: list[Debt] = []
        self.freed_minimum_payment = ZERO_MONEY

    @property
    def active_debts(self) -> list[Debt]:
        """Return debts that still have an outstanding balance."""
        self._remove_paid_off_debts()
        return list(self.debts)

    @property
    def first_active_debt(self) -> Debt | None:
        """Return the next snowball target in configured payoff order."""
        debts = self.active_debts
        if not debts:
            return None

        return debts[0]

    def accrue_interest(self) -> dict[str, Decimal]:
        """Accrue one pay-period of interest for each active debt."""
        interest_by_debt: dict[str, Decimal] = {}

        for debt in list(self.debts):
            interest_by_debt[debt.name] = debt.add_interest()

        return interest_by_debt

    def due_debt_names(self, scheduled_payments: list[ScheduledPayment]) -> set[str]:
        """Return debt names that have minimum payments due this period."""
        return {
            payment.name
            for payment in scheduled_payments
            if payment.payment_type == "debt"
        }

    def pay_minimums_due(
        self,
        scheduled_payments: list[ScheduledPayment],
    ) -> dict[str, Decimal]:
        """Pay minimums for debts present in the scheduled payments."""
        return self.pay_due_minimums(self.due_debt_names(scheduled_payments))

    def pay_due_minimums(self, due_debt_names: set[str]) -> dict[str, Decimal]:
        """Pay minimums for the supplied debt names."""
        payments: dict[str, Decimal] = {}
        due_debt_names = set(due_debt_names)

        for debt in list(self.debts):
            if debt.name not in due_debt_names:
                continue

            payments[debt.name] = debt.make_payment(debt.minimum)

        self._remove_paid_off_debts()
        return payments

    def snowball_available(self, base_amount: Decimal) -> Decimal:
        """Return base snowball plus minimums freed by prior payoffs."""
        return money(max(money(base_amount), ZERO_MONEY) + self.freed_minimum_payment)

    def apply_snowball(
        self,
        amount: Decimal,
        include_freed_minimums: bool = True,
    ) -> tuple[dict[str, Decimal], Decimal]:
        """Apply snowball money across debts in payoff order."""
        if include_freed_minimums:
            remaining = self.snowball_available(amount)
        else:
            remaining = money(max(money(amount), ZERO_MONEY))

        payments: dict[str, Decimal] = {}

        while remaining > ZERO_MONEY:
            target = self.first_active_debt
            if target is None:
                break

            paid = target.make_payment(remaining)
            if paid <= ZERO_MONEY:
                break

            payments[target.name] = money(
                payments.get(target.name, ZERO_MONEY) + paid
            )
            remaining = money(remaining - paid)
            self._remove_paid_off_debts()

        return payments, remaining

    def process_pay_period(
        self,
        scheduled_payments: list[ScheduledPayment],
        snowball_amount: Decimal,
    ) -> dict[str, object]:
        """Run interest, minimum payments, and snowball for one pay period."""
        snowball_with_prior_freed_minimums = self.snowball_available(snowball_amount)

        interest = self.accrue_interest()
        minimums = self.pay_minimums_due(scheduled_payments)
        snowball_payments, snowball_remaining = self.apply_snowball(
            snowball_with_prior_freed_minimums,
            include_freed_minimums=False,
        )

        return {
            "interest": interest,
            "minimums": minimums,
            "snowball": snowball_payments,
            "snowball_remaining": snowball_remaining,
            "freed_minimum_payment": self.freed_minimum_payment,
            "active_debts": self.summary(),
            "paid_off_debts": self.paid_off_summary(),
        }

    def paid_off_summary(self) -> list[dict[str, object]]:
        """Return serializable snapshots for paid-off debts."""
        return [
            {
                "name": debt.name,
                "balance": money(debt.balance),
                "minimum": debt.minimum,
                "total_paid": money(debt.total_paid),
                "total_interest_paid": money(debt.total_interest_paid),
                "status": debt.payoff_status,
            }
            for debt in self.paid_off_debts
        ]

    def _remove_paid_off_debts(self) -> None:
        remaining_debts: list[Debt] = []

        for debt in self.debts:
            if debt.active:
                remaining_debts.append(debt)
                continue

            if debt in self.paid_off_debts:
                continue

            self.paid_off_debts.append(debt)
            self.freed_minimum_payment = money(
                self.freed_minimum_payment + debt.minimum,
            )

        self.debts = remaining_debts

    def summary(self) -> list[dict[str, object]]:
        """Return serializable snapshots for active debts."""
        return [
            {
                "name": debt.name,
                "balance": money(debt.balance),
                "apr": debt.apr,
                "minimum": debt.minimum,
                "total_paid": money(debt.total_paid),
                "total_interest_paid": money(debt.total_interest_paid),
                "status": debt.payoff_status,
            }
            for debt in self.debts
        ]
