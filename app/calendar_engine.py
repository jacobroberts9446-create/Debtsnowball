"""
calendar_engine.py

Generates every paycheck for the year.
"""

from datetime import timedelta


class CalendarEngine:

    def __init__(self, settings):

        self.settings = settings

    def generate_paychecks(self, end_date):

        paychecks = []

        current = self.settings.first_paycheck

        while current <= end_date:

            paychecks.append(current)

            current += timedelta(days=14)

        return paychecks