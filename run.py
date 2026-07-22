"""
run.py

DebtSnowball
"""

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Callable

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

APP_VERSION = "1.1.0"
InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]
MenuAction = Callable[[], bool]


@dataclass(frozen=True)
class MenuOption:
    """One selectable interactive menu option."""

    key: str
    label: str
    action: MenuAction


def main(argv: list[str] | None = None) -> None:
    """Run either the existing argparse CLI or the interactive main menu."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command:
        run_cli(args)
        return

    run_main_menu()


def run_main_menu(
    generate_budget_plan_func: Callable[[], None] | None = None,
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    config_loader: Callable[[], Config] | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Show the interactive menu for normal no-argument runs."""
    generate_budget_plan_func = generate_budget_plan_func or generate_budget_plan
    config_loader = config_loader or load_current_config
    options = build_main_menu_options(
        generate_budget_plan_func,
        plan_history_service_factory=plan_history_service_factory,
        config_loader=config_loader,
        input_func=input_func,
        output_func=output_func,
    )

    run_menu(
        title=f"DebtSnowball v{APP_VERSION}",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )


def run_menu(
    title: str,
    options: list[MenuOption],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Display a menu until an action requests exit."""
    option_map = {option.key: option for option in options}
    while True:
        display_menu(title, options, output_func)

        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)

        if option is not None:
            should_exit = option.action()
            if should_exit:
                return
            continue

        output_func(f"Please choose one of: {', '.join(option_map)}.")


def display_menu(
    title: str,
    options: list[MenuOption],
    output_func: OutputFunc = print,
) -> None:
    """Print a numbered interactive menu."""
    output_func("")
    output_func(title)
    output_func("")
    for option in options:
        output_func(f"{option.key}. {option.label}")
    output_func("")


def build_main_menu_options(
    generate_budget_plan_func: Callable[[], None],
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    config_loader: Callable[[], Config] | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> list[MenuOption]:
    """Build the top-level interactive menu options."""
    config_loader = config_loader or load_current_config
    return [
        MenuOption(
            "1",
            "Generate Budget Plan",
            lambda: run_generate_budget_plan_action(generate_budget_plan_func),
        ),
        MenuOption(
            "2",
            "Saved Plans",
            lambda: run_saved_plans_menu(
                plan_history_service_factory=plan_history_service_factory,
                config_loader=config_loader,
                input_func=input_func,
                output_func=output_func,
            ),
        ),
        MenuOption(
            "3",
            "History",
            lambda: show_placeholder_screen(
                "History tools are coming in a future v1.1 update.",
                input_func=input_func,
                output_func=output_func,
            ),
        ),
        MenuOption(
            "4",
            "Help",
            lambda: show_menu_help(input_func=input_func, output_func=output_func),
        ),
        MenuOption("5", "Exit", lambda: exit_menu(output_func)),
    ]


def run_generate_budget_plan_action(generate_budget_plan_func: Callable[[], None]) -> bool:
    """Run budget generation and exit the interactive menu."""
    generate_budget_plan_func()
    return True


def run_saved_plans_menu(
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    config_loader: Callable[[], Config] | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open saved-plan tools and return to the main menu when finished."""
    config_loader = config_loader or load_current_config
    try:
        service = plan_history_service_factory()
    except (FileNotFoundError, ValueError) as exc:
        output_func(f"Error: {exc}")
        wait_for_enter(input_func)
        return False
    options = [
        MenuOption(
            "1",
            "List Saved Plans",
            lambda: list_saved_plans_action(service, input_func, output_func),
        ),
        MenuOption(
            "2",
            "Save Current Plan",
            lambda: save_current_plan_action(
                service,
                config_loader,
                input_func,
                output_func,
            ),
        ),
        MenuOption("3", "Back", lambda: True),
    ]

    run_menu(
        title="Saved Plans",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def list_saved_plans_action(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """List saved plans, then return to the saved-plans submenu."""
    output_func("")
    try:
        plans = service.list_plans()
    except ValueError as exc:
        output_func(f"Error: {exc}")
    else:
        if not plans:
            output_func("No saved plans found.")
        for plan in plans:
            output_func(f"{plan.id}: {plan.name}")
            if plan.description:
                output_func(f"   {plan.description}")

    wait_for_enter(input_func)
    return False


def save_current_plan_action(
    service: PlanHistoryService,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for saved-plan details and save through PlanHistoryService."""
    output_func("")
    name = input_func("Plan name: ").strip()
    if not name:
        output_func("Plan name cannot be blank.")
        wait_for_enter(input_func)
        return False

    description = input_func("Description (optional): ").strip()
    try:
        config = config_loader()
        existing = [plan for plan in service.list_plans() if plan.name == name]
    except (FileNotFoundError, ValueError) as exc:
        output_func(f"Error: {exc}")
        wait_for_enter(input_func)
        return False

    if existing:
        confirm = input_func(
            f"A plan named '{name}' already exists. Save a new version? [y/N]: "
        ).strip()
        if confirm.casefold() not in {"y", "yes"}:
            output_func("Save cancelled.")
            wait_for_enter(input_func)
            return False

    try:
        result = save_current_plan(
            service,
            config,
            name=name,
            description=description,
            change_note="Saved from interactive menu",
            source="interactive",
            force=bool(existing),
        )
    except (FileNotFoundError, ValueError) as exc:
        output_func(f"Error: {exc}")
    else:
        if result[0] == "created":
            output_func(f"Created plan {result[1].id}: {result[1].name}")
        else:
            output_func(f"Saved version {result[1].version_number} for {name}")

    wait_for_enter(input_func)
    return False


def show_placeholder_screen(
    message: str,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Show a future-feature message before returning to the main menu."""
    output_func("")
    output_func(message)
    wait_for_enter(input_func)
    return False


def wait_for_enter(input_func: InputFunc = input) -> None:
    """Wait for the user to press Enter."""
    input_func("Press Enter to return to the main menu...")


def exit_menu(output_func: OutputFunc = print) -> bool:
    """Exit the interactive menu cleanly."""
    output_func("Goodbye.")
    return True


def show_menu_help(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Print brief help for the interactive menu before returning."""
    output_func("")
    output_func("Generate Budget Plan: creates the budget plan and Excel workbook.")
    output_func("Saved Plans: opens saved plan tools when they are available.")
    output_func("History: opens plan history tools when they are available.")
    output_func("Help: explains the menu options.")
    output_func("Exit: closes DebtSnowball without generating a plan.")
    wait_for_enter(input_func)
    return False


def load_current_config() -> Config:
    """Load the current application configuration."""
    return Config().load()


def save_current_plan(
    service: PlanHistoryService,
    config: Config,
    *,
    name: str,
    description: str = "",
    change_note: str = "Saved from CLI",
    source: str = "cli",
    force: bool = False,
):
    """Create a plan or save a new version using the existing service behavior."""
    existing = [plan for plan in service.list_plans() if plan.name == name]
    if existing:
        version = service.save_plan_version(
            existing[0].id,
            config,
            change_note=change_note,
            source=source,
            force=force,
        )
        return "version", version

    plan = service.create_plan(
        name,
        config,
        description=description,
        change_note=change_note,
        source=source,
    )
    return "created", plan


def generate_budget_plan() -> None:
    """Run the existing default budget-generation workflow."""
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
    print(f"DebtSnowball v{APP_VERSION}")
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
    plan_delete = plan_sub.add_parser("delete", help="Permanently delete an archived plan")
    plan_delete.add_argument("--plan-id", type=int, required=True)
    plan_delete.add_argument("--confirm-name", required=True)
    plan_delete.add_argument("--export-path")

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
    actual_balance = actual_sub.add_parser("balance", help="Record an actual balance observation")
    actual_balance.add_argument("--plan-id", type=int, required=True)
    actual_balance.add_argument("--date", required=True)
    actual_balance.add_argument(
        "--type",
        choices=[
            ActualEntryType.DEBT_BALANCE_OBSERVATION.value,
            ActualEntryType.SAVINGS_BALANCE_OBSERVATION.value,
        ],
        required=True,
    )
    actual_balance.add_argument("--balance", required=True)
    actual_balance.add_argument("--debt")
    actual_balance.add_argument("--note", default="")

    export_cmd = subparsers.add_parser("export", help="Export a saved plan")
    export_cmd.add_argument("--plan-id", type=int, required=True)
    export_cmd.add_argument("--path", required=True)
    export_cmd.add_argument("--format", choices=["json", "csv"], default="json")

    import_cmd = subparsers.add_parser("import", help="Import a saved plan")
    import_cmd.add_argument("--path", required=True)
    import_cmd.add_argument("--name")

    report_cmd = subparsers.add_parser("history-report", help="Create a saved history workbook")
    report_cmd.add_argument("--plan-id", type=int, required=True)
    report_cmd.add_argument("--path", required=True)
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
            if args.format == "csv":
                paths = service.export_csv_bundle(args.plan_id, args.path)
                print(f"Exported {len(paths)} CSV files to {args.path}")
            else:
                path = service.export_plan_json(args.plan_id, args.path)
                print(f"Exported plan to {path}")
        elif args.command == "import":
            plan = service.import_plan_json(args.path, new_name=args.name)
            print(f"Imported plan {plan.id}: {plan.name}")
        elif args.command == "history-report":
            write_history_report(service, args.plan_id, args.path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")


def run_plan_command(service: PlanHistoryService, args) -> None:
    """Run plan-history CLI commands."""
    if args.plan_command == "save":
        result = save_current_plan(
            service,
            load_current_config(),
            name=args.name,
            description=args.description,
            change_note=args.note,
            source="cli",
            force=args.force,
        )
        if result[0] == "version":
            version = result[1]
            print(f"Saved version {version.version_number} for {args.name}")
        else:
            plan = result[1]
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
    elif args.plan_command == "delete":
        service.delete_plan_permanently(
            args.plan_id,
            confirmation_name=args.confirm_name,
            export_path=args.export_path,
        )
        print(f"Deleted archived plan {args.plan_id}")


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
    elif args.actual_command == "balance":
        observation = service.add_balance_observation(
            args.plan_id,
            date.fromisoformat(args.date),
            ActualEntryType(args.type),
            args.balance,
            debt_identifier=args.debt,
            note=args.note,
            source="cli",
        )
        print(f"Added balance observation {observation.id}")


def write_history_report(service: PlanHistoryService, plan_id: int, path: str) -> None:
    """Write a detailed local history workbook for a saved plan."""
    plan = service.get_plan(plan_id)
    details = service.history_report_rows(plan_id)
    workbook_path = ExcelWriter(path).write_history_report(
        plan,
        service.list_plan_versions(plan_id),
        actual_comparison=service.compare_forecast_to_actual(plan_id),
        forecast_snapshots=details["forecast_snapshots"],
        actual_periods=service.compare_forecast_to_actual_periods(plan_id),
        debt_history=details["debt_history"],
        savings_history=details["savings_history"],
        warnings=details["warnings"],
    )
    print(f"History report created: {workbook_path}")


if __name__ == "__main__":
    main()
