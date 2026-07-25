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


def actual_entry(
    entry_id: int,
    entry_date: date,
    entry_type: ActualEntryType,
    amount: str,
    *,
    category: str = "",
    debt_identifier: str | None = None,
    note: str = "",
    corrected_entry_id: int | None = None,
):
    """Build one deterministic actual-activity test record."""
    return SimpleNamespace(
        id=entry_id,
        plan_id=732,
        entry_date=entry_date,
        entry_type=entry_type,
        amount=Decimal(amount),
        category=category,
        description="",
        source="interactive",
        debt_identifier=debt_identifier,
        corrected_entry_id=corrected_entry_id,
        note=note,
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
        self.balance_observation_calls = []
        self.reversal_calls = []

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

    def add_balance_observation(
        self,
        plan_id,
        observation_date,
        observation_type,
        balance,
        **kwargs,
    ):
        self.calls.append(("add_balance_observation", plan_id))
        self._raise_if_requested("add_balance_observation")
        call = {
            "plan_id": plan_id,
            "observation_date": observation_date,
            "observation_type": observation_type,
            "balance": balance,
            **kwargs,
        }
        self.balance_observation_calls.append(call)
        observation = SimpleNamespace(
            id=3000 + len(self.observations),
            **call,
        )
        self.observations.append(observation)
        return observation

    def reverse_actual_entry(self, entry_id, *, plan_id=None, note="Correction"):
        self.calls.append(("reverse_actual_entry", entry_id))
        self._raise_if_requested("reverse_actual_entry")
        original = next(
            (entry for entry in self.actual_entries if entry.id == entry_id),
            None,
        )
        if original is None:
            raise ValueError("recorded activity was not found.")
        if plan_id is not None and original.plan_id != plan_id:
            raise ValueError(
                "recorded activity does not belong to the selected plan."
            )
        if original.corrected_entry_id is not None:
            raise ValueError("a reversal entry cannot be reversed.")
        if any(
            entry.corrected_entry_id == original.id
            for entry in self.actual_entries
        ):
            raise ValueError("recorded activity has already been reversed.")
        reversal = actual_entry(
            max(entry.id for entry in self.actual_entries) + 1,
            original.entry_date,
            original.entry_type,
            str(-original.amount),
            category=original.category,
            debt_identifier=original.debt_identifier,
            note=note,
            corrected_entry_id=original.id,
        )
        self.actual_entries.append(reversal)
        self.reversal_calls.append(
            {
                "entry_id": entry_id,
                "plan_id": plan_id,
                "note": note,
            }
        )
        return reversal


def output_text(output: list[str]) -> str:
    """Join captured output for readable assertions."""
    return "\n".join(output)


def test_progress_menu_routes_summary_and_forecast_actions() -> None:
    """The menu opens both read-only views and returns through Back."""
    service = FakeProgressService()
    choices = iter(["1", "", "2", "", "6"])
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
    assert "4. Record Balance" in text
    assert "5. Review Recorded Activity" in text
    assert "6. Back" in text
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
    choices = iter(["invalid", "6"])
    output = []

    plan_progress.run_plan_progress_menu(
        service,
        service.plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please choose one of: 1, 2, 3, 4, 5, 6." in output
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

    run_with_inputs(plan_progress.run_plan_progress_menu, service, ["3", "6"])

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


def test_progress_menu_routes_to_record_balance(monkeypatch) -> None:
    """Track Progress opens Record Balance and returns to its caller."""
    service = FakeProgressService()
    calls = []
    monkeypatch.setattr(
        plan_progress,
        "run_record_balance_menu",
        lambda *_args, **_kwargs: calls.append("balance") or False,
    )

    run_with_inputs(plan_progress.run_plan_progress_menu, service, ["4", "6"])

    assert calls == ["balance"]


def test_record_balance_menu_routes_debt_and_savings(monkeypatch) -> None:
    """The balance menu delegates both choices to their public actions."""
    service = FakeProgressService()
    routed = []
    monkeypatch.setattr(
        plan_progress,
        "record_debt_balance_action",
        lambda *_args, **_kwargs: routed.append("debt") or False,
    )
    monkeypatch.setattr(
        plan_progress,
        "record_savings_balance_action",
        lambda *_args, **_kwargs: routed.append("savings") or False,
    )

    _, output = run_with_inputs(
        plan_progress.run_record_balance_menu,
        service,
        ["1", "2", "3"],
    )

    assert routed == ["debt", "savings"]
    text = output_text(output)
    assert "Record Balance" in text
    assert "1. Debt Balance" in text
    assert "2. Savings Balance" in text
    assert "3. Back" in text


def test_record_balance_menu_invalid_selection_then_back() -> None:
    """Invalid balance-menu choices show the shared friendly warning."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.run_record_balance_menu,
        service,
        ["invalid", "3"],
    )

    assert "Warning: Please choose one of: 1, 2, 3." in output
    assert output.count("Record Balance") == 2


def test_debt_balance_validates_fields_and_persists_debt_name() -> None:
    """Debt observations store exact Decimal balances under the chosen debt name."""
    service = FakeProgressService()

    result, output = run_with_inputs(
        plan_progress.record_debt_balance_action,
        service,
        [
            "invalid",
            "2",
            "02/30/2026",
            "07/24/2026",
            "not money",
            "-1.00",
            "$1,245.67",
            "Statement balance",
            "",
        ],
    )

    assert result is False
    assert "Warning: Please choose one of: 1, 2, 3." in output
    assert "Warning: Enter a valid date in MM/DD/YYYY format." in output
    assert output.count("Warning: Enter a valid balance of $0.00 or greater.") == 2
    assert service.balance_observation_calls == [
        {
            "plan_id": 732,
            "observation_date": date(2026, 7, 24),
            "observation_type": ActualEntryType.DEBT_BALANCE_OBSERVATION,
            "balance": Decimal("1245.67"),
            "source": "interactive",
            "debt_identifier": "Card B",
            "note": "Statement balance",
        }
    ]
    text = output_text(output)
    assert "Success: Debt Balance recorded." in text
    assert "Debt: Card B" in text
    assert "Date: 07/24/2026" in text
    assert "Balance: $1,245.67" in text
    assert "732" not in text
    assert "902" not in text


def test_savings_balance_accepts_zero_and_optional_note() -> None:
    """A plan-level savings observation accepts the service-supported zero balance."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.record_savings_balance_action,
        service,
        ["07/24/2026", "0", "", ""],
    )

    call = service.balance_observation_calls[0]
    assert call == {
        "plan_id": 732,
        "observation_date": date(2026, 7, 24),
        "observation_type": ActualEntryType.SAVINGS_BALANCE_OBSERVATION,
        "balance": Decimal("0.00"),
        "source": "interactive",
        "debt_identifier": None,
        "note": "",
    }
    assert "Success: Savings Balance recorded." in output
    assert "Balance: $0.00" in output


def test_debt_balance_accepts_exact_zero() -> None:
    """A paid-off debt can be observed at the service-supported zero balance."""
    service = FakeProgressService()

    run_with_inputs(
        plan_progress.record_debt_balance_action,
        service,
        ["1", "07/24/2026", "0.00", "", ""],
    )

    call = service.balance_observation_calls[0]
    assert call["balance"] == Decimal("0.00")
    assert call["debt_identifier"] == "Card A"


def test_savings_balance_reprompts_invalid_date_and_negative_balance() -> None:
    """Savings observations reject malformed dates and negative balances."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.record_savings_balance_action,
        service,
        ["bad date", "07/25/2026", "-0.01", "2,000.00", "Current total", ""],
    )

    assert "Warning: Enter a valid date in MM/DD/YYYY format." in output
    assert "Warning: Enter a valid balance of $0.00 or greater." in output
    assert service.balance_observation_calls[0]["balance"] == Decimal("2000.00")
    assert service.balance_observation_calls[0]["note"] == "Current total"


@pytest.mark.parametrize(
    "action",
    [
        plan_progress.record_debt_balance_action,
        plan_progress.record_savings_balance_action,
    ],
)
def test_balance_date_defaults_to_today(action, monkeypatch) -> None:
    """Blank observation dates use the same deterministic today seam."""
    service = FakeProgressService()

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 24)

    monkeypatch.setattr(plan_progress, "date", FixedDate)
    leading = ["1"] if action is plan_progress.record_debt_balance_action else []
    run_with_inputs(action, service, [*leading, "", "10.00", "", ""])

    assert service.balance_observation_calls[0]["observation_date"] == date(
        2026,
        7,
        24,
    )


def test_debt_balance_handles_no_debts() -> None:
    """A debt-free saved plan returns without creating an observation."""
    service = FakeProgressService()
    replace_snapshot_collection(service, "debts", [])

    _, output = run_with_inputs(
        plan_progress.record_debt_balance_action,
        service,
        [],
    )

    assert "Warning: No debts are available for this plan." in output
    assert "Warning: Debt Balance cancelled." in output
    assert service.balance_observation_calls == []


def test_debt_balance_handles_malformed_saved_debt(monkeypatch) -> None:
    """Malformed debt names are reported without exposing immutable-version IDs."""
    service = FakeProgressService()
    monkeypatch.setattr(
        plan_progress,
        "_latest_plan_config",
        lambda *_args: SimpleNamespace(debts=[SimpleNamespace(name=None)]),
    )

    _, output = run_with_inputs(
        plan_progress.record_debt_balance_action,
        service,
        [""],
    )

    assert (
        "Error: The latest saved plan contains a debt without a usable name."
        in output
    )
    assert service.balance_observation_calls == []


@pytest.mark.parametrize(
    ("action", "inputs", "message"),
    [
        (
            plan_progress.record_debt_balance_action,
            ["3"],
            "Warning: Debt Balance cancelled.",
        ),
        (
            plan_progress.record_savings_balance_action,
            ["cancel"],
            "Warning: Savings Balance cancelled.",
        ),
        (
            plan_progress.record_savings_balance_action,
            ["07/24/2026", "cancel"],
            "Warning: Savings Balance cancelled.",
        ),
        (
            plan_progress.record_savings_balance_action,
            ["07/24/2026", "50.00", "cancel"],
            "Warning: Savings Balance cancelled.",
        ),
    ],
)
def test_balance_cancellation_never_persists(action, inputs, message) -> None:
    """Entity, date, amount, and note cancellation points are write-free."""
    service = FakeProgressService()

    _, output = run_with_inputs(action, service, inputs)

    assert message in output
    assert service.balance_observation_calls == []


@pytest.mark.parametrize(
    "action_inputs",
    [
        (
            plan_progress.record_debt_balance_action,
            ["1", "07/24/2026", "100.00", "", ""],
        ),
        (
            plan_progress.record_savings_balance_action,
            ["07/24/2026", "100.00", "", ""],
        ),
    ],
)
def test_balance_actions_handle_service_failure(action_inputs) -> None:
    """Expected persistence failures show errors and never claim success."""
    action, inputs = action_inputs
    service = FakeProgressService()
    service.error_method = "add_balance_observation"

    _, output = run_with_inputs(action, service, inputs)

    assert "Error: add_balance_observation failed" in output
    assert not any(line.startswith("Success:") for line in output)


def test_record_balance_handles_stale_selected_plan() -> None:
    """A stale selected plan returns safely before showing balance choices."""
    service = FakeProgressService()
    output = []

    result = plan_progress.run_record_balance_menu(
        service,
        SimpleNamespace(id=9999, name="Missing"),
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert result is False
    assert "Error: selected plan was not found." in output
    assert "9999" not in output_text(output)
    assert service.balance_observation_calls == []


def test_record_balance_handles_missing_latest_version() -> None:
    """A selected plan without versions cannot receive an observation."""
    service = FakeProgressService()
    service.plan.current_version_id = None
    service.versions = []

    _, output = run_with_inputs(
        plan_progress.run_record_balance_menu,
        service,
        [""],
    )

    assert "Error: selected plan does not have any saved versions." in output
    assert service.balance_observation_calls == []


def test_balance_observation_isolated_to_selected_plan_and_latest_version() -> None:
    """Savings recording resolves the selected plan's latest version internally."""
    service = FakeProgressService()

    run_with_inputs(
        plan_progress.record_savings_balance_action,
        service,
        ["07/24/2026", "1500.00", "", ""],
    )

    assert ("get_plan_version", 902) in service.calls
    assert service.balance_observation_calls[0]["plan_id"] == service.plan.id
    assert service.balance_observation_calls[0]["debt_identifier"] is None


def test_balance_menu_returns_after_success_cancellation_and_failure() -> None:
    """Handled balance outcomes redisplay the submenu and keep navigation alive."""
    service = FakeProgressService()
    values = iter(
        [
            "2",
            "07/24/2026",
            "100.00",
            "",
            "",
            "2",
            "cancel",
            "2",
            "07/24/2026",
            "200.00",
            "",
            "",
            "3",
        ]
    )
    output = []
    original_add = service.add_balance_observation
    call_count = 0

    def fail_third_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("balance save failed")
        return original_add(*args, **kwargs)

    service.add_balance_observation = fail_third_call
    plan_progress.run_record_balance_menu(
        service,
        service.plan,
        input_func=lambda _prompt: next(values),
        output_func=output.append,
    )

    assert "Success: Savings Balance recorded." in output
    assert "Warning: Savings Balance cancelled." in output
    assert "Error: balance save failed" in output
    assert output.count("Record Balance") == 4


def test_recorded_balance_refreshes_progress_summary() -> None:
    """The summary reloads persisted observations instead of using cached state."""
    service = FakeProgressService()
    service.actual_entries = []
    service.observations = []

    run_with_inputs(
        plan_progress.record_savings_balance_action,
        service,
        ["07/24/2026", "1500.00", "", ""],
    )
    _, output = run_with_inputs(
        plan_progress.show_progress_summary,
        service,
        [""],
    )

    text = output_text(output)
    assert "Balance observations" in text
    assert "1" in text
    assert "On track" in text


def test_progress_menu_routes_to_recorded_activity_review(monkeypatch) -> None:
    """Track Progress opens the activity-review workflow."""
    service = FakeProgressService()
    calls = []
    monkeypatch.setattr(
        plan_progress,
        "run_entry_review_menu",
        lambda *_args, **_kwargs: calls.append("review") or False,
    )

    run_with_inputs(plan_progress.run_plan_progress_menu, service, ["5", "6"])

    assert calls == ["review"]


def test_entry_review_menu_routes_views_and_reversal(monkeypatch) -> None:
    """The review submenu delegates both supported actions and Back."""
    service = FakeProgressService()
    routed = []
    monkeypatch.setattr(
        plan_progress,
        "view_recent_entries_action",
        lambda *_args, **_kwargs: routed.append("view") or False,
    )
    monkeypatch.setattr(
        plan_progress,
        "reverse_entry_action",
        lambda *_args, **_kwargs: routed.append("reverse") or False,
    )

    _, output = run_with_inputs(
        plan_progress.run_entry_review_menu,
        service,
        ["1", "2", "3"],
    )

    assert routed == ["view", "reverse"]
    text = output_text(output)
    assert "Review Recorded Activity" in text
    assert "1. View Recent Entries" in text
    assert "2. Reverse an Entry" in text
    assert "3. Back" in text
    assert "Correct an Entry" not in text


def test_entry_review_menu_invalid_selection_then_back() -> None:
    """Invalid review choices receive shared menu validation."""
    service = FakeProgressService()

    _, output = run_with_inputs(
        plan_progress.run_entry_review_menu,
        service,
        ["invalid", "3"],
    )

    assert "Warning: Please choose one of: 1, 2, 3." in output
    assert output.count("Review Recorded Activity") == 2


def test_recent_entries_display_names_notes_status_and_newest_first() -> None:
    """Recent activity is readable, ordered, and marks both audit states."""
    service = FakeProgressService()
    original = actual_entry(
        7101,
        date(2026, 7, 22),
        ActualEntryType.DEBT_PAYMENT,
        "125.00",
        debt_identifier="Citi",
        note="Online payment",
    )
    bill = actual_entry(
        7102,
        date(2026, 7, 23),
        ActualEntryType.BILL_PAID,
        "45.00",
        category="Electric",
    )
    reversal = actual_entry(
        7103,
        date(2026, 7, 22),
        ActualEntryType.DEBT_PAYMENT,
        "-125.00",
        debt_identifier="Citi",
        note="Duplicate payment",
        corrected_entry_id=original.id,
    )
    service.actual_entries = [original, bill, reversal]

    _, output = run_with_inputs(
        plan_progress.view_recent_entries_action,
        service,
        [""],
    )

    text = output_text(output)
    bill_position = text.index("07/23/2026 - Bill Payment - Electric - $45.00")
    reversal_position = text.index(
        "07/22/2026 - Debt Payment - Citi - $-125.00"
    )
    original_position = text.index(
        "07/22/2026 - Debt Payment - Citi - $125.00"
    )
    assert bill_position < reversal_position < original_position
    assert "Note: Online payment" in text
    assert "Note: Duplicate payment - Reversal" in text
    assert "Online payment - Reversed" in text
    assert "7101" not in text
    assert "7102" not in text
    assert "7103" not in text


def test_recent_entries_handles_empty_activity_and_excludes_balances() -> None:
    """Balance observations never appear in actual-activity review."""
    service = FakeProgressService()
    service.actual_entries = []
    service.observations = [
        SimpleNamespace(id=8801, note="Private balance observation")
    ]

    _, output = run_with_inputs(
        plan_progress.view_recent_entries_action,
        service,
        [""],
    )

    assert "Warning: No recorded activity entries were found." in output
    assert "Private balance observation" not in output_text(output)
    assert "8801" not in output_text(output)


def test_recent_entries_handles_service_failure() -> None:
    """Expected list failures are reported without escaping the review screen."""
    service = FakeProgressService()
    service.error_method = "list_actual_entries"

    _, output = run_with_inputs(
        plan_progress.view_recent_entries_action,
        service,
        [""],
    )

    assert "Error: list_actual_entries failed" in output


def test_recent_entries_limit_is_newest_twenty() -> None:
    """Review uses a bounded, deterministic recent-entry list."""
    service = FakeProgressService()
    service.actual_entries = [
        actual_entry(
            8000 + index,
            date(2026, 7, 24),
            ActualEntryType.PERSONAL_SPENDING,
            str(index),
            note=f"Recent {index}",
        )
        for index in range(1, 26)
    ]

    _, output = run_with_inputs(
        plan_progress.view_recent_entries_action,
        service,
        [""],
    )

    text = output_text(output)
    assert "Recent 25" in text
    assert "Recent 6" in text
    assert "Recent 5" not in text
    assert sum(line[:1].isdigit() for line in output) == 20


def test_reverse_entry_requires_explicit_confirmation_and_preserves_original() -> None:
    """A confirmed reversal appends an opposite entry without editing the original."""
    service = FakeProgressService()
    original = actual_entry(
        7201,
        date(2026, 7, 24),
        ActualEntryType.INCOME_RECEIVED,
        "2100.00",
        note="Paycheck",
    )
    service.actual_entries = [original]

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["1", "REVERSE", ""],
    )

    assert service.reversal_calls == [
        {
            "entry_id": original.id,
            "plan_id": service.plan.id,
            "note": "Reversed from interactive review",
        }
    ]
    assert service.actual_entries[0] is original
    assert service.actual_entries[0].amount == Decimal("2100.00")
    reversal = service.actual_entries[1]
    assert reversal.amount == Decimal("-2100.00")
    assert reversal.corrected_entry_id == original.id
    assert "Success: Recorded activity reversed successfully." in output
    assert "7201" not in output_text(output)


@pytest.mark.parametrize("confirmation", ["", "yes", "reverse", "cancel"])
def test_reverse_entry_rejects_nonexact_confirmation(confirmation) -> None:
    """Only the exact explicit REVERSE token permits persistence."""
    service = FakeProgressService()
    service.actual_entries = [
        actual_entry(
            7301,
            date(2026, 7, 24),
            ActualEntryType.PERSONAL_SPENDING,
            "20.00",
        )
    ]

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["1", confirmation],
    )

    assert service.reversal_calls == []
    assert "Warning: Activity reversal cancelled." in output


def test_reverse_entry_selection_can_be_cancelled() -> None:
    """The numbered selection Cancel option performs no persistence."""
    service = FakeProgressService()
    service.actual_entries = [
        actual_entry(
            7401,
            date(2026, 7, 24),
            ActualEntryType.SAVINGS_DEPOSIT,
            "50.00",
        )
    ]

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["2"],
    )

    assert service.reversal_calls == []
    assert "Warning: Activity reversal cancelled." in output


def test_reverse_entry_handles_no_recorded_activity() -> None:
    """An empty activity history returns safely without offering raw identifiers."""
    service = FakeProgressService()
    service.actual_entries = []

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        [],
    )

    assert "Warning: No recorded activity entries were found." in output
    assert "Warning: Activity reversal cancelled." in output
    assert service.reversal_calls == []


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ("2", "Error: That recorded activity has already been reversed."),
        ("1", "Error: A reversal entry cannot be reversed."),
    ],
)
def test_reverse_entry_rejects_ineligible_audit_rows(selection, message) -> None:
    """Reversed originals and reversal rows are visibly ineligible."""
    service = FakeProgressService()
    original = actual_entry(
        7501,
        date(2026, 7, 24),
        ActualEntryType.BILL_PAID,
        "80.00",
        category="Internet",
    )
    reversal = actual_entry(
        7502,
        date(2026, 7, 24),
        ActualEntryType.BILL_PAID,
        "-80.00",
        category="Internet",
        corrected_entry_id=original.id,
    )
    service.actual_entries = [original, reversal]

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        [selection, ""],
    )

    assert message in output
    assert service.reversal_calls == []


def test_reverse_entry_reprompts_invalid_numbered_choice() -> None:
    """Invalid entry selections re-prompt without exposing identifiers."""
    service = FakeProgressService()
    service.actual_entries = [
        actual_entry(
            7601,
            date(2026, 7, 24),
            ActualEntryType.ADJUSTMENT,
            "10.00",
        )
    ]

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["invalid", "1", "REVERSE", ""],
    )

    assert "Warning: Please choose one of: 1, 2." in output
    assert len(service.reversal_calls) == 1
    assert "7601" not in output_text(output)


def test_reverse_entry_handles_service_failure() -> None:
    """Expected service failures return safely without false success."""
    service = FakeProgressService()
    service.actual_entries = [
        actual_entry(
            7701,
            date(2026, 7, 24),
            ActualEntryType.INCOME_RECEIVED,
            "100.00",
        )
    ]
    service.error_method = "reverse_actual_entry"

    _, output = run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["1", "REVERSE", ""],
    )

    assert "Error: reverse_actual_entry failed" in output
    assert not any(line.startswith("Success:") for line in output)


def test_entry_review_handles_stale_plan_and_missing_version() -> None:
    """Review resolves the selected plan and latest version before listing activity."""
    service = FakeProgressService()
    stale_output = []

    plan_progress.run_entry_review_menu(
        service,
        SimpleNamespace(id=9999, name="Missing"),
        input_func=lambda _prompt: "",
        output_func=stale_output.append,
    )

    assert "Error: selected plan was not found." in stale_output
    assert "9999" not in output_text(stale_output)

    service.plan.current_version_id = None
    service.versions = []
    _, missing_output = run_with_inputs(
        plan_progress.run_entry_review_menu,
        service,
        [""],
    )
    assert "Error: selected plan does not have any saved versions." in missing_output


def test_reversal_refreshes_progress_from_service_history() -> None:
    """Subsequent progress output reloads the appended immutable audit entry."""
    service = FakeProgressService()
    original = actual_entry(
        7801,
        date(2026, 7, 24),
        ActualEntryType.DEBT_PAYMENT,
        "125.00",
        debt_identifier="Citi",
    )
    service.actual_entries = [original]
    service.observations = []

    run_with_inputs(
        plan_progress.reverse_entry_action,
        service,
        ["1", "REVERSE", ""],
    )
    _, output = run_with_inputs(
        plan_progress.show_progress_summary,
        service,
        [""],
    )

    assert sum(entry.amount for entry in service.actual_entries) == Decimal("0.00")
    assert "Recorded transactions" in output_text(output)
    assert "2" in output_text(output)
