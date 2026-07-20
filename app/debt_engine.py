"""
debt_engine.py

Calculates debt interest, minimum payments, and snowball payments.
"""

from app.models import Debt, ScheduledPayment


class DebtEngine:
    """Apply interest, minimum payments, and snowball payments to debts."""

    def __init__(self, debts: list[Debt]) -> None:
        self.debts = sorted(debts, key=lambda debt: debt.snowball_order)
        self.paid_off_debts: list[Debt] = []
        self.freed_minimum_payment = 0.0

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

    def accrue_interest(self) -> dict[str, float]:
        """Accrue one pay-period of interest for each active debt."""
        interest_by_debt: dict[str, float] = {}

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
    ) -> dict[str, float]:
        """Pay minimums for debts present in the scheduled payments."""
        return self.pay_due_minimums(self.due_debt_names(scheduled_payments))

    def pay_due_minimums(self, due_debt_names: set[str]) -> dict[str, float]:
        """Pay minimums for the supplied debt names."""
        payments: dict[str, float] = {}
        due_debt_names = set(due_debt_names)

        for debt in list(self.debts):
            if debt.name not in due_debt_names:
                continue

            payments[debt.name] = debt.make_payment(debt.minimum)

        self._remove_paid_off_debts()
        return payments

    def snowball_available(self, base_amount: float) -> float:
        """Return base snowball plus minimums freed by prior payoffs."""
        return round(max(float(base_amount), 0.0) + self.freed_minimum_payment, 2)

    def apply_snowball(
        self,
        amount: float,
        include_freed_minimums: bool = True,
    ) -> tuple[dict[str, float], float]:
        """Apply snowball money across debts in payoff order."""
        if include_freed_minimums:
            remaining = self.snowball_available(amount)
        else:
            remaining = round(max(float(amount), 0.0), 2)

        payments: dict[str, float] = {}

        while remaining > 0:
            target = self.first_active_debt
            if target is None:
                break

            paid = target.make_payment(remaining)
            if paid <= 0:
                break

            payments[target.name] = round(payments.get(target.name, 0.0) + paid, 2)
            remaining = round(remaining - paid, 2)
            self._remove_paid_off_debts()

        return payments, remaining

    def process_pay_period(
        self,
        scheduled_payments: list[ScheduledPayment],
        snowball_amount: float,
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
                "balance": round(debt.balance, 2),
                "minimum": debt.minimum,
                "total_paid": round(debt.total_paid, 2),
                "total_interest_paid": round(debt.total_interest_paid, 2),
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

            self.paid_off_debts.append(debt)
            self.freed_minimum_payment = round(
                self.freed_minimum_payment + debt.minimum,
                2,
            )

        self.debts = remaining_debts

    def summary(self) -> list[dict[str, object]]:
        """Return serializable snapshots for active debts."""
        return [
            {
                "name": debt.name,
                "balance": round(debt.balance, 2),
                "apr": debt.apr,
                "minimum": debt.minimum,
                "total_paid": round(debt.total_paid, 2),
                "total_interest_paid": round(debt.total_interest_paid, 2),
                "status": debt.payoff_status,
            }
            for debt in self.debts
        ]
