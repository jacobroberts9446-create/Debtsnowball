"""Interactive guided setup for a new debt plan."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.console import (
    OutputFunc,
    print_section_header,
    print_success,
    print_table,
    print_warning,
)
from app.debt_input import collect_debts, parse_nonnegative_money, total_debt_balance
from app.menu import InputFunc, MenuOption, display_menu
from app.models import Debt


class PayFrequency(StrEnum):
    """Stable internal pay-frequency values for setup."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMIMONTHLY = "semimonthly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class PlanSetupResult:
    """Validated inputs collected by the interactive setup workflow."""

    plan_name: str
    pay_frequency: PayFrequency
    first_paycheck_date: date
    net_paycheck_amount: Decimal
    debts: list[Debt]


class PlanSetupCancelled(Exception):
    """Raised internally when the user cancels setup from a prompt."""


def collect_plan_setup(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    debt_collector=collect_debts,
) -> PlanSetupResult | None:
    """Collect basic plan information and debts from the console."""
    try:
        while True:
            result = collect_basic_setup_fields(input_func, output_func)
            debts = collect_setup_debts(
                [],
                input_func,
                output_func,
                debt_collector,
                allow_back_to_review=False,
            )
            if debts == "back":
                continue
            if debts is None:
                return None
            result = PlanSetupResult(
                plan_name=result.plan_name,
                pay_frequency=result.pay_frequency,
                first_paycheck_date=result.first_paycheck_date,
                net_paycheck_amount=result.net_paycheck_amount,
                debts=debts,
            )
            return review_plan_setup(result, input_func, output_func, debt_collector)
    except PlanSetupCancelled:
        print_warning("Plan setup cancelled.", output_func)
        return None


def collect_basic_setup_fields(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> PlanSetupResult:
    """Collect all non-debt setup fields in the required order."""
    print_section_header("Create New Plan", output_func)
    output_func("Enter 'cancel' at any prompt to cancel plan creation.")

    plan_name = prompt_plan_name(input_func, output_func)
    pay_frequency = prompt_pay_frequency(input_func, output_func)
    first_paycheck_date = prompt_first_paycheck_date(input_func, output_func)
    net_paycheck_amount = prompt_net_paycheck_amount(input_func, output_func)

    return PlanSetupResult(
        plan_name=plan_name,
        pay_frequency=pay_frequency,
        first_paycheck_date=first_paycheck_date,
        net_paycheck_amount=net_paycheck_amount,
        debts=[],
    )


def review_plan_setup(
    result: PlanSetupResult,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    debt_collector=collect_debts,
) -> PlanSetupResult | None:
    """Review setup inputs until the user confirms, edits, or cancels."""
    current = result
    while True:
        print_plan_setup_review(current, output_func)
        options = [
            MenuOption("1", "Confirm", lambda: True),
            MenuOption("2", "Edit Plan Name", lambda: True),
            MenuOption("3", "Edit Pay Frequency", lambda: True),
            MenuOption("4", "Edit First Paycheck Date", lambda: True),
            MenuOption("5", "Edit Net Paycheck Amount", lambda: True),
            MenuOption("6", "Edit Debts", lambda: True),
            MenuOption("7", "Cancel", lambda: True),
        ]
        display_menu("Review Plan Setup", options, output_func)
        choice = input_func("Choose an option: ").strip()

        try:
            if choice == "1":
                print_success("Plan setup complete.", output_func)
                return current
            if choice == "2":
                current = replace_result(current, plan_name=prompt_plan_name(input_func, output_func))
            elif choice == "3":
                current = replace_result(
                    current,
                    pay_frequency=prompt_pay_frequency(input_func, output_func),
                )
            elif choice == "4":
                current = replace_result(
                    current,
                    first_paycheck_date=prompt_first_paycheck_date(
                        input_func,
                        output_func,
                    ),
                )
            elif choice == "5":
                current = replace_result(
                    current,
                    net_paycheck_amount=prompt_net_paycheck_amount(
                        input_func,
                        output_func,
                    ),
                )
            elif choice == "6":
                debts = collect_setup_debts(
                    current.debts,
                    input_func,
                    output_func,
                    debt_collector,
                    allow_back_to_review=True,
                )
                if debts == "back":
                    continue
                if debts is None:
                    return None
                current = replace_result(current, debts=debts)
            elif choice == "7":
                print_warning("Plan setup cancelled.", output_func)
                return None
            else:
                print_warning("Please choose one of: 1, 2, 3, 4, 5, 6, 7.", output_func)
        except PlanSetupCancelled:
            print_warning("Edit cancelled.", output_func)


def collect_setup_debts(
    current_debts: list[Debt],
    input_func: InputFunc,
    output_func: OutputFunc,
    debt_collector,
    *,
    allow_back_to_review: bool,
) -> list[Debt] | str | None:
    """Run debt entry and resolve cancellation choices."""
    initial_debts = current_debts if current_debts else None
    while True:
        debts = debt_collector(
            input_func=input_func,
            output_func=output_func,
            initial_debts=initial_debts,
        )
        if debts is not None:
            return debts

        action = prompt_debt_cancellation_action(
            input_func,
            output_func,
            allow_back_to_review=allow_back_to_review,
        )
        if action == "retry":
            initial_debts = current_debts if current_debts else None
            continue
        if action == "back":
            return "back"
        return None


def prompt_debt_cancellation_action(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    *,
    allow_back_to_review: bool,
) -> str:
    """Ask what to do after debt entry is cancelled."""
    back_label = "Back To Plan Review" if allow_back_to_review else "Back To Setup Fields"
    options = [
        MenuOption("1", "Retry Debt Entry", lambda: True),
        MenuOption("2", back_label, lambda: True),
        MenuOption("3", "Cancel Plan Creation", lambda: True),
    ]
    while True:
        display_menu("Debt Entry Cancelled", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return "retry"
        if choice == "2":
            return "back"
        if choice == "3":
            print_warning("Plan setup cancelled.", output_func)
            return "cancel"
        print_warning("Please choose one of: 1, 2, 3.", output_func)


def prompt_plan_name(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    """Prompt for a nonblank plan name."""
    while True:
        raw_value = input_func("Plan name: ")
        raise_if_cancelled(raw_value)
        plan_name = raw_value.strip()
        if plan_name:
            return plan_name
        print_warning("Plan name cannot be blank.", output_func)


def prompt_pay_frequency(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> PayFrequency:
    """Prompt for a stable pay-frequency value using a menu."""
    options = [
        MenuOption("1", "Weekly", lambda: True),
        MenuOption("2", "Biweekly", lambda: True),
        MenuOption("3", "Semimonthly", lambda: True),
        MenuOption("4", "Monthly", lambda: True),
        MenuOption("5", "Cancel", lambda: True),
    ]
    choices = {
        "1": PayFrequency.WEEKLY,
        "2": PayFrequency.BIWEEKLY,
        "3": PayFrequency.SEMIMONTHLY,
        "4": PayFrequency.MONTHLY,
    }
    while True:
        display_menu("Pay Frequency", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice in choices:
            return choices[choice]
        if choice == "5" or choice.casefold() == "cancel":
            raise PlanSetupCancelled
        print_warning("Please choose one of: 1, 2, 3, 4, 5.", output_func)


def prompt_first_paycheck_date(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> date:
    """Prompt for a valid first paycheck date in MM/DD/YYYY format."""
    while True:
        raw_value = input_func("First paycheck date (MM/DD/YYYY): ")
        raise_if_cancelled(raw_value)
        try:
            parsed = parse_first_paycheck_date(raw_value)
        except ValueError:
            print_warning("Enter a valid date in MM/DD/YYYY format.", output_func)
        else:
            print_success(f"Accepted date: {format_setup_date(parsed)}", output_func)
            return parsed


def prompt_net_paycheck_amount(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Decimal:
    """Prompt for a positive net paycheck amount."""
    while True:
        raw_value = input_func("Net paycheck amount: ")
        raise_if_cancelled(raw_value)
        try:
            amount = parse_net_paycheck_amount(raw_value)
        except ValueError:
            print_warning("Net paycheck amount must be greater than zero.", output_func)
        else:
            return amount


def parse_first_paycheck_date(value: str) -> date:
    """Parse a strict MM/DD/YYYY calendar date."""
    stripped = value.strip()
    if len(stripped) != 10 or stripped[2] != "/" or stripped[5] != "/":
        raise ValueError("date must be MM/DD/YYYY.")
    try:
        return datetime.strptime(stripped, "%m/%d/%Y").date()
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date.") from exc


def parse_net_paycheck_amount(value: str) -> Decimal:
    """Parse a positive net paycheck currency value."""
    amount = parse_nonnegative_money(value)
    if amount <= Decimal("0.00"):
        raise ValueError("paycheck amount must be positive.")
    return amount


def print_plan_setup_review(
    result: PlanSetupResult,
    output_func: OutputFunc = print,
) -> None:
    """Print the final setup review summary."""
    print_section_header("Plan Setup Review", output_func)
    print_table(
        ["Field", "Value"],
        [
            ["Plan Name", result.plan_name],
            ["Pay Frequency", pay_frequency_label(result.pay_frequency)],
            ["First Paycheck Date", format_setup_date(result.first_paycheck_date)],
            ["Net Paycheck Amount", f"${result.net_paycheck_amount:,.2f}"],
            ["Number of Debts", str(len(result.debts))],
            ["Total Debt Balance", f"${total_debt_balance(result.debts):,.2f}"],
        ],
        output_func,
    )


def replace_result(result: PlanSetupResult, **changes) -> PlanSetupResult:
    """Return a copy of a setup result with selected fields changed."""
    values = {
        "plan_name": result.plan_name,
        "pay_frequency": result.pay_frequency,
        "first_paycheck_date": result.first_paycheck_date,
        "net_paycheck_amount": result.net_paycheck_amount,
        "debts": result.debts,
    }
    values.update(changes)
    return PlanSetupResult(**values)


def pay_frequency_label(pay_frequency: PayFrequency) -> str:
    """Return the display label for a pay frequency."""
    return {
        PayFrequency.WEEKLY: "Weekly",
        PayFrequency.BIWEEKLY: "Biweekly",
        PayFrequency.SEMIMONTHLY: "Semimonthly",
        PayFrequency.MONTHLY: "Monthly",
    }[pay_frequency]


def format_setup_date(value: date) -> str:
    """Format setup dates consistently for console output."""
    return value.strftime("%b %d, %Y")


def raise_if_cancelled(value: str) -> None:
    """Raise when a prompt receives the setup cancellation keyword."""
    if value.strip().casefold() == "cancel":
        raise PlanSetupCancelled
