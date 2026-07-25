"""Tests for read-only interactive plan progress workflows."""

from decimal import Decimal
from types import SimpleNamespace

from app.models import ForecastActualComparison
from app.workflows import plan_progress


def comparison(status: str = "On track") -> ForecastActualComparison:
    """Build a deterministic existing-service comparison result."""
    return ForecastActualComparison(
        planned_income=Decimal("2000.00"),
        actual_income=Decimal("2000.00"),
        planned_bills=Decimal("800.00"),
        actual_bills=Decimal("790.00"),
        planned_debt_payments=Decimal("600.00"),
        actual_debt_payments=Decimal("610.00"),
        planned_savings=Decimal("300.00"),
        actual_savings=Decimal("300.00"),
        planned_personal_spending=Decimal("200.00"),
        actual_personal_spending=Decimal("200.00"),
        planned_remaining_cash=Decimal("100.00"),
        actual_remaining_cash=Decimal("100.00"),
        status=status,
    )


class FakeProgressService:
    """Small service fake exposing only existing read-only progress methods."""

    def __init__(self) -> None:
        self.plan = SimpleNamespace(
            id=732,
            name="Household Plan",
            created_at="2026-07-20T12:00:00+00:00",
            updated_at="2026-07-24T20:15:00+00:00",
        )
        self.versions = [
            SimpleNamespace(id=901, version_number=1),
            SimpleNamespace(id=902, version_number=2),
        ]
        self.actual_entries = [SimpleNamespace(id=1201)]
        self.observations = [SimpleNamespace(id=1301)]
        self.comparison = comparison()
        self.error_method = None
        self.calls = []

    def _raise_if_requested(self, method: str) -> None:
        if self.error_method == method:
            raise ValueError(f"{method} failed")

    def get_plan(self, plan_id):
        self.calls.append(("get_plan", plan_id))
        self._raise_if_requested("get_plan")
        if plan_id != self.plan.id:
            raise ValueError("selected plan was not found.")
        return self.plan

    def list_plan_versions(self, plan_id):
        self.calls.append(("list_plan_versions", plan_id))
        self._raise_if_requested("list_plan_versions")
        return self.versions

    def list_actual_entries(self, plan_id):
        self.calls.append(("list_actual_entries", plan_id))
        self._raise_if_requested("list_actual_entries")
        return self.actual_entries

    def list_balance_observations(self, plan_id):
        self.calls.append(("list_balance_observations", plan_id))
        self._raise_if_requested("list_balance_observations")
        return self.observations

    def compare_forecast_to_actual(self, plan_id):
        self.calls.append(("compare_forecast_to_actual", plan_id))
        self._raise_if_requested("compare_forecast_to_actual")
        return self.comparison


def output_text(output: list[str]) -> str:
    """Join captured output for readable assertions."""
    return "\n".join(output)


def test_progress_menu_routes_summary_and_forecast_actions() -> None:
    """The menu opens both read-only views and returns through Back."""
    service = FakeProgressService()
    choices = iter(["1", "", "2", "", "3"])
    output = []

    result = plan_progress.run_plan_progress_menu(
        service,
        service.plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert result is False
    assert "Track Progress - Household Plan" in text
    assert "1. Progress Summary" in text
    assert "2. Forecast vs Actual" in text
    assert "3. Back" in text
    assert "Recorded transactions" in text
    assert "Debt payments" in text


def test_progress_summary_displays_persisted_plan_information() -> None:
    """Summary shows persisted metadata and counts without internal IDs."""
    service = FakeProgressService()
    output = []

    result = plan_progress.show_progress_summary(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    text = output_text(output)
    assert result is False
    assert "Household Plan" in text
    assert "Jul 24, 2026 at 8:15 PM" in text
    assert "Saved versions" in text
    assert "2" in text
    assert "Recorded transactions" in text
    assert "Balance observations" in text
    assert "On track" in text
    assert "732" not in text
    assert "901" not in text
    assert "1201" not in text


def test_forecast_vs_actual_displays_existing_comparison_values() -> None:
    """Comparison renders service-provided totals without recalculating them."""
    service = FakeProgressService()
    output = []

    plan_progress.show_forecast_vs_actual(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    text = output_text(output)
    assert "Status: On track" in text
    assert "Category" in text
    assert "Forecast" in text
    assert "Actual" in text
    assert "$2,000.00" in text
    assert "$790.00" in text
    assert "$610.00" in text
    assert "$300.00" in text
    assert "$200.00" in text
    assert "$100.00" in text
    assert "732" not in text


def test_forecast_vs_actual_explains_when_no_progress_exists() -> None:
    """An empty actual history receives clear read-only guidance."""
    service = FakeProgressService()
    service.actual_entries = []
    service.observations = []
    service.comparison = comparison("Insufficient actual data")
    output = []

    plan_progress.show_forecast_vs_actual(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert (
        "Warning: No progress has been recorded yet. Forecast values are available, "
        "but there are no actual transactions to compare."
    ) in output
    assert "Status: Insufficient actual data" in output


def test_progress_summary_reports_empty_progress_without_comparison_call() -> None:
    """The empty summary does not request an unnecessary comparison."""
    service = FakeProgressService()
    service.actual_entries = []
    service.observations = []
    output = []

    plan_progress.show_progress_summary(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert "No progress recorded" in output_text(output)
    assert not any(call[0] == "compare_forecast_to_actual" for call in service.calls)


def test_progress_menu_invalid_selection_then_back() -> None:
    """Invalid choices receive the generic friendly warning and redisplay."""
    service = FakeProgressService()
    choices = iter(["invalid", "3"])
    output = []

    plan_progress.run_plan_progress_menu(
        service,
        service.plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please choose one of: 1, 2, 3." in output
    assert output.count("Track Progress - Household Plan") == 2


def test_progress_menu_handles_missing_selected_plan() -> None:
    """A stale selected plan returns cleanly without exposing its ID."""
    service = FakeProgressService()
    output = []

    result = plan_progress.run_plan_progress_menu(
        service,
        SimpleNamespace(id=9999, name="Missing"),
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert result is False
    assert "Error: selected plan was not found." in output
    assert "9999" not in output_text(output)


def test_progress_views_handle_service_failures() -> None:
    """Expected read failures are displayed without escaping the workflow."""
    service = FakeProgressService()
    service.error_method = "list_plan_versions"
    summary_output = []
    plan_progress.show_progress_summary(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=summary_output.append,
    )

    service.error_method = "compare_forecast_to_actual"
    comparison_output = []
    plan_progress.show_forecast_vs_actual(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=comparison_output.append,
    )

    assert "Error: list_plan_versions failed" in summary_output
    assert "Error: compare_forecast_to_actual failed" in comparison_output


def test_insufficient_recorded_progress_uses_existing_status() -> None:
    """A service-provided insufficient status receives accurate guidance."""
    service = FakeProgressService()
    service.comparison = comparison("Insufficient actual data")
    output = []

    plan_progress.show_forecast_vs_actual(
        service,
        service.plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert "Warning: There is not enough recorded progress to compare yet." in output
