"""Interactive viewer for generated in-memory plan results."""

from datetime import date
from decimal import Decimal

from app.console import OutputFunc, print_section_header, print_table, print_warning
from app.debt_input import total_debt_balance
from app.menu import InputFunc, MenuOption, display_menu
from app.money import format_currency
from app.plan_save import PlanSaveState, run_save_plan_workflow
from app.plan_setup import pay_frequency_label
from app.savings_setup import strategy_label

TIMELINE_PAGE_SIZE = 20


def view_results(
    generated_plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    save_workflow=run_save_plan_workflow,
    save_state: PlanSaveState | None = None,
) -> None:
    """Display generated results until the user returns to the main menu."""
    save_state = save_state or PlanSaveState()
    while True:
        print_results_summary(generated_plan, output_func)
        display_menu(
            "Results",
            [
                MenuOption("1", "View Debt Summary", lambda: True),
                MenuOption("2", "View Budget Summary", lambda: True),
                MenuOption("3", "View Payoff Timeline", lambda: True),
                MenuOption("4", "Save Plan", lambda: True),
                MenuOption("5", "Return to Main Menu", lambda: True),
            ],
            output_func,
        )
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            if screen_navigation(
                print_debt_summary,
                generated_plan,
                input_func,
                output_func,
            ):
                return
        elif choice == "2":
            if screen_navigation(
                print_budget_summary,
                generated_plan,
                input_func,
                output_func,
            ):
                return
        elif choice == "3":
            if view_payoff_timeline(generated_plan, input_func, output_func):
                return
        elif choice == "4":
            save_workflow(
                generated_plan,
                save_state,
                input_func=input_func,
                output_func=output_func,
            )
        elif choice == "5":
            return
        else:
            print_warning("Please choose one of: 1, 2, 3, 4, 5.", output_func)


def screen_navigation(
    renderer,
    generated_plan,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> bool:
    """Render one screen and return True when navigating to main menu."""
    while True:
        renderer(generated_plan, output_func)
        display_menu(
            "Navigation",
            [
                MenuOption("1", "Return to Results Summary", lambda: True),
                MenuOption("2", "Return to Main Menu", lambda: True),
            ],
            output_func,
        )
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return False
        if choice == "2":
            return True
        print_warning("Please choose one of: 1, 2.", output_func)


def print_results_summary(generated_plan, output_func: OutputFunc = print) -> None:
    """Print the top-level generated results summary."""
    setup = getattr(generated_plan, "setup", None)
    print_section_header("Results Summary", output_func)
    print_table(
        ["Section", "Metric", "Value"],
        [
            [
                "GENERAL",
                "Plan Name",
                available(getattr(generated_plan, "plan_name", None)),
            ],
            [
                "GENERAL",
                "Number of Debts",
                available(getattr(generated_plan, "debt_count", None)),
            ],
            [
                "GENERAL",
                "Total Starting Debt",
                money_value(getattr(generated_plan, "total_starting_debt", None)),
            ],
            [
                "PAYOFF",
                "Projected Debt-Free Date",
                date_value(getattr(generated_plan, "projected_debt_free_date", None)),
            ],
            [
                "PAYOFF",
                "Projected Payoff Duration",
                duration_value(
                    getattr(generated_plan, "projected_payoff_duration_days", None),
                ),
            ],
            [
                "PAYOFF",
                "Total Projected Interest",
                money_value(getattr(generated_plan, "total_projected_interest", None)),
            ],
            [
                "PAYOFF",
                "Total Projected Payments",
                money_value(getattr(generated_plan, "total_projected_payments", None)),
            ],
            [
                "BUDGET",
                "First-Period Snowball",
                money_value(
                    getattr(generated_plan, "first_period_snowball_amount", None),
                ),
            ],
            ["BUDGET", "Current Savings", setup_money(setup, "current_savings")],
            [
                "BUDGET",
                "Ending Savings",
                money_value(getattr(generated_plan, "ending_savings", None)),
            ],
            [
                "BUDGET",
                "Emergency-Fund Target",
                setup_money(setup, "emergency_fund_target"),
            ],
            [
                "BUDGET",
                "Savings Strategy",
                strategy_value(setup),
            ],
            [
                "BUDGET",
                "Emergency-Fund Status",
                status_value(getattr(generated_plan, "savings_goal_met", None)),
            ],
        ],
        output_func,
    )


def print_debt_summary(generated_plan, output_func: OutputFunc = print) -> None:
    """Print debt-level generated results."""
    setup = getattr(generated_plan, "setup", None)
    debts = list(getattr(setup, "debts", []) or [])
    print_section_header("Debt Summary", output_func)
    if not debts:
        print_warning("No debts available.", output_func)
        return

    payoff_by_name = {
        payoff.debt_name: payoff
        for payoff in getattr(
            getattr(generated_plan, "forecast", None),
            "debt_payoffs",
            [],
        )
    }
    print_table(
        ["Debt", "Starting Balance", "APR", "Minimum", "Payoff Order", "Payoff Date"],
        [
            [
                debt.name,
                format_currency(debt.balance),
                f"{debt.apr:.2f}%",
                format_currency(debt.minimum),
                str(debt.snowball_order),
                date_value(getattr(payoff_by_name.get(debt.name), "payoff_date", None)),
            ]
            for debt in sorted(debts, key=lambda debt: debt.snowball_order)
        ],
        output_func,
    )
    output_func(f"Total Starting Debt: {format_currency(total_debt_balance(debts))}")


def print_budget_summary(generated_plan, output_func: OutputFunc = print) -> None:
    """Print setup budget values and forecast ending values."""
    setup = getattr(generated_plan, "setup", None)
    print_section_header("Budget Summary", output_func)
    print_table(
        ["Section", "Metric", "Value"],
        [
            ["Income", "Paycheck Amount", setup_money(setup, "net_paycheck_amount")],
            ["Income", "Pay Frequency", setup_frequency(setup)],
            ["Budget", "Monthly Bills", money_value(sum_bill_amounts(setup))],
            [
                "Budget",
                "Monthly Personal Spending",
                setup_money(setup, "monthly_personal_spending"),
            ],
            ["Budget", "Current Savings", setup_money(setup, "current_savings")],
            [
                "Budget",
                "Emergency-Fund Target",
                setup_money(setup, "emergency_fund_target"),
            ],
            [
                "Budget",
                "Savings Strategy",
                strategy_value(setup),
            ],
            [
                "Forecast",
                "First-Period Snowball",
                money_value(
                    getattr(generated_plan, "first_period_snowball_amount", None),
                ),
            ],
            [
                "Forecast",
                "Ending Savings",
                money_value(getattr(generated_plan, "ending_savings", None)),
            ],
        ],
        output_func,
    )


def view_payoff_timeline(
    generated_plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display paginated forecast periods. Return True for main-menu navigation."""
    periods = list(getattr(getattr(generated_plan, "forecast", None), "periods", []) or [])
    if not periods:
        print_section_header("Payoff Timeline", output_func)
        output_func("Not available.")
        return timeline_navigation(False, input_func, output_func) == "main"

    page = 0
    while True:
        print_timeline_page(generated_plan, periods, page, output_func)
        action = timeline_navigation(
            len(periods) > TIMELINE_PAGE_SIZE,
            input_func,
            output_func,
        )
        if action == "summary":
            return False
        if action == "main":
            return True
        if action == "next":
            page = min(page + 1, max_page(periods))
        elif action == "previous":
            page = max(page - 1, 0)


def print_timeline_page(
    generated_plan,
    periods: list,
    page: int,
    output_func: OutputFunc = print,
) -> None:
    """Print one payoff timeline page."""
    start = page * TIMELINE_PAGE_SIZE
    end = start + TIMELINE_PAGE_SIZE
    payoff_events = payoff_events_by_date(generated_plan)
    print_section_header(
        f"Payoff Timeline Page {page + 1} of {max_page(periods) + 1}",
        output_func,
    )
    print_table(
        ["Date", "Snowball", "Major Payoff Events", "Ending Debt", "Ending Savings"],
        [
            [
                date_value(getattr(period, "paycheck_date", None)),
                money_value(getattr(period, "snowball_paid", None)),
                ", ".join(
                    payoff_events.get(getattr(period, "paycheck_date", None), []),
                )
                or "None",
                money_value(getattr(period, "total_debt_balance", None)),
                money_value(getattr(period, "savings_balance", None)),
            ]
            for period in periods[start:end]
        ],
        output_func,
    )


def timeline_navigation(
    paginated: bool,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str | bool:
    """Navigate timeline pages or return out of the viewer."""
    options = []
    if paginated:
        options.extend(
            [
                MenuOption("1", "Next Page", lambda: True),
                MenuOption("2", "Previous Page", lambda: True),
                MenuOption("3", "Return to Results Summary", lambda: True),
                MenuOption("4", "Return to Main Menu", lambda: True),
            ]
        )
    else:
        options.extend(
            [
                MenuOption("1", "Return to Results Summary", lambda: True),
                MenuOption("2", "Return to Main Menu", lambda: True),
            ]
        )
    while True:
        display_menu("Timeline Navigation", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if paginated:
            if choice == "1":
                return "next"
            if choice == "2":
                return "previous"
            if choice == "3":
                return "summary"
            if choice == "4":
                return "main"
            print_warning("Please choose one of: 1, 2, 3, 4.", output_func)
        else:
            if choice == "1":
                return "summary"
            if choice == "2":
                return "main"
            print_warning("Please choose one of: 1, 2.", output_func)


def payoff_events_by_date(generated_plan) -> dict[date, list[str]]:
    """Map forecast payoff dates to debt names."""
    events: dict[date, list[str]] = {}
    for payoff in (
        getattr(getattr(generated_plan, "forecast", None), "debt_payoffs", []) or []
    ):
        payoff_date = getattr(payoff, "payoff_date", None)
        if payoff_date is None:
            continue
        events.setdefault(payoff_date, []).append(payoff.debt_name)
    return events


def max_page(periods: list) -> int:
    """Return the highest zero-based page index."""
    return max((len(periods) - 1) // TIMELINE_PAGE_SIZE, 0)


def available(value) -> str:
    """Return a printable value or Not available."""
    if value is None:
        return "Not available."
    return str(value)


def money_value(value) -> str:
    """Return a printable money value or Not available."""
    if value is None:
        return "Not available."
    return format_currency(value)


def date_value(value) -> str:
    """Return an ISO date or Not available."""
    if value is None:
        return "Not available."
    return value.isoformat()


def duration_value(value) -> str:
    """Return a duration string or Not available."""
    if value is None:
        return "Not available."
    return f"{value} days"


def status_value(value) -> str:
    """Return a savings status string or Not available."""
    if value is None:
        return "Not available."
    return "Met" if value else "Not met"


def setup_money(setup, field: str) -> str:
    """Return a money value from setup or Not available."""
    return money_value(getattr(setup, field, None))


def setup_frequency(setup) -> str:
    """Return setup pay frequency or Not available."""
    pay_frequency = getattr(setup, "pay_frequency", None)
    if pay_frequency is None:
        return "Not available."
    return pay_frequency_label(pay_frequency)


def strategy_value(setup) -> str:
    """Return setup savings strategy or Not available."""
    return strategy_label(getattr(setup, "savings_strategy", None))


def sum_bill_amounts(setup) -> Decimal | None:
    """Return setup bill total when setup is available."""
    if setup is None:
        return None
    return sum(
        (bill.amount for bill in getattr(setup, "bills", []) or []),
        Decimal("0.00"),
    )
