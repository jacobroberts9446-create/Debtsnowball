from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from openpyxl import load_workbook

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.excel_writer import ExcelWriter
from app.forecast_engine import ForecastEngine
from app.models import (
    BudgetSettings,
    Debt,
    PlannedSavingsWithdrawal,
    SavingsFundingMode,
    SavingsGoalStage,
    SavingsPlan,
)


def make_config():
    return SimpleNamespace(
        settings=BudgetSettings(
            paycheck=1000,
            first_paycheck=date(2026, 1, 1),
            rent_per_paycheck=0,
            insurance_per_paycheck=0,
            personal_per_paycheck=0,
            starting_savings=0,
            savings_goal=1000,
            snowball_split=0.50,
        ),
        bills=[],
        debts=[
            Debt(
                "Long debt",
                balance=100000,
                apr=0,
                minimum=0,
                due_day=10,
                snowball_order=1,
            )
        ],
        scenarios=[],
        savings_plan=SavingsPlan(
            goals=[
                SavingsGoalStage(
                    name="August savings",
                    target_amount=Decimal("3900.00"),
                    start_date=date(2026, 1, 1),
                    target_date=date(2026, 8, 11),
                    funding_mode=SavingsFundingMode.DEADLINE_PRIORITY,
                ),
                SavingsGoalStage(
                    name="Replacement savings",
                    target_amount=Decimal("3000.00"),
                    start_date=date(2026, 8, 12),
                    funding_mode=SavingsFundingMode.PRIORITY_UNTIL_FUNDED,
                ),
            ],
            withdrawals=[
                PlannedSavingsWithdrawal(
                    name="August planned expense",
                    withdrawal_date=date(2026, 8, 11),
                    drain_balance=True,
                )
            ],
        ),
    )


def test_savings_plan_sections_and_dashboard_metrics(tmp_path):
    config = make_config()
    periods = CalendarEngine(config.settings).generate(date(2026, 8, 28))
    summaries = BudgetEngine(config).build_plan(periods)
    forecast = ForecastEngine(config).forecast()
    workbook_path = tmp_path / "savings_plan.xlsx"

    ExcelWriter(workbook_path).write(summaries, forecast)

    workbook = load_workbook(workbook_path)
    sheet = workbook["Savings Progress"]
    dashboard = workbook["Dashboard"]

    assert sheet["A1"].value == "Savings Plan Summary"
    assert sheet["A4"].value == "August savings"
    assert sheet["C4"].value.date() == date(2026, 8, 11)
    assert sheet["D4"].value == 3900
    assert sheet["L4"].value == "Yes"
    assert sheet["A5"].value == "Replacement savings"
    assert sheet["D5"].value == 3000
    assert sheet["A7"].value == "Planned Withdrawals"
    assert sheet["A9"].value == "August planned expense"
    assert sheet["B9"].value.date() == date(2026, 8, 11)
    assert sheet["D9"].value is True
    assert sheet["E9"].value > 0
    assert sheet["G9"].value == 0
    assert sheet["H9"].value.date() == date(2026, 8, 13)
    assert sheet["A12"].value == "Savings Progress Detail"
    assert sheet["B30"].value == "Replacement savings"
    assert sheet["C30"].value == 1000
    assert sheet["F30"].value > 0
    assert sheet["H30"].value > 0
    assert sheet["J30"].value == 0
    assert sheet["N30"].value == 3000
    assert sheet["P30"].value == "Aug 13"
    assert sheet.column_dimensions["P"].hidden is True
    assert dashboard["A12"].value == "Active Savings Goal"
    assert dashboard["B12"].value == "Replacement savings"
    assert dashboard["A13"].value == "Active Savings Target"
    assert dashboard["B13"].value == 3000
    assert dashboard["A18"].value == "Goal Feasible"
    assert dashboard["A19"].value == "Snowball Currently Reduced"
    workbook.close()


def test_current_plan_workbook_reports_deadline_priority_shortfall(tmp_path):
    config = Config().load("config.json")
    forecast_config = deepcopy(config)
    periods = CalendarEngine(config.settings).generate(date(2026, 12, 31))
    summaries = BudgetEngine(config).build_plan(periods)
    forecast = ForecastEngine(forecast_config).forecast()
    workbook_path = tmp_path / "current_plan.xlsx"

    ExcelWriter(workbook_path).write(summaries, forecast)

    workbook = load_workbook(workbook_path, data_only=True)
    pay_periods = workbook["Pay Period Summaries"]
    savings = workbook["Savings Progress"]

    assert pay_periods["H2"].value == 430
    assert pay_periods["K2"].value == 846.5
    assert pay_periods["M2"].value == 215
    assert pay_periods["N2"].value == 416.5
    assert pay_periods["O2"].value == 0
    assert pay_periods["H3"].value == 1137
    assert pay_periods["K3"].value == 1553.5
    assert pay_periods["M3"].value == 568.5
    assert pay_periods["N3"].value == 416.5
    assert pay_periods["O3"].value == 0
    assert savings["I4"].value == 3900
    assert savings["J4"].value == 0
    assert savings["K4"].value == 0
    assert savings["L4"].value == "Yes"
    assert savings["E9"].value == 3900
    assert savings["G9"].value == 0
    assert savings["B16"].value == "Replacement savings"
    assert savings["F16"].value == 430
    assert savings["L16"].value == 3900
    workbook.close()
