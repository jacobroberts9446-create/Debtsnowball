"""Interactive progress views and recording workflows for one saved plan."""

from datetime import date
from decimal import Decimal
from typing import Any

from app.console import (
    print_error,
    print_section_header,
    print_success,
    print_table,
    print_warning,
)
from app.debt_input import parse_nonnegative_money
from app.history import PlanHistoryService
from app.menu import (
    InputFunc,
    MenuOption,
    OutputFunc,
    display_menu,
    run_menu,
    wait_for_enter,
)
from app.models import ActualDataCompleteness, ActualEntryType, DebtBalanceComparison
from app.money import format_currency, money
from app.plan_setup import parse_first_paycheck_date
from app.workflows.plan_presenter import display_plan_name, format_saved_datetime
from app.workflows.workbook_export import config_from_plan_version, latest_plan_version

__all__ = [
    "correct_entry_action",
    "record_adjustment_action",
    "record_bill_payment_action",
    "record_debt_balance_action",
    "record_debt_payment_action",
    "record_income_action",
    "record_personal_spending_action",
    "record_savings_balance_action",
    "record_savings_deposit_action",
    "reverse_entry_action",
    "run_entry_review_menu",
    "run_record_activity_menu",
    "run_record_balance_menu",
    "run_plan_progress_menu",
    "show_forecast_vs_actual",
    "show_progress_summary",
    "view_recent_entries_action",
]

EXPECTED_SERVICE_ERRORS = (FileNotFoundError, ValueError, RuntimeError, OSError)
RECENT_ENTRY_LIMIT = 20

ACTIVITY_TYPE_LABELS = {
    ActualEntryType.INCOME_RECEIVED: "Income Received",
    ActualEntryType.BILL_PAID: "Bill Payment",
    ActualEntryType.DEBT_PAYMENT: "Debt Payment",
    ActualEntryType.SAVINGS_DEPOSIT: "Savings Deposit",
    ActualEntryType.SAVINGS_WITHDRAWAL: "Savings Withdrawal",
    ActualEntryType.PERSONAL_SPENDING: "Personal Spending",
    ActualEntryType.ADJUSTMENT: "Adjustment",
}
COMPLETENESS_LABELS = {
    ActualEntryType.INCOME_RECEIVED: "Income",
    ActualEntryType.BILL_PAID: "Bills",
    ActualEntryType.DEBT_PAYMENT: "Debt Payments",
    ActualEntryType.SAVINGS_DEPOSIT: "Savings",
    ActualEntryType.PERSONAL_SPENDING: "Personal Spending",
}


def run_plan_progress_menu(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open progress views and recording actions for one selected saved plan."""
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
        MenuOption(
            "3",
            "Record Activity",
            lambda: run_record_activity_menu(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "4",
            "Record Balance",
            lambda: run_record_balance_menu(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "5",
            "Review Recorded Activity",
            lambda: run_entry_review_menu(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("6", "Back", lambda: True),
    ]
    run_menu(
        title=f"Track Progress - {display_plan_name(current_plan)}",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def run_record_activity_menu(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open supported activity-recording actions for one selected plan."""
    try:
        current_plan = _current_plan_with_version(service, plan)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "Income Received",
            lambda: record_income_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Bill Payment",
            lambda: record_bill_payment_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "3",
            "Debt Payment",
            lambda: record_debt_payment_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "4",
            "Savings Deposit",
            lambda: record_savings_deposit_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "5",
            "Personal Spending",
            lambda: record_personal_spending_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "6",
            "Adjustment",
            lambda: record_adjustment_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("7", "Back", lambda: True),
    ]
    run_menu(
        title="Record Activity",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def run_entry_review_menu(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open recent-activity review, correction, and reversal actions."""
    try:
        current_plan = _current_plan_with_version(service, plan)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "View Recent Entries",
            lambda: view_recent_entries_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Correct an Entry",
            lambda: correct_entry_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "3",
            "Reverse an Entry",
            lambda: reverse_entry_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
    ]
    run_menu(
        title="Review Recorded Activity",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def view_recent_entries_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display recent actual activity without internal identifiers."""
    output_func("")
    print_section_header("Recent Recorded Activity", output_func)
    try:
        current_plan = _current_plan_with_version(service, plan)
        entries = _recent_actual_entries(service, current_plan)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
    else:
        if not entries:
            print_warning("No recorded activity entries were found.", output_func)
        else:
            status_context = _entry_status_context(entries)
            for index, entry in enumerate(entries, start=1):
                output_func(
                    f"{index}. {_format_actual_entry(entry, status_context)}"
                )
    wait_for_enter(input_func)
    return False


def correct_entry_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Collect a complete replacement and atomically correct one activity."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        all_entries = service.list_actual_entries(current_plan.id)
        entries = _sort_recent_actual_entries(all_entries)
        status_context = _entry_status_context(all_entries)
        selected = _select_actual_entry(
            entries,
            input_func,
            output_func,
            status_context=status_context,
        )
        if selected is None:
            print_warning("Activity correction cancelled.", output_func)
            return False
        _validate_correction_selection(selected, status_context)
        entry_type = ActualEntryType(str(selected.entry_type))

        output_func("")
        print_section_header("Current Recorded Activity", output_func)
        output_func(_format_actual_entry(selected, status_context))

        replacement_date = _prompt_replacement_date(
            selected.entry_date,
            input_func,
            output_func,
        )
        replacement_amount = _prompt_replacement_amount(
            selected.amount,
            entry_type,
            input_func,
            output_func,
        )
        category, debt_identifier = _prompt_replacement_association(
            service,
            current_plan,
            selected,
            entry_type,
            input_func,
            output_func,
        )
        note = _prompt_replacement_note(selected.note, input_func)

        output_func("")
        print_section_header("Corrected Recorded Activity", output_func)
        output_func(f"Date: {replacement_date:%m/%d/%Y}")
        output_func(f"Amount: {format_currency(replacement_amount)}")
        if debt_identifier:
            output_func(f"Debt: {debt_identifier}")
        elif category:
            output_func(f"Category: {category}")
        output_func(f"Note: {note or 'None'}")
        confirmation = input_func("Type CORRECT to confirm: ").strip()
        if confirmation != "CORRECT":
            print_warning("Activity correction cancelled.", output_func)
            return False

        service.correct_actual_entry(
            current_plan.id,
            selected.id,
            entry_date=replacement_date,
            amount=replacement_amount,
            category=category,
            debt_identifier=debt_identifier,
            note=note,
        )
    except _ActivityCancelled:
        print_warning("Activity correction cancelled.", output_func)
        return False
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    print_success("Recorded activity corrected successfully.", output_func)
    wait_for_enter(input_func)
    return False


def reverse_entry_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Select and explicitly confirm one eligible activity reversal."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        all_entries = service.list_actual_entries(current_plan.id)
        entries = _sort_recent_actual_entries(all_entries)
        status_context = _entry_status_context(all_entries)
        selected = _select_actual_entry(
            entries,
            input_func,
            output_func,
            status_context=status_context,
        )
        if selected is None:
            print_warning("Activity reversal cancelled.", output_func)
            return False
        _validate_reversal_selection(selected, status_context)
        output_func("")
        output_func(f"Selected: {_format_actual_entry(selected, status_context)}")
        output_func(
            "Reversal preserves the original entry and adds an equal opposite entry."
        )
        confirmation = input_func("Type REVERSE to confirm: ").strip()
        if confirmation != "REVERSE":
            print_warning("Activity reversal cancelled.", output_func)
            return False
        service.reverse_actual_entry(
            selected.id,
            plan_id=current_plan.id,
            note="Reversed from interactive review",
        )
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    print_success("Recorded activity reversed successfully.", output_func)
    wait_for_enter(input_func)
    return False


def run_record_balance_menu(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open supported balance-observation actions for one selected plan."""
    try:
        current_plan = _current_plan_with_version(service, plan)
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "Debt Balance",
            lambda: record_debt_balance_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Savings Balance",
            lambda: record_savings_balance_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("3", "Back", lambda: True),
    ]
    run_menu(
        title="Record Balance",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def record_debt_balance_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record the current balance for a numbered saved debt."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        config = _latest_plan_config(service, current_plan)
        selected = _select_named_entity(
            config.debts,
            "debt",
            input_func,
            output_func,
        )
        if selected is None:
            print_warning("Debt Balance cancelled.", output_func)
            return False
        debt_name = selected.name.strip()
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    return _record_balance(
        service,
        current_plan,
        ActualEntryType.DEBT_BALANCE_OBSERVATION,
        "Debt Balance",
        input_func,
        output_func,
        debt_name=debt_name,
    )


def record_savings_balance_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record the current plan-level savings balance."""
    return _record_balance(
        service,
        plan,
        ActualEntryType.SAVINGS_BALANCE_OBSERVATION,
        "Savings Balance",
        input_func,
        output_func,
    )


def record_income_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record received income against the selected plan."""
    return _record_activity(
        service,
        plan,
        ActualEntryType.INCOME_RECEIVED,
        "Income Received",
        input_func,
        output_func,
        category="Income",
    )


def record_bill_payment_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record a payment for a numbered bill from the latest saved plan."""
    return _record_named_activity(
        service,
        plan,
        ActualEntryType.BILL_PAID,
        "Bill Payment",
        "bill",
        input_func,
        output_func,
    )


def record_debt_payment_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record a payment for a numbered debt from the latest saved plan."""
    return _record_named_activity(
        service,
        plan,
        ActualEntryType.DEBT_PAYMENT,
        "Debt Payment",
        "debt",
        input_func,
        output_func,
    )


def record_savings_deposit_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record a savings deposit against the selected plan."""
    return _record_activity(
        service,
        plan,
        ActualEntryType.SAVINGS_DEPOSIT,
        "Savings Deposit",
        input_func,
        output_func,
        category="Savings",
    )


def record_personal_spending_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record personal spending against the selected plan."""
    return _record_activity(
        service,
        plan,
        ActualEntryType.PERSONAL_SPENDING,
        "Personal Spending",
        input_func,
        output_func,
        category="Personal Spending",
    )


def record_adjustment_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Record an explicitly signed nonzero adjustment."""
    output_func("")
    output_func(
        "Use a positive amount to add money or a negative amount to subtract money."
    )
    return _record_activity(
        service,
        plan,
        ActualEntryType.ADJUSTMENT,
        "Adjustment",
        input_func,
        output_func,
        category="Adjustment",
        allow_negative=True,
    )


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
        comparison = (
            None
            if not actual_entries and not observations
            else service.compare_forecast_to_actual(current_plan.id)
        )
        status = "No progress recorded" if comparison is None else comparison.status
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
        if comparison is not None:
            _show_debt_balance_comparisons(
                comparison.debt_balance_comparisons,
                output_func,
            )
            _show_completeness_details(comparison.completeness, output_func)
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
        elif comparison.completeness.missing_categories:
            _show_completeness_details(comparison.completeness, output_func)
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
        _show_debt_balance_comparisons(
            comparison.debt_balance_comparisons,
            output_func,
        )
    wait_for_enter(input_func)
    return False


class _ActivityCancelled(Exception):
    """Signal cancellation before an activity is persisted."""


def _recent_actual_entries(
    service: PlanHistoryService,
    plan: Any,
) -> list[Any]:
    """Return the most recent actual entries for the selected plan."""
    return _sort_recent_actual_entries(service.list_actual_entries(plan.id))


def _sort_recent_actual_entries(entries: list[Any]) -> list[Any]:
    """Sort and bound an already-loaded actual-entry collection."""
    return sorted(
        entries,
        key=lambda entry: (entry.entry_date, entry.id),
        reverse=True,
    )[:RECENT_ENTRY_LIMIT]


def _entry_status_context(entries: list[Any]) -> dict[int, set[str]]:
    """Map each superseded entry to the source labels of its audit children."""
    context: dict[int, set[str]] = {}
    for entry in entries:
        target_id = entry.corrected_entry_id
        if target_id is not None:
            context.setdefault(target_id, set()).add(entry.source)
    return context


def _select_actual_entry(
    entries: list[Any],
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    status_context: dict[int, set[str]] | None = None,
) -> Any | None:
    """Select recent actual activity by display number."""
    if not entries:
        print_warning("No recorded activity entries were found.", output_func)
        return None

    status_context = status_context or _entry_status_context(entries)
    output_func("")
    print_section_header("Select Recorded Activity", output_func)
    for index, entry in enumerate(entries, start=1):
        output_func(f"{index}. {_format_actual_entry(entry, status_context)}")
    cancel_key = str(len(entries) + 1)
    output_func(f"{cancel_key}. Cancel")
    while True:
        choice = input_func("Choose an option: ").strip()
        if choice == cancel_key:
            return None
        try:
            selected_index = int(choice)
        except ValueError:
            selected_index = 0
        if 1 <= selected_index <= len(entries):
            return entries[selected_index - 1]
        print_warning(
            f"Please choose one of: {', '.join(str(index) for index in range(1, len(entries) + 2))}.",
            output_func,
        )


def _validate_reversal_selection(
    entry: Any,
    status_context: dict[int, set[str]],
) -> None:
    """Reject reversal entries and originals already neutralized."""
    if entry.source == "correction" or (
        entry.corrected_entry_id is not None
        and entry.source != "correction_replacement"
    ):
        raise ValueError("A reversal entry cannot be reversed.")
    if entry.id in status_context:
        raise ValueError("That recorded activity has already been reversed.")


def _validate_correction_selection(
    entry: Any,
    status_context: dict[int, set[str]],
) -> None:
    """Reject reversal entries and activity already superseded."""
    if entry.source == "correction" or (
        entry.corrected_entry_id is not None
        and entry.source != "correction_replacement"
    ):
        raise ValueError("A reversal entry cannot be corrected.")
    if entry.id in status_context:
        raise ValueError("That recorded activity has already been corrected or reversed.")


def _format_actual_entry(
    entry: Any,
    status_context: dict[int, set[str]],
) -> str:
    """Format one actual entry using only user-facing values."""
    entry_type = ActualEntryType(str(entry.entry_type))
    label = ACTIVITY_TYPE_LABELS.get(
        entry_type,
        entry_type.value.replace("_", " ").title(),
    )
    details = [f"{entry.entry_date:%m/%d/%Y}", label]
    association = _entry_association(entry, entry_type)
    if association:
        details.append(association)
    details.append(format_currency(entry.amount))
    if entry.note:
        details.append(f"Note: {entry.note}")
    if entry.source == "correction":
        details.append("Reversal")
    elif entry.source == "correction_replacement":
        status = "Corrected" if entry.id in status_context else "Current"
        details.append(f"Corrected Entry ({status})")
    elif "correction_replacement" in status_context.get(entry.id, set()):
        details.append("Corrected Original")
    elif entry.id in status_context:
        details.append("Reversed")
    else:
        details.append("Current")
    return " - ".join(details)


def _entry_association(entry: Any, entry_type: ActualEntryType) -> str:
    """Return a useful debt or bill name for one actual entry."""
    if entry.debt_identifier:
        return entry.debt_identifier
    if entry_type == ActualEntryType.BILL_PAID and entry.category:
        return entry.category
    return ""


def _record_balance(
    service: PlanHistoryService,
    plan: Any,
    observation_type: ActualEntryType,
    label: str,
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    debt_name: str | None = None,
) -> bool:
    """Collect and persist a nonnegative balance through the history service."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        observation_date = _prompt_balance_date(input_func, output_func)
        balance = _prompt_balance_amount(input_func, output_func)
        note = _prompt_optional_note(input_func)
        service.add_balance_observation(
            current_plan.id,
            observation_date,
            observation_type,
            balance,
            source="interactive",
            debt_identifier=debt_name,
            note=note,
        )
    except _ActivityCancelled:
        print_warning(f"{label} cancelled.", output_func)
        return False
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    print_success(f"{label} recorded.", output_func)
    if debt_name is not None:
        output_func(f"Debt: {debt_name}")
    output_func(f"Date: {observation_date:%m/%d/%Y}")
    output_func(f"Balance: {format_currency(balance)}")
    wait_for_enter(input_func)
    return False


def _record_named_activity(
    service: PlanHistoryService,
    plan: Any,
    entry_type: ActualEntryType,
    label: str,
    entity_kind: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> bool:
    """Select a saved bill or debt by name, then record its activity."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        config = _latest_plan_config(service, current_plan)
        entities = config.bills if entity_kind == "bill" else config.debts
        selected = _select_named_entity(
            entities,
            entity_kind,
            input_func,
            output_func,
        )
        if selected is None:
            print_warning(f"{label} cancelled.", output_func)
            return False
        name = selected.name.strip()
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    return _record_activity(
        service,
        current_plan,
        entry_type,
        label,
        input_func,
        output_func,
        category=name if entity_kind == "bill" else "Debt Payment",
        description=f"{label}: {name}",
        debt_identifier=name if entity_kind == "debt" else None,
    )


def _record_activity(
    service: PlanHistoryService,
    plan: Any,
    entry_type: ActualEntryType,
    label: str,
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    category: str,
    description: str = "",
    debt_identifier: str | None = None,
    allow_negative: bool = False,
) -> bool:
    """Collect shared activity fields and persist through the existing service."""
    try:
        current_plan = _current_plan_with_version(service, plan)
        entry_date = _prompt_activity_date(input_func, output_func)
        amount = _prompt_activity_amount(
            input_func,
            output_func,
            allow_negative=allow_negative,
        )
        note = _prompt_optional_note(input_func)
        service.add_actual_entry(
            current_plan.id,
            entry_date,
            entry_type,
            amount,
            category=category,
            description=description,
            source="interactive",
            debt_identifier=debt_identifier,
            note=note,
        )
    except _ActivityCancelled:
        print_warning(f"{label} cancelled.", output_func)
        return False
    except EXPECTED_SERVICE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    print_success(
        f"Recorded {label} on {_format_date(entry_date)} for "
        f"{format_currency(amount)}.",
        output_func,
    )
    wait_for_enter(input_func)
    return False


def _current_plan_with_version(service: PlanHistoryService, plan: Any) -> Any:
    """Return the selected plan after confirming a latest version exists."""
    current_plan = service.get_plan(plan.id)
    latest_plan_version(service, current_plan)
    return current_plan


def _latest_plan_config(service: PlanHistoryService, plan: Any):
    """Load the latest immutable config without exposing internal identifiers."""
    version = latest_plan_version(service, plan)
    try:
        return config_from_plan_version(version)
    except ValueError as exc:
        raise ValueError(
            "The latest saved plan details could not be loaded."
        ) from exc


def _select_named_entity(
    entities: list[Any],
    entity_kind: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> Any | None:
    """Return a numbered bill or debt selection without displaying IDs."""
    plural = f"{entity_kind}s"
    if not entities:
        print_warning(f"No {plural} are available for this plan.", output_func)
        return None

    names = []
    for entity in entities:
        name = getattr(entity, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"The latest saved plan contains a {entity_kind} "
                "without a usable name."
            )
        names.append(name.strip())

    options = [
        MenuOption(str(index), name, lambda entity=entity: entity)
        for index, (entity, name) in enumerate(
            zip(entities, names, strict=True),
            start=1,
        )
    ]
    cancel_key = str(len(options) + 1)
    options.append(MenuOption(cancel_key, "Cancel", lambda: None))
    display_menu(f"Select {entity_kind.title()}", options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is not None:
            return option.action()
        print_warning(
            f"Please choose one of: {', '.join(option_map)}.",
            output_func,
        )


def _prompt_activity_date(
    input_func: InputFunc,
    output_func: OutputFunc,
) -> date:
    """Prompt for an activity date, defaulting a blank value to today."""
    while True:
        raw_value = input_func(
            "Activity date (MM/DD/YYYY, Enter for today, or 'cancel'): "
        ).strip()
        _raise_if_cancelled(raw_value)
        if not raw_value:
            return date.today()
        try:
            return parse_first_paycheck_date(raw_value)
        except ValueError:
            print_warning("Enter a valid date in MM/DD/YYYY format.", output_func)


def _prompt_replacement_date(
    current: date,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> date:
    """Prompt for a replacement date while retaining the current value on blank."""
    while True:
        raw_value = input_func(
            f"Activity date [{current:%m/%d/%Y}] (or 'cancel'): "
        ).strip()
        _raise_if_cancelled(raw_value)
        if not raw_value:
            return current
        try:
            return parse_first_paycheck_date(raw_value)
        except ValueError:
            print_warning("Enter a valid date in MM/DD/YYYY format.", output_func)


def _prompt_replacement_amount(
    current: Decimal,
    entry_type: ActualEntryType,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> Decimal:
    """Prompt for a replacement amount while preserving existing sign rules."""
    allow_negative = entry_type == ActualEntryType.ADJUSTMENT
    while True:
        raw_value = input_func(
            f"Amount in USD [{current:.2f}] (or 'cancel'): "
        ).strip()
        _raise_if_cancelled(raw_value)
        if not raw_value:
            return current
        try:
            amount = (
                money(raw_value.replace("$", "").replace(",", ""))
                if allow_negative
                else parse_nonnegative_money(raw_value)
            )
        except ValueError:
            amount = Decimal("0.00")
        if allow_negative and amount != Decimal("0.00"):
            return amount
        if not allow_negative and amount > Decimal("0.00"):
            return amount
        print_warning(
            "Enter a nonzero positive or negative amount."
            if allow_negative
            else "Enter an amount greater than $0.00.",
            output_func,
        )


def _prompt_replacement_association(
    service: PlanHistoryService,
    plan: Any,
    entry: Any,
    entry_type: ActualEntryType,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> tuple[str, str | None]:
    """Retain or replace the user-facing bill/debt association."""
    if entry_type not in {ActualEntryType.BILL_PAID, ActualEntryType.DEBT_PAYMENT}:
        raw_category = input_func(
            f"Category [{entry.category or 'None'}] (or 'cancel'): "
        ).strip()
        _raise_if_cancelled(raw_category)
        return raw_category or entry.category, None

    config = _latest_plan_config(service, plan)
    entity_kind = "bill" if entry_type == ActualEntryType.BILL_PAID else "debt"
    entities = config.bills if entity_kind == "bill" else config.debts
    current_name = (
        entry.category if entity_kind == "bill" else entry.debt_identifier
    )
    selected_name = _select_named_entity_with_default(
        entities,
        entity_kind,
        current_name,
        input_func,
        output_func,
    )
    if entity_kind == "bill":
        return selected_name, None
    return entry.category, selected_name


def _select_named_entity_with_default(
    entities: list[Any],
    entity_kind: str,
    current_name: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str:
    """Select a saved bill/debt by number, with blank retaining the current name."""
    names = []
    for entity in entities:
        name = getattr(entity, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"The latest saved plan contains a {entity_kind} "
                "without a usable name."
            )
        names.append(name.strip())
    if not names:
        raise ValueError(f"No {entity_kind}s are available for this plan.")

    output_func("")
    print_section_header(f"Select {entity_kind.title()}", output_func)
    for index, name in enumerate(names, start=1):
        output_func(f"{index}. {name}")
    while True:
        choice = input_func(
            f"Choose an option [Enter to keep {current_name}] (or 'cancel'): "
        ).strip()
        _raise_if_cancelled(choice)
        if not choice:
            if current_name:
                return current_name
            print_warning(f"Choose a {entity_kind}.", output_func)
            continue
        try:
            selected_index = int(choice)
        except ValueError:
            selected_index = 0
        if 1 <= selected_index <= len(names):
            return names[selected_index - 1]
        print_warning(
            f"Please choose one of: {', '.join(str(index) for index in range(1, len(names) + 1))}.",
            output_func,
        )


def _prompt_replacement_note(current: str, input_func: InputFunc) -> str:
    """Prompt for a replacement note, retaining the current value on blank."""
    raw_value = input_func(
        f"Note [{current or 'None'}] (Enter to keep, or 'cancel'): "
    ).strip()
    _raise_if_cancelled(raw_value)
    return raw_value or current


def _prompt_balance_date(
    input_func: InputFunc,
    output_func: OutputFunc,
) -> date:
    """Prompt for an observation date, defaulting a blank value to today."""
    while True:
        raw_value = input_func(
            "Observation date (MM/DD/YYYY, Enter for today, or 'cancel'): "
        ).strip()
        _raise_if_cancelled(raw_value)
        if not raw_value:
            return date.today()
        try:
            return parse_first_paycheck_date(raw_value)
        except ValueError:
            print_warning("Enter a valid date in MM/DD/YYYY format.", output_func)


def _prompt_balance_amount(
    input_func: InputFunc,
    output_func: OutputFunc,
) -> Decimal:
    """Prompt for a nonnegative current balance."""
    while True:
        raw_value = input_func("Current balance in USD (or 'cancel'): ").strip()
        _raise_if_cancelled(raw_value)
        try:
            return parse_nonnegative_money(raw_value)
        except ValueError:
            print_warning(
                "Enter a valid balance of $0.00 or greater.",
                output_func,
            )


def _prompt_activity_amount(
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    allow_negative: bool,
) -> Decimal:
    """Prompt for a positive amount or a signed nonzero adjustment."""
    while True:
        raw_value = input_func("Amount in USD (or 'cancel'): ").strip()
        _raise_if_cancelled(raw_value)
        try:
            amount = (
                money(raw_value.replace("$", "").replace(",", ""))
                if allow_negative
                else parse_nonnegative_money(raw_value)
            )
        except ValueError:
            amount = Decimal("0.00")
        if allow_negative and amount != Decimal("0.00"):
            return amount
        if not allow_negative and amount > Decimal("0.00"):
            return amount
        message = (
            "Enter a nonzero positive or negative amount."
            if allow_negative
            else "Enter an amount greater than $0.00."
        )
        print_warning(message, output_func)


def _prompt_optional_note(input_func: InputFunc) -> str:
    """Return an optional note or cancel before persistence."""
    note = input_func("Note (optional, or 'cancel'): ").strip()
    _raise_if_cancelled(note)
    return note


def _raise_if_cancelled(value: str) -> None:
    """Raise the internal cancellation signal for the shared keyword."""
    if value.strip().casefold() == "cancel":
        raise _ActivityCancelled


def _format_date(value: date) -> str:
    """Format an activity date for confirmation output."""
    return f"{value:%b} {value.day}, {value:%Y}"


def _show_completeness_details(
    completeness: ActualDataCompleteness,
    output_func: OutputFunc,
) -> None:
    """Display concise human-readable actual-data completeness details."""
    if not completeness.missing_categories:
        return
    print_warning("Progress data is incomplete.", output_func)
    output_func("")
    output_func("Recorded:")
    if completeness.recorded_categories:
        for category in completeness.recorded_categories:
            output_func(f"- {COMPLETENESS_LABELS[category]}")
    else:
        output_func("- None yet")
    output_func("")
    output_func("Still needed:")
    for category in completeness.missing_categories:
        output_func(f"- {COMPLETENESS_LABELS[category]}")


def _show_debt_balance_comparisons(
    comparisons: tuple[DebtBalanceComparison, ...],
    output_func: OutputFunc,
) -> None:
    """Display independent debt balance observations without internal IDs."""
    if not comparisons:
        return
    output_func("")
    print_section_header("Debt Balances", output_func)
    rows = []
    for comparison in comparisons:
        rows.append(
            [
                comparison.debt_name,
                _format_optional_currency(comparison.planned_balance),
                format_currency(comparison.observed_balance),
                _format_variance(comparison.variance),
                comparison.status,
            ]
        )
    print_table(
        ["Debt", "Planned", "Observed", "Variance", "Status"],
        rows,
        output_func,
    )


def _format_optional_currency(value: Decimal | None) -> str:
    """Format optional money for comparison output."""
    return "Not available" if value is None else format_currency(value)


def _format_variance(value: Decimal | None) -> str:
    """Format an optional signed currency variance."""
    if value is None:
        return "Not available"
    if value > Decimal("0.00"):
        return f"+{format_currency(value)}"
    if value < Decimal("0.00"):
        return f"-{format_currency(abs(value))}"
    return format_currency(value)
