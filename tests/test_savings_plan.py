from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.forecast_engine import ForecastEngine
from app.models import (
    BudgetSettings,
    Debt,
    DebtFreeTargetRequest,
    PayPeriod,
    PlannedSavingsWithdrawal,
    SavingsFundingMode,
    SavingsGoalStage,
    SavingsGoalStatus,
    SavingsPlan,
    ScenarioDefinition,
)
from app.scenario_engine import ScenarioEngine
from app.target_calculator import DebtFreeTargetCalculator


def make_config(
    paycheck=1000,
    first_paycheck=date(2026, 1, 1),
    starting_savings=0,
    savings_goal=1000,
    savings_plan=None,
    debts=None,
    personal_per_paycheck=0,
):
    return SimpleNamespace(
        settings=BudgetSettings(
            paycheck=paycheck,
            first_paycheck=first_paycheck,
            rent_per_paycheck=0,
            insurance_per_paycheck=0,
            personal_per_paycheck=personal_per_paycheck,
            starting_savings=starting_savings,
            savings_goal=savings_goal,
            snowball_split=0.50,
        ),
        bills=[],
        debts=debts if debts is not None else [],
        scenarios=[],
        savings_plan=savings_plan,
    )


def staged_plan(
    first_target=Decimal("500.00"),
    second_target=Decimal("500.00"),
    withdrawal_date=date(2026, 1, 11),
    withdrawal=None,
    first_mode=SavingsFundingMode.PERCENTAGE,
    second_mode=SavingsFundingMode.PERCENTAGE,
):
    return SavingsPlan(
        goals=[
            SavingsGoalStage(
                name="First goal",
                target_amount=first_target,
                start_date=date(2026, 1, 1),
                target_date=date(2026, 1, 11),
                funding_mode=first_mode,
            ),
            SavingsGoalStage(
                name="Second goal",
                target_amount=second_target,
                start_date=date(2026, 1, 12),
                funding_mode=second_mode,
            ),
        ],
        withdrawals=[
            withdrawal
            or PlannedSavingsWithdrawal(
                name="Planned drain",
                withdrawal_date=withdrawal_date,
                drain_balance=True,
            )
        ],
    )


def test_backward_compatibility_without_savings_plan():
    config = make_config(savings_plan=None)
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.savings_contribution == 500.0
    assert summary.savings_balance == 500.0
    assert summary.active_savings_goal_name is None


def test_dated_goal_with_drain_and_second_goal_transition():
    config = make_config(savings_plan=staged_plan())
    periods = CalendarEngine(config.settings).generate(date(2026, 1, 29))

    engine = BudgetEngine(config)
    summaries = engine.build_plan(periods)

    assert summaries[0].active_savings_goal_name == "First goal"
    assert summaries[0].savings_balance == 500.0
    assert summaries[1].active_savings_goal_name == "Second goal"
    assert summaries[1].savings_balance_before_withdrawal == 500.0
    assert summaries[1].planned_withdrawal_amount == 500.0
    assert summaries[1].savings_balance_after_withdrawal == 0.0
    assert summaries[1].savings_contribution == 500.0
    assert summaries[1].savings_balance == 500.0
    assert summaries[1].active_savings_target == 500.0

    stage_results = engine.savings_stage_results()
    first_stage = stage_results[0]
    second_stage = stage_results[1]
    withdrawal = engine.planned_withdrawal_results()[0]

    assert first_stage.amount_at_deadline == Decimal("500.00")
    assert first_stage.status == SavingsGoalStatus.ACHIEVED_EARLY
    assert withdrawal.scheduled_date == date(2026, 1, 11)
    assert withdrawal.applied_date == date(2026, 1, 15)
    assert withdrawal.actual_amount_withdrawn == Decimal("500.00")
    assert withdrawal.balance_after == Decimal("0.00")
    assert second_stage.starting_balance == Decimal("0.00")
    assert second_stage.target_amount == Decimal("500.00")


def test_withdrawal_does_not_affect_debt_minimums_or_balances():
    config = make_config(
        paycheck=1000,
        savings_plan=staged_plan(),
        debts=[
            Debt("Card", balance=2000, apr=0, minimum=100, due_day=20, snowball_order=1)
        ],
    )
    periods = CalendarEngine(config.settings).generate(date(2026, 1, 29))

    summaries = BudgetEngine(config).build_plan(periods)

    assert summaries[1].debt_minimums == 100.0
    assert summaries[1].active_debt_balances[0].balance == 950.0
    assert summaries[1].snowball_payment == 450.0


def test_fixed_withdrawal_is_capped_and_reports_shortfall():
    plan = staged_plan(
        withdrawal=PlannedSavingsWithdrawal(
            name="Fixed withdrawal",
            withdrawal_date=date(2026, 1, 11),
            amount=Decimal("800.00"),
        )
    )
    config = make_config(savings_plan=plan)

    result = BudgetEngine(config)
    result.build_plan(CalendarEngine(config.settings).generate(date(2026, 1, 29)))
    withdrawal = result.planned_withdrawal_results()[0]

    assert withdrawal.actual_amount_withdrawn == Decimal("500.00")
    assert withdrawal.shortfall == Decimal("300.00")
    assert withdrawal.balance_after == Decimal("0.00")
    assert withdrawal.status == "partial"


def test_missed_deadline_reports_shortfall():
    config = make_config(
        paycheck=400,
        savings_plan=staged_plan(first_target=Decimal("500.00")),
    )

    forecast = ForecastEngine(config).forecast()

    first_stage = forecast.savings_stage_results[0]
    assert first_stage.amount_at_deadline == Decimal("200.00")
    assert first_stage.shortfall_at_deadline == Decimal("300.00")
    assert first_stage.status == SavingsGoalStatus.NOT_ACHIEVED


def test_deadline_priority_reduces_snowball_without_reducing_minimums():
    plan = staged_plan(
        first_target=Decimal("600.00"),
        first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
    )
    config = make_config(
        paycheck=1000,
        savings_plan=plan,
        debts=[
            Debt("Card", balance=2000, apr=0, minimum=100, due_day=10, snowball_order=1)
        ],
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.available_after_required_payments == 900.0
    assert summary.debt_minimums == 100.0
    assert summary.savings_contribution == 600.0
    assert summary.snowball_payment == 300.0
    assert summary.snowball_reduction == 150.0


def test_deadline_priority_can_reduce_snowball_to_zero():
    plan = staged_plan(
        first_target=Decimal("3000.00"),
        first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
    )
    config = make_config(paycheck=1000, savings_plan=plan)
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.available_after_required_payments == 1000.0
    assert summary.deadline_required_savings_contribution == 3000.0
    assert summary.savings_contribution == 1000.0
    assert summary.snowball_payment == 0.0
    assert summary.snowball_reduction == 500.0
    assert summary.projected_savings_shortfall == 2000.0


def test_deadline_priority_personal_reduction_is_capped_at_allowance():
    plan = staged_plan(
        first_target=Decimal("3000.00"),
        first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
    )
    config = make_config(
        paycheck=1000,
        savings_plan=plan,
        personal_per_paycheck=100,
    )
    period = PayPeriod(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 14))

    summary = BudgetEngine(config).process_pay_period(period)

    assert summary.personal_expense_reduction == 100.0
    assert summary.bills_paid == 0.0
    assert summary.savings_contribution == 1000.0


def test_priority_until_funded_rebuilds_savings_before_snowball_resumes():
    plan = staged_plan(
        first_target=Decimal("500.00"),
        second_target=Decimal("1500.00"),
        first_mode=SavingsFundingMode.PERCENTAGE,
        second_mode=SavingsFundingMode.PRIORITY_UNTIL_FUNDED,
    )
    config = make_config(
        paycheck=1000,
        savings_plan=plan,
        debts=[Debt("Card", balance=5000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    periods = CalendarEngine(config.settings).generate(date(2026, 2, 12))

    summaries = BudgetEngine(config).build_plan(periods)

    assert summaries[1].active_savings_goal_name == "Second goal"
    assert summaries[1].savings_balance == 1000.0
    assert summaries[1].snowball_payment == 0.0
    assert summaries[2].savings_contribution == 500.0
    assert summaries[2].snowball_payment == 500.0
    assert summaries[3].savings_contribution == 0.0
    assert summaries[3].snowball_payment == 1000.0


def test_current_plan_priority_reaches_maximum_possible_deadline_balance():
    config = Config().load("config.json")
    periods = CalendarEngine(config.settings).generate(date(2026, 8, 14))

    engine = BudgetEngine(config)
    summaries = engine.build_plan(periods)
    first_stage = engine.savings_stage_results()[0]
    withdrawal = engine.planned_withdrawal_results()[0]

    assert summaries[0].available_after_required_payments == 430.0
    assert summaries[0].bills_paid == 349.5
    assert summaries[0].normal_savings_contribution == 215.0
    assert summaries[0].snowball_reduction == 215.0
    assert summaries[0].personal_expense_reduction == 416.5
    assert summaries[0].savings_contribution == 846.5
    assert summaries[0].snowball_payment == 0.0
    assert summaries[1].available_after_required_payments == 1137.0
    assert summaries[1].bills_paid == 507.5
    assert summaries[1].normal_savings_contribution == 568.5
    assert summaries[1].snowball_reduction == 568.5
    assert summaries[1].personal_expense_reduction == 416.5
    assert summaries[1].savings_contribution == 1553.5
    assert summaries[1].snowball_payment == 0.0
    assert first_stage.amount_at_deadline == Decimal("3900.00")
    assert first_stage.shortfall_at_deadline == Decimal("0.00")
    assert first_stage.feasible_under_current_plan is True
    assert first_stage.additional_funding_needed == Decimal("0.00")
    assert withdrawal.actual_amount_withdrawn == Decimal("3900.00")
    assert summaries[2].active_savings_goal_name == "Replacement savings"
    assert summaries[2].savings_balance_after_withdrawal == 0.0
    assert summaries[2].savings_contribution == 430.0


def test_august_14_paycheck_is_not_eligible_for_august_11_deadline():
    config = Config().load("config.json")
    periods = CalendarEngine(config.settings).generate(date(2026, 8, 14))

    engine = BudgetEngine(config)
    engine.build_plan(periods)
    first_stage = engine.savings_stage_results()[0]

    assert first_stage.eligible_paychecks_remaining == 0
    assert first_stage.amount_at_deadline == Decimal("3900.00")


def test_state_is_not_mutated_and_repeated_forecasts_are_deterministic():
    config = make_config(savings_plan=staged_plan())
    original = deepcopy(config)

    first = ForecastEngine(config).forecast()
    second = ForecastEngine(config).forecast()

    assert config.savings_plan == original.savings_plan
    assert first.planned_withdrawal_results[0].actual_amount_withdrawn == (
        second.planned_withdrawal_results[0].actual_amount_withdrawn
    )
    assert first.savings_stage_results[1].starting_balance == Decimal("0.00")


def test_scenarios_preserve_withdrawals():
    config = make_config(
        savings_plan=staged_plan(),
        debts=[Debt("Card", balance=500, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    assert comparison.scenarios[0].forecast.planned_withdrawal_results[0].name == (
        "Planned drain"
    )
    assert comparison.scenarios[0].forecast.savings_stage_results[1].starting_balance == (
        Decimal("0.00")
    )


def test_scenarios_preserve_savings_priority_before_extra_snowball():
    config = make_config(
        paycheck=1000,
        savings_plan=staged_plan(
            first_target=Decimal("3000.00"),
            first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
        ),
        debts=[Debt("Card", balance=2000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    scenario = ScenarioDefinition(name="Extra", extra_per_paycheck=Decimal("100.00"))

    comparison = ScenarioEngine(config, [scenario]).compare()

    baseline_period = comparison.baseline.periods[0]
    scenario_period = comparison.scenarios[0].periods[0]
    assert baseline_period.savings_contribution == Decimal("1000.00")
    assert baseline_period.snowball_paid == Decimal("0.00")
    assert scenario_period.savings_contribution == Decimal("1000.00")
    assert scenario_period.snowball_paid == Decimal("100.00")


def test_target_calculator_preserves_withdrawals():
    config = make_config(
        paycheck=1000,
        savings_plan=staged_plan(),
        debts=[Debt("Card", balance=500, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    target_request = DebtFreeTargetRequest(
        enabled=True,
        target_date=date(2026, 1, 15),
        maximum_extra_per_paycheck=Decimal("100.00"),
    )

    result = DebtFreeTargetCalculator(config, target_request).calculate()

    assert result.iterations[0].projected_debt_free_date == date(2026, 1, 1)
    assert ForecastEngine(config).forecast().planned_withdrawal_results[0].name == (
        "Planned drain"
    )


def test_target_calculator_preserves_savings_priority():
    config = make_config(
        paycheck=1000,
        savings_plan=staged_plan(
            first_target=Decimal("3000.00"),
            first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
        ),
        debts=[Debt("Card", balance=2000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    target_request = DebtFreeTargetRequest(
        enabled=True,
        target_date=date(2026, 1, 15),
        maximum_extra_per_paycheck=Decimal("2000.00"),
    )

    result = DebtFreeTargetCalculator(config, target_request).calculate()

    assert result.iterations[0].extra_per_paycheck == Decimal("0.00")
    assert ForecastEngine(config).forecast().periods[0].snowball_paid == Decimal("0.00")


def test_priority_savings_moves_debt_free_date_later_than_percentage_mode():
    priority_config = make_config(
        paycheck=1000,
        savings_plan=staged_plan(
            first_target=Decimal("3000.00"),
            first_mode=SavingsFundingMode.DEADLINE_PRIORITY,
        ),
        debts=[Debt("Card", balance=2000, apr=0, minimum=0, due_day=10, snowball_order=1)],
    )
    percentage_config = deepcopy(priority_config)
    percentage_config.savings_plan.goals[0].funding_mode = SavingsFundingMode.PERCENTAGE

    priority_forecast = ForecastEngine(priority_config).forecast()
    percentage_forecast = ForecastEngine(percentage_config).forecast()

    assert priority_forecast.debt_free_date > percentage_forecast.debt_free_date


def test_current_config_savings_plan_parses():
    config = Config().load("config.json")

    assert config.savings_plan.goals[0].target_amount == Decimal("3900.00")
    assert config.savings_plan.goals[0].target_date == date(2026, 8, 11)
    assert config.savings_plan.goals[0].funding_mode == (
        SavingsFundingMode.DEADLINE_PRIORITY
    )
    assert config.savings_plan.withdrawals[0].withdrawal_date == date(2026, 8, 11)
    assert config.savings_plan.withdrawals[0].drain_balance is True
    assert config.savings_plan.goals[1].start_date == date(2026, 8, 12)
    assert config.savings_plan.goals[1].target_amount == Decimal("3000.00")
    assert config.savings_plan.goals[1].funding_mode == (
        SavingsFundingMode.PRIORITY_UNTIL_FUNDED
    )


@pytest.mark.parametrize(
    "plan",
    [
        {"goals": "bad", "withdrawals": []},
        {
            "goals": [
                {"name": "A", "target_amount": "100.00", "start_date": "bad-date"}
            ],
            "withdrawals": [],
        },
        {
            "goals": [
                {
                    "name": "A",
                    "target_amount": "100.00",
                    "start_date": "2026-01-01",
                },
                {
                    "name": "a",
                    "target_amount": "100.00",
                    "start_date": "2026-01-02",
                },
            ],
            "withdrawals": [],
        },
        {
            "goals": [
                {
                    "name": "A",
                    "target_amount": "100.00",
                    "start_date": "2026-01-01",
                    "target_date": "2026-01-10",
                },
                {
                    "name": "B",
                    "target_amount": "100.00",
                    "start_date": "2026-01-10",
                },
            ],
            "withdrawals": [],
        },
        {
            "goals": [],
            "withdrawals": [
                {
                    "name": "Bad",
                    "date": "2026-01-10",
                    "amount": "10.00",
                    "drain_balance": True,
                }
            ],
        },
        {
            "goals": [
                {
                    "name": "A",
                    "target_amount": "100.00",
                    "start_date": "2026-01-01",
                    "funding_mode": "deadline_priority",
                }
            ],
            "withdrawals": [],
        },
        {
            "goals": [
                {
                    "name": "A",
                    "target_amount": "100.00",
                    "start_date": "2026-01-01",
                    "funding_mode": "unsupported",
                }
            ],
            "withdrawals": [],
        },
    ],
)
def test_invalid_savings_plan_configuration_is_rejected(tmp_path, plan):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "budget": {
                    "paycheck": 1000,
                    "first_paycheck": "2026-01-01",
                    "rent_per_paycheck": 0,
                    "insurance_per_paycheck": 0,
                    "personal_per_paycheck": 0,
                    "starting_savings": 0,
                    "savings_goal": 1000,
                    "snowball_split": 0.5,
                },
                "bills": [],
                "debts": [],
                "savings_plan": plan,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        Config().load(config_path)
