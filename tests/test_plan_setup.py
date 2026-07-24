"""Tests for interactive plan setup."""

from datetime import date
from decimal import Decimal

from app.models import Debt
from app import plan_setup


def sample_debt(name: str = "Visa", balance: str = "100.00") -> Debt:
    """Build a simple debt for setup tests."""
    return Debt(
        name=name,
        balance=Decimal(balance),
        apr=Decimal("0"),
        minimum=Decimal("10.00"),
        due_day=1,
        snowball_order=1,
    )


def output_text(output: list[str]) -> str:
    """Join captured output for readable assertions."""
    return "\n".join(output)


def run_setup(choices: list[str], debt_results=None):
    """Run plan setup with canned input and debt collector results."""
    output = []
    prompts = []
    inputs = iter(choices)
    debt_results = iter(debt_results or [[sample_debt()]])
    debt_calls = []

    def fake_debt_collector(**kwargs):
        debt_calls.append(kwargs)
        return next(debt_results)

    result = plan_setup.collect_plan_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(inputs),
        output_func=output.append,
        debt_collector=fake_debt_collector,
    )

    return result, prompts, output, debt_calls


def test_valid_weekly_setup() -> None:
    """A weekly setup returns stable values and debts."""
    result, _prompts, output, _debt_calls = run_setup(
        [" Household Plan ", "1", "07/17/2026", "$2,000.00", "1"]
    )

    assert result is not None
    assert result.plan_name == "Household Plan"
    assert result.pay_frequency == plan_setup.PayFrequency.WEEKLY
    assert result.first_paycheck_date == date(2026, 7, 17)
    assert result.net_paycheck_amount == Decimal("2000.00")
    assert result.debts[0].name == "Visa"
    assert "Success: Accepted date: Jul 17, 2026" in output


def test_valid_biweekly_setup() -> None:
    """Biweekly is returned as the stable internal value."""
    result, _prompts, _output, _debt_calls = run_setup(
        ["Plan", "2", "07/17/2026", "2000", "1"]
    )

    assert result is not None
    assert result.pay_frequency == plan_setup.PayFrequency.BIWEEKLY


def test_valid_semimonthly_setup() -> None:
    """Semimonthly is returned as the stable internal value."""
    result, _prompts, _output, _debt_calls = run_setup(
        ["Plan", "3", "07/17/2026", "2000", "1"]
    )

    assert result is not None
    assert result.pay_frequency == plan_setup.PayFrequency.SEMIMONTHLY


def test_valid_monthly_setup() -> None:
    """Monthly is returned as the stable internal value."""
    result, _prompts, _output, _debt_calls = run_setup(
        ["Plan", "4", "07/17/2026", "2000", "1"]
    )

    assert result is not None
    assert result.pay_frequency == plan_setup.PayFrequency.MONTHLY


def test_blank_plan_name_reprompts_only_name() -> None:
    """Blank plan names are rejected and re-prompted."""
    result, prompts, output, _debt_calls = run_setup(
        ["   ", "Plan", "1", "07/17/2026", "2000", "1"]
    )

    assert result is not None
    assert result.plan_name == "Plan"
    assert prompts.count("Plan name: ") == 2
    assert "Warning: Plan name cannot be blank." in output


def test_invalid_date_reprompts_only_date() -> None:
    """Invalid calendar dates re-prompt only the date field."""
    result, prompts, output, _debt_calls = run_setup(
        ["Plan", "1", "02/30/2026", "07/17/2026", "2000", "1"]
    )

    assert result is not None
    assert result.first_paycheck_date == date(2026, 7, 17)
    assert prompts.count("First paycheck date (MM/DD/YYYY): ") == 2
    assert "Warning: Enter a valid date in MM/DD/YYYY format." in output


def test_invalid_paycheck_amount_reprompts_only_amount() -> None:
    """Invalid or zero paycheck values are rejected and re-prompted."""
    result, prompts, output, _debt_calls = run_setup(
        ["Plan", "1", "07/17/2026", "0", "$1,500.00", "1"]
    )

    assert result is not None
    assert result.net_paycheck_amount == Decimal("1500.00")
    assert prompts.count("Net paycheck amount: ") == 2
    assert "Warning: Net paycheck amount must be greater than zero." in output


def test_currency_parsing_accepts_commas_and_dollar_signs() -> None:
    """Net paycheck parsing accepts common currency formatting."""
    assert plan_setup.parse_net_paycheck_amount("$1,234.56") == Decimal("1234.56")


def test_debt_entry_cancellation_can_retry() -> None:
    """Cancelled debt entry can retry without crashing setup."""
    result, _prompts, output, debt_calls = run_setup(
        ["Plan", "1", "07/17/2026", "2000", "1", "1"],
        debt_results=[None, [sample_debt("Retry Debt")]],
    )

    assert result is not None
    assert result.debts[0].name == "Retry Debt"
    assert len(debt_calls) == 2
    assert "Debt Entry Cancelled" in output


def test_debt_entry_cancellation_can_go_back_to_setup_fields() -> None:
    """Cancelled initial debt entry can return to earlier setup fields."""
    result, _prompts, _output, debt_calls = run_setup(
        [
            "Old Plan",
            "1",
            "07/17/2026",
            "1000",
            "2",
            "New Plan",
            "2",
            "07/31/2026",
            "2000",
            "1",
        ],
        debt_results=[None, [sample_debt("New Debt")]],
    )

    assert result is not None
    assert result.plan_name == "New Plan"
    assert result.pay_frequency == plan_setup.PayFrequency.BIWEEKLY
    assert len(debt_calls) == 2


def test_editing_each_setup_field() -> None:
    """The review screen can edit every non-debt setup field."""
    result, _prompts, output, _debt_calls = run_setup(
        [
            "Old",
            "1",
            "07/17/2026",
            "1000",
            "2",
            "New",
            "3",
            "3",
            "4",
            "08/01/2026",
            "5",
            "2500",
            "1",
        ]
    )

    assert result is not None
    assert result.plan_name == "New"
    assert result.pay_frequency == plan_setup.PayFrequency.SEMIMONTHLY
    assert result.first_paycheck_date == date(2026, 8, 1)
    assert result.net_paycheck_amount == Decimal("2500.00")
    text = output_text(output)
    assert "Plan Name           | New" in text
    assert "Pay Frequency       | Semimonthly" in text


def test_editing_preloaded_debts() -> None:
    """Editing debts reopens debt entry with current debts preloaded."""
    edited_debt = sample_debt("Edited", "300.00")
    result, _prompts, output, debt_calls = run_setup(
        ["Plan", "1", "07/17/2026", "1000", "6", "1"],
        debt_results=[[sample_debt("Original", "100.00")], [edited_debt]],
    )

    assert result is not None
    assert result.debts == [edited_debt]
    assert debt_calls[1]["initial_debts"][0].name == "Original"
    assert "Total Debt Balance  | $300.00" in output_text(output)


def test_review_totals_show_number_of_debts_and_balance() -> None:
    """The final review includes debt count and total balance."""
    result, _prompts, output, _debt_calls = run_setup(
        ["Plan", "1", "07/17/2026", "1000", "1"],
        debt_results=[[sample_debt("A", "100.00"), sample_debt("B", "250.50")]],
    )

    assert result is not None
    text = output_text(output)
    assert "Number of Debts     | 2" in text
    assert "Total Debt Balance  | $350.50" in text


def test_full_cancellation_from_prompt_returns_none() -> None:
    """Typing cancel in setup stops without collected data."""
    result, _prompts, output, debt_calls = run_setup(["cancel"])

    assert result is None
    assert debt_calls == []
    assert "Warning: Plan setup cancelled." in output


def test_full_cancellation_after_debt_entry_cancel_returns_none() -> None:
    """Debt-entry cancellation can cancel the whole setup."""
    result, _prompts, output, _debt_calls = run_setup(
        ["Plan", "1", "07/17/2026", "1000", "3"],
        debt_results=[None],
    )

    assert result is None
    assert "Warning: Plan setup cancelled." in output


def test_collect_plan_setup_with_real_preloaded_debt_editor() -> None:
    """Debt input can edit preloaded debts through the setup review."""
    output = []
    prompts = []
    choices = iter(
        [
            "Plan",
            "1",
            "07/17/2026",
            "1000",
            "Visa",
            "100",
            "10",
            "20",
            "5",
            "n",
            "4",
            "6",
            "2",
            "1",
            "Edited",
            "125",
            "11",
            "25",
            "6",
            "4",
            "1",
        ]
    )

    result = plan_setup.collect_plan_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert result is not None
    assert result.debts[0].name == "Edited"
    assert result.debts[0].balance == Decimal("125.00")

