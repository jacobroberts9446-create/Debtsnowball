"""Interactive full budget setup and in-memory generation workflow."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.bill_input import collect_bills, total_monthly_bills
from app.console import (
    OutputFunc,
    print_error,
    print_section_header,
    print_success,
    print_table,
    print_warning,
)
from app.debt_input import collect_debts, parse_nonnegative_money, total_debt_balance
from app.menu import InputFunc, MenuOption, display_menu
from app.models import Bill, Debt
from app.money import format_currency
from app.plan_generation import generate_plan_from_setup
from app.plan_setup import (
    PayFrequency,
    PlanSetupCancelled,
    collect_basic_setup_fields,
    collect_setup_debts,
    pay_frequency_label,
    parse_net_paycheck_amount,
    raise_if_cancelled,
)


@dataclass(frozen=True)
class BudgetSetupResult:
    """Complete interactive setup inputs needed for in-memory generation."""

    plan_name: str
    pay_frequency: PayFrequency
    first_paycheck_date: date
    net_paycheck_amount: Decimal
    debts: list[Debt]
    bills: list[Bill]
    monthly_personal_spending: Decimal
    current_savings: Decimal
    emergency_fund_target: Decimal


def collect_budget_setup(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    debt_collector=collect_setup_debts,
    bill_collector=collect_bills,
    generator=generate_plan_from_setup,
):
    """Collect full setup, review it, and generate an in-memory plan."""
    try:
        basics = collect_basic_setup_fields(input_func, output_func)
        if prompt_section_navigation("Plan Basics", False, input_func, output_func) == "cancel":
            return None

        debts = collect_debts_section([], input_func, output_func, debt_collector)
        if debts == "back":
            return collect_budget_setup(
                input_func,
                output_func,
                debt_collector,
                bill_collector,
                generator,
            )
        if debts is None:
            return None
        nav = prompt_section_navigation("Debts", True, input_func, output_func)
        if nav == "cancel":
            return None
        if nav == "back":
            return collect_budget_setup(
                input_func,
                output_func,
                debt_collector,
                bill_collector,
                generator,
            )

        bills = collect_bills_section([], input_func, output_func, bill_collector)
        if bills == "back":
            return collect_budget_setup(
                input_func,
                output_func,
                debt_collector,
                bill_collector,
                generator,
            )
        if bills is None:
            return None
        nav = prompt_section_navigation("Recurring Bills", True, input_func, output_func)
        if nav == "cancel":
            return None
        if nav == "back":
            return collect_budget_setup(
                input_func,
                output_func,
                debt_collector,
                bill_collector,
                generator,
            )

        personal = prompt_monthly_personal_spending(input_func, output_func)
        nav = prompt_section_navigation("Personal Spending", True, input_func, output_func)
        if nav == "cancel":
            return None
        if nav == "back":
            return collect_budget_setup(
                input_func,
                output_func,
                debt_collector,
                bill_collector,
                generator,
            )

        current_savings, emergency_target = prompt_savings(input_func, output_func)
        setup = BudgetSetupResult(
            plan_name=basics.plan_name,
            pay_frequency=basics.pay_frequency,
            first_paycheck_date=basics.first_paycheck_date,
            net_paycheck_amount=basics.net_paycheck_amount,
            debts=debts,
            bills=bills,
            monthly_personal_spending=personal,
            current_savings=current_savings,
            emergency_fund_target=emergency_target,
        )
        return review_budget_setup(
            setup,
            input_func,
            output_func,
            debt_collector,
            bill_collector,
            generator,
        )
    except PlanSetupCancelled:
        print_warning("Plan setup cancelled.", output_func)
        return None


def review_budget_setup(
    setup: BudgetSetupResult,
    input_func: InputFunc,
    output_func: OutputFunc,
    debt_collector,
    bill_collector,
    generator,
):
    """Review full setup and generate when confirmed."""
    current = setup
    while True:
        print_full_review(current, output_func)
        options = [
            MenuOption("1", "Confirm And Generate", lambda: True),
            MenuOption("2", "Edit Plan Basics", lambda: True),
            MenuOption("3", "Edit Debts", lambda: True),
            MenuOption("4", "Edit Bills", lambda: True),
            MenuOption("5", "Edit Personal Spending", lambda: True),
            MenuOption("6", "Edit Savings", lambda: True),
            MenuOption("7", "Cancel", lambda: True),
        ]
        display_menu("Full Plan Review", options, output_func)
        choice = input_func("Choose an option: ").strip()
        try:
            if choice == "1":
                try:
                    summary = generator(current)
                except (ValueError, RuntimeError) as exc:
                    print_error(str(exc), output_func)
                    continue
                return summary
            if choice == "2":
                basics = collect_basic_setup_fields(input_func, output_func)
                current = replace_budget_setup(
                    current,
                    plan_name=basics.plan_name,
                    pay_frequency=basics.pay_frequency,
                    first_paycheck_date=basics.first_paycheck_date,
                    net_paycheck_amount=basics.net_paycheck_amount,
                )
            elif choice == "3":
                debts = collect_debts_section(
                    current.debts,
                    input_func,
                    output_func,
                    debt_collector,
                    allow_back_to_review=True,
                )
                if debts == "back":
                    continue
                if debts is not None:
                    current = replace_budget_setup(current, debts=debts)
            elif choice == "4":
                bills = collect_bills_section(
                    current.bills,
                    input_func,
                    output_func,
                    bill_collector,
                    allow_back_to_review=True,
                )
                if bills == "back":
                    continue
                if bills is not None:
                    current = replace_budget_setup(current, bills=bills)
            elif choice == "5":
                current = replace_budget_setup(
                    current,
                    monthly_personal_spending=prompt_monthly_personal_spending(
                        input_func,
                        output_func,
                    ),
                )
            elif choice == "6":
                current_savings, emergency_target = prompt_savings(input_func, output_func)
                current = replace_budget_setup(
                    current,
                    current_savings=current_savings,
                    emergency_fund_target=emergency_target,
                )
            elif choice == "7":
                print_warning("Plan setup cancelled.", output_func)
                return None
            else:
                print_warning("Please choose one of: 1, 2, 3, 4, 5, 6, 7.", output_func)
        except PlanSetupCancelled:
            print_warning("Edit cancelled.", output_func)


def collect_debts_section(
    current_debts: list[Debt],
    input_func: InputFunc,
    output_func: OutputFunc,
    debt_collector,
    *,
    allow_back_to_review: bool = False,
) -> list[Debt] | str | None:
    """Collect debts and resolve cancellation for this setup stage."""
    while True:
        result = debt_collector(
            current_debts,
            input_func,
            output_func,
            collect_debts,
            allow_back_to_review=allow_back_to_review,
        )
        if result == "back":
            return "back"
        if result is not None:
            return result
        return None


def collect_bills_section(
    current_bills: list[Bill],
    input_func: InputFunc,
    output_func: OutputFunc,
    bill_collector,
    *,
    allow_back_to_review: bool = False,
) -> list[Bill] | str | None:
    """Collect bills and resolve cancellation for this setup stage."""
    while True:
        bills = bill_collector(
            input_func=input_func,
            output_func=output_func,
            initial_bills=current_bills if current_bills else None,
        )
        if bills is not None:
            return bills
        action = prompt_cancelled_section_action(
            "Bill Entry Cancelled",
            "Retry Bill Entry",
            "Back To Plan Review" if allow_back_to_review else "Back To Previous Section",
            input_func,
            output_func,
        )
        if action == "retry":
            continue
        if action == "back":
            return "back"
        return None


def prompt_monthly_personal_spending(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Decimal:
    """Prompt for monthly personal spending allowance."""
    print_section_header("Personal Spending", output_func)
    return prompt_net_paycheck_amount_with_message(
        "Monthly personal spending allowance: ",
        "Monthly personal spending must be zero or greater.",
        input_func,
        output_func,
        allow_zero=True,
    )


def prompt_savings(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> tuple[Decimal, Decimal]:
    """Prompt for current savings and emergency-fund target."""
    print_section_header("Savings", output_func)
    current = prompt_net_paycheck_amount_with_message(
        "Current savings: ",
        "Current savings must be zero or greater.",
        input_func,
        output_func,
        allow_zero=True,
    )
    target = prompt_net_paycheck_amount_with_message(
        "Emergency-fund target: ",
        "Emergency-fund target must be zero or greater.",
        input_func,
        output_func,
        allow_zero=True,
    )
    return current, target


def prompt_net_paycheck_amount_with_message(
    prompt: str,
    message: str,
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    allow_zero: bool,
) -> Decimal:
    """Prompt for a currency amount with optional zero allowance."""
    while True:
        raw_value = input_func(prompt)
        raise_if_cancelled(raw_value)
        try:
            amount = prompt_net_paycheck_amount_parser(raw_value, allow_zero=allow_zero)
        except ValueError:
            print_warning(message, output_func)
        else:
            return amount


def prompt_net_paycheck_amount_parser(value: str, *, allow_zero: bool) -> Decimal:
    """Parse a setup currency amount."""
    amount = parse_nonnegative_money(value) if allow_zero else parse_net_paycheck_amount(value)
    if not allow_zero and amount <= Decimal("0.00"):
        raise ValueError("amount must be positive.")
    return amount


def prompt_section_navigation(
    title: str,
    can_go_back: bool,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    """Allow continuing, going back, or cancelling after a major section."""
    options = [MenuOption("1", "Continue", lambda: True)]
    if can_go_back:
        options.append(MenuOption("2", "Back", lambda: True))
        cancel_key = "3"
    else:
        cancel_key = "2"
    options.append(MenuOption(cancel_key, "Cancel Plan Creation", lambda: True))
    while True:
        display_menu(f"{title} Complete", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return "continue"
        if can_go_back and choice == "2":
            return "back"
        if choice == cancel_key:
            print_warning("Plan setup cancelled.", output_func)
            return "cancel"
        allowed = ", ".join(option.key for option in options)
        print_warning(f"Please choose one of: {allowed}.", output_func)


def prompt_cancelled_section_action(
    title: str,
    retry_label: str,
    back_label: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str:
    """Ask how to proceed after a section-level cancellation."""
    options = [
        MenuOption("1", retry_label, lambda: True),
        MenuOption("2", back_label, lambda: True),
        MenuOption("3", "Cancel Plan Creation", lambda: True),
    ]
    while True:
        display_menu(title, options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return "retry"
        if choice == "2":
            return "back"
        if choice == "3":
            print_warning("Plan setup cancelled.", output_func)
            return "cancel"
        print_warning("Please choose one of: 1, 2, 3.", output_func)


def print_full_review(setup: BudgetSetupResult, output_func: OutputFunc = print) -> None:
    """Print the full collected setup review."""
    print_section_header("Full Plan Review", output_func)
    print_table(
        ["Section", "Field", "Value"],
        [
            ["PLAN", "Plan Name", setup.plan_name],
            ["PLAN", "Pay Frequency", pay_frequency_label(setup.pay_frequency)],
            ["PLAN", "First Paycheck Date", setup.first_paycheck_date.strftime("%m/%d/%Y")],
            ["PLAN", "Net Paycheck Amount", format_currency(setup.net_paycheck_amount)],
            ["DEBTS", "Number of Debts", str(len(setup.debts))],
            ["DEBTS", "Total Debt Balance", format_currency(total_debt_balance(setup.debts))],
            [
                "DEBTS",
                "Total Minimum Payments",
                format_currency(sum((debt.minimum for debt in setup.debts), Decimal("0.00"))),
            ],
            ["MONTHLY BILLS", "Number of Bills", str(len(setup.bills))],
            [
                "MONTHLY BILLS",
                "Total Monthly Bills",
                format_currency(total_monthly_bills(setup.bills)),
            ],
            [
                "BUDGET",
                "Monthly Personal Spending",
                format_currency(setup.monthly_personal_spending),
            ],
            ["BUDGET", "Current Savings", format_currency(setup.current_savings)],
            ["BUDGET", "Emergency-Fund Target", format_currency(setup.emergency_fund_target)],
        ],
        output_func,
    )


def print_generated_summary(summary, output_func: OutputFunc = print) -> None:
    """Print a concise generated plan summary from real engine output."""
    print_section_header("Generated Plan Summary", output_func)
    rows = [
        ["Plan Name", summary.plan_name],
        ["Number of Debts", str(summary.debt_count)],
        ["Total Starting Debt", format_currency(summary.total_starting_debt)],
        [
            "Projected Debt-Free Date",
            summary.projected_debt_free_date.isoformat()
            if summary.projected_debt_free_date
            else "Not projected",
        ],
        [
            "Projected Payoff Duration",
            f"{summary.projected_payoff_duration_days} days"
            if summary.projected_payoff_duration_days is not None
            else "Not projected",
        ],
        ["Total Projected Interest", format_currency(summary.total_projected_interest)],
        ["First-Period Snowball", format_currency(summary.first_period_snowball_amount)],
        ["Ending Savings", format_currency(summary.ending_savings)],
        ["Emergency Fund Status", "Met" if summary.savings_goal_met else "Not met"],
    ]
    print_table(["Metric", "Value"], rows, output_func)
    print_success("Plan generated in memory.", output_func)


def replace_budget_setup(setup: BudgetSetupResult, **changes) -> BudgetSetupResult:
    """Return a setup result with selected fields replaced."""
    values = {
        "plan_name": setup.plan_name,
        "pay_frequency": setup.pay_frequency,
        "first_paycheck_date": setup.first_paycheck_date,
        "net_paycheck_amount": setup.net_paycheck_amount,
        "debts": setup.debts,
        "bills": setup.bills,
        "monthly_personal_spending": setup.monthly_personal_spending,
        "current_savings": setup.current_savings,
        "emergency_fund_target": setup.emergency_fund_target,
    }
    values.update(changes)
    return BudgetSetupResult(**values)
