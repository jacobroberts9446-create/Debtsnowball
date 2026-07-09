from datetime import date

from app.calendar_engine import CalendarEngine
from app.config import Config
from app.scheduler import Scheduler


def main():

    config = Config()
    config.load()

    calendar = CalendarEngine(config.settings)

    scheduler = Scheduler(config)

    paychecks = calendar.generate_paychecks(
        date(2026, 12, 31)
    )

    print("=" * 60)
    print("DebtSnowball v0.1")
    print("=" * 60)

    for pay_date in paychecks:

        print()
        print("-" * 60)
        print(pay_date.strftime("%B %d, %Y"))
        print("-" * 60)

        bills = scheduler.bills_for_paycheck(
            pay_date
        )

        if not bills:

            print("No bills due.")
            continue

        total = 0

        for bill in bills:

            print(
                f"{bill['name']:<20} ${bill['amount']:>8.2f}"
            )

            total += bill["amount"]

        print()

        print(
            f"{'TOTAL':<20} ${total:>8.2f}"
        )


if __name__ == "__main__":
    main()