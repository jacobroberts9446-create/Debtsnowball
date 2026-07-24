"""Interactive recurring bill-entry workflow."""

from copy import deepcopy
from decimal import Decimal

from app.console import OutputFunc, print_success, print_table, print_warning
from app.debt_input import parse_debt_name, parse_due_day, prompt_validated
from app.menu import InputFunc, MenuOption, display_menu
from app.models import Bill
from app.money import format_currency, money


def collect_bills(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    initial_bills: list[Bill] | None = None,
) -> list[Bill] | None:
    """Collect recurring monthly bills and return confirmed Bill objects."""
    bills: list[Bill] = [] if initial_bills is None else deepcopy(initial_bills)

    if initial_bills is None:
        while prompt_yes_no("Add a recurring monthly bill? [y/N]: ", input_func):
            bills.append(prompt_bill(input_func, output_func))

    return review_bills(bills, input_func, output_func)


def prompt_bill(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Bill:
    """Prompt for one valid recurring monthly bill."""
    output_func("")
    output_func("Recurring Bill")
    output_func("--------------")
    name = prompt_validated(
        "Bill name: ",
        parse_debt_name,
        "Bill name cannot be blank.",
        input_func,
        output_func,
    )
    amount = prompt_validated(
        "Monthly amount: ",
        parse_nonnegative_bill_amount,
        "Bill amount must be a nonnegative currency amount.",
        input_func,
        output_func,
    )
    due_day = prompt_validated(
        "Due day: ",
        parse_due_day,
        "Due day must be a whole number between 1 and 31.",
        input_func,
        output_func,
    )
    return Bill(name=name, amount=amount, due_day=due_day)


def review_bills(
    bills: list[Bill],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> list[Bill] | None:
    """Show the bill review screen until confirmed or cancelled."""
    while True:
        print_bill_review(bills, output_func)
        options = [
            MenuOption(
                "1",
                "Add Another Bill",
                lambda: add_bill_action(bills, input_func, output_func),
            ),
            MenuOption("2", "Edit Bill", lambda: edit_bill_action(bills, input_func, output_func)),
            MenuOption(
                "3",
                "Remove Bill",
                lambda: remove_bill_action(bills, input_func, output_func),
            ),
            MenuOption("4", "Confirm", lambda: True),
            MenuOption("5", "Cancel", lambda: True),
        ]
        display_menu("Review Bills", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice == "4":
            print_success("Bill entry complete.", output_func)
            return list(bills)
        if choice == "5":
            print_warning("Bill entry cancelled.", output_func)
            return None
        option = {option.key: option for option in options}.get(choice)
        if option is None:
            print_warning("Please choose one of: 1, 2, 3, 4, 5.", output_func)
            continue
        option.action()


def add_bill_action(
    bills: list[Bill],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Add a bill from the review screen."""
    bills.append(prompt_bill(input_func, output_func))
    return False


def edit_bill_action(
    bills: list[Bill],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Replace a selected bill."""
    index = prompt_bill_index("Bill number to edit: ", bills, input_func, output_func)
    if index is not None:
        bills[index] = prompt_bill(input_func, output_func)
    return False


def remove_bill_action(
    bills: list[Bill],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Remove a selected bill."""
    index = prompt_bill_index("Bill number to remove: ", bills, input_func, output_func)
    if index is not None:
        removed = bills.pop(index)
        print_success(f"Removed {removed.name}.", output_func)
    return False


def prompt_bill_index(
    prompt: str,
    bills: list[Bill],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int | None:
    """Prompt for a one-based bill number."""
    if not bills:
        print_warning("There are no bills to select.", output_func)
        return None
    try:
        selected = int(input_func(prompt).strip())
    except ValueError:
        print_warning("Please enter a valid bill number.", output_func)
        return None
    if not 1 <= selected <= len(bills):
        print_warning("Please enter a valid bill number.", output_func)
        return None
    return selected - 1


def print_bill_review(bills: list[Bill], output_func: OutputFunc = print) -> None:
    """Print entered bills and total monthly bills."""
    output_func("")
    if bills:
        print_table(
            ["#", "Name", "Amount", "Due Day"],
            [
                [str(index), bill.name, format_currency(bill.amount), str(bill.due_day)]
                for index, bill in enumerate(bills, start=1)
            ],
            output_func,
        )
    else:
        print_warning("No recurring bills entered.", output_func)
    output_func(f"Total Monthly Bills: {format_currency(total_monthly_bills(bills))}")


def parse_nonnegative_bill_amount(value: str) -> Decimal:
    """Parse a nonnegative bill amount."""
    normalized = value.strip().replace("$", "").replace(",", "")
    amount = money(normalized)
    if amount < Decimal("0.00"):
        raise ValueError("bill amount cannot be negative.")
    return amount


def total_monthly_bills(bills: list[Bill]) -> Decimal:
    """Return total recurring monthly bills."""
    return money(sum((bill.amount for bill in bills), Decimal("0.00")))


def prompt_yes_no(prompt: str, input_func: InputFunc = input) -> bool:
    """Return True only for y/yes."""
    return input_func(prompt).strip().casefold() in {"y", "yes"}
