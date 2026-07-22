"""
run.py

DebtSnowball
"""

import argparse
from copy import deepcopy
from datetime import date

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.excel_writer import ExcelWriter
from app.forecast_engine import ForecastEngine
from app.history import PlanHistoryService
from app.models import ActualEntryType
from app.money import format_currency
from app.scenario_engine import ScenarioEngine
from app.target_calculator import DebtFreeTargetCalculator


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command:
        run_cli(args)
        return

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
        print(f"  Income:          {format_currency(summary.income)}")
        print(f"  Bills:           {format_currency(summary.bills_paid)}")
        print(f"  Debt Minimums:   {format_currency(summary.debt_minimums)}")
        if summary.active_savings_goal_name:
            print(f"  Savings Goal:    {summary.active_savings_goal_name}")
            print(
                "  Available After Required Payments: "
                f"{format_currency(summary.available_after_required_payments)}"
            )
            print(
                "  Snowball Redirected To Savings: "
                f"{format_currency(summary.snowball_reduction)}"
            )
            if summary.personal_expense_reduction > 0:
                print(
                    "  Personal Expense Reduction: "
                    f"{format_currency(summary.personal_expense_reduction)}"
                )
            if summary.projected_savings_shortfall > 0:
                print(
                    "  Projected Savings Shortfall: "
                    f"{format_currency(summary.projected_savings_shortfall)}"
                )
        print(f"  Savings Deposit: {format_currency(summary.savings_contribution)}")
        print(f"  Snowball Payment: {format_currency(summary.snowball_payment)}")
        print(f"  Remaining Cash:  {format_currency(summary.remaining_cash)}")
        print("  Debt Balances:")

        for debt in summary.active_debt_balances:
            print(f"    {debt.name:<15} {format_currency(debt.balance)}")

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


def build_parser():
    """Create optional history CLI commands while preserving default behavior."""
    parser = argparse.ArgumentParser(description="DebtSnowball")
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="Manage saved local plans")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_save = plan_sub.add_parser("save", help="Save the current config as a plan")
    plan_save.add_argument("--name", required=True)
    plan_save.add_argument("--description", default="")
    plan_save.add_argument("--note", default="Saved from CLI")
    plan_save.add_argument("--force", action="store_true")

    plan_sub.add_parser("list", help="List saved plans")
    plan_history = plan_sub.add_parser("history", help="List versions for a plan")
    plan_history.add_argument("--plan-id", type=int, required=True)
    plan_compare = plan_sub.add_parser("compare", help="Compare two saved versions")
    plan_compare.add_argument("--from-version", type=int, required=True)
    plan_compare.add_argument("--to-version", type=int, required=True)
    plan_restore = plan_sub.add_parser("restore", help="Restore a version as a new version")
    plan_restore.add_argument("--version", type=int, required=True)

    actual = subparsers.add_parser("actual", help="Record actual local activity")
    actual_sub = actual.add_subparsers(dest="actual_command", required=True)
    actual_add = actual_sub.add_parser("add", help="Add an actual entry")
    actual_add.add_argument("--plan-id", type=int, required=True)
    actual_add.add_argument("--date", required=True)
    actual_add.add_argument("--type", choices=[entry.value for entry in ActualEntryType], required=True)
    actual_add.add_argument("--amount", required=True)
    actual_add.add_argument("--category", default="")
    actual_add.add_argument("--description", default="")
    actual_sub.add_parser("summary", help="Summarize actuals against the latest forecast").add_argument(
        "--plan-id",
        type=int,
        required=True,
    )

    export_cmd = subparsers.add_parser("export", help="Export a saved plan")
    export_cmd.add_argument("--plan-id", type=int, required=True)
    export_cmd.add_argument("--path", required=True)

    import_cmd = subparsers.add_parser("import", help="Import a saved plan")
    import_cmd.add_argument("--path", required=True)
    import_cmd.add_argument("--name")
    return parser


def run_cli(args) -> None:
    """Run optional CLI commands with plain errors for normal user mistakes."""
    try:
        service = PlanHistoryService()
        if args.command == "plan":
            run_plan_command(service, args)
        elif args.command == "actual":
            run_actual_command(service, args)
        elif args.command == "export":
            path = service.export_plan_json(args.plan_id, args.path)
            print(f"Exported plan to {path}")
        elif args.command == "import":
            plan = service.import_plan_json(args.path, new_name=args.name)
            print(f"Imported plan {plan.id}: {plan.name}")
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")


def run_plan_command(service: PlanHistoryService, args) -> None:
    """Run plan-history CLI commands."""
    if args.plan_command == "save":
        config = Config().load()
        existing = [plan for plan in service.list_plans() if plan.name == args.name]
        if existing:
            version = service.save_plan_version(
                existing[0].id,
                config,
                change_note=args.note,
                source="cli",
                force=args.force,
            )
            print(f"Saved version {version.version_number} for {args.name}")
        else:
            plan = service.create_plan(
                args.name,
                config,
                description=args.description,
                change_note=args.note,
                source="cli",
            )
            print(f"Created plan {plan.id}: {plan.name}")
    elif args.plan_command == "list":
        for plan in service.list_plans():
            print(f"{plan.id}: {plan.name}")
    elif args.plan_command == "history":
        for version in service.list_plan_versions(args.plan_id):
            active = " active" if version.active else ""
            print(f"v{version.version_number}: {version.change_note}{active}")
    elif args.plan_command == "compare":
        comparison = service.compare_plan_versions(args.from_version, args.to_version)
        print(comparison.explanation)
    elif args.plan_command == "restore":
        version = service.restore_plan_version(args.version)
        print(f"Restored as version {version.version_number}")


def run_actual_command(service: PlanHistoryService, args) -> None:
    """Run actual-entry CLI commands."""
    if args.actual_command == "add":
        entry = service.add_actual_entry(
            args.plan_id,
            date.fromisoformat(args.date),
            args.type,
            args.amount,
            category=args.category,
            description=args.description,
            source="cli",
        )
        print(f"Added actual entry {entry.id}")
    elif args.actual_command == "summary":
        summary = service.compare_forecast_to_actual(args.plan_id)
        print(summary.status)


if __name__ == "__main__":
    main()
