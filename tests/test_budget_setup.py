"""Tests for full interactive budget setup."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app import budget_setup
from app.models import Bill, Debt
from app.plan_setup import PayFrequency


def sample_debt(name: str = "Visa", balance: str = "100.00") -> Debt:
    """Build a simple test debt."""
    return Debt(name, Decimal(balance), Decimal("0"), Decimal("10.00"), 1, 1)


def sample_bill(name: str = "Rent", amount: str = "1200.00") -> Bill:
    """Build a simple test bill."""
    return Bill(name, Decimal(amount), 1)


def summary_stub(setup=None):
    """Build a generated-plan summary stub."""
    return SimpleNamespace(
        plan_name=getattr(setup, "plan_name", "Plan"),
        pay_frequency=getattr(setup, "pay_frequency", None),
        debts=getattr(setup, "debts", []),
        bills=getattr(setup, "bills", []),
        monthly_personal_spending=getattr(setup, "monthly_personal_spending", None),
        current_savings=getattr(setup, "current_savings", None),
        emergency_fund_target=getattr(setup, "emergency_fund_target", None),
        debt_count=1,
        total_starting_debt=Decimal("100.00"),
        projected_debt_free_date=date(2026, 8, 14),
        projected_payoff_duration_days=28,
        total_projected_interest=Decimal("0.00"),
        total_projected_payments=Decimal("100.00"),
        first_period_snowball_amount=Decimal("50.00"),
        ending_savings=Decimal("500.00"),
        savings_goal_met=True,
        setup=setup,
    )


def run_budget_setup(choices: list[str], *, debts=None, bills=None, generator=None):
    """Run full setup with canned inputs and fake collectors."""
    output = []
    prompts = []
    inputs = iter(choices)
    calls = {"debt": [], "bill": [], "generated": []}

    def debt_collector(current_debts, *_args, **kwargs):
        calls["debt"].append((current_debts, kwargs))
        return debts if debts is not None else [sample_debt()]

    def bill_collector(**kwargs):
        calls["bill"].append(kwargs)
        return bills if bills is not None else [sample_bill()]

    def fake_generator(setup):
        calls["generated"].append(setup)
        if generator is not None:
            return generator(setup)
        return summary_stub(setup)

    result = budget_setup.collect_budget_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(inputs),
        output_func=output.append,
        debt_collector=debt_collector,
        bill_collector=bill_collector,
        generator=fake_generator,
    )
    return result, prompts, output, calls


def test_valid_full_setup_generates_in_memory_summary() -> None:
    """A valid full setup returns setup data and calls the generator once."""
    result, _prompts, output, calls = run_budget_setup(
        ["Plan", "1", "07/17/2026", "2000", "1", "1", "1", "300", "1", "500", "400", "1"]
    )

    assert result is not None
    assert result.plan_name == "Plan"
    assert result.pay_frequency == PayFrequency.WEEKLY
    assert result.debts == [sample_debt()]
    assert result.bills == [sample_bill()]
    assert result.monthly_personal_spending == Decimal("300.00")
    assert result.current_savings == Decimal("500.00")
    assert result.emergency_fund_target == Decimal("400.00")
    assert calls["generated"] == [result.setup]
    assert "Full Plan Review" in output


def test_zero_bills_zero_personal_and_zero_savings_are_valid() -> None:
    """Zero values are accepted where required by setup."""
    result, _prompts, _output, _calls = run_budget_setup(
        ["Plan", "2", "07/17/2026", "2000", "1", "1", "1", "0", "1", "0", "0", "1"],
        bills=[],
    )

    assert result is not None
    assert result.bills == []
    assert result.monthly_personal_spending == Decimal("0.00")
    assert result.current_savings == Decimal("0.00")
    assert result.emergency_fund_target == Decimal("0.00")


def test_back_navigation_between_sections_restarts_previous_flow() -> None:
    """Back navigation returns to earlier setup instead of saving state."""
    result, _prompts, _output, calls = run_budget_setup(
        [
            "Old",
            "1",
            "07/17/2026",
            "1000",
            "1",
            "2",
            "New",
            "2",
            "07/31/2026",
            "2000",
            "1",
            "1",
            "1",
            "0",
            "1",
            "0",
            "0",
            "1",
        ]
    )

    assert result is not None
    assert result.plan_name == "New"
    assert result.pay_frequency == PayFrequency.BIWEEKLY
    assert len(calls["debt"]) == 2


def test_debt_entry_back_navigation_restarts_setup_fields() -> None:
    """Choosing back after cancelled debt entry returns to setup fields."""
    debt_results = ["back", [sample_debt("New")]]

    def debt_collector(*_args, **_kwargs):
        return debt_results.pop(0)

    output = []
    prompts = []
    choices = iter(
        [
            "Old",
            "1",
            "07/17/2026",
            "1000",
            "1",
            "New",
            "2",
            "07/31/2026",
            "2000",
            "1",
            "1",
            "1",
            "0",
            "1",
            "0",
            "0",
            "1",
        ],
    )

    result = budget_setup.collect_budget_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
        debt_collector=debt_collector,
        bill_collector=lambda **_kwargs: [sample_bill()],
        generator=summary_stub,
    )

    assert result is not None
    assert result.plan_name == "New"
    assert result.pay_frequency == PayFrequency.BIWEEKLY


def test_bill_entry_back_navigation_restarts_setup_fields() -> None:
    """Choosing back after cancelled bill entry returns to setup fields."""
    bill_results = ["back", [sample_bill("New Bill")]]

    def bill_collector(**_kwargs):
        return bill_results.pop(0)

    output = []
    prompts = []
    choices = iter(
        [
            "Old",
            "1",
            "07/17/2026",
            "1000",
            "1",
            "1",
            "New",
            "2",
            "07/31/2026",
            "2000",
            "1",
            "1",
            "1",
            "0",
            "1",
            "0",
            "0",
            "1",
        ]
    )

    result = budget_setup.collect_budget_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
        debt_collector=lambda *_args, **_kwargs: [sample_debt()],
        bill_collector=bill_collector,
        generator=summary_stub,
    )

    assert result is not None
    assert result.plan_name == "New"
    assert result.bills[0].name == "New Bill"


def test_cancellation_from_major_section_returns_none() -> None:
    """Cancelling from section navigation exits cleanly."""
    result, _prompts, output, calls = run_budget_setup(
        ["Plan", "1", "07/17/2026", "2000", "2"]
    )

    assert result is None
    assert calls["debt"] == []
    assert "Warning: Plan setup cancelled." in output


def test_editing_each_review_section() -> None:
    """The full review can edit basics, debts, bills, personal spending, and savings."""
    debt_results = [[sample_debt("Original")], [sample_debt("Edited", "250.00")]]
    bill_results = [[sample_bill("Original Bill")], [sample_bill("Edited Bill", "50.00")]]

    def debt_collector(current_debts, *_args, **kwargs):
        debt_collector.calls.append((current_debts, kwargs))
        return debt_results.pop(0)

    debt_collector.calls = []

    def bill_collector(**kwargs):
        bill_collector.calls.append(kwargs)
        return bill_results.pop(0)

    bill_collector.calls = []
    output = []
    prompts = []
    choices = iter(
        [
            "Old",
            "1",
            "07/17/2026",
            "1000",
            "1",
            "1",
            "1",
            "100",
            "1",
            "200",
            "300",
            "2",
            "New",
            "4",
            "08/01/2026",
            "2000",
            "3",
            "4",
            "5",
            "150",
            "6",
            "250",
            "350",
            "1",
        ]
    )

    result = budget_setup.collect_budget_setup(
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
        debt_collector=debt_collector,
        bill_collector=bill_collector,
        generator=summary_stub,
    )

    assert result is not None
    assert result.plan_name == "New"
    assert result.pay_frequency == PayFrequency.MONTHLY
    assert result.debts[0].name == "Edited"
    assert result.bills[0].name == "Edited Bill"
    assert result.monthly_personal_spending == Decimal("150.00")
    assert result.current_savings == Decimal("250.00")
    assert result.emergency_fund_target == Decimal("350.00")
    assert debt_collector.calls[1][0][0].name == "Original"
    assert bill_collector.calls[1]["initial_bills"][0].name == "Original Bill"


def test_final_review_totals() -> None:
    """The final review shows totals for debts, bills, and minimums."""
    result, _prompts, output, _calls = run_budget_setup(
        ["Plan", "1", "07/17/2026", "2000", "1", "1", "1", "0", "1", "0", "0", "1"],
        debts=[sample_debt("A", "100.00"), sample_debt("B", "200.00")],
        bills=[sample_bill("Rent", "1200.00"), sample_bill("Phone", "50.00")],
    )

    assert result is not None
    text = "\n".join(output)
    assert "Total Debt Balance" in text
    assert "$300.00" in text
    assert "Total Monthly Bills" in text
    assert "$1,250.00" in text
    assert "Total Minimum Payments" in text


def test_generation_validation_failure_returns_to_review_and_can_retry() -> None:
    """Generation failures preserve setup values and allow edit/retry."""
    attempts = []

    def generator(setup):
        attempts.append(setup)
        if len(attempts) == 1:
            raise ValueError("validation failed")
        return summary_stub(setup)

    result, _prompts, output, _calls = run_budget_setup(
        [
            "Plan",
            "1",
            "07/17/2026",
            "2000",
            "1",
            "1",
            "1",
            "0",
            "1",
            "0",
            "0",
            "1",
            "5",
            "100",
            "1",
        ],
        generator=generator,
    )

    assert result is not None
    assert result.monthly_personal_spending == Decimal("100.00")
    assert len(attempts) == 2
    assert "Error: validation failed" in output


def test_full_cancellation_from_review() -> None:
    """Cancelling from full review exits without generation."""
    result, _prompts, output, calls = run_budget_setup(
        ["Plan", "1", "07/17/2026", "2000", "1", "1", "1", "0", "1", "0", "0", "7"]
    )

    assert result is None
    assert calls["generated"] == []
    assert "Warning: Plan setup cancelled." in output
