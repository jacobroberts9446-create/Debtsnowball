from datetime import date
from decimal import Decimal

from openpyxl import load_workbook

from app.excel_writer import ExcelWriter
from app.models import (
    DebtPayoffForecast,
    ForecastSummary,
    ScenarioComparison,
    ScenarioResult,
)


def make_forecast(
    debt_free_date=date(2026, 1, 29),
    savings_goal_date=date(2026, 1, 15),
    total_interest=Decimal("100.00"),
    total_snowball=Decimal("1000.00"),
    ending_savings=Decimal("500.00"),
    remaining_debt=Decimal("0.00"),
    completed=True,
    payoffs=None,
):
    return ForecastSummary(
        forecast_start_date=date(2026, 1, 1),
        forecast_end_date=date(2026, 1, 29),
        debt_free_date=debt_free_date,
        savings_goal_date=savings_goal_date,
        starting_debt=Decimal("1000.00"),
        total_interest_paid=total_interest,
        total_minimum_payments=Decimal("200.00"),
        total_snowball_payments=total_snowball,
        ending_savings=ending_savings,
        remaining_debt=remaining_debt,
        completed=completed,
        debt_payoffs=payoffs if payoffs is not None else make_payoffs(),
        periods=[],
    )


def make_payoffs(loan_date=date(2026, 1, 29)):
    return [
        DebtPayoffForecast(
            debt_name="Card",
            starting_balance=Decimal("300.00"),
            payoff_date=date(2026, 1, 15),
            total_interest_paid=Decimal("10.00"),
            total_paid=Decimal("310.00"),
        ),
        DebtPayoffForecast(
            debt_name="Loan",
            starting_balance=Decimal("700.00"),
            payoff_date=loan_date,
            total_interest_paid=Decimal("90.00"),
            total_paid=Decimal("790.00"),
        ),
    ]


def make_result(name, extra, forecast):
    return ScenarioResult(
        name=name,
        extra_per_paycheck=Decimal(extra),
        forecast=forecast,
        debt_payoffs=forecast.debt_payoffs,
        periods=forecast.periods,
    )


def make_comparison():
    baseline = make_result("Baseline", "0.00", make_forecast())
    extra_100 = make_result(
        "Extra $100",
        "100.00",
        make_forecast(
            debt_free_date=date(2026, 1, 15),
            total_interest=Decimal("60.00"),
            total_snowball=Decimal("1100.00"),
            payoffs=make_payoffs(loan_date=date(2026, 1, 15)),
        ),
    )
    extra_50 = make_result(
        "Extra $50",
        "50.00",
        make_forecast(
            debt_free_date=date(2026, 1, 15),
            total_interest=Decimal("70.00"),
            total_snowball=Decimal("1050.00"),
            payoffs=make_payoffs(loan_date=date(2026, 1, 15)),
        ),
    )
    return ScenarioComparison(baseline=baseline, scenarios=[extra_100, extra_50])


def test_scenario_comparison_sheet_summary_formatting_and_charts(tmp_path):
    workbook_path = tmp_path / "scenario_report.xlsx"

    ExcelWriter(workbook_path).write([], None, make_comparison())

    workbook = load_workbook(workbook_path)
    sheet = workbook["Scenario Comparison"]

    assert sheet["A1"].value == "Scenario Comparison"
    assert [sheet.cell(row=3, column=column).value for column in range(1, 14)] == [
        "Scenario",
        "Extra Per Paycheck",
        "Debt-Free Date",
        "Days Saved",
        "Savings Goal Date",
        "Savings Goal Days Changed",
        "Total Interest",
        "Interest Saved",
        "Total Snowball Paid",
        "Additional Snowball Paid",
        "Ending Debt",
        "Ending Savings",
        "Completed",
    ]
    assert [sheet.cell(row=row, column=1).value for row in range(4, 7)] == [
        "Baseline",
        "Extra $100",
        "Extra $50",
    ]
    assert sheet["B4"].value == 0
    assert sheet["D4"].value == 0
    assert sheet["H4"].value == 0
    assert sheet["J4"].value == 0
    assert sheet["D5"].value == 14
    assert sheet["H5"].value == 40
    assert sheet["J5"].value == 100
    assert sheet["B5"].number_format == "$#,##0.00"
    assert sheet["C5"].number_format == "mmm d, yyyy"
    assert sheet["D5"].number_format == "0"
    assert sheet["C20"].value == 28
    assert sheet["C20"].number_format == "0"
    assert sheet["C21"].value == 14
    assert sheet["C21"].number_format == "0"
    assert sheet["C22"].value == 14
    assert sheet["C22"].number_format == "0"
    assert sheet.freeze_panes == "A4"
    assert sheet.auto_filter.ref == "A3:M6"
    assert len(sheet._charts) == 2
    workbook.close()


def test_scenario_deltas_payoff_comparison_and_dashboard_metrics(tmp_path):
    workbook_path = tmp_path / "scenario_dashboard.xlsx"

    ExcelWriter(workbook_path).write([], None, make_comparison())

    workbook = load_workbook(workbook_path)
    scenario_sheet = workbook["Scenario Comparison"]
    dashboard = workbook["Dashboard"]

    assert scenario_sheet["A8"].value == "Scenario Deltas"
    assert scenario_sheet["A10"].value == "Extra $100"
    assert scenario_sheet["B10"].value == 14
    assert scenario_sheet["D10"].value == 40
    assert scenario_sheet["E10"].value == 100
    assert scenario_sheet["A13"].value == "Debt Payoff Comparison"
    assert [scenario_sheet.cell(row=14, column=column).value for column in range(1, 4)] == [
        "Debt",
        "Baseline",
        "Extra $100",
    ]
    assert scenario_sheet["A15"].value == "Card"
    assert scenario_sheet["B16"].value.date() == date(2026, 1, 29)
    assert dashboard["A4"].value == "Best Scenario"
    assert dashboard["B4"].value == "Extra $50"
    assert dashboard["A5"].value == "Earliest Debt-Free Date"
    assert dashboard["B5"].value.date() == date(2026, 1, 15)
    assert dashboard["A6"].value == "Maximum Interest Saved"
    assert dashboard["B6"].value == 30
    assert dashboard["A7"].value == "Extra Payment Required"
    assert dashboard["B7"].value == 50
    workbook.close()


def test_missing_payoff_dates_show_fallback_text(tmp_path):
    workbook_path = tmp_path / "missing_payoff.xlsx"
    incomplete_payoffs = make_payoffs(loan_date=None)
    comparison = ScenarioComparison(
        baseline=make_result("Baseline", "0.00", make_forecast(payoffs=incomplete_payoffs)),
        scenarios=[],
    )

    ExcelWriter(workbook_path).write([], None, comparison)

    workbook = load_workbook(workbook_path)
    assert workbook["Scenario Comparison"]["B13"].value == "Not paid within horizon"
    workbook.close()


def test_baseline_only_no_debt_and_no_comparison_cases_save(tmp_path):
    baseline_only_path = tmp_path / "baseline_only.xlsx"
    no_debt_path = tmp_path / "no_debt.xlsx"
    no_comparison_path = tmp_path / "no_comparison.xlsx"
    baseline = make_result("Baseline", "0.00", make_forecast())
    no_debt = make_result(
        "Baseline",
        "0.00",
        make_forecast(
            debt_free_date=date(2026, 1, 1),
            total_interest=Decimal("0.00"),
            total_snowball=Decimal("0.00"),
            remaining_debt=Decimal("0.00"),
            payoffs=[],
        ),
    )

    ExcelWriter(baseline_only_path).write(
        [],
        None,
        ScenarioComparison(baseline=baseline, scenarios=[]),
    )
    ExcelWriter(no_debt_path).write(
        [],
        None,
        ScenarioComparison(baseline=no_debt, scenarios=[]),
    )
    ExcelWriter(no_comparison_path).write([], None, None)

    baseline_workbook = load_workbook(baseline_only_path)
    no_debt_workbook = load_workbook(no_debt_path)
    no_comparison_workbook = load_workbook(no_comparison_path)

    assert baseline_workbook["Scenario Comparison"]["A4"].value == "Baseline"
    assert no_debt_workbook["Scenario Comparison"]["A12"].value is None
    assert no_comparison_workbook["Scenario Comparison"]["A1"].value == (
        "Scenario Comparison"
    )
    assert len(no_comparison_workbook["Scenario Comparison"]._charts) == 0

    baseline_workbook.close()
    no_debt_workbook.close()
    no_comparison_workbook.close()


def test_incomplete_horizon_dashboard_fallback(tmp_path):
    workbook_path = tmp_path / "incomplete.xlsx"
    incomplete_forecast = make_forecast(
        debt_free_date=None,
        savings_goal_date=None,
        remaining_debt=Decimal("900.00"),
        completed=False,
        payoffs=make_payoffs(loan_date=None),
    )
    comparison = ScenarioComparison(
        baseline=make_result("Baseline", "0.00", incomplete_forecast),
        scenarios=[
            make_result(
                "Extra $50",
                "50.00",
                make_forecast(
                    debt_free_date=None,
                    savings_goal_date=None,
                    remaining_debt=Decimal("800.00"),
                    completed=False,
                    payoffs=make_payoffs(loan_date=None),
                ),
            )
        ],
    )

    ExcelWriter(workbook_path).write([], None, comparison)

    workbook = load_workbook(workbook_path)
    assert workbook["Dashboard"]["B4"].value == "No scenario completed within horizon"
    assert workbook["Scenario Comparison"]["D4"].value == 0
    assert workbook["Scenario Comparison"]["D5"].value is None
    workbook.close()
