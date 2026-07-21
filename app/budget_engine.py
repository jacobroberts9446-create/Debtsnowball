"""
budget_engine.py

Coordinates scheduled bills, debt payments, savings, and snowball payments
for each pay period.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from app.calendar_engine import CalendarEngine
from app.debt_engine import DebtEngine
from app.models import (
    PlannedWithdrawalResult,
    SavingsFundingMode,
    SavingsGoalStatus,
    SavingsStageResult,
)
from app.scheduler import Scheduler


MONEY = Decimal("0.01")


@dataclass
class DebtBalance:
    """Snapshot of a debt after a pay period is processed."""

    name: str
    balance: float
    minimum: float
    status: str


@dataclass
class PayPeriodSummary:
    """Budget result for one pay period."""

    pay_date: date
    start_date: date
    end_date: date
    income: float
    bills_paid: float
    debt_minimums: float
    snowball_payment: float
    savings_contribution: float
    savings_balance: float
    savings_goal: float
    remaining_cash: float
    active_debt_balances: list[DebtBalance]
    paid_off_debts: list[DebtBalance]
    active_savings_goal_name: str | None = None
    active_savings_target: float | None = None
    savings_goal_target_date: date | None = None
    savings_balance_before_withdrawal: float | None = None
    planned_withdrawal_amount: float = 0.0
    savings_balance_after_withdrawal: float | None = None
    withdrawal_name: str | None = None
    goal_progress_percentage: float | None = None
    savings_stage_changed: bool = False
    available_after_required_payments: float = 0.0
    normal_savings_contribution: float = 0.0
    deadline_required_savings_contribution: float = 0.0
    snowball_before_savings_adjustment: float = 0.0
    snowball_reduction: float = 0.0
    personal_expense_reduction: float = 0.0
    projected_savings_shortfall: float = 0.0


@dataclass
class SavingsAllocation:
    """Savings and snowball allocation details for one pay period."""

    savings_contribution: float
    snowball_amount: float
    normal_savings_contribution: float
    deadline_required_savings_contribution: float
    snowball_before_adjustment: float
    snowball_reduction: float
    personal_expense_reduction: float
    projected_shortfall: float


class BudgetEngine:
    """Builds pay-period budget summaries from project configuration."""

    def __init__(
        self,
        config,
        extra_snowball_per_paycheck: Decimal = Decimal("0.00"),
        savings_percentage_override: Decimal | None = None,
    ):
        self.config = config
        self.settings = config.settings
        self.scheduler = Scheduler(config)
        self.debt_engine = DebtEngine(config.debts)
        self.savings_balance = round(float(self.settings.starting_savings), 2)
        self.extra_snowball_per_paycheck = self._money_float(
            extra_snowball_per_paycheck
        )
        self.savings_percentage_override = savings_percentage_override
        self.savings_plan = getattr(config, "savings_plan", None)
        self._applied_withdrawal_indexes: set[int] = set()
        self._seen_stage_names: set[str] = set()
        self._stage_results: dict[str, SavingsStageResult] = {}
        self._withdrawal_results: list[PlannedWithdrawalResult] = []
        self._last_active_stage_name: str | None = None

    def build_plan(self, periods) -> list[PayPeriodSummary]:
        """Process each pay period and return budget summaries."""
        return [self.process_pay_period(period) for period in periods]

    def process_pay_period(self, period) -> PayPeriodSummary:
        """Process scheduled payments, savings, and debt snowball for one period."""
        withdrawal_context = self._process_savings_events(period.pay_date)
        active_stage = self._active_savings_stage(period.pay_date)
        stage_changed = withdrawal_context["stage_changed"]
        if active_stage is not None and active_stage.name != self._last_active_stage_name:
            stage_changed = True
            self._last_active_stage_name = active_stage.name
        self._ensure_stage_started(active_stage)

        scheduled_payments = self.scheduler.payments_for_period(period)
        bills_paid = self._scheduled_bill_total(scheduled_payments)
        reserved_minimums = self._scheduled_debt_minimum_total(scheduled_payments)
        surplus = self._surplus_after_required_payments(
            bills_paid=bills_paid,
            debt_minimums=reserved_minimums,
        )

        allocation = self._split_surplus(surplus, active_stage, period.pay_date)
        bills_paid = round(bills_paid - allocation.personal_expense_reduction, 2)
        self._record_stage_progress(active_stage, period.pay_date)
        snowball_amount = round(
            allocation.snowball_amount + self.extra_snowball_per_paycheck,
            2,
        )
        debt_engine_snowball = self._debt_engine_snowball_amount(snowball_amount)
        debt_result = self.debt_engine.process_pay_period(
            scheduled_payments=scheduled_payments,
            snowball_amount=debt_engine_snowball,
        )

        debt_minimums_paid = round(sum(debt_result["minimums"].values()), 2)
        snowball_paid = round(sum(debt_result["snowball"].values()), 2)
        remaining_cash = self._remaining_cash(
            bills_paid=bills_paid,
            debt_minimums=debt_minimums_paid,
            savings_contribution=allocation.savings_contribution,
            snowball_payment=snowball_paid,
        )

        return PayPeriodSummary(
            pay_date=period.pay_date,
            start_date=period.start_date,
            end_date=period.end_date,
            income=self.settings.paycheck,
            bills_paid=bills_paid,
            debt_minimums=debt_minimums_paid,
            snowball_payment=snowball_paid,
            savings_contribution=allocation.savings_contribution,
            savings_balance=self.savings_balance,
            savings_goal=self.settings.savings_goal,
            remaining_cash=remaining_cash,
            active_debt_balances=self._debt_balances(debt_result["active_debts"]),
            paid_off_debts=self._debt_balances(debt_result["paid_off_debts"]),
            active_savings_goal_name=active_stage.name if active_stage else None,
            active_savings_target=self._active_savings_target(),
            savings_goal_target_date=active_stage.target_date if active_stage else None,
            savings_balance_before_withdrawal=withdrawal_context[
                "balance_before_withdrawal"
            ],
            planned_withdrawal_amount=withdrawal_context["withdrawal_amount"],
            savings_balance_after_withdrawal=withdrawal_context[
                "balance_after_withdrawal"
            ],
            withdrawal_name=withdrawal_context["withdrawal_name"],
            goal_progress_percentage=self._goal_progress_percentage(),
            savings_stage_changed=stage_changed,
            available_after_required_payments=surplus,
            normal_savings_contribution=allocation.normal_savings_contribution,
            deadline_required_savings_contribution=(
                allocation.deadline_required_savings_contribution
            ),
            snowball_before_savings_adjustment=(
                allocation.snowball_before_adjustment
            ),
            snowball_reduction=allocation.snowball_reduction,
            personal_expense_reduction=allocation.personal_expense_reduction,
            projected_savings_shortfall=allocation.projected_shortfall,
        )

    def _scheduled_bill_total(self, scheduled_payments) -> float:
        """Return fixed expenses plus scheduled non-debt bills for the pay period."""
        return round(
            self._fixed_expense_total()
            +
            sum(
                payment.amount
                for payment in scheduled_payments
                if payment.payment_type != "debt"
            ),
            2,
        )

    def _fixed_expense_total(self) -> float:
        """Return configured expenses that are reserved every paycheck."""
        return round(
            self.settings.rent_per_paycheck
            + self.settings.insurance_per_paycheck
            + self.settings.personal_per_paycheck,
            2,
        )

    def _scheduled_debt_minimum_total(self, scheduled_payments) -> float:
        """Return scheduled debt minimum total for the pay period."""
        return round(
            sum(
                self._debt_minimum_after_interest(payment.name)
                for payment in scheduled_payments
                if payment.payment_type == "debt"
            ),
            2,
        )

    def _debt_minimum_after_interest(self, debt_name: str) -> float:
        """Return the expected minimum payment after this period's interest."""
        debt = next(
            debt for debt in self.debt_engine.debts if debt.name == debt_name
        )
        balance_after_interest = debt.balance + (debt.balance * debt.rate_per_paycheck)
        return round(min(debt.minimum, balance_after_interest), 2)

    def _surplus_after_required_payments(
        self,
        bills_paid: float,
        debt_minimums: float,
    ) -> float:
        """Return cash left after bills and reserved debt minimums."""
        return round(max(self.settings.paycheck - bills_paid - debt_minimums, 0.0), 2)

    def _split_surplus(
        self,
        surplus: float,
        active_stage,
        pay_date: date,
    ) -> SavingsAllocation:
        """Apply the savings rule and return detailed allocation amounts."""
        if surplus <= 0:
            return SavingsAllocation(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        active_target = self._active_savings_target()
        if self.savings_balance >= active_target:
            snowball = round(surplus, 2)
            return SavingsAllocation(0.0, snowball, 0.0, 0.0, snowball, 0.0, 0.0, 0.0)

        savings_needed = round(active_target - self.savings_balance, 2)
        normal_savings = min(
            round(surplus * self._savings_percentage(), 2),
            savings_needed,
        )
        funding_mode = (
            active_stage.funding_mode
            if active_stage is not None
            else SavingsFundingMode.PERCENTAGE
        )
        deadline_required = 0.0
        projected_shortfall = 0.0
        personal_reduction = 0.0
        if funding_mode == SavingsFundingMode.DEADLINE_PRIORITY:
            deadline_required = self._deadline_required_savings(
                goal=active_stage,
                pay_date=pay_date,
            )
            snowball_available = round(max(surplus - normal_savings, 0.0), 2)
            redirected_snowball = min(
                snowball_available,
                round(max(savings_needed - normal_savings, 0.0), 2),
            )
            personal_reduction = self._personal_expense_reduction(
                goal=active_stage,
                pay_date=pay_date,
                current_discretionary_savings=round(
                    normal_savings + redirected_snowball,
                    2,
                ),
            )
            savings_contribution = min(
                round(normal_savings + redirected_snowball + personal_reduction, 2),
                savings_needed,
            )
            projected_shortfall = self._projected_shortfall_after_contribution(
                goal=active_stage,
                savings_contribution=savings_contribution,
            )
        elif funding_mode == SavingsFundingMode.PRIORITY_UNTIL_FUNDED:
            savings_contribution = min(surplus, savings_needed)
        else:
            savings_contribution = normal_savings

        self.savings_balance = round(self.savings_balance + savings_contribution, 2)
        snowball_amount = round(
            max(surplus - normal_savings - (savings_contribution - normal_savings), 0.0),
            2,
        )
        if funding_mode == SavingsFundingMode.DEADLINE_PRIORITY:
            snowball_amount = round(
                max(surplus - normal_savings - (savings_contribution - normal_savings - personal_reduction), 0.0),
                2,
            )
        snowball_before_adjustment = round(max(surplus - normal_savings, 0.0), 2)
        snowball_reduction = round(
            max(snowball_before_adjustment - snowball_amount, 0.0),
            2,
        )

        return SavingsAllocation(
            savings_contribution=savings_contribution,
            snowball_amount=snowball_amount,
            normal_savings_contribution=normal_savings,
            deadline_required_savings_contribution=deadline_required,
            snowball_before_adjustment=snowball_before_adjustment,
            snowball_reduction=snowball_reduction,
            personal_expense_reduction=personal_reduction,
            projected_shortfall=projected_shortfall,
        )

    def _savings_percentage(self) -> float:
        """Return the scenario-specific savings percentage, or the default rule."""
        if self.savings_percentage_override is None:
            active_stage = self._active_savings_stage_for_balance()
            if (
                active_stage is not None
                and active_stage.savings_percentage_override is not None
            ):
                return float(active_stage.savings_percentage_override)
            return 0.50

        return float(self.savings_percentage_override)

    def _deadline_required_savings(self, goal, pay_date: date) -> float:
        """Return the per-paycheck savings needed for a dated priority goal."""
        if goal is None or goal.target_date is None:
            return 0.0

        paychecks_remaining = self._eligible_paycheck_count(pay_date, goal.target_date)
        if paychecks_remaining <= 0:
            return 0.0

        amount_needed = max(
            self._money_decimal(goal.target_amount - self._money_decimal(self.savings_balance)),
            Decimal("0.00"),
        )
        if amount_needed == Decimal("0.00"):
            return 0.0

        required = (amount_needed / Decimal(paychecks_remaining)).quantize(
            MONEY,
            rounding=ROUND_CEILING,
        )
        return float(required)

    def _eligible_paycheck_count(self, pay_date: date, target_date: date) -> int:
        """Count paychecks whose pay date can still fund a target-date goal."""
        if pay_date > target_date:
            return 0

        calendar_engine = CalendarEngine(self.settings)
        return sum(
            1
            for period in calendar_engine.generate(target_date)
            if pay_date <= period.pay_date <= target_date
        )

    def _personal_expense_reduction(
        self,
        goal,
        pay_date: date,
        current_discretionary_savings: float,
    ) -> float:
        """Return the personal allowance reduction needed after snowball redirect."""
        if goal is None or goal.target_date is None:
            return 0.0

        eligible_paychecks = self._eligible_paycheck_count(pay_date, goal.target_date)
        if eligible_paychecks <= 0:
            return 0.0

        projected_available = self._projected_available_surplus(
            pay_date,
            goal.target_date,
        )
        projected_shortfall = max(
            self._money_decimal(
                goal.target_amount
                - self._money_decimal(self.savings_balance)
                - projected_available
            ),
            Decimal("0.00"),
        )
        if projected_shortfall == Decimal("0.00"):
            return 0.0

        per_paycheck_gap = (projected_shortfall / Decimal(eligible_paychecks)).quantize(
            MONEY,
            rounding=ROUND_CEILING,
        )
        current_remaining_need = max(
            self._money_decimal(
                goal.target_amount
                - self._money_decimal(self.savings_balance)
                - self._money_decimal(current_discretionary_savings)
            ),
            Decimal("0.00"),
        )
        reduction = min(
            per_paycheck_gap,
            self._money_decimal(self.settings.personal_per_paycheck),
            current_remaining_need,
        )
        return float(self._money_decimal(reduction))

    def _projected_available_surplus(
        self,
        start_pay_date: date,
        target_date: date,
    ) -> Decimal:
        """Project post-required cash available through a savings deadline."""
        calendar_engine = CalendarEngine(self.settings)
        total = Decimal("0.00")
        for period in calendar_engine.generate(target_date):
            if not start_pay_date <= period.pay_date <= target_date:
                continue

            scheduled_payments = self.scheduler.payments_for_period(period)
            bills_paid = self._scheduled_bill_total(scheduled_payments)
            debt_minimums = self._scheduled_debt_minimum_total(scheduled_payments)
            total += self._money_decimal(
                self._surplus_after_required_payments(
                    bills_paid=bills_paid,
                    debt_minimums=debt_minimums,
                )
            )

        return self._money_decimal(total)

    def _projected_shortfall_after_contribution(
        self,
        goal,
        savings_contribution: float,
    ) -> float:
        """Return remaining target shortfall after the current contribution."""
        if goal is None or goal.target_date is None:
            return 0.0

        projected_balance = self._money_decimal(self.savings_balance) + self._money_decimal(
            savings_contribution
        )
        shortfall = max(
            self._money_decimal(goal.target_amount - projected_balance),
            Decimal("0.00"),
        )
        return float(shortfall)

    def _debt_engine_snowball_amount(self, snowball_amount: float) -> float:
        """Return the snowball amount to pass before debt-engine rollover is added."""
        return round(
            max(snowball_amount - self.debt_engine.freed_minimum_payment, 0.0),
            2,
        )

    def _remaining_cash(
        self,
        bills_paid: float,
        debt_minimums: float,
        savings_contribution: float,
        snowball_payment: float,
    ) -> float:
        """Return cash left after all budgeted outflows."""
        return round(
            self.settings.paycheck
            - bills_paid
            - debt_minimums
            - savings_contribution
            - snowball_payment,
            2,
        )

    def _debt_balances(self, debts) -> list[DebtBalance]:
        """Convert debt engine snapshots to debt balance dataclasses."""
        return [
            DebtBalance(
                name=debt["name"],
                balance=round(debt["balance"], 2),
                minimum=round(debt["minimum"], 2),
                status=debt["status"],
            )
            for debt in debts
        ]

    def _money_float(self, value) -> float:
        """Convert Decimal-compatible money values to a rounded float."""
        return float(Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP))

    def savings_stage_results(self) -> list[SavingsStageResult]:
        """Return savings stage progress snapshots."""
        self._finalize_savings_stages()
        return list(self._stage_results.values())

    def planned_withdrawal_results(self) -> list[PlannedWithdrawalResult]:
        """Return planned withdrawal application results."""
        return list(self._withdrawal_results)

    def _process_savings_events(self, pay_date: date) -> dict[str, object]:
        context = {
            "balance_before_withdrawal": None,
            "withdrawal_amount": 0.0,
            "balance_after_withdrawal": None,
            "withdrawal_name": None,
            "stage_changed": False,
        }
        if self.savings_plan is None:
            return context

        self._evaluate_due_stage_deadlines(pay_date)
        for index, withdrawal in enumerate(self.savings_plan.withdrawals):
            if index in self._applied_withdrawal_indexes:
                continue
            if withdrawal.withdrawal_date > pay_date:
                continue

            before = self._money_decimal(self.savings_balance)
            requested = before if withdrawal.drain_balance else withdrawal.amount
            actual = min(requested, before)
            after = self._money_decimal(before - actual)
            shortfall = self._money_decimal(requested - actual)
            self.savings_balance = float(after)
            self._applied_withdrawal_indexes.add(index)
            self._withdrawal_results.append(
                PlannedWithdrawalResult(
                    name=withdrawal.name,
                    scheduled_date=withdrawal.withdrawal_date,
                    requested_amount=requested,
                    drain_balance=withdrawal.drain_balance,
                    actual_amount_withdrawn=actual,
                    balance_before=before,
                    balance_after=after,
                    applied_date=pay_date,
                    shortfall=shortfall,
                    status="applied" if shortfall == Decimal("0.00") else "partial",
                )
            )
            context = {
                "balance_before_withdrawal": float(before),
                "withdrawal_amount": float(actual),
                "balance_after_withdrawal": float(after),
                "withdrawal_name": withdrawal.name,
                "stage_changed": True,
            }

        return context

    def _active_savings_stage(self, pay_date: date):
        if self.savings_plan is None or not self.savings_plan.goals:
            return None

        active = [
            goal for goal in self.savings_plan.goals if goal.start_date <= pay_date
        ]
        if not active:
            return None

        return active[-1]

    def _active_savings_stage_for_balance(self):
        if self.savings_plan is None or not self.savings_plan.goals:
            return None

        if self._last_active_stage_name is None:
            return None

        return next(
            goal
            for goal in self.savings_plan.goals
            if goal.name == self._last_active_stage_name
        )

    def _active_savings_target(self) -> float:
        active_stage = self._active_savings_stage_for_balance()
        if active_stage is None:
            return self.settings.savings_goal

        return float(active_stage.target_amount)

    def _ensure_stage_started(self, active_stage) -> None:
        if active_stage is None or active_stage.name in self._stage_results:
            return

        if active_stage.starting_balance_override is not None:
            self.savings_balance = float(active_stage.starting_balance_override)

        self._seen_stage_names.add(active_stage.name)
        self._stage_results[active_stage.name] = SavingsStageResult(
            goal_name=active_stage.name,
            start_date=active_stage.start_date,
            target_date=active_stage.target_date,
            target_amount=active_stage.target_amount,
            starting_balance=self._money_decimal(self.savings_balance),
            ending_balance=self._money_decimal(self.savings_balance),
            status=SavingsGoalStatus.ACTIVE,
        )

    def _record_stage_progress(self, active_stage, pay_date: date) -> None:
        if active_stage is None:
            return

        result = self._stage_results[active_stage.name]
        result.ending_balance = self._money_decimal(self.savings_balance)
        result.amount_needed = max(
            self._money_decimal(active_stage.target_amount - result.ending_balance),
            Decimal("0.00"),
        )
        result.eligible_paychecks_remaining = (
            self._eligible_paycheck_count(pay_date, active_stage.target_date)
            if active_stage.target_date is not None
            else 0
        )
        result.projected_available_contributions = self._money_decimal(
            result.ending_balance - result.starting_balance
        )
        if active_stage.target_date is not None:
            result.projected_balance_at_deadline = result.ending_balance
            result.projected_shortfall = result.amount_needed
            result.additional_funding_needed = result.amount_needed
            result.feasible_under_current_plan = result.amount_needed == Decimal("0.00")
        if (
            result.achieved_date is None
            and result.ending_balance >= result.target_amount
        ):
            result.achieved_date = pay_date
            result.status = self._achieved_status(active_stage, pay_date)
            result.days_early_or_late = self._days_early_or_late(active_stage, pay_date)

    def _evaluate_due_stage_deadlines(self, pay_date: date) -> None:
        for goal in self.savings_plan.goals:
            if goal.target_date is None or goal.target_date > pay_date:
                continue
            self._ensure_stage_started(goal)
            result = self._stage_results[goal.name]
            if result.amount_at_deadline is not None:
                continue

            amount = self._money_decimal(self.savings_balance)
            result.amount_at_deadline = amount
            result.shortfall_at_deadline = max(
                self._money_decimal(goal.target_amount - amount),
                Decimal("0.00"),
            )
            result.amount_needed = result.shortfall_at_deadline
            result.eligible_paychecks_remaining = 0
            result.projected_balance_at_deadline = amount
            result.projected_shortfall = result.shortfall_at_deadline
            result.additional_funding_needed = result.shortfall_at_deadline
            result.feasible_under_current_plan = (
                result.shortfall_at_deadline == Decimal("0.00")
            )
            if result.achieved_date is None and amount >= goal.target_amount:
                result.achieved_date = goal.target_date
                result.status = SavingsGoalStatus.ACHIEVED_ON_TIME
                result.days_early_or_late = 0
            elif result.achieved_date is None:
                result.status = SavingsGoalStatus.NOT_ACHIEVED

    def _finalize_savings_stages(self) -> None:
        if self.savings_plan is None:
            return

        for goal in self.savings_plan.goals:
            if goal.name not in self._stage_results:
                self._stage_results[goal.name] = SavingsStageResult(
                    goal_name=goal.name,
                    start_date=goal.start_date,
                    target_date=goal.target_date,
                    target_amount=goal.target_amount,
                    starting_balance=Decimal("0.00"),
                    status=SavingsGoalStatus.UPCOMING,
                )
            result = self._stage_results[goal.name]
            if result.achieved_date is not None:
                result.status = self._achieved_status(goal, result.achieved_date)
                result.days_early_or_late = self._days_early_or_late(
                    goal,
                    result.achieved_date,
                )
            elif result.status != SavingsGoalStatus.NOT_ACHIEVED:
                result.status = (
                    SavingsGoalStatus.ACTIVE
                    if goal.name in self._seen_stage_names
                    else SavingsGoalStatus.UPCOMING
                )

    def _achieved_status(self, goal, achieved_date: date) -> SavingsGoalStatus:
        if goal.target_date is None:
            return SavingsGoalStatus.ACHIEVED_ON_TIME
        if achieved_date < goal.target_date:
            return SavingsGoalStatus.ACHIEVED_EARLY
        if achieved_date == goal.target_date:
            return SavingsGoalStatus.ACHIEVED_ON_TIME
        return SavingsGoalStatus.ACHIEVED_LATE

    def _days_early_or_late(self, goal, achieved_date: date) -> int | None:
        if goal.target_date is None:
            return None

        return (achieved_date - goal.target_date).days

    def _goal_progress_percentage(self) -> float | None:
        target = self._active_savings_target()
        if target <= 0:
            return None

        return round(min(self.savings_balance / target, 1.0), 4)

    def _money_decimal(self, value) -> Decimal:
        return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
