"""
run.py

DebtSnowball
"""

from datetime import date

from app.calendar_engine import CalendarEngine
from app.config import Config
from app.scheduler import Scheduler


def main():

    config = Config()
    config.load()

    calendar = CalendarEngine(
        config.settings
    )

    periods = calendar.generate(date(2026, 12, 31))

    scheduler = Scheduler(config)
    schedule = scheduler.schedule_for_periods(periods)

    print()

    print("=" * 70)
    print("DebtSnowball v0.2")
    print("=" * 70)

    print()

    print(f"{'Pay Date':15} {'Scheduled Payments'}")

    print("-" * 70)

    for period in periods:
        payments = schedule[period.pay_date]
        if payments:
            scheduled = "; ".join(
                f"{payment.name} ${payment.amount:,.2f} due {payment.due_date:%b %d, %Y}"
                for payment in payments
            )
        else:
            scheduled = "No scheduled payments"

        print(
            f"{period.pay_date:%b %d, %Y}   "
            f"{scheduled}"
        )


if __name__ == "__main__":
    main()
