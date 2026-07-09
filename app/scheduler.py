"""
scheduler.py

Determines which bills and debt minimums belong to each paycheck.
"""

import calendar
from datetime import date, timedelta

from app.models import ScheduledPayment


class Scheduler:

    def __init__(self, config):

        self.config = config

    def payments_for_period(self, period):
        next_paycheck = period.end_date + timedelta(days=1)
        return self.payments_for_paycheck(period.pay_date, next_paycheck)

    def payments_for_paycheck(self, pay_date, next_paycheck=None):
        if next_paycheck is None:
            next_paycheck = pay_date + timedelta(days=14)

        if next_paycheck <= pay_date:
            raise ValueError("next_paycheck must be after pay_date.")

        payments = []

        for bill in self.config.bills:
            for due_date in self._due_dates_between(bill.due_day, pay_date, next_paycheck):
                payments.append(
                    ScheduledPayment(
                        name=bill.name,
                        amount=bill.amount,
                        due_date=due_date,
                        payment_type="bill",
                    )
                )

        for debt in self.config.debts:
            if not debt.active:
                continue

            for due_date in self._due_dates_between(debt.due_day, pay_date, next_paycheck):
                payments.append(
                    ScheduledPayment(
                        name=debt.name,
                        amount=min(debt.minimum, debt.balance),
                        due_date=due_date,
                        payment_type="debt",
                    )
                )

        return sorted(
            payments,
            key=lambda payment: (payment.due_date, payment.payment_type, payment.name),
        )

    def schedule_for_periods(self, periods):
        schedule = {}

        for index, period in enumerate(periods):
            if index + 1 < len(periods):
                next_paycheck = periods[index + 1].pay_date
            else:
                next_paycheck = period.pay_date + timedelta(days=14)

            schedule[period.pay_date] = self.payments_for_paycheck(
                period.pay_date,
                next_paycheck,
            )

        return schedule

    def bills_for_paycheck(self, pay_date):
        return self.payments_for_paycheck(pay_date)

    def payments_for_dates(self, start_date, end_date):
        return self.payments_for_paycheck(start_date, end_date + timedelta(days=1))

    def _due_dates_between(self, due_day, pay_date, next_paycheck):
        current = self._first_day_of_month(pay_date)
        last_month = self._first_day_of_month(next_paycheck)

        while current <= last_month:
            due_date = self._due_date_for_month(due_day, current.year, current.month)

            if pay_date < due_date <= next_paycheck:
                yield due_date

            current = self._next_month(current)

    def _due_date_for_month(self, due_day, year, month):
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(due_day, last_day))

    def _first_day_of_month(self, value):
        return date(value.year, value.month, 1)

    def _next_month(self, value):
        if value.month == 12:
            return date(value.year + 1, 1, 1)

        return date(value.year, value.month + 1, 1)
