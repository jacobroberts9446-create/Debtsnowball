"""DebtPilot application composition root."""

from decimal import Decimal
from typing import Callable

from app.budget_setup import BudgetSetupResult, collect_budget_setup
from app.cli import CliDependencies, main as cli_main
from app.config import Config
from app.console import print_application_banner, print_success
from app.history import PlanHistoryService
from app.menu import (
    InputFunc,
    MenuOption,
    OutputFunc,
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
from app.version import APP_NAME, APP_VERSION
from app.workflows.saved_plans import (
    run_saved_plans_menu,
    save_current_plan,
)
from app.workflows.plan_history import print_plan_comparison
from app.workflows.workbook_export import (
    generate_budget_plan,
    write_history_report,
)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint with DebtPilot workflow callbacks."""
    return cli_main(
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
        title=f"{APP_NAME} v{APP_VERSION}",
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
    """Run interactive plan setup and display its generated results."""
    result = plan_setup_func(input_func=input_func, output_func=output_func)
    if result is not None:
        results_viewer_func(result, input_func=input_func, output_func=output_func)
        output_func("Returned from plan results.")
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
    output_func("Create New Plan: enter your budget, debts, and savings strategy.")
    output_func(
        "Saved Plans: reopen plans, generate workbooks, and track progress."
    )
    output_func(
        "Track Progress: record activity and balances, then compare them "
        "with your forecast."
    )
    output_func(
        "History: review versions, compare changes, restore a version, "
        "or correct recorded activity."
    )
    output_func("Workbook: generate the Excel plan for a selected saved plan.")
    output_func("Versioning: save plan changes without overwriting prior versions.")
    output_func(f"Exit: closes {APP_NAME}.")
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
    raise SystemExit(main())
