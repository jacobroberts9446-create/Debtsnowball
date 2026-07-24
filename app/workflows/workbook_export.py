"""Workbook and report export workflows."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from typing import Callable

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.console import (
    print_application_banner,
    print_error,
    print_section_header,
    print_success,
)
from app.excel_writer import ExcelWriter
from app.forecast_engine import ForecastEngine
from app.history import PlanHistoryService
from app.menu import InputFunc, OutputFunc, wait_for_enter
from app.money import format_currency
from app.scenario_engine import ScenarioEngine
from app.target_calculator import DebtFreeTargetCalculator
from app.version import APP_VERSION


def run_generate_budget_plan_action(
    generate_budget_plan_func: Callable[[], None],
    output_func: OutputFunc = print,
) -> bool:
    """Run budget generation and return to the interactive menu."""
    try:
        generate_budget_plan_func()
    except SystemExit as exc:
        print_error(f"Plan generation stopped unexpectedly: {exc}", output_func)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print_error(str(exc), output_func)
    return False


def generate_saved_plan_workbook_action(
    service: PlanHistoryService,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Generate an Excel workbook from the selected plan's latest saved inputs."""
    output_func("")
    try:
        config = config_from_plan_version(latest_plan_version(service, plan))
        summaries, forecast, scenario_comparison, target_result = build_workbook_outputs(
            config,
        )
        workbook_path = ExcelWriter().write(
            summaries,
            forecast,
            scenario_comparison,
            target_result,
        )
        verify_workbook_created(workbook_path)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print_error(str(exc), output_func)
    else:
        print_success("Workbook created successfully.", output_func)
        output_func("")
        output_func("Location:")
        output_func(str(workbook_path))
    wait_for_enter(input_func)
    return False


def config_from_plan_version(version) -> Config:
    """Rebuild a Config object from one saved immutable version snapshot."""
    try:
        snapshot = json.loads(version.config_snapshot)
        return Config().load_mapping(snapshot)
    except (AttributeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"saved plan version {version.id} has an invalid config snapshot: {exc}",
        ) from exc


def latest_plan_version(service: PlanHistoryService, plan):
    """Return the current version for a saved plan."""
    current_version_id = getattr(plan, "current_version_id", None)
    if current_version_id is not None:
        return service.get_plan_version(current_version_id)
    versions = service.list_plan_versions(plan.id)
    if not versions:
        raise ValueError("selected plan does not have any saved versions.")
    return versions[-1]


def build_workbook_outputs(config: Config):
    """Build workbook inputs using the existing budget and forecast pipeline."""
    calendar = CalendarEngine(config.settings)
    periods = calendar.generate(date(2026, 12, 31))
    forecast_config = deepcopy(config)
    summaries = BudgetEngine(config).build_plan(periods)
    forecast = ForecastEngine(forecast_config).forecast()
    scenario_comparison = build_scenario_comparison(forecast_config)
    target_result = build_debt_free_target_result(forecast_config)
    return summaries, forecast, scenario_comparison, target_result


def verify_workbook_created(path: str | Path) -> Path:
    """Return a workbook path only after confirming the file exists."""
    workbook_path = Path(path)
    if not workbook_path.exists():
        raise RuntimeError(f"workbook was not created: {workbook_path}")
    if not workbook_path.is_file():
        raise RuntimeError(f"workbook path is not a file: {workbook_path}")
    if workbook_path.stat().st_size <= 0:
        raise RuntimeError(f"workbook file is empty: {workbook_path}")
    return workbook_path


def generate_budget_plan() -> None:
    """Run the existing default budget-generation workflow."""
    config = Config().load()
    summaries, forecast, scenario_comparison, target_result = build_workbook_outputs(
        config,
    )
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

    print_success("Excel workbook created:")
    print(workbook_path)


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
