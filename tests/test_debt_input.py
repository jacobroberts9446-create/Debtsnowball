"""Tests for interactive debt entry."""

from decimal import Decimal

from app import debt_input


def output_text(output: list[str]) -> str:
    """Join captured output for substring assertions."""
    return "\n".join(output)


def run_collect_debts(choices: list[str]):
    """Run debt collection with canned input."""
    output = []
    prompts = []
    inputs = iter(choices)

    result = debt_input.collect_debts(
        input_func=lambda prompt: prompts.append(prompt) or next(inputs),
        output_func=output.append,
    )

    return result, prompts, output


def test_collect_one_valid_debt() -> None:
    """One valid debt is returned as a Debt model."""
    debts, _prompts, output = run_collect_debts(
        ["Visa", "1200.50", "27.24", "75.00", "15", "n", "4"]
    )

    assert debts is not None
    assert len(debts) == 1
    assert debts[0].name == "Visa"
    assert debts[0].balance == Decimal("1200.50")
    assert debts[0].apr == Decimal("27.24")
    assert debts[0].minimum == Decimal("75.00")
    assert debts[0].due_day == 15
    assert debts[0].snowball_order == 1
    assert "Total Debt Balance: $1,200.50" in output


def test_collect_multiple_debts() -> None:
    """Multiple debts are returned in entry order."""
    debts, _prompts, output = run_collect_debts(
        [
            "Visa",
            "100.00",
            "10",
            "20",
            "5",
            "yes",
            "Car",
            "500.00",
            "5.5",
            "50",
            "20",
            "n",
            "4",
        ]
    )

    assert debts is not None
    assert [debt.name for debt in debts] == ["Visa", "Car"]
    assert [debt.snowball_order for debt in debts] == [1, 2]
    assert "Total Debt Balance: $600.00" in output


def test_currency_parsing_accepts_symbols_and_commas() -> None:
    """Currency values can include dollar signs and commas."""
    assert debt_input.parse_nonnegative_money("$1,234.56") == Decimal("1234.56")


def test_percentage_parsing_accepts_percent_sign() -> None:
    """APR values can include a percent sign."""
    assert debt_input.parse_nonnegative_percentage("27.24%") == Decimal("27.24")


def test_invalid_values_reprompt_only_invalid_fields() -> None:
    """Invalid field values show warnings and re-prompt that field."""
    debts, prompts, output = run_collect_debts(
        [
            "",
            "Discover",
            "-1",
            "$1,500.00",
            "-2%",
            "24.99%",
            "-10",
            "35.00",
            "32",
            "8",
            "n",
            "4",
        ]
    )

    assert debts is not None
    assert debts[0].name == "Discover"
    assert debts[0].balance == Decimal("1500.00")
    assert debts[0].apr == Decimal("24.99")
    assert debts[0].minimum == Decimal("35.00")
    assert debts[0].due_day == 8
    assert prompts.count("Debt name: ") == 2
    assert prompts.count("Current balance: ") == 2
    assert prompts.count("APR: ") == 2
    assert prompts.count("Minimum monthly payment: ") == 2
    assert prompts.count("Payment due day: ") == 2
    assert "Warning: Debt name cannot be blank." in output
    assert "Warning: Balance must be a nonnegative currency amount." in output
    assert "Warning: APR must be a nonnegative percentage." in output
    assert "Warning: Minimum payment must be a nonnegative currency amount." in output
    assert "Warning: Due day must be a whole number between 1 and 31." in output


def test_editing_a_debt_replaces_the_selected_debt() -> None:
    """The review screen can edit an existing debt."""
    debts, _prompts, output = run_collect_debts(
        [
            "Old Card",
            "100.00",
            "10",
            "20",
            "5",
            "n",
            "2",
            "1",
            "New Card",
            "250.00",
            "15.5",
            "30.00",
            "10",
            "4",
        ]
    )

    assert debts is not None
    assert len(debts) == 1
    assert debts[0].name == "New Card"
    assert debts[0].balance == Decimal("250.00")
    assert "Old Card" in output_text(output)
    assert "New Card" in output_text(output)


def test_removing_a_debt_updates_review_totals_and_order() -> None:
    """The review screen can remove a selected debt."""
    debts, _prompts, output = run_collect_debts(
        [
            "A",
            "100.00",
            "0",
            "10",
            "1",
            "y",
            "B",
            "200.00",
            "0",
            "20",
            "2",
            "n",
            "3",
            "1",
            "4",
        ]
    )

    assert debts is not None
    assert [debt.name for debt in debts] == ["B"]
    assert debts[0].snowball_order == 1
    assert "Success: Removed A." in output
    assert "Total Debt Balance: $200.00" in output


def test_cancel_returns_none_without_confirming() -> None:
    """Cancel returns None so callers do not save or continue setup."""
    debts, _prompts, output = run_collect_debts(
        ["Visa", "100.00", "10", "20", "5", "n", "5"]
    )

    assert debts is None
    assert "Warning: Debt entry cancelled." in output


def test_review_invalid_selection_returns_to_review() -> None:
    """Invalid review choices show a friendly warning."""
    debts, _prompts, output = run_collect_debts(
        ["Visa", "100.00", "10", "20", "5", "n", "bad", "4"]
    )

    assert debts is not None
    assert "Warning: Please choose one of: 1, 2, 3, 4, 5." in output
