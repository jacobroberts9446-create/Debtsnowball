import json
import sqlite3
from contextlib import closing
from datetime import date
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.budget_engine import DebtBalance, PayPeriodSummary
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.database import Database
from app.excel_writer import ExcelWriter
from app.models import Bill, BudgetSettings, Debt, Savings


def test_calendar_engine_generates_biweekly_periods():
    settings = SimpleNamespace(first_paycheck=date(2026, 1, 2))

    periods = CalendarEngine(settings).generate(date(2026, 1, 30))

    assert [period.pay_date for period in periods] == [
        date(2026, 1, 2),
        date(2026, 1, 16),
        date(2026, 1, 30),
    ]
    assert periods[0].start_date == date(2026, 1, 2)
    assert periods[0].end_date == date(2026, 1, 15)


def test_config_loads_budget_bills_and_sorted_debts(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "budget": {
                    "paycheck": 1000,
                    "first_paycheck": "2026-01-02",
                    "rent_per_paycheck": 100,
                    "insurance_per_paycheck": 50,
                    "personal_per_paycheck": 200,
                    "starting_savings": 300,
                    "savings_goal": 1000,
                    "snowball_split": 0.5,
                },
                "bills": [{"name": "Phone", "amount": 100, "due_day": 10}],
                "debts": [
                    {
                        "name": "Second",
                        "balance": 200,
                        "apr": 0,
                        "minimum": 20,
                        "due_day": 12,
                        "snowball_order": 2,
                    },
                    {
                        "name": "First",
                        "balance": 100,
                        "apr": 0,
                        "minimum": 10,
                        "due_day": 11,
                        "snowball_order": 1,
                    },
                ],
            }
        )
    )

    config = Config()
    config.load(config_path)

    assert config.settings.first_paycheck == date(2026, 1, 2)
    assert config.bills[0].name == "Phone"
    assert [debt.name for debt in config.debts] == ["First", "Second"]


def test_config_raises_for_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Config().load(tmp_path / "missing.json")


def test_database_saves_paychecks_and_debts(tmp_path):
    db_path = tmp_path / "plan.sqlite"
    paychecks = [
        SimpleNamespace(
            pay_date=date(2026, 1, 2),
            income=1000,
            bills_paid=100,
            debt_minimums=50,
            snowball_payment=400,
            savings_added=450,
            checking_remaining=0,
            notes=["ok"],
        )
    ]
    debts = [
        {
            "name": "Card",
            "balance": 500,
            "apr": 20,
            "minimum": 50,
            "total_paid": 100,
            "total_interest_paid": 10,
            "status": "active",
        }
    ]

    Database(db_path).save_plan(paychecks, debts)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paychecks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM debts").fetchone()[0] == 1


def test_excel_writer_creates_dashboard_tables_and_charts(tmp_path):
    workbook_path = tmp_path / "plan.xlsx"
    summaries = [
        PayPeriodSummary(
            pay_date=date(2026, 1, 2),
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 15),
            income=1000,
            bills_paid=100,
            debt_minimums=50,
            snowball_payment=400,
            savings_contribution=450,
            savings_balance=950,
            savings_goal=1000,
            remaining_cash=0,
            active_debt_balances=[
                DebtBalance("Card", 500, 50, "active"),
                DebtBalance("Loan", 1000, 100, "active"),
            ],
            paid_off_debts=[],
        ),
        PayPeriodSummary(
            pay_date=date(2026, 1, 16),
            start_date=date(2026, 1, 16),
            end_date=date(2026, 1, 29),
            income=1000,
            bills_paid=100,
            debt_minimums=50,
            snowball_payment=850,
            savings_contribution=0,
            savings_balance=1000,
            savings_goal=1000,
            remaining_cash=0,
            active_debt_balances=[DebtBalance("Loan", 650, 100, "active")],
            paid_off_debts=[DebtBalance("Card", 0, 50, "paid")],
        ),
    ]

    ExcelWriter(workbook_path).write(summaries)

    workbook = load_workbook(workbook_path)
    assert workbook.sheetnames == [
        "Dashboard",
        "Pay Period Summaries",
        "Active Debts",
        "Paid-Off Debts",
        "Savings Progress",
    ]
    assert len(workbook["Dashboard"]._charts) == 2
    assert workbook["Dashboard"].freeze_panes == "A4"
    assert workbook["Pay Period Summaries"].auto_filter.ref == "A1:K3"
    assert workbook["Active Debts"].max_row == 4
    assert workbook["Paid-Off Debts"].max_row == 2
    assert workbook["Savings Progress"]["C3"].value == 1000
    workbook.close()


def test_model_validation_and_savings_goal():
    with pytest.raises(ValueError):
        Bill("Bad Bill", 10, 0)

    with pytest.raises(ValueError):
        Debt("Bad Balance", -1, 0, 10, 1, 1)

    with pytest.raises(ValueError):
        Debt("Bad Apr", 100, -1, 10, 1, 1)

    with pytest.raises(ValueError):
        Debt("Bad Minimum", 100, 0, -1, 1, 1)

    with pytest.raises(ValueError):
        Debt("Bad Due Day", 100, 0, 10, 32, 1)

    with pytest.raises(ValueError):
        BudgetSettings(100, date(2026, 1, 1), 0, 0, 0, 0, 100, 1.5)

    with pytest.raises(ValueError):
        BudgetSettings(-1, date(2026, 1, 1), 0, 0, 0, 0, 100, 0.5)

    savings = Savings(50, 100)
    assert not savings.goal_met
    savings.add(50)
    assert savings.goal_met
