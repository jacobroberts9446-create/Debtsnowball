"""
run.py

DebtSnowball
"""

from copy import deepcopy
from datetime import date

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.excel_writer import ExcelWriter
from app.forecast_engine import ForecastEngine
from app.scenario_engine import ScenarioEngine
from app.target_calculator import DebtFreeTargetCalculator


def main():

    config = Config()
    config.load()

    calendar = CalendarEngine(config.settings)

    periods = calendar.generate(date(2026, 12, 31))

    forecast_config = deepcopy(config)
    budget = BudgetEngine(config)
    summaries = budget.build_plan(periods)
    forecast = ForecastEngine(forecast_config).forecast()
    scenario_comparison = build_scenario_comparison(forecast_config)
    target_result = build_debt_free_target_result(forecast_config)
    workbook_path = ExcelWriter().write(
        summaries,
        forecast,
        scenario_comparison,
        target_result,
    )

    print()

    print("=" * 70)
    print("DebtSnowball v1.0.0")
    print("=" * 70)

    print()

    print("Budget plan")
    print("-" * 70)

    for summary in summaries:
        print(
            f"Pay Period: {summary.start_date:%b %d, %Y} "
            f"to {summary.end_date:%b %d, %Y}"
        )
        print(f"  Income:          ${summary.income:,.2f}")
        print(f"  Bills:           ${summary.bills_paid:,.2f}")
        print(f"  Debt Minimums:   ${summary.debt_minimums:,.2f}")
        if summary.active_savings_goal_name:
            print(f"  Savings Goal:    {summary.active_savings_goal_name}")
            print(
                "  Available After Required Payments: "
                f"${summary.available_after_required_payments:,.2f}"
            )
            print(
                "  Snowball Redirected To Savings: "
                f"${summary.snowball_reduction:,.2f}"
            )
            if summary.personal_expense_reduction > 0:
                print(
                    "  Personal Expense Reduction: "
                    f"${summary.personal_expense_reduction:,.2f}"
                )
            if summary.projected_savings_shortfall > 0:
                print(
                    "  Projected Savings Shortfall: "
                    f"${summary.projected_savings_shortfall:,.2f}"
                )
        print(f"  Savings Deposit: ${summary.savings_contribution:,.2f}")
        print(f"  Snowball Payment: ${summary.snowball_payment:,.2f}")
        print(f"  Remaining Cash:  ${summary.remaining_cash:,.2f}")
        print("  Debt Balances:")

        for debt in summary.active_debt_balances:
            print(f"    {debt.name:<15} ${debt.balance:,.2f}")

        if summary.paid_off_debts:
            paid_off = ", ".join(debt.name for debt in summary.paid_off_debts)
            print(f"  Paid Off: {paid_off}")

        print()

    print(f"Workbook created: {workbook_path}")


def build_scenario_comparison(config):
    """Build baseline plus configured scenario forecasts."""
    return ScenarioEngine(config, config.scenarios).compare()


def build_debt_free_target_result(config):
    """Build an optional debt-free target result from configuration."""
    if not config.debt_free_target.enabled:
        return None

    return DebtFreeTargetCalculator(config, config.debt_free_target).calculate()


if __name__ == "__main__":
    main()
