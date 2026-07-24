"""Tests for the interactive generated-results viewer."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.models import Debt, DebtPayoffForecast
from app.plan_save import PlanSaveState
from app.plan_setup import PayFrequency
from app.savings_setup import default_savings_strategy
from app import results_viewer


def output_text(output: list[str]) -> str:
    """Join captured output for assertions."""
    return "\n".join(output)


def setup_stub(debts=None):
    """Build setup data consumed by the viewer."""
    return SimpleNamespace(
        plan_name="Plan",
        pay_frequency=PayFrequency.BIWEEKLY,
        first_paycheck_date=date(2026, 7, 17),
        net_paycheck_amount=Decimal("2500.00"),
        debts=debts
        if debts is not None
        else [Debt("Visa", Decimal("100.00"), Decimal("12.50"), Decimal("10.00"), 1, 1)],
        bills=[SimpleNamespace(name="Rent", amount=Decimal("1200.00"), due_day=1)],
        monthly_personal_spending=Decimal("300.00"),
        current_savings=Decimal("500.00"),
        emergency_fund_target=Decimal("1000.00"),
        savings_strategy=default_savings_strategy(),
    )


def period(index: int):
    """Build a forecast period-like object."""
    return SimpleNamespace(
        paycheck_date=date(2026, 7, 17) + timedelta(days=14 * index),
        snowball_paid=Decimal("50.00"),
        total_debt_balance=Decimal("100.00") - Decimal(index),
        savings_balance=Decimal("500.00") + Decimal(index),
    )


def generated_stub(*, periods=None, debts=None):
    """Build generated summary data consumed by the viewer."""
    setup = setup_stub(debts)
    forecast_periods = [period(0)] if periods is None else periods
    return SimpleNamespace(
        plan_name="Plan",
        debt_count=len(setup.debts),
        total_starting_debt=Decimal("100.00"),
        projected_debt_free_date=date(2026, 8, 14),
        projected_payoff_duration_days=28,
        total_projected_interest=Decimal("5.00"),
        total_projected_payments=Decimal("105.00"),
        first_period_snowball_amount=Decimal("50.00"),
        ending_savings=Decimal("1000.00"),
        savings_goal_met=True,
        setup=setup,
        forecast=SimpleNamespace(
            debt_payoffs=[
                DebtPayoffForecast(
                    "Visa",
                    Decimal("100.00"),
                    date(2026, 8, 14),
                    Decimal("5.00"),
                    Decimal("105.00"),
                )
            ],
            periods=forecast_periods,
        ),
    )


def run_viewer(choices: list[str], generated=None):
    """Run the viewer with canned input."""
    output = []
    inputs = iter(choices)
    results_viewer.view_results(
        generated or generated_stub(),
        input_func=lambda _prompt: next(inputs),
        output_func=output.append,
    )
    return output


def test_results_summary_displays_available_engine_values() -> None:
    """The summary screen prints available generated values."""
    output = run_viewer(["5"])
    text = output_text(output)

    assert "Results Summary" in text
    assert "Plan Name" in text
    assert "Plan" in text
    assert "Total Starting Debt" in text
    assert "$100.00" in text
    assert "Projected Debt-Free Date" in text
    assert "Aug 14, 2026" in text
    assert "Total Projected Payments" in text
    assert "$105.00" in text
    assert "Emergency-Fund Status" in text
    assert "Met" in text
    assert "Savings Strategy" in text
    assert "Split Between Savings and Snowball" in text


def test_results_summary_handles_missing_optional_values() -> None:
    """Missing optional generated values render as Not available."""
    generated = SimpleNamespace(forecast=SimpleNamespace(periods=[], debt_payoffs=[]))
    output = run_viewer(["5"], generated)

    assert "Not available." in output_text(output)


def test_results_summary_describes_immediate_payoff() -> None:
    """A zero-day payoff reads clearly in the summary."""
    generated = generated_stub()
    generated.projected_payoff_duration_days = 0
    output = run_viewer(["5"], generated)

    assert "Paid off on the first paycheck" in output_text(output)


def test_debt_summary_table_formatting() -> None:
    """Debt summary displays debt details and payoff dates."""
    output = run_viewer(["1", "1", "5"])
    text = output_text(output)

    assert "Debt Summary" in text
    assert "Debt | Starting Balance | APR" in text
    assert "Visa" in text
    assert "12.50%" in text
    assert "Aug 14, 2026" in text


def test_debt_summary_empty_debt_list() -> None:
    """Debt summary handles an empty debt list."""
    output = run_viewer(["1", "2"], generated_stub(debts=[]))

    assert "Warning: No debts available." in output


def test_budget_summary_displays_configuration_values() -> None:
    """Budget summary uses setup/configuration values."""
    output = run_viewer(["2", "2"])
    text = output_text(output)

    assert "Budget Summary" in text
    assert "Paycheck Amount" in text
    assert "$2,500.00" in text
    assert "Pay Frequency" in text
    assert "Biweekly" in text
    assert "Monthly Bills" in text
    assert "$1,200.00" in text
    assert "Savings Strategy" in text
    assert "Split Between Savings and Snowball" in text
    assert "Ending Savings" in text


def test_timeline_one_page() -> None:
    """A short timeline renders one page and can return to summary."""
    output = run_viewer(["3", "1", "5"])
    text = output_text(output)

    assert "Payoff Timeline Page 1 of 1" in text
    assert "Jul 17, 2026" in text
    assert "$50.00" in text


def test_timeline_multiple_pages_next_previous_and_return() -> None:
    """A long timeline can navigate forward, back, and return."""
    periods = [period(index) for index in range(25)]
    output = run_viewer(["3", "1", "1", "2", "5"], generated_stub(periods=periods))
    text = output_text(output)

    assert "Payoff Timeline Page 1 of 2" in text
    assert "Payoff Timeline Page 2 of 2" in text
    assert "Next Page" in text
    assert "Previous Page" in text


def test_timeline_hides_unavailable_page_directions() -> None:
    """Timeline navigation only shows page actions that can move."""
    periods = [period(index) for index in range(25)]
    output = run_viewer(["3", "1", "3"], generated_stub(periods=periods))
    text = output_text(output)
    first_page = text.split("Payoff Timeline Page 2 of 2")[0]
    second_page = text.split("Payoff Timeline Page 2 of 2")[1]

    assert "Next Page" in first_page
    assert "Previous Page" not in first_page
    assert "Previous Page" in second_page
    assert "Next Page" not in second_page


def test_timeline_empty_forecast() -> None:
    """An empty forecast timeline displays Not available."""
    output = run_viewer(["3", "2"], generated_stub(periods=[]))

    assert "Not available." in output


def test_return_to_main_menu_from_summary() -> None:
    """Return to main menu exits the viewer cleanly."""
    output = run_viewer(["5"])

    assert output.count("Results Summary") == 1


def test_invalid_navigation_returns_to_viewer_menu() -> None:
    """Invalid result menu choices display a friendly warning."""
    output = run_viewer(["bad", "5"])

    assert "Warning: Please choose one of: 1, 2, 3, 4, 5." in output
    assert output.count("Results Summary") == 2


def test_save_plan_option_invokes_save_workflow() -> None:
    """The Save Plan result action delegates to the save workflow."""
    generated = generated_stub()
    save_state = PlanSaveState()
    calls = []
    output = []
    inputs = iter(["4", "5"])

    def save_workflow(plan, state, **_kwargs) -> None:
        calls.append((plan, state))

    results_viewer.view_results(
        generated,
        input_func=lambda _prompt: next(inputs),
        output_func=output.append,
        save_workflow=save_workflow,
        save_state=save_state,
    )

    assert calls == [(generated, save_state)]
    assert output.count("Results Summary") == 2


def test_viewer_does_not_rerun_engine_or_touch_persistence(monkeypatch) -> None:
    """The viewer only reads the generated summary object it receives."""
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("viewer should not call external boundaries")

    monkeypatch.setattr("app.plan_generation.ForecastEngine", forbidden)
    monkeypatch.setattr("app.database.Database", forbidden)
    monkeypatch.setattr("app.excel_writer.ExcelWriter", forbidden)

    output = run_viewer(["5"])

    assert calls == []
    assert "Results Summary" in output
