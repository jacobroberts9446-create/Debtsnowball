"""
calendar_engine.py

Generates all paycheck periods.
"""

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
            period = PayPeriod(
                pay_date=pay_date,
                start_date=pay_date,
                end_date=pay_date + timedelta(days=13),
            )

            periods.append(period)

            pay_date += timedelta(days=14)

        return periods
