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


class ExcelWriter:
    """Creates a simple workbook from PayPeriodSummary objects."""

    def __init__(self, filename: str | Path = "output/debtsnowball_plan.xlsx") -> None:
        self.path = Path(filename)

    def write(self, summaries: list[PayPeriodSummary]) -> Path:
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
        self._write_dashboard(dashboard_sheet, summaries)

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
    ) -> None:
        sheet["A1"] = "DebtSnowball Dashboard"
        sheet["A1"].font = Font(bold=True, size=16)
        sheet.merge_cells("A1:D1")

        sheet["A3"] = "Metric"
        sheet["B3"] = "Value"

        metrics = self._dashboard_metrics(summaries)
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
    ) -> list[tuple[str, float | int, str]]:
        if not summaries:
            return []

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
        total_minimums = round(sum(summary.debt_minimums for summary in summaries), 2)

        return [
            ("Starting Savings", starting_savings, "$#,##0.00"),
            ("Current Savings", current_savings, "$#,##0.00"),
            ("Savings Goal", savings_goal, "$#,##0.00"),
            ("Remaining To Goal", remaining_to_goal, "$#,##0.00"),
            ("Current Active Debt", current_debt, "$#,##0.00"),
            ("Paid-Off Debts", paid_off_count, "0"),
            ("Total Snowball Paid", total_snowball, "$#,##0.00"),
            ("Total Minimums Paid", total_minimums, "$#,##0.00"),
        ]

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
