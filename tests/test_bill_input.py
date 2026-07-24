"""Tests for recurring bill entry."""

from decimal import Decimal

from app import bill_input
from app.models import Bill


def run_collect_bills(choices: list[str], initial_bills=None):
    """Run bill collection with canned input."""
    output = []
    prompts = []
    inputs = iter(choices)
    result = bill_input.collect_bills(
        input_func=lambda prompt: prompts.append(prompt) or next(inputs),
        output_func=output.append,
        initial_bills=initial_bills,
    )
    return result, prompts, output


def test_collect_one_valid_bill() -> None:
    """One valid bill is returned as a Bill model."""
    bills, _prompts, output = run_collect_bills(["y", "Rent", "$1,200.00", "1", "n", "4"])

    assert bills == [Bill("Rent", Decimal("1200.00"), 1)]
    assert "Total Monthly Bills: $1,200.00" in output


def test_collect_multiple_bills() -> None:
    """Multiple bills are collected in entry order."""
    bills, _prompts, output = run_collect_bills(
        ["y", "Rent", "1200", "1", "y", "Phone", "50", "20", "n", "4"]
    )

    assert bills is not None
    assert [bill.name for bill in bills] == ["Rent", "Phone"]
    assert "Total Monthly Bills: $1,250.00" in output


def test_currency_parsing_accepts_symbols_and_commas() -> None:
    """Bill amount parsing accepts common currency formatting."""
    assert bill_input.parse_nonnegative_bill_amount("$1,234.56") == Decimal("1234.56")


def test_invalid_amount_reprompts_only_amount() -> None:
    """Invalid bill amounts re-prompt the amount field."""
    bills, prompts, output = run_collect_bills(
        ["y", "Internet", "-1", "$80.00", "15", "n", "4"]
    )

    assert bills is not None
    assert bills[0].amount == Decimal("80.00")
    assert prompts.count("Monthly amount: ") == 2
    assert "Warning: Bill amount must be a nonnegative currency amount." in output


def test_invalid_due_day_reprompts_only_due_day() -> None:
    """Invalid due days re-prompt only the due-day field."""
    bills, prompts, output = run_collect_bills(
        ["y", "Internet", "80", "32", "15", "n", "4"]
    )

    assert bills is not None
    assert bills[0].due_day == 15
    assert prompts.count("Due day: ") == 2
    assert "Warning: Due day must be a whole number between 1 and 31." in output


def test_editing_a_bill() -> None:
    """The review screen can edit an existing bill."""
    bills, _prompts, output = run_collect_bills(
        ["y", "Old", "10", "1", "n", "2", "1", "New", "20", "2", "4"]
    )

    assert bills == [Bill("New", Decimal("20.00"), 2)]
    assert "Old" in "\n".join(output)
    assert "New" in "\n".join(output)


def test_removing_a_bill_updates_total() -> None:
    """The review screen can remove a selected bill."""
    bills, _prompts, output = run_collect_bills(
        ["y", "A", "10", "1", "y", "B", "20", "2", "n", "3", "1", "4"]
    )

    assert bills == [Bill("B", Decimal("20.00"), 2)]
    assert "Success: Removed A." in output
    assert "Total Monthly Bills: $20.00" in output


def test_preloaded_bill_editing() -> None:
    """Preloaded bills open directly to review/edit."""
    bills, _prompts, _output = run_collect_bills(
        ["2", "1", "Edited", "30", "3", "4"],
        initial_bills=[Bill("Original", Decimal("10.00"), 1)],
    )

    assert bills == [Bill("Edited", Decimal("30.00"), 3)]


def test_cancel_returns_none() -> None:
    """Cancel returns None without confirming bills."""
    bills, _prompts, output = run_collect_bills(["n", "5"])

    assert bills is None
    assert "Warning: Bill entry cancelled." in output


def test_total_monthly_bills() -> None:
    """Bill totals are exact Decimal money."""
    assert bill_input.total_monthly_bills(
        [Bill("A", Decimal("10.00"), 1), Bill("B", Decimal("20.50"), 2)]
    ) == Decimal("30.50")
