"""
run.py

DebtSnowball
"""

from datetime import date

from app.calendar_engine import CalendarEngine
from app.config import Config


def main():

    config = Config()
    config.load()

    calendar = CalendarEngine(
        config.settings
    )

    periods = calendar.generate(
        date(2026, 12, 31)
    )

    print()

    print("=" * 70)
    print("DebtSnowball v0.2")
    print("=" * 70)

    print()

    print(f"{'Pay Date':15} {'Period'}")

    print("-" * 70)

    for period in periods:

        print(
            f"{period.pay_date:%b %d, %Y}   "
            f"{period.start_date:%b %d} -> "
            f"{period.end_date:%b %d}"
        )


if __name__ == "__main__":
    main()