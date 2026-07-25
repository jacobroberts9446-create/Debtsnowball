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
from app.models import ActualEntryType
from app.money import format_currency, money
from app.plan_setup import parse_first_paycheck_date
from app.workflows.plan_presenter import display_plan_name, format_saved_datetime
from app.workflows.workbook_export import config_from_plan_version, latest_plan_version

__all__ = [
    "record_adjustment_action",
    "record_bill_payment_action",
    "record_debt_balance_action",
    "record_debt_payment_action",
    "record_income_action",
    "record_personal_spending_action",
    "record_savings_balance_action",
    "record_savings_deposit_action",
    "run_record_activity_menu",
    "run_record_balance_menu",
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
        MenuOption("5", "Back", lambda: True),
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


class _ActivityCancelled(Exception):
    """Signal cancellation before an activity is persisted."""


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
