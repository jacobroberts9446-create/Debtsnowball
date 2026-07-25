"""Tests for interactive plan progress and activity workflows."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import ActualEntryType, ForecastActualComparison
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
    """Small service fake exposing the progress workflow's service boundary."""

    def __init__(self) -> None:
        config_snapshot = json.dumps(
            {
                "budget": {
                    "paycheck": "1000.00",
                    "first_paycheck": "2026-07-17",
                    "rent_per_paycheck": "0.00",
                    "insurance_per_paycheck": "0.00",
                    "personal_per_paycheck": "100.00",
                    "starting_savings": "500.00",
                    "savings_goal": "1000.00",
                    "snowball_split": "0.50",
                },
                "bills": [
                    {
                        "name": "Electric",
                        "amount": "125.00",
                        "due_day": 20,
                    },
                    {
                        "name": "Internet",
                        "amount": "75.00",
                        "due_day": 22,
                    },
                ],
                "debts": [
                    {
                        "name": "Card A",
                        "balance": "1000.00",
                        "apr": "0.00",
                        "minimum": "50.00",
                        "due_day": 25,
                        "snowball_order": 1,
                    },
                    {
                        "name": "Card B",
                        "balance": "2000.00",
                        "apr": "10.00",
                        "minimum": "75.00",
                        "due_day": 28,
                        "snowball_order": 2,
                    },
                ],
            }
        )
        self.plan = SimpleNamespace(
            id=732,
            name="Household Plan",
            created_at="2026-07-20T12:00:00+00:00",
            updated_at="2026-07-24T20:15:00+00:00",
            current_version_id=902,
        )
        self.versions = [
            SimpleNamespace(
                id=901,
                version_number=1,
                config_snapshot=config_snapshot,
            ),
            SimpleNamespace(
                id=902,
                version_number=2,
                config_snapshot=config_snapshot,
            ),
        ]
        self.actual_entries = [SimpleNamespace(id=1201)]
        self.observations = [SimpleNamespace(id=1301)]
        self.comparison = comparison()
        self.error_method = None
        self.calls = []
        self.actual_entry_calls = []

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

    def get_plan_version(self, version_id):
        self.calls.append(("get_plan_version", version_id))
        self._raise_if_requested("get_plan_version")
        for version in self.versions:
            if version.id == version_id:
                return version
        raise ValueError("saved plan version was not found.")

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

    def add_actual_entry(
        self,
        plan_id,
        entry_date,
        entry_type,
        amount,
        **kwargs,
    ):
        self.calls.append(("add_actual_entry", plan_id))
        self._raise_if_requested("add_actual_entry")
        call = {
            "plan_id": plan_id,
            "entry_date": entry_date,
            "entry_type": entry_type,
            "amount": amount,
            **kwargs,
        }
        self.actual_entry_calls.append(call)
        entry = SimpleNamespace(id=2000 + len(self.actual_entries), **call)
        self.actual_entries.append(entry)
        return entry


def output_text(output: list[str]) -> str:
    """Join captured output for readable assertions."""
    return "\n".join(output)


def test_progress_menu_routes_summary_and_forecast_actions() -> None:
    """The menu opens both read-only views and returns through Back."""
    service = FakeProgressService()
    choices = iter(["1", "", "2", "", "4"])
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
    assert "3. Record Activity" in text
    assert "4. Back" in text
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
    choices = iter(["invalid", "4"])
    output = []

    plan_progress.run_plan_progress_menu(
        service,
        service.plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please choose one of: 1, 2, 3, 4." in output
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


def run_with_inputs(action, service, values):
    """Run one workflow action with deterministic input and captured output."""
    choices = iter(values)
    output = []
    result = action(
        service,
        service.plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )
    return result, output


def replace_snapshot_collection(
    service: FakeProgressService,
    collection: str,
    values: list[dict],
) -> None:
    """Replace one collection in every fake immutable config snapshot."""
    for version in service.versions:
        snapshot = json.loads(version.config_snapshot)
        snapshot[collection] = values
        version.config_snapshot = json.dumps(snapshot)


def test_record_activity_menu_routes_every_action(monkeypatch) -> None:
    """Every numbered activity option delegates to its public workflow action."""
    service = FakeProgressService()
    routed = []
    action_names = [
        "record_income_action",
        "record_bill_payment_action",
        "record_debt_payment_action",
        "record_savings_deposit_action",
        "record_personal_spending_action",
        "record_adjustment_action",
    ]

    for action_name in action_names:
        monkeypatch.setattr(
            plan_progress,
            action_name,
            lambda *_args, action_name=action_name, **_kwargs: routed.append(
                action_name
            )
            or False,
        )

    _, output = run_with_inputs(
        plan_progress.run_record_activity_menu,
        service,
        ["1", "2", "3", "4", "5", "6", "7"],
    )

    assert routed == action_names
    text = output_text(output)
    assert "Record Activity" in text
    assert "1. Income Received" in text
    assert "6. Adjustment" in text
    assert "7. Back" in text


def test_progress_menu_routes_to_record_activity(monkeypatch) -> None:
    """Track Progress opens Record Activity and then returns to its caller."""
    service = FakeProgressService()
    calls = []
    monkeypatch.setattr(
        plan_progress,
        "run_record_activity_menu",
        lambda *_args, **_kwargs: calls.append("record") or False,
    )

    run_with_inputs(plan_progress.run_plan_progress_menu, service, ["3", "4"])

    assert calls == ["record"]


def test_record_activity_menu_invalid_selection_then_back() -> None:
    """Invalid Record Activity choices use the shared menu validation."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.run_record_activity_menu,
        service,
        ["invalid", "7"],
    )

    assert "Warning: Please choose one of: 1, 2, 3, 4, 5, 6, 7." in output
    assert output.count("Record Activity") == 2


def test_income_reprompts_invalid_date_and_amount_and_stores_note() -> None:
    """Income validates only bad fields and persists exact Decimal input."""
    service = FakeProgressService()

    result, output = run_with_inputs(
        plan_progress.record_income_action,
        service,
        [
            "02/30/2026",
            "07/17/2026",
            "not money",
            "$1,234.56",
            "Regular paycheck",
            "",
        ],
    )

    assert result is False
    assert "Warning: Enter a valid date in MM/DD/YYYY format." in output
    assert "Warning: Enter an amount greater than $0.00." in output
    assert service.actual_entry_calls == [
        {
            "plan_id": 732,
            "entry_date": date(2026, 7, 17),
            "entry_type": ActualEntryType.INCOME_RECEIVED,
            "amount": Decimal("1234.56"),
            "category": "Income",
            "description": "",
            "source": "interactive",
            "debt_identifier": None,
            "note": "Regular paycheck",
        }
    ]
    assert (
        "Success: Recorded Income Received on Jul 17, 2026 for $1,234.56."
        in output
    )


@pytest.mark.parametrize(
    ("action", "entry_type", "category"),
    [
        (
            plan_progress.record_savings_deposit_action,
            ActualEntryType.SAVINGS_DEPOSIT,
            "Savings",
        ),
        (
            plan_progress.record_personal_spending_action,
            ActualEntryType.PERSONAL_SPENDING,
            "Personal Spending",
        ),
    ],
)
def test_simple_activity_types_validate_and_persist(
    action,
    entry_type,
    category,
) -> None:
    """Savings and personal spending use the shared positive-money workflow."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        action,
        service,
        ["07/18/2026", "-1.00", "45.67", "", ""],
    )

    call = service.actual_entry_calls[0]
    assert call["entry_type"] == entry_type
    assert call["amount"] == Decimal("45.67")
    assert call["category"] == category
    assert "Warning: Enter an amount greater than $0.00." in output


@pytest.mark.parametrize(
    "action",
    [
        plan_progress.record_income_action,
        plan_progress.record_savings_deposit_action,
        plan_progress.record_personal_spending_action,
    ],
)
def test_activity_cancellation_never_persists(action) -> None:
    """Cancellation exits an ordinary activity without creating an entry."""
    service = FakeProgressService()

    _, output = run_with_inputs(action, service, ["cancel"])

    assert service.actual_entry_calls == []
    assert any("cancelled" in line for line in output)


def test_cancellation_from_optional_note_never_persists() -> None:
    """The final note prompt remains a safe cancellation point."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.record_income_action,
        service,
        ["07/17/2026", "100.00", "cancel"],
    )

    assert service.actual_entry_calls == []
    assert "Warning: Income Received cancelled." in output


def test_blank_activity_date_defaults_to_today(monkeypatch) -> None:
    """A blank date uses the documented user-friendly current-date default."""
    service = FakeProgressService()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 24)

    monkeypatch.setattr(plan_progress, "date", FixedDate)
    run_with_inputs(
        plan_progress.record_income_action,
        service,
        ["", "10.00", "", ""],
    )

    assert service.actual_entry_calls[0]["entry_date"] == date(2026, 7, 24)


@pytest.mark.parametrize(
    ("action", "selection", "entry_type", "expected_name", "association_key"),
    [
        (
            plan_progress.record_bill_payment_action,
            "2",
            ActualEntryType.BILL_PAID,
            "Internet",
            "category",
        ),
        (
            plan_progress.record_debt_payment_action,
            "2",
            ActualEntryType.DEBT_PAYMENT,
            "Card B",
            "debt_identifier",
        ),
    ],
)
def test_numbered_bill_and_debt_selection(
    action,
    selection,
    entry_type,
    expected_name,
    association_key,
) -> None:
    """Bill and debt choices persist names without displaying internal IDs."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        action,
        service,
        ["invalid", selection, "07/20/2026", "80.25", "Paid online", ""],
    )

    call = service.actual_entry_calls[0]
    assert call["entry_type"] == entry_type
    assert call[association_key] == expected_name
    assert call["amount"] == Decimal("80.25")
    assert "Warning: Please choose one of: 1, 2, 3." in output
    text = output_text(output)
    assert "901" not in text
    assert "902" not in text
    assert "732" not in text


@pytest.mark.parametrize(
    ("action", "collection", "message"),
    [
        (
            plan_progress.record_bill_payment_action,
            "bills",
            "Warning: No bills are available for this plan.",
        ),
        (
            plan_progress.record_debt_payment_action,
            "debts",
            "Warning: No debts are available for this plan.",
        ),
    ],
)
def test_named_activity_handles_empty_collection(action, collection, message) -> None:
    """Plans without a requested entity return without persistence."""
    service = FakeProgressService()
    replace_snapshot_collection(service, collection, [])

    _, output = run_with_inputs(action, service, [])

    assert message in output
    assert service.actual_entry_calls == []


def test_malformed_bill_data_is_reported_without_persistence(monkeypatch) -> None:
    """A malformed saved bill produces a controlled user-facing error."""
    service = FakeProgressService()
    malformed_config = SimpleNamespace(
        bills=[SimpleNamespace(name=" ")],
        debts=[],
    )
    monkeypatch.setattr(
        plan_progress,
        "_latest_plan_config",
        lambda *_args: malformed_config,
    )

    _, output = run_with_inputs(
        plan_progress.record_bill_payment_action,
        service,
        [""],
    )

    assert (
        "Error: The latest saved plan contains a bill without a usable name."
        in output
    )
    assert service.actual_entry_calls == []


def test_malformed_latest_config_hides_internal_version_id() -> None:
    """An unreadable immutable config reports a useful sanitized error."""
    service = FakeProgressService()
    service.versions[-1].config_snapshot = "{not json"

    _, output = run_with_inputs(
        plan_progress.record_bill_payment_action,
        service,
        [""],
    )

    assert "Error: The latest saved plan details could not be loaded." in output
    assert "902" not in output_text(output)
    assert service.actual_entry_calls == []


@pytest.mark.parametrize(
    "action",
    [
        plan_progress.record_bill_payment_action,
        plan_progress.record_debt_payment_action,
    ],
)
def test_named_entity_selection_can_be_cancelled(action) -> None:
    """Cancelling a numbered entity choice returns without persistence."""
    service = FakeProgressService()

    _, output = run_with_inputs(action, service, ["3"])

    assert service.actual_entry_calls == []
    assert any("cancelled" in line for line in output)


@pytest.mark.parametrize(
    ("action", "leading_inputs"),
    [
        (plan_progress.record_bill_payment_action, ["1"]),
        (plan_progress.record_debt_payment_action, ["1"]),
    ],
)
def test_named_activity_handles_service_failure(action, leading_inputs) -> None:
    """Expected persistence failures return to the menu without false success."""
    service = FakeProgressService()
    service.error_method = "add_actual_entry"

    _, output = run_with_inputs(
        action,
        service,
        [*leading_inputs, "07/20/2026", "25.00", "", ""],
    )

    assert "Error: add_actual_entry failed" in output
    assert not any(line.startswith("Success:") for line in output)


@pytest.mark.parametrize("raw_amount", ["25.00", "-25.00"])
def test_adjustment_accepts_positive_and_negative_values(raw_amount) -> None:
    """Adjustments preserve the signed Decimal semantics of the service."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.record_adjustment_action,
        service,
        ["07/21/2026", "0", raw_amount, "Correction", ""],
    )

    assert (
        "Use a positive amount to add money or a negative amount to subtract money."
        in output
    )
    assert "Warning: Enter a nonzero positive or negative amount." in output
    assert service.actual_entry_calls[0]["amount"] == Decimal(raw_amount)
    assert service.actual_entry_calls[0]["entry_type"] == ActualEntryType.ADJUSTMENT


def test_selected_plan_and_latest_version_are_used() -> None:
    """Recording stays isolated to the selected plan and validates its latest version."""
    service = FakeProgressService()

    run_with_inputs(
        plan_progress.record_income_action,
        service,
        ["07/17/2026", "100.00", "", ""],
    )

    assert ("get_plan_version", 902) in service.calls
    assert service.actual_entry_calls[0]["plan_id"] == service.plan.id
    assert all(
        call[1] == service.plan.id
        for call in service.calls
        if call[0] in {"get_plan", "add_actual_entry"}
    )


def test_missing_latest_version_returns_safely() -> None:
    """A plan without a saved version cannot receive interactive activity."""
    service = FakeProgressService()
    service.plan.current_version_id = None
    service.versions = []

    _, output = run_with_inputs(
        plan_progress.run_record_activity_menu,
        service,
        [""],
    )

    assert "Error: selected plan does not have any saved versions." in output
    assert service.actual_entry_calls == []


def test_record_activity_handles_selected_plan_service_failure() -> None:
    """Failure to load the selected plan is handled before showing activity choices."""
    service = FakeProgressService()
    service.error_method = "get_plan"

    _, output = run_with_inputs(
        plan_progress.run_record_activity_menu,
        service,
        [""],
    )

    assert "Error: get_plan failed" in output
    assert service.actual_entry_calls == []


def test_recorded_activity_appears_in_subsequent_progress_summary() -> None:
    """The read-only summary refreshes from service data after persistence."""
    service = FakeProgressService()
    service.actual_entries = []
    service.observations = []

    run_with_inputs(
        plan_progress.record_income_action,
        service,
        ["07/17/2026", "1000.00", "", ""],
    )
    _, output = run_with_inputs(
        plan_progress.show_progress_summary,
        service,
        [""],
    )

    text = output_text(output)
    assert "Recorded transactions" in text
    assert "1" in text
    assert "On track" in text
