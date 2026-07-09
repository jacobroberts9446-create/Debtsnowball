"""
scheduler.py

Determines which bills belong to each paycheck.
"""

from datetime import timedelta


class Scheduler:

    def __init__(self, config):

        self.config = config

    def bills_for_paycheck(self, pay_date):

        bills_due = []

        next_paycheck = pay_date + timedelta(days=14)

        #
        # Regular Bills
        #

        for bill in self.config.bills:

            if pay_date.day < bill.due_day <= next_paycheck.day:

                bills_due.append(
                    {
                        "name": bill.name,
                        "amount": bill.amount,
                        "due_day": bill.due_day,
                    }
                )

        #
        # Debts
        #

        for debt in self.config.debts:

            if debt.balance <= 0:
                continue

            if pay_date.day < debt.due_day <= next_paycheck.day:

                bills_due.append(
                    {
                        "name": debt.name,
                        "amount": debt.minimum,
                        "due_day": debt.due_day,
                    }
                )

        return bills_due