"""
calendar_engine.py

Generates all paycheck periods.
"""

from datetime import timedelta

from app.models import PayPeriod


class CalendarEngine:

    def __init__(self, settings):
        self.settings = settings

    def generate(self, end_date):

        periods = []

        pay_date = self.settings.first_paycheck

        while pay_date <= end_date:

            period = PayPeriod(
                pay_date=pay_date,
                start_date=pay_date,
                end_date=pay_date + timedelta(days=13)
            )

            periods.append(period)

            pay_date += timedelta(days=14)

        return periods