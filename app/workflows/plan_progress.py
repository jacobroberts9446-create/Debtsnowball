"""Read-only interactive progress views for one saved plan."""

from typing import Any

from app.console import print_error, print_section_header, print_table, print_warning
from app.history import PlanHistoryService
from app.menu import InputFunc, MenuOption, OutputFunc, run_menu, wait_for_enter
from app.money import format_currency
from app.workflows.plan_presenter import display_plan_name, format_saved_datetime

__all__ = [
    "run_plan_progress_menu",
    "show_forecast_vs_actual",
    "show_progress_summary",
]

EXPECTED_SERVICE_ERRORS = (FileNotFoundError, ValueError, RuntimeError, OSError)


def run_plan_progress_menu(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open read-only progress actions for one selected saved plan."""
    try:
        current_plan = service.get_plan(plan.id)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "Progress Summary",
            lambda: show_progress_summary(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Forecast vs Actual",
            lambda: show_forecast_vs_actual(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("3", "Back", lambda: True),
    ]
    run_menu(
        title=f"Track Progress - {display_plan_name(current_plan)}",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def show_progress_summary(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display persisted plan metadata and recorded progress counts."""
    output_func("")
    print_section_header("Progress Summary", output_func)
    try:
        current_plan = service.get_plan(plan.id)
        versions = service.list_plan_versions(current_plan.id)
        actual_entries = service.list_actual_entries(current_plan.id)
        observations = service.list_balance_observations(current_plan.id)
        status = _progress_status(service, current_plan.id, actual_entries, observations)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
    else:
        print_table(
            ["Item", "Value"],
            [
                ["Plan", display_plan_name(current_plan)],
                [
                    "Last updated",
                    format_saved_datetime(
                        getattr(current_plan, "updated_at", None)
                        or getattr(current_plan, "created_at", None)
                    ),
                ],
                ["Saved versions", str(len(versions))],
                ["Recorded transactions", str(len(actual_entries))],
                ["Balance observations", str(len(observations))],
                ["Status", status],
            ],
            output_func,
        )
    wait_for_enter(input_func)
    return False


def show_forecast_vs_actual(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display the existing aggregate forecast-versus-actual comparison."""
    output_func("")
    print_section_header("Forecast vs Actual", output_func)
    try:
        current_plan = service.get_plan(plan.id)
        actual_entries = service.list_actual_entries(current_plan.id)
        comparison = service.compare_forecast_to_actual(current_plan.id)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
    else:
        output_func(f"Plan: {display_plan_name(current_plan)}")
        output_func(f"Status: {comparison.status}")
        if not actual_entries:
            print_warning(
                "No progress has been recorded yet. "
                "Forecast values are available, but there are no actual transactions "
                "to compare.",
                output_func,
            )
        elif comparison.status == "Insufficient actual data":
            print_warning(
                "There is not enough recorded progress to compare yet.",
                output_func,
            )
        print_table(
            ["Category", "Forecast", "Actual"],
            [
                [
                    "Income",
                    format_currency(comparison.planned_income),
                    format_currency(comparison.actual_income),
                ],
                [
                    "Bills",
                    format_currency(comparison.planned_bills),
                    format_currency(comparison.actual_bills),
                ],
                [
                    "Debt payments",
                    format_currency(comparison.planned_debt_payments),
                    format_currency(comparison.actual_debt_payments),
                ],
                [
                    "Savings",
                    format_currency(comparison.planned_savings),
                    format_currency(comparison.actual_savings),
                ],
                [
                    "Personal spending",
                    format_currency(comparison.planned_personal_spending),
                    format_currency(comparison.actual_personal_spending),
                ],
                [
                    "Remaining cash",
                    format_currency(comparison.planned_remaining_cash),
                    format_currency(comparison.actual_remaining_cash),
                ],
            ],
            output_func,
        )
    wait_for_enter(input_func)
    return False


def _progress_status(
    service: PlanHistoryService,
    plan_id: int,
    actual_entries: list[Any],
    observations: list[Any],
) -> str:
    """Return the existing comparison status when progress has been recorded."""
    if not actual_entries and not observations:
        return "No progress recorded"
    return service.compare_forecast_to_actual(plan_id).status
