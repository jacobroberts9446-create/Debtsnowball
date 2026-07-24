"""Interactive debt-entry workflow for console plan setup."""

from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from app.console import OutputFunc, print_success, print_table, print_warning
from app.menu import InputFunc, MenuOption, display_menu
from app.models import Debt
from app.money import format_currency, money, to_decimal


def collect_debts(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    initial_debts: list[Debt] | None = None,
) -> list[Debt] | None:
    """Collect debts interactively and return confirmed Debt objects."""
    debts: list[Debt] = [] if initial_debts is None else deepcopy(initial_debts)
    normalize_snowball_order(debts)

    if initial_debts is None:
        while True:
            debts.append(prompt_debt(len(debts) + 1, input_func, output_func))
            if not prompt_yes_no("Add another debt? [y/N]: ", input_func):
                break

    return review_debts(debts, input_func, output_func)


def prompt_debt(
    snowball_order: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Debt:
    """Prompt for one valid debt."""
    output_func("")
    output_func(f"Debt {snowball_order}")
    output_func("-" * len(f"Debt {snowball_order}"))

    name = prompt_validated(
        "Debt name: ",
        parse_debt_name,
        "Debt name cannot be blank.",
        input_func,
        output_func,
    )
    balance = prompt_validated(
        "Current balance: ",
        parse_nonnegative_money,
        "Balance must be a nonnegative currency amount.",
        input_func,
        output_func,
    )
    apr = prompt_validated(
        "APR: ",
        parse_nonnegative_percentage,
        "APR must be a nonnegative percentage.",
        input_func,
        output_func,
    )
    minimum = prompt_validated(
        "Minimum monthly payment: ",
        parse_nonnegative_money,
        "Minimum payment must be a nonnegative currency amount.",
        input_func,
        output_func,
    )
    due_day = prompt_validated(
        "Payment due day: ",
        parse_due_day,
        "Due day must be a whole number between 1 and 31.",
        input_func,
        output_func,
    )

    return Debt(
        name=name,
        balance=balance,
        apr=apr,
        minimum=minimum,
        due_day=due_day,
        snowball_order=snowball_order,
    )


def review_debts(
    debts: list[Debt],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> list[Debt] | None:
    """Show the review screen until the user confirms or cancels."""
    while True:
        print_debt_review(debts, output_func)
        options = [
            MenuOption(
                "1",
                "Add Another Debt",
                lambda: add_debt_action(debts, input_func, output_func),
            ),
            MenuOption(
                "2",
                "Edit Debt",
                lambda: edit_debt_action(debts, input_func, output_func),
            ),
            MenuOption(
                "3",
                "Remove Debt",
                lambda: remove_debt_action(debts, input_func, output_func),
            ),
            MenuOption("4", "Confirm", lambda: True),
            MenuOption("5", "Cancel", lambda: True),
        ]
        display_menu("Review Debts", options, output_func)

        choice = input_func("Choose an option: ").strip()
        if choice == "4":
            print_success("Debt entry complete.", output_func)
            return list(debts)
        if choice == "5":
            print_warning("Debt entry cancelled.", output_func)
            return None

        option = {option.key: option for option in options}.get(choice)
        if option is None:
            print_warning("Please choose one of: 1, 2, 3, 4, 5.", output_func)
            continue

        option.action()
        normalize_snowball_order(debts)


def add_debt_action(
    debts: list[Debt],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Add one debt from the review screen."""
    debts.append(prompt_debt(len(debts) + 1, input_func, output_func))
    return False


def edit_debt_action(
    debts: list[Debt],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Replace an existing debt with newly entered values."""
    index = prompt_debt_index("Debt number to edit: ", debts, input_func, output_func)
    if index is None:
        return False

    debts[index] = prompt_debt(index + 1, input_func, output_func)
    return False


def remove_debt_action(
    debts: list[Debt],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Remove one debt from the review screen."""
    index = prompt_debt_index("Debt number to remove: ", debts, input_func, output_func)
    if index is None:
        return False

    removed = debts.pop(index)
    print_success(f"Removed {removed.name}.", output_func)
    return False


def prompt_debt_index(
    prompt: str,
    debts: list[Debt],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int | None:
    """Prompt for a one-based debt number."""
    if not debts:
        print_warning("There are no debts to select.", output_func)
        return None

    raw_value = input_func(prompt).strip()
    try:
        selected = int(raw_value)
    except ValueError:
        print_warning("Please enter a valid debt number.", output_func)
        return None

    if not 1 <= selected <= len(debts):
        print_warning("Please enter a valid debt number.", output_func)
        return None

    return selected - 1


def print_debt_review(
    debts: list[Debt],
    output_func: OutputFunc = print,
) -> None:
    """Print entered debts and the total balance."""
    output_func("")
    if not debts:
        print_warning("No debts entered.", output_func)
        return

    print_table(
        ["#", "Name", "Balance", "APR", "Minimum", "Due Day"],
        [
            [
                str(index),
                debt.name,
                format_currency(debt.balance),
                f"{debt.apr:.2f}%",
                format_currency(debt.minimum),
                str(debt.due_day),
            ]
            for index, debt in enumerate(debts, start=1)
        ],
        output_func,
    )
    output_func(f"Total Debt Balance: {format_currency(total_debt_balance(debts))}")


def prompt_validated[T](
    prompt: str,
    parser: Callable[[str], T],
    error_message: str,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> T:
    """Prompt until a parser returns a valid value."""
    while True:
        raw_value = input_func(prompt)
        try:
            return parser(raw_value)
        except ValueError:
            print_warning(error_message, output_func)


def prompt_yes_no(
    prompt: str,
    input_func: InputFunc = input,
) -> bool:
    """Return True only for y/yes."""
    return input_func(prompt).strip().casefold() in {"y", "yes"}


def parse_debt_name(value: str) -> str:
    """Parse a nonblank debt name."""
    name = value.strip()
    if not name:
        raise ValueError("debt name is required.")
    return name


def parse_nonnegative_money(value: str) -> Decimal:
    """Parse a nonnegative currency value with optional symbols or commas."""
    normalized = value.strip().replace("$", "").replace(",", "")
    parsed = money(normalized)
    if parsed < Decimal("0.00"):
        raise ValueError("money value cannot be negative.")
    return parsed


def parse_nonnegative_percentage(value: str) -> Decimal:
    """Parse a nonnegative percentage with an optional percent sign."""
    normalized = value.strip().removesuffix("%").strip()
    try:
        parsed = to_decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("percentage value is invalid.") from exc
    if parsed < Decimal("0"):
        raise ValueError("percentage value cannot be negative.")
    return parsed


def parse_due_day(value: str) -> int:
    """Parse a payment due day between 1 and 31."""
    try:
        due_day = int(value.strip())
    except ValueError as exc:
        raise ValueError("due day is invalid.") from exc
    if not 1 <= due_day <= 31:
        raise ValueError("due day must be between 1 and 31.")
    return due_day


def total_debt_balance(debts: list[Debt]) -> Decimal:
    """Return the total current balance for entered debts."""
    total = sum((debt.balance for debt in debts), Decimal("0.00"))
    return money(total)


def normalize_snowball_order(debts: list[Debt]) -> None:
    """Keep snowball order sequential after review edits."""
    for index, debt in enumerate(debts, start=1):
        debt.snowball_order = index
