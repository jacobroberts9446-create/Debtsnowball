"""
debt_engine.py

Calculates debt interest, minimum payments, and snowball payments.
"""

class DebtEngine:
    def __init__(self, debts):
        self.debts = sorted(debts, key=lambda debt: debt.snowball_order)

    @property
    def active_debts(self):
        return [debt for debt in self.debts if debt.active]

    def accrue_interest(self):
        interest_by_debt = {}

        for debt in self.active_debts:
            interest_by_debt[debt.name] = debt.add_interest()

        return interest_by_debt

    def pay_due_minimums(self, due_debt_names):
        payments = {}

        for debt in self.active_debts:
            if debt.name not in due_debt_names:
                continue

            payments[debt.name] = debt.make_payment(debt.minimum)

        return payments

    def apply_snowball(self, amount):
        remaining = round(max(float(amount), 0.0), 2)
        payments = {}

        for debt in self.active_debts:
            if remaining <= 0:
                break

            paid = debt.make_payment(remaining)
            if paid > 0:
                payments[debt.name] = paid
                remaining = round(remaining - paid, 2)

        return payments, remaining

    def summary(self):
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
