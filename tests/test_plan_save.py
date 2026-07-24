"""Tests for saving generated in-memory plans."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.budget_setup import BudgetSetupResult
from app.models import Bill, Debt
from app.plan_save import PlanSaveState, run_save_plan_workflow
from app.plan_setup import PayFrequency
from app.savings_setup import default_savings_strategy


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

    def create_plan(self, name, config, **kwargs):
        if self.fail_on == "create":
            raise RuntimeError("database unavailable")
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
