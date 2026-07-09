"""
excel_writer.py

Writes generated plans to an Excel workbook.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


class ExcelWriter:
    def __init__(self, filename="output/debtsnowball_plan.xlsx"):
        self.path = Path(filename)

    def write(self, paychecks, debts):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        workbook = Workbook()
        plan_sheet = workbook.active
        plan_sheet.title = "Paycheck Plan"
        self._write_paychecks(plan_sheet, paychecks)

        debt_sheet = workbook.create_sheet("Debt Summary")
        self._write_debts(debt_sheet, debts)

        workbook.save(self.path)
        return self.path

    def _write_paychecks(self, sheet, paychecks):
        headers = [
            "Pay Date",
            "Income",
            "Bills Paid",
            "Debt Minimums",
            "Snowball Payment",
            "Savings Added",
            "Checking Remaining",
            "Notes",
        ]
        sheet.append(headers)

        for paycheck in paychecks:
            sheet.append(
                [
                    paycheck.pay_date,
                    paycheck.income,
                    paycheck.bills_paid,
                    paycheck.debt_minimums,
                    paycheck.snowball_payment,
                    paycheck.savings_added,
                    paycheck.checking_remaining,
                    "\n".join(paycheck.notes),
                ]
            )

        self._format_table(sheet, currency_columns=range(2, 8))

    def _write_debts(self, sheet, debts):
        headers = [
            "Name",
            "Balance",
            "APR",
            "Minimum",
            "Total Paid",
            "Total Interest Paid",
            "Status",
        ]
        sheet.append(headers)

        for debt in debts:
            sheet.append(
                [
                    debt["name"],
                    debt["balance"],
                    debt["apr"] / 100,
                    debt["minimum"],
                    debt["total_paid"],
                    debt["total_interest_paid"],
                    debt["status"],
                ]
            )

        self._format_table(sheet, currency_columns=[2, 4, 5, 6])
        for cell in sheet["C"][1:]:
            cell.number_format = "0.00%"

    def _format_table(self, sheet, currency_columns):
        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)

        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font

        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = cell.alignment.copy(wrap_text=True)

        for column_index in currency_columns:
            for cell in sheet[get_column_letter(column_index)][1:]:
                cell.number_format = "$#,##0.00"

        for column in sheet.columns:
            max_length = max(
                len(str(cell.value)) if cell.value is not None else 0
                for cell in column
            )
            sheet.column_dimensions[column[0].column_letter].width = min(
                max(max_length + 2, 12),
                36,
            )

        sheet.freeze_panes = "A2"
