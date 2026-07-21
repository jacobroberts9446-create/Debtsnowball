"""
forecast_engine.py

Projects future payoff, savings, and interest milestones without mutating
the live application configuration.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal

from app.budget_engine import BudgetEngine, PayPeriodSummary
from app.calendar_engine import CalendarEngine
from app.money import ZERO_MONEY, money, to_decimal
from app.models import DebtPayoffForecast, ForecastPeriod, ForecastSummary


class ForecastEngine:
    """Simulate future pay periods and summarize payoff milestones."""

    def __init__(
        self,
        config: object,
        max_years: int = 30,
        extra_snowball_per_paycheck: Decimal = Decimal("0.00"),
        savings_percentage_override: Decimal | None = None,
    ) -> None:
        self.config = config
        self.max_years = max_years
        self.extra_snowball_per_paycheck = extra_snowball_per_paycheck
        self.savings_percentage_override = savings_percentage_override

    def forecast(self) -> ForecastSummary:
        """Run the forecast and return a structured forecast summary."""
        forecast_config = deepcopy(self.config)
        settings = forecast_config.settings
        forecast_start_date = settings.first_paycheck
        starting_debt = self._starting_debt(forecast_config.debts)
        savings_goal = self._money(settings.savings_goal)
        starting_savings = self._money(settings.starting_savings)

        debt_payoffs = self._initial_debt_payoffs(forecast_config.debts)
        savings_goal_date = (
            forecast_start_date if starting_savings >= savings_goal else None
        )
        debt_free_date = forecast_start_date if starting_debt == ZERO_MONEY else None

        periods: list[ForecastPeriod] = []
        total_minimums = ZERO_MONEY
        total_snowball = ZERO_MONEY
        forecast_end_date = forecast_start_date

        if debt_free_date is not None and savings_goal_date is not None:
            return self._summary(
                forecast_start_date=forecast_start_date,
                forecast_end_date=forecast_end_date,
                debt_free_date=debt_free_date,
                savings_goal_date=savings_goal_date,
                starting_debt=starting_debt,
                total_minimums=total_minimums,
                total_snowball=total_snowball,
                ending_savings=starting_savings,
                remaining_debt=ZERO_MONEY,
                debt_payoffs=debt_payoffs,
                periods=periods,
                debt_engine=None,
            )

        budget_engine = BudgetEngine(
            forecast_config,
            extra_snowball_per_paycheck=self.extra_snowball_per_paycheck,
            savings_percentage_override=self.savings_percentage_override,
        )
        calendar_engine = CalendarEngine(settings)
        horizon_end = self._add_years(forecast_start_date, self.max_years)

        for period in calendar_engine.generate(horizon_end):
            summary = budget_engine.process_pay_period(period)
            forecast_end_date = summary.pay_date

            minimums_paid = self._money(summary.debt_minimums)
            snowball_paid = self._money(summary.snowball_payment)
            total_minimums += minimums_paid
            total_snowball += snowball_paid

            interest_paid = self._interest_paid_this_period(
                budget_engine.debt_engine,
                periods,
            )
            total_debt_balance = self._active_debt_total(summary)
            savings_balance = self._money(summary.savings_balance)

            periods.append(
                ForecastPeriod(
                    paycheck_date=summary.pay_date,
                    total_debt_balance=total_debt_balance,
                    savings_balance=savings_balance,
                    interest_paid=interest_paid,
                    minimums_paid=minimums_paid,
                    snowball_paid=snowball_paid,
                    active_savings_goal_name=summary.active_savings_goal_name,
                    active_savings_target=self._optional_money(
                        summary.active_savings_target
                    ),
                    savings_balance_before_withdrawal=self._optional_money(
                        summary.savings_balance_before_withdrawal
                    ),
                    planned_withdrawal_amount=self._money(
                        summary.planned_withdrawal_amount
                    ),
                    savings_balance_after_withdrawal=self._optional_money(
                        summary.savings_balance_after_withdrawal
                    ),
                    savings_contribution=self._money(summary.savings_contribution),
                    ending_savings_balance=savings_balance,
                    goal_progress_percentage=self._optional_decimal(
                        summary.goal_progress_percentage
                    ),
                    savings_stage_changed=summary.savings_stage_changed,
                    required_fixed_expenses=self._money(
                        summary.required_fixed_expenses
                    ),
                    normal_personal_allowance=self._money(
                        summary.normal_personal_allowance
                    ),
                    actual_personal_allowance=self._money(
                        summary.actual_personal_allowance
                    ),
                    available_after_required_payments=self._money(
                        summary.available_after_required_payments
                    ),
                    normal_savings_contribution=self._money(
                        summary.normal_savings_contribution
                    ),
                    deadline_required_savings_contribution=self._money(
                        summary.deadline_required_savings_contribution
                    ),
                    snowball_before_savings_adjustment=self._money(
                        summary.snowball_before_savings_adjustment
                    ),
                    snowball_reduction=self._money(summary.snowball_reduction),
                    personal_expense_reduction=self._money(
                        summary.personal_expense_reduction
                    ),
                    projected_savings_shortfall=self._money(
                        summary.projected_savings_shortfall
                    ),
                )
            )

            self._record_payoffs(
                debt_payoffs=debt_payoffs,
                summary=summary,
                paycheck_date=summary.pay_date,
                debt_engine=budget_engine.debt_engine,
            )

            if savings_goal_date is None and savings_balance >= savings_goal:
                savings_goal_date = summary.pay_date

            if debt_free_date is None and total_debt_balance == ZERO_MONEY:
                debt_free_date = summary.pay_date

            if debt_free_date is not None and savings_goal_date is not None:
                break

        remaining_debt = periods[-1].total_debt_balance if periods else starting_debt
        ending_savings = periods[-1].savings_balance if periods else starting_savings

        return self._summary(
            forecast_start_date=forecast_start_date,
            forecast_end_date=forecast_end_date,
            debt_free_date=debt_free_date,
            savings_goal_date=savings_goal_date,
            starting_debt=starting_debt,
            total_minimums=total_minimums,
            total_snowball=total_snowball,
            ending_savings=ending_savings,
            remaining_debt=remaining_debt,
            debt_payoffs=debt_payoffs,
            periods=periods,
            debt_engine=budget_engine.debt_engine,
            savings_stage_results=budget_engine.savings_stage_results(),
            planned_withdrawal_results=budget_engine.planned_withdrawal_results(),
        )

    def _summary(
        self,
        forecast_start_date: date,
        forecast_end_date: date,
        debt_free_date: date | None,
        savings_goal_date: date | None,
        starting_debt: Decimal,
        total_minimums: Decimal,
        total_snowball: Decimal,
        ending_savings: Decimal,
        remaining_debt: Decimal,
        debt_payoffs: dict[str, DebtPayoffForecast],
        periods: list[ForecastPeriod],
        debt_engine: object | None,
        savings_stage_results=None,
        planned_withdrawal_results=None,
    ) -> ForecastSummary:
        total_interest = self._total_interest_paid(debt_engine)
        completed = debt_free_date is not None and savings_goal_date is not None

        if not completed and remaining_debt > ZERO_MONEY:
            debt_free_date = None

        return ForecastSummary(
            forecast_start_date=forecast_start_date,
            forecast_end_date=forecast_end_date,
            debt_free_date=debt_free_date,
            savings_goal_date=savings_goal_date,
            starting_debt=self._money(starting_debt),
            total_interest_paid=total_interest,
            total_minimum_payments=self._money(total_minimums),
            total_snowball_payments=self._money(total_snowball),
            ending_savings=self._money(ending_savings),
            remaining_debt=self._money(remaining_debt),
            completed=completed,
            debt_payoffs=list(debt_payoffs.values()),
            periods=periods,
            savings_stage_results=savings_stage_results or [],
            planned_withdrawal_results=planned_withdrawal_results or [],
        )

    def _record_payoffs(
        self,
        debt_payoffs: dict[str, DebtPayoffForecast],
        summary: PayPeriodSummary,
        paycheck_date: date,
        debt_engine: object,
    ) -> None:
        paid_off_names = {debt.name for debt in summary.paid_off_debts}
        if not paid_off_names:
            return

        paid_off_debts = {debt.name: debt for debt in debt_engine.paid_off_debts}
        for debt_name in paid_off_names:
            current = debt_payoffs[debt_name]
            if current.payoff_date is not None:
                continue

            paid_off_debt = paid_off_debts[debt_name]
            debt_payoffs[debt_name] = DebtPayoffForecast(
                debt_name=debt_name,
                starting_balance=current.starting_balance,
                payoff_date=paycheck_date,
                total_interest_paid=self._money(paid_off_debt.total_interest_paid),
                total_paid=self._money(paid_off_debt.total_paid),
            )

    def _initial_debt_payoffs(self, debts) -> dict[str, DebtPayoffForecast]:
        return {
            debt.name: DebtPayoffForecast(
                debt_name=debt.name,
                starting_balance=self._money(debt.balance),
                payoff_date=None,
                total_interest_paid=ZERO_MONEY,
                total_paid=ZERO_MONEY,
            )
            for debt in debts
        }

    def _starting_debt(self, debts) -> Decimal:
        return self._money(sum(self._money(debt.balance) for debt in debts))

    def _active_debt_total(self, summary: PayPeriodSummary) -> Decimal:
        return self._money(
            sum(self._money(debt.balance) for debt in summary.active_debt_balances)
        )

    def _interest_paid_this_period(
        self,
        debt_engine: object,
        periods: list[ForecastPeriod],
    ) -> Decimal:
        current_total = self._total_interest_paid(debt_engine)
        previous_total = sum(period.interest_paid for period in periods)
        return self._money(current_total - previous_total)

    def _total_interest_paid(self, debt_engine: object | None) -> Decimal:
        if debt_engine is None:
            return ZERO_MONEY

        active_interest = sum(
            self._money(debt["total_interest_paid"]) for debt in debt_engine.summary()
        )
        paid_interest = sum(
            self._money(debt["total_interest_paid"])
            for debt in debt_engine.paid_off_summary()
        )
        return self._money(active_interest + paid_interest)

    def _money(self, value) -> Decimal:
        return money(value)

    def _optional_money(self, value) -> Decimal | None:
        if value is None:
            return None

        return self._money(value)

    def _optional_decimal(self, value) -> Decimal | None:
        if value is None:
            return None

        return to_decimal(value)

    def _add_years(self, value: date, years: int) -> date:
        try:
            return value.replace(year=value.year + years)
        except ValueError:
            return value.replace(year=value.year + years, day=28)
