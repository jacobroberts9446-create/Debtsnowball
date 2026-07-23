"""
run.py

DebtSnowball
"""

from copy import deepcopy
from datetime import date
from typing import Callable

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.cli import CliDependencies, main as cli_main
from app.config import Config
from app.console import (
    print_application_banner,
    print_error,
    print_section_header,
    print_success,
    print_table,
    print_warning,
)
from app.excel_writer import ExcelWriter
from app.forecast_engine import ForecastEngine
from app.history import PlanHistoryService
from app.menu import (
    InputFunc,
    MenuOption,
    OutputFunc,
    display_menu,
    run_menu,
    wait_for_enter,
)
from app.money import format_currency
from app.preferences import RecentPlanPreferences
from app.scenario_engine import ScenarioEngine
from app.target_calculator import DebtFreeTargetCalculator

APP_VERSION = "1.1.0"


def main(argv: list[str] | None = None) -> None:
    """Run the CLI entrypoint with DebtSnowball workflow callbacks."""
    cli_main(
        argv,
        interactive_runner=run_main_menu,
        dependencies=CliDependencies(
            load_current_config=load_current_config,
            save_current_plan=save_current_plan,
            print_plan_comparison=print_plan_comparison,
            write_history_report=write_history_report,
        ),
    )


def run_main_menu(
    generate_budget_plan_func: Callable[[], None] | None = None,
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
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
        preferences_factory=preferences_factory,
        config_loader=config_loader,
        input_func=input_func,
        output_func=output_func,
    )

    run_menu(
        title=f"DebtSnowball v{APP_VERSION}",
        options=options,
        input_func=input_func,
        output_func=output_func,
        title_renderer=render_main_menu_title,
    )


def render_main_menu_title(
    _title: str,
    output_func: OutputFunc = print,
) -> None:
    """Render the application banner used by the top-level interactive menu."""
    print_application_banner(APP_VERSION, output_func)


def build_main_menu_options(
    generate_budget_plan_func: Callable[[], None],
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
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
                preferences_factory=preferences_factory,
                config_loader=config_loader,
                input_func=input_func,
                output_func=output_func,
            ),
        ),
        MenuOption(
            "3",
            "History",
            lambda: run_history_menu(
                plan_history_service_factory=plan_history_service_factory,
                preferences_factory=preferences_factory,
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
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
    config_loader: Callable[[], Config] | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open saved-plan tools and return to the main menu when finished."""
    config_loader = config_loader or load_current_config
    try:
        service = plan_history_service_factory()
        preferences = preferences_factory()
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False
    options = [
        MenuOption(
            "1",
            "Recent Plans",
            lambda: list_recent_plans_action(
                service,
                preferences,
                config_loader,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "List All Saved Plans",
            lambda: list_saved_plans_action(service, input_func, output_func),
        ),
        MenuOption(
            "3",
            "Save Current Plan",
            lambda: save_current_plan_action(
                service,
                preferences,
                config_loader,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
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
        print_error(str(exc), output_func)
    else:
        if not plans:
            print_warning("No saved plans found.", output_func)
        else:
            print_table(
                ["ID", "Name", "Description"],
                [
                    [str(plan.id), plan.name, plan.description or ""]
                    for plan in plans
                ],
                output_func,
            )

    wait_for_enter(input_func)
    return False


def list_recent_plans_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Show actionable recently used plans."""
    while True:
        try:
            plans = service.list_plans()
            recent_plans = preferences.list_existing_recent_plans(plans)
        except (OSError, ValueError) as exc:
            print_error(str(exc), output_func)
            wait_for_enter(input_func)
            return False
        if not recent_plans:
            output_func("")
            print_warning("No recent plans found.", output_func)
            wait_for_enter(input_func)
            return False

        options = [
            MenuOption(
                str(index),
                f"{plan.id:<3} {plan.name}",
                lambda plan=plan: open_recent_plan_action(
                    service,
                    preferences,
                    config_loader,
                    plan.id,
                    input_func,
                    output_func,
                ),
            )
            for index, plan in enumerate(recent_plans, start=1)
        ]
        back_key = str(len(options) + 1)
        options.append(MenuOption(back_key, "Back", lambda: True))
        display_menu("Recent Plans", options, output_func)

        option_map = {option.key: option for option in options}
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is None:
            print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)
            continue
        if option.key == back_key:
            return False
        option.action()

    return False


def open_recent_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan_id: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open the selected recent plan or remove it if stale."""
    try:
        plan = service.get_plan(plan_id)
    except (FileNotFoundError, ValueError):
        preferences.remove_recent(plan_id)
        print_warning("That recent plan no longer exists and was removed.", output_func)
        wait_for_enter(input_func)
    else:
        preferences.mark_recent(plan.id, plan.name)
        run_selected_plan_menu(
            service,
            preferences,
            config_loader,
            plan,
            input_func=input_func,
            output_func=output_func,
        )

    return False


def run_selected_plan_menu(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Open actions for one selected recent plan."""
    options = [
        MenuOption(
            "1",
            "View History",
            lambda: view_selected_plan_history_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Save New Version",
            lambda: save_selected_plan_version_action(
                service,
                preferences,
                config_loader,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "3",
            "Restore Version",
            lambda: restore_selected_plan_version_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
    ]

    run_menu(
        title=f"Plan: {plan.name}",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )


def view_selected_plan_history_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display history for a selected plan without prompting for its ID."""
    output_func("")
    try:
        display_plan_history(service, preferences, plan, output_func)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    wait_for_enter(input_func)
    return False


def save_selected_plan_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Save a new version for a selected recent plan after confirmation."""
    output_func("")
    note = input_func("Version note (optional): ").strip()
    confirm = input_func(
        f"Save a new version for '{plan.name}'? [y/N]: "
    ).strip()
    if confirm.casefold() not in {"y", "yes"}:
        print_warning("Save cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        current_plan = service.get_plan(plan.id)
        result = save_current_plan(
            service,
            config_loader(),
            name=current_plan.name,
            change_note=note or "Saved from recent plan menu",
            source="interactive",
            force=True,
        )
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        version = result[1]
        preferences.mark_recent(current_plan.id, current_plan.name)
        print_success(
            f"Saved version {version.version_number} for {current_plan.name}",
            output_func,
        )

    wait_for_enter(input_func)
    return False


def restore_selected_plan_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Restore a version that belongs to the selected plan."""
    output_func("")
    version_id = prompt_positive_int("Version ID: ", input_func, output_func)
    if version_id is None:
        wait_for_enter(input_func)
        return False

    try:
        source_version = service.get_plan_version(version_id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if source_version.plan_id != plan.id:
        print_warning("That version does not belong to the selected plan.", output_func)
        wait_for_enter(input_func)
        return False

    return restore_version_by_id_action(
        service,
        preferences,
        version_id,
        input_func,
        output_func,
        plan=plan,
    )


def save_current_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for saved-plan details and save through PlanHistoryService."""
    output_func("")
    name = input_func("Plan name: ").strip()
    if not name:
        print_warning("Plan name cannot be blank.", output_func)
        wait_for_enter(input_func)
        return False

    description = input_func("Description (optional): ").strip()
    try:
        config = config_loader()
        existing = [plan for plan in service.list_plans() if plan.name == name]
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if existing:
        confirm = input_func(
            f"A plan named '{name}' already exists. Save a new version? [y/N]: "
        ).strip()
        if confirm.casefold() not in {"y", "yes"}:
            print_warning("Save cancelled.", output_func)
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
        print_error(str(exc), output_func)
    else:
        if result[0] == "created":
            preferences.mark_recent(result[1].id, result[1].name)
            print_success(f"Created plan {result[1].id}: {result[1].name}", output_func)
        else:
            preferences.mark_recent(existing[0].id, existing[0].name)
            print_success(
                f"Saved version {result[1].version_number} for {name}",
                output_func,
            )

    wait_for_enter(input_func)
    return False


def run_history_menu(
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open plan history tools and return to the main menu when finished."""
    try:
        service = plan_history_service_factory()
        preferences = preferences_factory()
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "View Plan History",
            lambda: view_plan_history_action(
                service,
                preferences,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Compare Versions",
            lambda: compare_versions_action(service, input_func, output_func),
        ),
        MenuOption(
            "3",
            "Restore Version",
            lambda: restore_version_action(
                service,
                preferences,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
    ]

    run_menu(
        title="History",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def view_plan_history_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for a plan ID and display saved versions."""
    output_func("")
    plan_id = prompt_positive_int("Plan ID: ", input_func, output_func)
    if plan_id is None:
        wait_for_enter(input_func)
        return False

    try:
        plan = service.get_plan(plan_id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        try:
            display_plan_history(service, preferences, plan, output_func)
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)

    wait_for_enter(input_func)
    return False


def compare_versions_action(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for two version IDs and display their existing comparison."""
    output_func("")
    from_version = prompt_positive_int("Starting version ID: ", input_func, output_func)
    if from_version is None:
        wait_for_enter(input_func)
        return False

    to_version = prompt_positive_int("Ending version ID: ", input_func, output_func)
    if to_version is None:
        wait_for_enter(input_func)
        return False

    try:
        comparison = service.compare_plan_versions(from_version, to_version)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        print_plan_comparison(comparison, output_func)

    wait_for_enter(input_func)
    return False


def restore_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for a version ID and restore it after confirmation."""
    output_func("")
    version_id = prompt_positive_int("Version ID: ", input_func, output_func)
    if version_id is None:
        wait_for_enter(input_func)
        return False

    return restore_version_by_id_action(
        service,
        preferences,
        version_id,
        input_func,
        output_func,
    )


def display_plan_history(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    output_func: OutputFunc = print,
) -> None:
    """Display versions for an already-selected plan."""
    versions = service.list_plan_versions(plan.id)
    preferences.mark_recent(plan.id, plan.name)
    print_plan_versions(versions, output_func)


def restore_version_by_id_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    version_id: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    *,
    plan=None,
) -> bool:
    """Restore one version by ID after confirmation."""
    confirm = input_func(f"Restore version {version_id} as a new version? [y/N]: ")
    if confirm.strip().casefold() not in {"y", "yes"}:
        print_warning("Restore cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        version = service.restore_plan_version(version_id)
        restored_plan = plan or service.get_plan(version.plan_id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        preferences.mark_recent(restored_plan.id, restored_plan.name)
        print_success(
            f"Restored as version {version.version_number} "
            f"(version ID {version.id}).",
            output_func,
        )

    wait_for_enter(input_func)
    return False


def prompt_positive_int(
    prompt: str,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int | None:
    """Prompt for a positive integer and return None for invalid input."""
    raw_value = input_func(prompt).strip()
    try:
        value = int(raw_value)
    except ValueError:
        print_warning("Please enter a positive whole number.", output_func)
        return None

    if value <= 0:
        print_warning("Please enter a positive whole number.", output_func)
        return None

    return value


def print_plan_versions(
    versions,
    output_func: OutputFunc = print,
) -> None:
    """Print saved plan versions in a clear, compact format."""
    if not versions:
        print_warning("No saved versions found for that plan.", output_func)
        return

    print_table(
        ["ID", "Version", "Created", "Status", "Note"],
        [
            [
                str(version.id),
                f"v{version.version_number}",
                version.created_at,
                "active" if version.active else "",
                version.change_note or "",
            ]
            for version in versions
        ],
        output_func,
    )


def print_plan_comparison(
    comparison,
    output_func: OutputFunc = print,
) -> None:
    """Print the existing plan-comparison result."""
    output_func(comparison.explanation)


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


def exit_menu(output_func: OutputFunc = print) -> bool:
    """Exit the interactive menu cleanly."""
    print_success("Goodbye.", output_func)
    return True


def show_menu_help(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Print brief help for the interactive menu before returning."""
    output_func("")
    output_func("Generate Budget Plan: creates the budget plan and Excel workbook.")
    output_func("Saved Plans: lists saved plans or saves the current plan.")
    output_func("History: views, compares, or restores saved plan versions.")
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
    print_application_banner(APP_VERSION)
    print_section_header("Budget Plan")

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

    print_success(f"Workbook created: {workbook_path}")

def build_scenario_comparison(config):
    """Build baseline plus configured scenario forecasts."""
    return ScenarioEngine(config, config.scenarios).compare()


def build_debt_free_target_result(config):
    """Build an optional debt-free target result from configuration."""
    if not config.debt_free_target.enabled:
        return None

    return DebtFreeTargetCalculator(config, config.debt_free_target).calculate()


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
