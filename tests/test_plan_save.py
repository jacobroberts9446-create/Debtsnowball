"""Tests for saving generated in-memory plans."""

import sqlite3
import json
from contextlib import closing
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.budget_setup import BudgetSetupResult
from app.database import Database
from app.forecast_engine import ForecastEngine
from app.history import PlanHistoryService, forecast_fingerprint
from app.models import Bill, BudgetSettings, Debt
from app.plan_generation import generate_plan_from_setup, setup_to_engine_config
from app.plan_save import PlanSaveState, run_save_plan_workflow
from app.plan_setup import PayFrequency
from app.savings_setup import (
    SavingsStrategy,
    SavingsStrategySelection,
    default_savings_strategy,
)


def setup_stub(name: str = "Plan") -> BudgetSetupResult:
    """Build setup data for save tests."""
    return BudgetSetupResult(
        plan_name=name,
        pay_frequency=PayFrequency.BIWEEKLY,
        first_paycheck_date=date(2026, 7, 17),
        net_paycheck_amount=Decimal("2500.00"),
        debts=[Debt("Visa", Decimal("100.00"), Decimal("0"), Decimal("10.00"), 1, 1)],
        bills=[Bill("Rent", Decimal("100.00"), 1)],
        monthly_personal_spending=Decimal("260.00"),
        current_savings=Decimal("1000.00"),
        emergency_fund_target=Decimal("1000.00"),
        savings_strategy=default_savings_strategy(),
    )


def generated_stub(name: str = "Plan"):
    """Build a generated plan summary-like object."""
    setup = setup_stub(name)
    return SimpleNamespace(
        plan_name=name,
        setup=setup,
        forecast=SimpleNamespace(periods=[], debt_payoffs=[]),
    )


class FakePlanHistoryService:
    """Fake history service that records save calls."""

    def __init__(self, plans=None, *, fail_on=None, duplicate_version=False) -> None:
        self.plans = plans or []
        self.fail_on = fail_on
        self.duplicate_version = duplicate_version
        self.created = []
        self.saved_versions = []
        self.forecasts = []
        self.versions_by_plan = {}

    def list_plans(self):
        return self.plans

    def save_generated_plan(
        self,
        *,
        name,
        config,
        forecast,
        starting_savings,
        starting_debts=None,
        plan_id=None,
        **kwargs,
    ):
        if plan_id is None:
            plan = self.create_plan(name, config, **kwargs)
            version = self.list_plan_versions(plan.id)[-1]
            snapshot = self.generate_and_save_forecast(
                version.id,
                forecast,
                starting_savings=starting_savings,
                starting_debts=starting_debts,
            )
            return SimpleNamespace(
                plan=plan,
                version=version,
                snapshot=snapshot,
                created_new_plan=True,
                created_new_version=True,
            )

        existing_versions = list(self.list_plan_versions(plan_id))
        version = self.save_plan_version(plan_id, config, **kwargs)
        created_new_version = version not in existing_versions
        snapshot = None
        if created_new_version:
            snapshot = self.generate_and_save_forecast(
                version.id,
                forecast,
                starting_savings=starting_savings,
                starting_debts=starting_debts,
            )
        return SimpleNamespace(
            plan=self.get_plan(plan_id),
            version=version,
            snapshot=snapshot,
            created_new_plan=False,
            created_new_version=created_new_version,
        )

    def create_plan(self, name, config, **kwargs):
        if self.fail_on == "create":
            raise sqlite3.OperationalError("database unavailable")
        plan = SimpleNamespace(id=len(self.plans) + 1, name=name, config=config, **kwargs)
        version = SimpleNamespace(
            id=plan.id * 10,
            plan_id=plan.id,
            version_number=1,
            created_at="2026-07-24T12:00:00+00:00",
        )
        self.plans.append(plan)
        self.created.append(plan)
        self.versions_by_plan[plan.id] = [version]
        return plan

    def list_plan_versions(self, plan_id):
        return self.versions_by_plan.get(plan_id, [])

    def save_plan_version(self, plan_id, config, **kwargs):
        if self.fail_on == "version":
            raise ValueError("version failed")
        versions = self.versions_by_plan.setdefault(
            plan_id,
            [
                SimpleNamespace(
                    id=20,
                    plan_id=plan_id,
                    version_number=1,
                    created_at="2026-07-24T12:00:00+00:00",
                )
            ],
        )
        if self.duplicate_version:
            version = versions[-1]
        else:
            version = SimpleNamespace(
                id=versions[-1].id + 1,
                plan_id=plan_id,
                version_number=versions[-1].version_number + 1,
                created_at="2026-07-24T12:30:00+00:00",
            )
            versions.append(version)
        self.saved_versions.append((plan_id, config, kwargs, version))
        return version

    def get_plan(self, plan_id):
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        plan = SimpleNamespace(id=plan_id, name=f"Plan {plan_id}")
        self.plans.append(plan)
        return plan

    def generate_and_save_forecast(self, version_id, forecast, **kwargs):
        if self.fail_on == "forecast":
            raise RuntimeError("forecast save failed")
        snapshot = SimpleNamespace(id=len(self.forecasts) + 1, version_id=version_id)
        self.forecasts.append((version_id, forecast, kwargs))
        return snapshot


def run_save(choices, generated=None, state=None, service=None):
    """Run save workflow with fakes."""
    output = []
    prompts = []
    inputs = iter(choices)
    state = state or PlanSaveState()
    service = service or FakePlanHistoryService()
    run_save_plan_workflow(
        generated or generated_stub(),
        state,
        service_factory=lambda: service,
        input_func=lambda prompt: prompts.append(prompt) or next(inputs),
        output_func=output.append,
    )
    return state, service, prompts, output


def test_save_new_plan() -> None:
    """An unsaved generated plan can be saved as a new plan."""
    state, service, _prompts, output = run_save(["", ""])

    assert state.saved
    assert state.plan_name == "Plan"
    assert state.version_number == 1
    assert service.created[0].name == "Plan"
    assert service.forecasts[0][0] == state.version_id
    assert "✓ Plan saved successfully." in output
    assert "Version:" in output
    assert "1" in output


def test_overwrite_existing_plan_saves_new_version() -> None:
    """A matching existing name saves as a version when confirmed."""
    existing = SimpleNamespace(id=7, name="Plan")
    service = FakePlanHistoryService([existing])
    state, service, _prompts, output = run_save(["", "yes", ""], service=service)

    assert state.plan_id == 7
    assert service.created == []
    assert service.saved_versions[0][0] == 7
    assert service.forecasts
    assert "✓ Plan saved successfully." in output


def test_existing_plan_duplicate_version_does_not_save_forecast_again() -> None:
    """Existing-plan duplicate protection does not duplicate forecast snapshots."""
    existing = SimpleNamespace(id=7, name="Plan")
    service = FakePlanHistoryService([existing], duplicate_version=True)
    service.versions_by_plan[7] = [
        SimpleNamespace(
            id=70,
            plan_id=7,
            version_number=1,
            created_at="2026-07-24T12:00:00+00:00",
        )
    ]

    state, service, _prompts, output = run_save(["", "yes", ""], service=service)

    assert state.plan_id == 7
    assert state.version_id == 70
    assert service.saved_versions
    assert service.forecasts == []
    assert "✓ Plan saved successfully." in output


def test_declining_existing_plan_cancels_without_save() -> None:
    """Declining an existing-plan overwrite cancels cleanly."""
    service = FakePlanHistoryService([SimpleNamespace(id=7, name="Plan")])
    state, service, _prompts, output = run_save(["", "n"], service=service)

    assert not state.saved
    assert service.created == []
    assert service.saved_versions == []
    assert service.forecasts == []
    assert "Warning: Save cancelled." in output


def test_saved_plan_can_save_new_version() -> None:
    """A saved plan can request a new version."""
    service = FakePlanHistoryService([SimpleNamespace(id=7, name="Plan")])
    service.versions_by_plan[7] = [
        SimpleNamespace(
            id=70,
            plan_id=7,
            version_number=1,
            created_at="2026-07-24T12:00:00+00:00",
        )
    ]
    state = PlanSaveState(7, "Plan", 70, 1, "2026-07-24T12:00:00+00:00")

    state, service, _prompts, output = run_save(["1", ""], state=state, service=service)

    assert state.version_number == 2
    assert service.saved_versions[0][0] == 7
    assert len(service.forecasts) == 1
    assert "✓ Plan saved successfully." in output


def test_duplicate_save_prevention_does_not_save_forecast_again() -> None:
    """If history returns the same version, no duplicate forecast snapshot is saved."""
    service = FakePlanHistoryService(
        [SimpleNamespace(id=7, name="Plan")],
        duplicate_version=True,
    )
    service.versions_by_plan[7] = [
        SimpleNamespace(
            id=70,
            plan_id=7,
            version_number=1,
            created_at="2026-07-24T12:00:00+00:00",
        )
    ]
    state = PlanSaveState(7, "Plan", 70, 1, "2026-07-24T12:00:00+00:00")

    state, service, _prompts, output = run_save(["1", ""], state=state, service=service)

    assert state.version_id == 70
    assert service.saved_versions
    assert service.forecasts == []
    assert "✓ Plan saved successfully." in output


def test_saved_plan_can_save_as_new_plan() -> None:
    """A saved plan can also be saved as a separate new plan."""
    state = PlanSaveState(7, "Plan", 70, 1, "2026-07-24T12:00:00+00:00")
    state, service, _prompts, _output = run_save(["2", "Copy", ""], state=state)

    assert state.plan_name == "Copy"
    assert service.created[0].name == "Copy"


def test_validation_failure_reprompts_plan_name() -> None:
    """Blank names are rejected when no default exists."""
    generated = generated_stub("")
    state, service, prompts, _output = run_save([" ", "Valid", ""], generated=generated)

    assert state.plan_name == "Valid"
    assert service.created[0].name == "Valid"
    assert prompts.count("Plan name []: ") == 2


def test_database_failure_does_not_mark_saved() -> None:
    """Database failures are displayed and keep the generated plan unsaved."""
    state, service, _prompts, output = run_save(
        ["", ""],
        service=FakePlanHistoryService(fail_on="create"),
    )

    assert not state.saved
    assert service.forecasts == []
    assert "Error: database unavailable" in output


def test_retry_after_failure_can_succeed() -> None:
    """A failed save leaves state available for a later retry."""
    state = PlanSaveState()
    failing = FakePlanHistoryService(fail_on="create")
    state, _service, _prompts, output = run_save(["", ""], state=state, service=failing)
    assert not state.saved
    assert "Error: database unavailable" in output

    state, service, _prompts, output = run_save(["", ""], state=state)

    assert state.saved
    assert service.created
    assert "✓ Plan saved successfully." in output


def test_no_regeneration_or_workbook_generation(monkeypatch) -> None:
    """Saving uses the existing generated forecast without rerunning outputs."""
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("should not be called")

    monkeypatch.setattr("app.plan_generation.ForecastEngine", forbidden)
    monkeypatch.setattr("app.excel_writer.ExcelWriter", forbidden)

    state, service, _prompts, _output = run_save(["", ""])

    assert state.saved
    assert service.forecasts
    assert calls == []


def test_unexpected_runtime_error_is_not_swallowed() -> None:
    """Programmer errors are not converted into ordinary save failures."""

    class BrokenService(FakePlanHistoryService):
        def save_generated_plan(self, **_kwargs):
            raise RuntimeError("programming error")

    with pytest.raises(RuntimeError, match="programming error"):
        run_save([""], service=BrokenService())


def integration_setup(
    strategy: SavingsStrategySelection | None = None,
    *,
    name: str = "Replay Plan",
    balance: Decimal = Decimal("300.00"),
) -> BudgetSetupResult:
    """Build a small real setup for integration save tests."""
    return BudgetSetupResult(
        plan_name=name,
        pay_frequency=PayFrequency.BIWEEKLY,
        first_paycheck_date=date(2026, 7, 17),
        net_paycheck_amount=Decimal("500.00"),
        debts=[Debt("Visa", balance, Decimal("0"), Decimal("25.00"), 1, 1)],
        bills=[],
        monthly_personal_spending=Decimal("0.00"),
        current_savings=Decimal("0.00"),
        emergency_fund_target=Decimal("300.00"),
        savings_strategy=strategy or default_savings_strategy(),
    )


def service_for_tmp_db(tmp_path) -> PlanHistoryService:
    """Return a history service backed by a temp SQLite database."""
    return PlanHistoryService(Database(tmp_path / "history.sqlite"))


def save_generated(service: PlanHistoryService, setup: BudgetSetupResult, *, plan_id=None):
    """Generate once and atomically save that generated forecast."""
    generated = generate_plan_from_setup(setup)
    result = service.save_generated_plan(
        name=setup.plan_name,
        plan_id=plan_id,
        config=setup_to_engine_config(setup),
        forecast=generated.forecast,
        starting_savings=setup.current_savings,
        starting_debts=setup.debts,
        change_note="integration save",
        source="test",
    )
    return generated, result


def config_from_snapshot(snapshot: dict) -> SimpleNamespace:
    """Rebuild an engine config from a persisted normalized config snapshot."""
    budget = snapshot["budget"]
    settings = BudgetSettings(
        paycheck=Decimal(str(budget["paycheck"])),
        first_paycheck=date.fromisoformat(budget["first_paycheck"]),
        rent_per_paycheck=Decimal(str(budget["rent_per_paycheck"])),
        insurance_per_paycheck=Decimal(str(budget["insurance_per_paycheck"])),
        personal_per_paycheck=Decimal(str(budget["personal_per_paycheck"])),
        starting_savings=Decimal(str(budget["starting_savings"])),
        savings_goal=Decimal(str(budget["savings_goal"])),
        snowball_split=Decimal(str(budget["snowball_split"])),
        savings_percentage_override=None
        if budget.get("savings_percentage_override") is None
        else Decimal(str(budget["savings_percentage_override"])),
    )
    settings.pay_frequency = budget.get("pay_frequency", PayFrequency.BIWEEKLY.value)
    return SimpleNamespace(
        settings=settings,
        bills=[
            Bill(
                row["name"],
                Decimal(str(row["amount"])),
                int(row["due_day"]),
            )
            for row in snapshot["bills"]
        ],
        debts=[
            Debt(
                row["name"],
                Decimal(str(row["balance"])),
                Decimal(str(row["apr"])),
                Decimal(str(row["minimum"])),
                int(row["due_day"]),
                int(row["snowball_order"]),
            )
            for row in snapshot["debts"]
        ],
        scenarios=[],
        debt_free_target=SimpleNamespace(enabled=False),
        savings_plan=None,
    )


def table_count(service: PlanHistoryService, table: str) -> int:
    """Return a table row count for integration assertions."""
    with closing(sqlite3.connect(service.database.path)) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.mark.parametrize(
    ("strategy", "expected_override"),
    [
        (
            SavingsStrategySelection(
                SavingsStrategy.EMERGENCY_FIRST,
                Decimal("100"),
                Decimal("0"),
            ),
            "1",
        ),
        (default_savings_strategy(), None),
        (
            SavingsStrategySelection(
                SavingsStrategy.SNOWBALL,
                Decimal("0"),
                Decimal("100"),
            ),
            "0",
        ),
        (
            SavingsStrategySelection(
                SavingsStrategy.CUSTOM,
                Decimal("25"),
                Decimal("75"),
            ),
            "0.25",
        ),
    ],
)
def test_saved_strategy_replays_reviewed_forecast(
    tmp_path,
    strategy,
    expected_override,
) -> None:
    """Saved config snapshots reproduce each reviewed savings strategy."""
    service = service_for_tmp_db(tmp_path)
    setup = integration_setup(strategy)
    generated, result = save_generated(service, setup)
    saved_snapshot = json.loads(result.version.config_snapshot)

    assert saved_snapshot["budget"]["savings_percentage_override"] == expected_override

    replayed = ForecastEngine(config_from_snapshot(saved_snapshot)).forecast()

    assert forecast_fingerprint(replayed) == forecast_fingerprint(generated.forecast)
    assert replayed.debt_free_date == generated.forecast.debt_free_date
    assert replayed.total_interest_paid == generated.forecast.total_interest_paid
    assert replayed.ending_savings == generated.forecast.ending_savings
    assert [p.savings_contribution for p in replayed.periods] == [
        p.savings_contribution for p in generated.forecast.periods
    ]
    assert [p.snowball_paid for p in replayed.periods] == [
        p.snowball_paid for p in generated.forecast.periods
    ]


def test_atomic_save_new_plan_successfully_persists_forecast(tmp_path) -> None:
    """A new generated plan save persists plan, version, and forecast rows."""
    service = service_for_tmp_db(tmp_path)
    generated, result = save_generated(service, integration_setup())

    assert result.created_new_plan is True
    assert result.created_new_version is True
    assert result.snapshot is not None
    assert result.snapshot.forecast_fingerprint == forecast_fingerprint(
        generated.forecast
    )
    assert table_count(service, "plans") == 1
    assert table_count(service, "plan_versions") == 1
    assert table_count(service, "forecast_snapshots") == 1
    assert table_count(service, "forecast_periods") == len(generated.forecast.periods)


def test_atomic_save_new_version_and_save_as_new_plan(tmp_path) -> None:
    """Existing-plan version saves and separate new-plan saves use the atomic path."""
    service = service_for_tmp_db(tmp_path)
    _generated, first = save_generated(service, integration_setup(name="Base"))
    changed_setup = integration_setup(name="Base", balance=Decimal("500.00"))

    _changed, second = save_generated(service, changed_setup, plan_id=first.plan.id)
    _copy, copied = save_generated(service, integration_setup(name="Copy"))

    assert second.plan.id == first.plan.id
    assert second.version.version_number == 2
    assert copied.created_new_plan is True
    assert copied.plan.name == "Copy"
    assert table_count(service, "plans") == 2
    assert table_count(service, "plan_versions") == 3
    assert table_count(service, "forecast_snapshots") == 3


def test_duplicate_unchanged_save_creates_no_version_or_snapshot(tmp_path) -> None:
    """Duplicate unchanged generated saves do not add versions or snapshots."""
    service = service_for_tmp_db(tmp_path)
    setup = integration_setup()
    _generated, first = save_generated(service, setup)
    _duplicate, second = save_generated(service, setup, plan_id=first.plan.id)

    assert second.created_new_version is False
    assert second.version.id == first.version.id
    assert second.snapshot is None
    assert table_count(service, "plan_versions") == 1
    assert table_count(service, "forecast_snapshots") == 1


class FailingForecastService:
    """Controlled failure seam for atomic save rollback tests."""

    def save_forecast_snapshot(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("snapshot failed")


def test_forecast_snapshot_failure_rolls_back_new_plan(tmp_path, monkeypatch) -> None:
    """Forecast persistence failure leaves no partial new plan rows."""
    service = service_for_tmp_db(tmp_path)
    monkeypatch.setattr(service, "_forecast_service", lambda: FailingForecastService())

    with pytest.raises(sqlite3.OperationalError, match="snapshot failed"):
        save_generated(service, integration_setup())

    assert table_count(service, "plans") == 0
    assert table_count(service, "plan_versions") == 0
    assert table_count(service, "forecast_snapshots") == 0


def test_forecast_snapshot_failure_rolls_back_new_version_and_retry(
    tmp_path,
    monkeypatch,
) -> None:
    """Failed version saves roll back and retry succeeds once."""
    service = service_for_tmp_db(tmp_path)
    _generated, first = save_generated(service, integration_setup())
    changed_setup = integration_setup(balance=Decimal("500.00"))

    monkeypatch.setattr(service, "_forecast_service", lambda: FailingForecastService())
    with pytest.raises(sqlite3.OperationalError, match="snapshot failed"):
        save_generated(service, changed_setup, plan_id=first.plan.id)

    assert table_count(service, "plans") == 1
    assert table_count(service, "plan_versions") == 1
    assert table_count(service, "forecast_snapshots") == 1

    monkeypatch.delattr(service, "_forecast_service")
    _changed, retry = save_generated(service, changed_setup, plan_id=first.plan.id)
    _duplicate, duplicate = save_generated(service, changed_setup, plan_id=first.plan.id)

    assert retry.version.version_number == 2
    assert duplicate.created_new_version is False
    assert table_count(service, "plan_versions") == 2
    assert table_count(service, "forecast_snapshots") == 2


def test_atomic_save_does_not_rerun_forecast_or_workbook(tmp_path, monkeypatch) -> None:
    """Saving consumes the generated forecast without rerunning output boundaries."""
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("should not be called")

    generated = generate_plan_from_setup(integration_setup())
    monkeypatch.setattr("app.plan_generation.ForecastEngine", forbidden)
    monkeypatch.setattr("app.excel_writer.ExcelWriter", forbidden)

    service = service_for_tmp_db(tmp_path)
    service.save_generated_plan(
        name=generated.plan_name,
        config=setup_to_engine_config(generated.setup),
        forecast=generated.forecast,
        starting_savings=generated.setup.current_savings,
        starting_debts=generated.setup.debts,
    )

    assert calls == []
