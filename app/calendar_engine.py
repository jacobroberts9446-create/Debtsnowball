"""
calendar_engine.py

Generates all paycheck periods.
"""

import calendar
from datetime import date, timedelta

from app.models import PayPeriod


class CalendarEngine:
    """Generate biweekly pay periods from the configured first paycheck."""

    def __init__(self, settings: object) -> None:
        self.settings = settings

    def generate(self, end_date: date) -> list[PayPeriod]:
        """Return pay periods from the first paycheck through the end date."""

        periods: list[PayPeriod] = []
        pay_date = self.settings.first_paycheck

        while pay_date <= end_date:
            next_pay_date = self._next_pay_date(pay_date)
            period = PayPeriod(
                pay_date=pay_date,
                start_date=pay_date,
                end_date=next_pay_date - timedelta(days=1),
            )

            periods.append(period)
            pay_date = next_pay_date

        return periods

    def _next_pay_date(self, pay_date: date) -> date:
        frequency = getattr(self.settings, "pay_frequency", "biweekly")
        if frequency == "weekly":
            return pay_date + timedelta(days=7)
        if frequency == "monthly":
            return self._add_month(pay_date)
        if frequency == "semimonthly":
            return self._next_semimonthly_date(pay_date)
        return pay_date + timedelta(days=14)

    def _next_semimonthly_date(self, pay_date: date) -> date:
        if pay_date.day < 15:
            return pay_date.replace(day=15)
        last_day = calendar.monthrange(pay_date.year, pay_date.month)[1]
        if pay_date.day < last_day:
            return pay_date.replace(day=last_day)
        next_month = self._add_month(pay_date.replace(day=1))
        return next_month.replace(day=15)

    def _add_month(self, pay_date: date) -> date:
        year = pay_date.year
        month = pay_date.month + 1
        if month == 13:
            year += 1
            month = 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(pay_date.day, last_day))
