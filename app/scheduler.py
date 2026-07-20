"""
scheduler.py

Determines which bills and debt minimums belong to each paycheck.
"""

import calendar
from datetime import date, timedelta

from app.models import PayPeriod, ScheduledPayment


class Scheduler:
    """Assign scheduled bills and debt minimums to paycheck dates."""

    def __init__(self, config: object) -> None:

        self.config = config

    def payments_for_period(self, period: PayPeriod) -> list[ScheduledPayment]:
        """Return payments assigned to the supplied pay period."""
        next_paycheck = period.end_date + timedelta(days=1)
        return self.payments_for_paycheck(period.pay_date, next_paycheck)

    def payments_for_paycheck(
        self,
        pay_date: date,
        next_paycheck: date | None = None,
    ) -> list[ScheduledPayment]:
        """Return payments due after pay_date and on or before next_paycheck."""
        if next_paycheck is None:
            next_paycheck = pay_date + timedelta(days=14)

        if next_paycheck <= pay_date:
            raise ValueError("next_paycheck must be after pay_date.")

        payments: list[ScheduledPayment] = []

        for bill in self.config.bills:
            for due_date in self._due_dates_between(
                bill.due_day, pay_date, next_paycheck
            ):
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

            for due_date in self._due_dates_between(
                debt.due_day, pay_date, next_paycheck
            ):
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

    def schedule_for_periods(
        self,
        periods: list[PayPeriod],
    ) -> dict[date, list[ScheduledPayment]]:
        """Build the payment schedule for a list of pay periods."""
        schedule: dict[date, list[ScheduledPayment]] = {}

        for index, period in enumerate(periods):
            next_paycheck = self._next_paycheck_for_period(periods, index)

            schedule[period.pay_date] = self.payments_for_paycheck(
                period.pay_date,
                next_paycheck,
            )

        return schedule

    def validate_schedule(
        self,
        periods: list[PayPeriod],
        schedule: dict[date, list[ScheduledPayment]] | None = None,
    ) -> list[str]:
        """Return validation issues for missing or misplaced payments."""
        if schedule is None:
            schedule = self.schedule_for_periods(periods)

        issues: list[str] = []
        expected: dict[tuple[str, str, date, float], date] = {}
        actual: dict[tuple[str, str, date, float], date] = {}

        for index, period in enumerate(periods):
            next_paycheck = self._next_paycheck_for_period(periods, index)
            expected.update(
                self._expected_assignments_for_window(
                    period.pay_date,
                    next_paycheck,
                )
            )

            for payment in schedule.get(period.pay_date, []):
                key = self._payment_key(payment)
                actual[key] = period.pay_date

                if not period.pay_date < payment.due_date <= next_paycheck:
                    issues.append(
                        (
                            f"{payment.name} due {payment.due_date.isoformat()} "
                            f"is assigned to {period.pay_date.isoformat()}, "
                            f"but must be after {period.pay_date.isoformat()} "
                            f"and on or before {next_paycheck.isoformat()}."
                        )
                    )

        for key, pay_date in expected.items():
            if key not in actual:
                issues.append(
                    f"{key[1]} due {key[2].isoformat()} is missing from {pay_date.isoformat()}."
                )

        for key, pay_date in actual.items():
            expected_pay_date = expected.get(key)
            if expected_pay_date is None:
                issues.append(
                    f"{key[1]} due {key[2].isoformat()} is unexpectedly scheduled on {pay_date.isoformat()}."
                )
            elif expected_pay_date != pay_date:
                issues.append(
                    (
                        f"{key[1]} due {key[2].isoformat()} is assigned to "
                        f"{pay_date.isoformat()} instead of {expected_pay_date.isoformat()}."
                    )
                )

        return issues

    def bills_for_paycheck(self, pay_date: date) -> list[ScheduledPayment]:
        """Return scheduled payments for a paycheck date."""
        return self.payments_for_paycheck(pay_date)

    def payments_for_dates(
        self,
        start_date: date,
        end_date: date,
    ) -> list[ScheduledPayment]:
        """Return payments assigned to an inclusive date range."""
        return self.payments_for_paycheck(start_date, end_date + timedelta(days=1))

    def _due_dates_between(
        self,
        due_day: int,
        pay_date: date,
        next_paycheck: date,
    ):
        current = self._first_day_of_month(pay_date)
        last_month = self._first_day_of_month(next_paycheck)

        while current <= last_month:
            due_date = self._due_date_for_month(due_day, current.year, current.month)

            if pay_date < due_date <= next_paycheck:
                yield due_date

            current = self._next_month(current)

    def _due_date_for_month(self, due_day: int, year: int, month: int) -> date:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(due_day, last_day))

    def _first_day_of_month(self, value: date) -> date:
        return date(value.year, value.month, 1)

    def _next_month(self, value: date) -> date:
        if value.month == 12:
            return date(value.year + 1, 1, 1)

        return date(value.year, value.month + 1, 1)

    def _next_paycheck_for_period(self, periods: list[PayPeriod], index: int) -> date:
        if index + 1 < len(periods):
            return periods[index + 1].pay_date

        return periods[index].pay_date + timedelta(days=14)

    def _expected_assignments_for_window(
        self,
        pay_date: date,
        next_paycheck: date,
    ) -> dict[tuple[str, str, date, float], date]:
        assignments: dict[tuple[str, str, date, float], date] = {}

        for bill in self.config.bills:
            for due_date in self._due_dates_between(
                bill.due_day, pay_date, next_paycheck
            ):
                payment = ScheduledPayment(
                    name=bill.name,
                    amount=bill.amount,
                    due_date=due_date,
                    payment_type="bill",
                )
                assignments[self._payment_key(payment)] = pay_date

        for debt in self.config.debts:
            if not debt.active:
                continue

            for due_date in self._due_dates_between(
                debt.due_day, pay_date, next_paycheck
            ):
                payment = ScheduledPayment(
                    name=debt.name,
                    amount=min(debt.minimum, debt.balance),
                    due_date=due_date,
                    payment_type="debt",
                )
                assignments[self._payment_key(payment)] = pay_date

        return assignments

    def _payment_key(self, payment: ScheduledPayment) -> tuple[str, str, date, float]:
        return (
            payment.payment_type,
            payment.name,
            payment.due_date,
            round(float(payment.amount), 2),
        )
