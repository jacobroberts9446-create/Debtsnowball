"""
run.py

DebtSnowball
"""

from decimal import Decimal
from typing import Callable

from app.budget_setup import BudgetSetupResult, collect_budget_setup
from app.cli import CliDependencies, main as cli_main
from app.config import Config
from app.console import (
    print_application_banner,
    print_error,
    print_success,
    print_table,
    print_warning,
)
from app.history import PlanHistoryService
from app.menu import (
    InputFunc,
    MenuOption,
    OutputFunc,
    display_menu,
    run_menu,
    wait_for_enter,
)
from app.preferences import RecentPlanPreferences
from app.plan_generation import PAYCHECKS_PER_MONTH
from app.plan_setup import PayFrequency
from app.results_viewer import view_results
from app.savings_setup import (
    SavingsStrategy,
    SavingsStrategySelection,
    default_savings_strategy,
)
from app.version import APP_VERSION
from app.workflows.saved_plans import (
    display_plan_name,
    format_saved_datetime,
    run_saved_plans_menu,
    save_current_plan,
    version_count_label,
)
from app.workflows.workbook_export import (
    generate_budget_plan,
    write_history_report,
)


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
    plan_setup_func: Callable[..., object | None] = collect_budget_setup,
    results_viewer_func: Callable[..., None] = view_results,
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
        plan_setup_func=plan_setup_func,
        results_viewer_func=results_viewer_func,
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
    plan_setup_func: Callable[..., object | None] = collect_budget_setup,
    results_viewer_func: Callable[..., None] = view_results,
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
            "Create New Plan",
            lambda: run_create_new_plan_action(
                plan_setup_func,
                results_viewer_func,
                input_func=input_func,
                output_func=output_func,
            ),
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
                selected_plan_history_action=view_selected_plan_history_action,
                setup_from_config_func=setup_from_config,
                setup_from_generated_plan_func=setup_from_generated_plan,
            ),
        ),
        MenuOption(
            "3",
            "Help",
            lambda: show_menu_help(input_func=input_func, output_func=output_func),
        ),
        MenuOption("4", "Exit", lambda: exit_menu(output_func)),
    ]


def run_create_new_plan_action(
    plan_setup_func: Callable[..., object | None],
    results_viewer_func: Callable[..., None],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Run interactive setup for the create-plan milestone."""
    result = plan_setup_func(input_func=input_func, output_func=output_func)
    if result is not None:
        results_viewer_func(result, input_func=input_func, output_func=output_func)
        output_func("Returned from plan results.")
    return False


def view_selected_plan_history_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display history for a selected plan and allow version summary viewing."""
    output_func("")
    try:
        versions = display_plan_history(service, preferences, plan, output_func)
        if versions:
            select_plan_version_summary(service, versions, input_func, output_func)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
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
    """Let the user select a saved plan and display its versions."""
    output_func("")
    try:
        plan = select_saved_plan_for_history(service, input_func, output_func)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if plan is not None:
        try:
            display_plan_history(service, preferences, plan, output_func)
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)

    wait_for_enter(input_func)
    return False


def select_saved_plan_for_history(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
):
    """Display saved plans and return the selected plan, or None for Back."""
    plans = service.list_plans()
    if not plans:
        output_func("No saved plans found.")
        return None

    rows = []
    for index, plan in enumerate(plans, start=1):
        rows.append(
            [
                str(index),
                plan.name,
                getattr(plan, "updated_at", "") or getattr(plan, "created_at", ""),
                version_count_label(service, plan),
            ]
        )
    print_table(["#", "Plan", "Updated", "Versions"], rows, output_func)

    back_key = str(len(plans) + 1)
    options = [
        *[
            MenuOption(str(index), plan.name, lambda plan=plan: plan)
            for index, plan in enumerate(plans, start=1)
        ],
        MenuOption(back_key, "Back", lambda: None),
    ]
    display_menu("Select Plan", options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is not None:
            return option.action()
        print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)


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
) -> list:
    """Display versions for an already-selected plan."""
    versions = service.list_plan_versions(plan.id)
    preferences.mark_recent(plan.id, plan.name)
    output_func(f"Plan: {display_plan_name(plan)}")
    print_plan_versions(versions, output_func)
    return versions


def select_plan_version_summary(
    service: PlanHistoryService,
    versions,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Allow the user to select a displayed version and view its summary."""
    options = [
        MenuOption(
            str(index),
            f"View Version {version.version_number}",
            lambda version=version: version,
        )
        for index, version in enumerate(versions, start=1)
    ]
    back_key = str(len(options) + 1)
    options.append(MenuOption(back_key, "Back", lambda: None))
    display_menu("Select Version", options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is None:
            print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)
            continue
        version = option.action()
        if version is None:
            return
        try:
            output_func("")
            output_func(service.plan_summary(version.id))
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)
        return


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
        ["Version", "Saved", "Status", "Note"],
        [
            [
                f"Version {version.version_number}",
                format_saved_datetime(version.created_at),
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
    output_func("Create New Plan: starts the guided interactive setup workflow.")
    output_func("Saved Plans: opens separate scenarios or people's saved plans.")
    output_func("History: select a saved plan, then view its prior versions.")
    output_func("Help: explains the menu options.")
    output_func("Exit: closes DebtSnowball without generating a plan.")
    wait_for_enter(input_func)
    return False


def load_current_config() -> Config:
    """Load the current application configuration."""
    return Config().load()


def setup_from_config(plan_name: str, config: Config) -> BudgetSetupResult:
    """Convert a saved engine config into the interactive setup model."""
    pay_frequency = PayFrequency(
        getattr(config.settings, "pay_frequency", PayFrequency.BIWEEKLY.value)
    )
    monthly_personal = format_currency_decimal(
        config.settings.personal_per_paycheck * PAYCHECKS_PER_MONTH[pay_frequency],
    )
    return BudgetSetupResult(
        plan_name=plan_name,
        pay_frequency=pay_frequency,
        first_paycheck_date=config.settings.first_paycheck,
        net_paycheck_amount=config.settings.paycheck,
        debts=list(config.debts),
        bills=list(config.bills),
        monthly_personal_spending=monthly_personal,
        current_savings=config.settings.starting_savings,
        emergency_fund_target=config.settings.savings_goal,
        savings_strategy=savings_strategy_from_config(config),
    )


def setup_from_generated_plan(generated_plan) -> Config:
    """Return the existing engine config shape for a generated interactive plan."""
    from app.plan_generation import setup_to_engine_config

    return setup_to_engine_config(generated_plan.setup)


def format_currency_decimal(value):
    """Normalize a Decimal-compatible currency value for setup reuse."""
    from app.money import money

    return money(value)


def savings_strategy_from_config(config: Config) -> SavingsStrategySelection:
    """Infer the closest interactive savings strategy from existing settings."""
    savings_percentage = getattr(
        config.settings,
        "savings_percentage_override",
        None,
    )
    if savings_percentage is None:
        return default_savings_strategy()
    percent = Decimal(str(savings_percentage)) * Decimal("100")
    if percent == Decimal("100"):
        return SavingsStrategySelection(
            SavingsStrategy.EMERGENCY_FIRST,
            savings_percent=Decimal("100"),
            snowball_percent=Decimal("0"),
        )
    if percent == Decimal("0"):
        return SavingsStrategySelection(
            SavingsStrategy.SNOWBALL,
            savings_percent=Decimal("0"),
            snowball_percent=Decimal("100"),
        )
    return SavingsStrategySelection(
        SavingsStrategy.CUSTOM,
        savings_percent=percent,
        snowball_percent=Decimal("100") - percent,
    )


if __name__ == "__main__":
    main()
