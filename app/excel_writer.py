"""
excel_writer.py

Writes budget summaries to an Excel workbook.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.budget_engine import PayPeriodSummary
from app.models import ForecastSummary


class ExcelWriter:
    """Creates a simple workbook from PayPeriodSummary objects."""

    def __init__(self, filename: str | Path = "output/debtsnowball_plan.xlsx") -> None:
        self.path = Path(filename)

    def write(
        self,
        summaries: list[PayPeriodSummary],
        forecast: ForecastSummary | None = None,
    ) -> Path:
        """Write pay-period, debt, payoff, and savings worksheets."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        workbook = Workbook()
        dashboard_sheet = workbook.active
        dashboard_sheet.title = "Dashboard"

        pay_period_sheet = workbook.create_sheet("Pay Period Summaries")

        self._write_pay_period_summaries(pay_period_sheet, summaries)
        self._write_active_debts(workbook.create_sheet("Active Debts"), summaries)
        self._write_paid_off_debts(workbook.create_sheet("Paid-Off Debts"), summaries)
        self._write_savings_progress(
            workbook.create_sheet("Savings Progress"), summaries
        )
        self._write_forecast(workbook.create_sheet("Forecast"), forecast)
        self._write_dashboard(dashboard_sheet, summaries, forecast)

        workbook.save(self.path)
        return self.path

    def _write_pay_period_summaries(
        self,
        sheet: Worksheet,
        summaries: list[PayPeriodSummary],
    ) -> None:
        sheet.append(
            [
                "Pay Date",
                "Start Date",
                "End Date",
                "Income",
                "Bills Paid",
                "Debt Minimums",
                "Savings Deposit",
                "Snowball Payment",
                "Remaining Cash",
                "Active Debt Total",
                "Paid-Off Debt Count",
            ]
        )

        for summary in summaries:
            active_debt_total = self._active_debt_total(summary)
            sheet.append(
                [
                    summary.pay_date,
                    summary.start_date,
                    summary.end_date,
                    summary.income,
                    summary.bills_paid,
                    summary.debt_minimums,
                    summary.savings_contribution,
                    summary.snowball_payment,
                    summary.remaining_cash,
                    active_debt_total,
                    len(summary.paid_off_debts),
                ]
            )

        self._format_table(
            sheet,
            currency_columns=[4, 5, 6, 7, 8, 9, 10],
            date_columns=[1, 2, 3],
        )

    def _write_active_debts(
        self,
        sheet: Worksheet,
        summaries: list[PayPeriodSummary],
    ) -> None:
        sheet.append(["Pay Date", "Debt", "Balance", "Minimum", "Status"])

        for summary in summaries:
            for debt in summary.active_debt_balances:
                sheet.append(
                    [
                        summary.pay_date,
                        debt.name,
                        debt.balance,
                        debt.minimum,
                        debt.status,
                    ]
                )

        self._format_table(sheet, currency_columns=[3, 4], date_columns=[1])

    def _write_paid_off_debts(
        self,
        sheet: Worksheet,
        summaries: list[PayPeriodSummary],
    ) -> None:
        sheet.append(["Pay Date", "Debt", "Balance", "Minimum", "Status"])

        seen = set()
        for summary in summaries:
            for debt in summary.paid_off_debts:
                if debt.name in seen:
                    continue

                seen.add(debt.name)
                sheet.append(
                    [
                        summary.pay_date,
                        debt.name,
                        debt.balance,
                        debt.minimum,
                        debt.status,
                    ]
                )

        self._format_table(sheet, currency_columns=[3, 4], date_columns=[1])

    def _write_savings_progress(
        self,
        sheet: Worksheet,
        summaries: list[PayPeriodSummary],
    ) -> None:
        sheet.append(
            [
                "Pay Date",
                "Savings Deposit",
                "Savings Balance",
                "Savings Goal",
                "Remaining To Goal",
            ]
        )

        for summary in summaries:
            remaining_to_goal = max(summary.savings_goal - summary.savings_balance, 0.0)
            sheet.append(
                [
                    summary.pay_date,
                    summary.savings_contribution,
                    summary.savings_balance,
                    summary.savings_goal,
                    round(remaining_to_goal, 2),
                ]
            )

        self._format_table(sheet, currency_columns=[2, 3, 4, 5], date_columns=[1])

    def _write_dashboard(
        self,
        sheet: Worksheet,
        summaries: list[PayPeriodSummary],
        forecast: ForecastSummary | None = None,
    ) -> None:
        sheet["A1"] = "DebtSnowball Dashboard"
        sheet["A1"].font = Font(bold=True, size=16)
        sheet.merge_cells("A1:D1")

        sheet["A3"] = "Metric"
        sheet["B3"] = "Value"

        metrics = self._dashboard_metrics(summaries, forecast)
        for index, (label, value, number_format) in enumerate(metrics, start=4):
            sheet.cell(row=index, column=1, value=label)
            value_cell = sheet.cell(row=index, column=2, value=value)
            value_cell.number_format = number_format

        self._format_table(sheet, currency_columns=[], date_columns=[], header_row=3)
        sheet.auto_filter.ref = f"A3:B{len(metrics) + 3}"
        sheet.freeze_panes = "A4"
        sheet.column_dimensions["A"].width = 28
        sheet.column_dimensions["B"].width = 18
        sheet["A1"].alignment = Alignment(horizontal="left")

        self._add_savings_chart(sheet, len(summaries))
        self._add_debt_chart(sheet, len(summaries))

    def _dashboard_metrics(
        self,
        summaries: list[PayPeriodSummary],
        forecast: ForecastSummary | None = None,
    ) -> list[tuple[str, object, str]]:
        if not summaries:
            metrics: list[tuple[str, object, str]] = []
        else:
            first_summary = summaries[0]
            last_summary = summaries[-1]
            starting_savings = round(
                first_summary.savings_balance - first_summary.savings_contribution,
                2,
            )
            current_savings = last_summary.savings_balance
            savings_goal = last_summary.savings_goal
            remaining_to_goal = max(savings_goal - current_savings, 0.0)
            current_debt = self._active_debt_total(last_summary)
            paid_off_count = len(last_summary.paid_off_debts)
            total_snowball = round(
                sum(summary.snowball_payment for summary in summaries), 2
            )
            total_minimums = round(
                sum(summary.debt_minimums for summary in summaries), 2
            )

            metrics = [
                ("Starting Savings", starting_savings, "$#,##0.00"),
                ("Current Savings", current_savings, "$#,##0.00"),
                ("Savings Goal", savings_goal, "$#,##0.00"),
                ("Remaining To Goal", remaining_to_goal, "$#,##0.00"),
                ("Current Active Debt", current_debt, "$#,##0.00"),
                ("Paid-Off Debts", paid_off_count, "0"),
                ("Total Snowball Paid", total_snowball, "$#,##0.00"),
                ("Total Minimums Paid", total_minimums, "$#,##0.00"),
            ]

        if forecast is not None:
            metrics.extend(
                [
                    (
                        "Estimated Debt-Free Date",
                        forecast.debt_free_date,
                        "mmm d, yyyy",
                    ),
                    (
                        "Estimated Savings Goal Date",
                        forecast.savings_goal_date,
                        "mmm d, yyyy",
                    ),
                    (
                        "Remaining Forecast Interest",
                        self._cell_value(forecast.total_interest_paid),
                        "$#,##0.00",
                    ),
                ]
            )

        return metrics

    def _write_forecast(
        self,
        sheet: Worksheet,
        forecast: ForecastSummary | None,
    ) -> None:
        sheet["A1"] = "Forecast"
        sheet["A1"].font = Font(bold=True, size=16)
        sheet.merge_cells("A1:E1")

        summary_header_row = 3
        sheet.cell(row=summary_header_row, column=1, value="Summary")
        sheet.cell(row=summary_header_row, column=1).font = Font(bold=True)

        summary_rows = self._forecast_summary_rows(forecast)
        for row_index, (label, value, number_format) in enumerate(
            summary_rows,
            start=summary_header_row + 1,
        ):
            sheet.cell(row=row_index, column=1, value=label)
            value_cell = sheet.cell(row=row_index, column=2, value=value)
            value_cell.number_format = number_format

        debt_header_row = summary_header_row + len(summary_rows) + 3
        sheet.cell(row=debt_header_row, column=1, value="Debt Forecast")
        sheet.cell(row=debt_header_row, column=1).font = Font(bold=True)
        debt_table_header_row = debt_header_row + 1
        debt_headers = [
            "Debt",
            "Starting Balance",
            "Estimated Payoff Date",
            "Interest Paid",
            "Total Paid",
        ]
        for column_index, header in enumerate(debt_headers, start=1):
            sheet.cell(row=debt_table_header_row, column=column_index, value=header)

        debt_rows = forecast.debt_payoffs if forecast is not None else []
        for row_index, payoff in enumerate(debt_rows, start=debt_table_header_row + 1):
            sheet.cell(row=row_index, column=1, value=payoff.debt_name)
            sheet.cell(row=row_index, column=2, value=self._cell_value(payoff.starting_balance))
            sheet.cell(row=row_index, column=3, value=payoff.payoff_date)
            sheet.cell(row=row_index, column=4, value=self._cell_value(payoff.total_interest_paid))
            sheet.cell(row=row_index, column=5, value=self._cell_value(payoff.total_paid))

        timeline_header_row = debt_table_header_row + max(len(debt_rows), 1) + 3
        sheet.cell(row=timeline_header_row, column=1, value="Forecast Timeline")
        sheet.cell(row=timeline_header_row, column=1).font = Font(bold=True)
        timeline_table_header_row = timeline_header_row + 1
        timeline_headers = [
            "Paycheck Date",
            "Debt Balance",
            "Savings Balance",
            "Interest Paid",
            "Snowball Paid",
        ]
        for column_index, header in enumerate(timeline_headers, start=1):
            sheet.cell(row=timeline_table_header_row, column=column_index, value=header)

        timeline_rows = forecast.periods if forecast is not None else []
        for row_index, period in enumerate(
            timeline_rows,
            start=timeline_table_header_row + 1,
        ):
            sheet.cell(row=row_index, column=1, value=period.paycheck_date)
            sheet.cell(row=row_index, column=2, value=self._cell_value(period.total_debt_balance))
            sheet.cell(row=row_index, column=3, value=self._cell_value(period.savings_balance))
            sheet.cell(row=row_index, column=4, value=self._cell_value(period.interest_paid))
            sheet.cell(row=row_index, column=5, value=self._cell_value(period.snowball_paid))

        self._format_forecast_sheet(
            sheet=sheet,
            debt_table_header_row=debt_table_header_row,
            timeline_table_header_row=timeline_table_header_row,
        )
        self._add_forecast_charts(sheet, timeline_table_header_row, len(timeline_rows))

    def _forecast_summary_rows(
        self,
        forecast: ForecastSummary | None,
    ) -> list[tuple[str, object, str]]:
        if forecast is None:
            return [
                ("Estimated Debt-Free Date", None, "mmm d, yyyy"),
                ("Estimated Savings Goal Date", None, "mmm d, yyyy"),
                ("Remaining Debt", 0.0, "$#,##0.00"),
                ("Ending Savings", 0.0, "$#,##0.00"),
                ("Total Interest Remaining", 0.0, "$#,##0.00"),
                ("Total Future Minimum Payments", 0.0, "$#,##0.00"),
                ("Total Future Snowball Payments", 0.0, "$#,##0.00"),
            ]

        return [
            ("Estimated Debt-Free Date", forecast.debt_free_date, "mmm d, yyyy"),
            ("Estimated Savings Goal Date", forecast.savings_goal_date, "mmm d, yyyy"),
            ("Remaining Debt", self._cell_value(forecast.remaining_debt), "$#,##0.00"),
            ("Ending Savings", self._cell_value(forecast.ending_savings), "$#,##0.00"),
            (
                "Total Interest Remaining",
                self._cell_value(forecast.total_interest_paid),
                "$#,##0.00",
            ),
            (
                "Total Future Minimum Payments",
                self._cell_value(forecast.total_minimum_payments),
                "$#,##0.00",
            ),
            (
                "Total Future Snowball Payments",
                self._cell_value(forecast.total_snowball_payments),
                "$#,##0.00",
            ),
        ]

    def _format_forecast_sheet(
        self,
        sheet: Worksheet,
        debt_table_header_row: int,
        timeline_table_header_row: int,
    ) -> None:
        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)

        for header_row in [debt_table_header_row, timeline_table_header_row]:
            for cell in sheet[header_row]:
                cell.fill = header_fill
                cell.font = header_font

        for cell in sheet["A"]:
            if cell.row > 1:
                cell.number_format = "mmm d, yyyy"

        for column_letter in ["B", "D", "E"]:
            for cell in sheet[column_letter][1:]:
                cell.number_format = "$#,##0.00"

        for cell in sheet["C"][1:]:
            if isinstance(cell.value, (int, float)):
                cell.number_format = "$#,##0.00"
            else:
                cell.number_format = "mmm d, yyyy"

        for column_index, column in enumerate(sheet.columns, start=1):
            max_length = max(
                len(str(cell.value)) if cell.value is not None else 0 for cell in column
            )
            sheet.column_dimensions[get_column_letter(column_index)].width = min(
                max(max_length + 2, 12),
                32,
            )

        sheet.freeze_panes = "A4"
        sheet.auto_filter.ref = f"A{timeline_table_header_row}:E{sheet.max_row}"

    def _add_forecast_charts(
        self,
        sheet: Worksheet,
        timeline_table_header_row: int,
        timeline_count: int,
    ) -> None:
        if timeline_count == 0:
            return

        max_row = timeline_table_header_row + timeline_count
        categories = Reference(
            sheet,
            min_col=1,
            min_row=timeline_table_header_row + 1,
            max_row=max_row,
        )

        debt_chart = LineChart()
        debt_chart.title = "Total Debt Balance Over Time"
        debt_chart.y_axis.title = "Debt Balance"
        debt_chart.x_axis.title = "Paycheck Date"
        debt_data = Reference(
            sheet,
            min_col=2,
            min_row=timeline_table_header_row,
            max_row=max_row,
        )
        debt_chart.add_data(debt_data, titles_from_data=True)
        debt_chart.set_categories(categories)
        debt_chart.height = 7
        debt_chart.width = 14
        sheet.add_chart(debt_chart, "G3")

        savings_chart = LineChart()
        savings_chart.title = "Savings Balance Over Time"
        savings_chart.y_axis.title = "Savings Balance"
        savings_chart.x_axis.title = "Paycheck Date"
        savings_data = Reference(
            sheet,
            min_col=3,
            min_row=timeline_table_header_row,
            max_row=max_row,
        )
        savings_chart.add_data(savings_data, titles_from_data=True)
        savings_chart.set_categories(categories)
        savings_chart.height = 7
        savings_chart.width = 14
        sheet.add_chart(savings_chart, "G20")

    def _add_savings_chart(self, sheet: Worksheet, summary_count: int) -> None:
        if summary_count == 0:
            return

        chart = LineChart()
        chart.title = "Savings Growth"
        chart.y_axis.title = "Savings Balance"
        chart.x_axis.title = "Pay Date"
        chart.style = 13

        data = Reference(
            sheet.parent["Savings Progress"],
            min_col=3,
            min_row=1,
            max_row=summary_count + 1,
        )
        categories = Reference(
            sheet.parent["Savings Progress"],
            min_col=1,
            min_row=2,
            max_row=summary_count + 1,
        )
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(categories)
        chart.height = 7
        chart.width = 14

        sheet.add_chart(chart, "D3")

    def _add_debt_chart(self, sheet: Worksheet, summary_count: int) -> None:
        if summary_count == 0:
            return

        chart = LineChart()
        chart.title = "Debt Reduction"
        chart.y_axis.title = "Active Debt Total"
        chart.x_axis.title = "Pay Date"
        chart.style = 12

        data = Reference(
            sheet.parent["Pay Period Summaries"],
            min_col=10,
            min_row=1,
            max_row=summary_count + 1,
        )
        categories = Reference(
            sheet.parent["Pay Period Summaries"],
            min_col=1,
            min_row=2,
            max_row=summary_count + 1,
        )
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(categories)
        chart.height = 7
        chart.width = 14

        sheet.add_chart(chart, "D20")

    def _format_table(
        self,
        sheet: Worksheet,
        currency_columns: list[int] | None = None,
        date_columns: list[int] | None = None,
        header_row: int = 1,
    ) -> None:
        currency_columns = currency_columns or []
        date_columns = date_columns or []

        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)

        for cell in sheet[header_row]:
            cell.fill = header_fill
            cell.font = header_font

        if sheet.max_row >= 1 and sheet.max_column >= 1:
            sheet.auto_filter.ref = sheet.dimensions

        for column_index in currency_columns:
            for cell in sheet[get_column_letter(column_index)][1:]:
                cell.number_format = "$#,##0.00"

        for column_index in date_columns:
            for cell in sheet[get_column_letter(column_index)][1:]:
                cell.number_format = "mmm d, yyyy"

        for column_index, column in enumerate(sheet.columns, start=1):
            max_length = max(
                len(str(cell.value)) if cell.value is not None else 0 for cell in column
            )
            sheet.column_dimensions[get_column_letter(column_index)].width = min(
                max(max_length + 2, 12),
                28,
            )

        sheet.freeze_panes = "A2"

    def _active_debt_total(self, summary: PayPeriodSummary) -> float:
        return round(sum(debt.balance for debt in summary.active_debt_balances), 2)

    def _cell_value(self, value: object) -> object:
        if hasattr(value, "quantize"):
            return float(value)

        return value
