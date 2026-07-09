"""
budget_engine.py

Builds a paycheck-by-paycheck budget plan.
"""

from app.debt_engine import DebtEngine
from app.models import Paycheck, Savings
from app.scheduler import Scheduler


class BudgetEngine:
    def __init__(self, config):
        self.config = config
        self.settings = config.settings
        self.scheduler = Scheduler(config)
        self.debt_engine = DebtEngine(config.debts)
        self.savings = Savings(
            current_balance=self.settings.starting_savings,
            goal=self.settings.savings_goal,
        )

    def build_plan(self, periods):
        paychecks = []

        for period in periods:
            paycheck = Paycheck(
                pay_date=period.pay_date,
                income=self.settings.paycheck,
            )

            scheduled = self.scheduler.payments_for_period(period)
            debt_names_due = {
                item.name
                for item in scheduled
                if item.payment_type == "debt"
            }

            interest = self.debt_engine.accrue_interest()
            minimums = self.debt_engine.pay_due_minimums(debt_names_due)

            paycheck.bills_paid = round(
                self.settings.rent_per_paycheck
                + self.settings.insurance_per_paycheck
                + sum(item.amount for item in scheduled if item.payment_type == "bill"),
                2,
            )
            paycheck.debt_minimums = round(sum(minimums.values()), 2)

            fixed_outflow = round(
                paycheck.bills_paid
                + paycheck.debt_minimums
                + self.settings.personal_per_paycheck,
                2,
            )
            available = round(paycheck.income - fixed_outflow, 2)

            if available < 0:
                paycheck.notes.append(
                    f"Shortfall before snowball/savings: ${abs(available):,.2f}"
                )
                available = 0.0

            snowball_budget = round(available * self.settings.snowball_split, 2)
            snowball_payments, snowball_leftover = self.debt_engine.apply_snowball(
                snowball_budget
            )
            paycheck.snowball_payment = round(sum(snowball_payments.values()), 2)

            savings_amount = round(
                available - snowball_budget + snowball_leftover,
                2,
            )
            if not self.savings.goal_met and savings_amount > 0:
                needed = round(self.savings.goal - self.savings.current_balance, 2)
                savings_amount = min(savings_amount, needed)
                self.savings.add(savings_amount)
            else:
                savings_amount = 0.0

            paycheck.savings_added = round(savings_amount, 2)
            paycheck.checking_remaining = round(
                paycheck.income
                - paycheck.bills_paid
                - paycheck.debt_minimums
                - paycheck.snowball_payment
                - paycheck.savings_added
                - self.settings.personal_per_paycheck,
                2,
            )

            self._add_notes(paycheck, scheduled, interest, minimums, snowball_payments)
            paychecks.append(paycheck)

        return paychecks

    def _add_notes(self, paycheck, scheduled, interest, minimums, snowball_payments):
        if scheduled:
            names = ", ".join(item.name for item in scheduled)
            paycheck.notes.append(f"Scheduled: {names}")

        interest_total = round(sum(interest.values()), 2)
        if interest_total:
            paycheck.notes.append(f"Interest accrued: ${interest_total:,.2f}")

        if minimums:
            total = round(sum(minimums.values()), 2)
            paycheck.notes.append(f"Debt minimums paid: ${total:,.2f}")

        if snowball_payments:
            target = next(iter(snowball_payments))
            total = round(sum(snowball_payments.values()), 2)
            paycheck.notes.append(f"Snowball paid to {target}: ${total:,.2f}")

    def debt_summary(self):
        return self.debt_engine.summary()
