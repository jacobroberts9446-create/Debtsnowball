"""
budget_engine.py

Coordinates scheduled bills, debt payments, savings, and snowball payments
for each pay period.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING

from app.calendar_engine import CalendarEngine
from app.debt_engine import DebtEngine
from app.money import CENT, ZERO_MONEY, money, to_decimal
from app.models import (
    PlannedWithdrawalResult,
    SavingsFundingMode,
    SavingsGoalStatus,
    SavingsStageResult,
)
from app.scheduler import Scheduler


@dataclass
class DebtBalance:
    """Snapshot of a debt after a pay period is processed."""

    name: str
    balance: Decimal
    minimum: Decimal
    status: str


@dataclass
class PayPeriodSummary:
    """Budget result for one pay period."""

    pay_date: date
    start_date: date
    end_date: date
    income: Decimal
    bills_paid: Decimal
    debt_minimums: Decimal
    snowball_payment: Decimal
    savings_contribution: Decimal
    savings_balance: Decimal
    savings_goal: Decimal
    remaining_cash: Decimal
    active_debt_balances: list[DebtBalance]
    paid_off_debts: list[DebtBalance]
    active_savings_goal_name: str | None = None
    active_savings_target: Decimal | None = None
    savings_goal_target_date: date | None = None
    savings_balance_before_withdrawal: Decimal | None = None
    planned_withdrawal_amount: Decimal = ZERO_MONEY
    savings_balance_after_withdrawal: Decimal | None = None
    withdrawal_name: str | None = None
    goal_progress_percentage: Decimal | None = None
    savings_stage_changed: bool = False
    required_fixed_expenses: Decimal = ZERO_MONEY
    normal_personal_allowance: Decimal = ZERO_MONEY
    actual_personal_allowance: Decimal = ZERO_MONEY
    available_after_required_payments: Decimal = ZERO_MONEY
    normal_savings_contribution: Decimal = ZERO_MONEY
    deadline_required_savings_contribution: Decimal = ZERO_MONEY
    snowball_before_savings_adjustment: Decimal = ZERO_MONEY
    snowball_reduction: Decimal = ZERO_MONEY
    personal_expense_reduction: Decimal = ZERO_MONEY
    projected_savings_shortfall: Decimal = ZERO_MONEY


@dataclass
class SavingsAllocation:
    """Savings and snowball allocation details for one pay period."""

    savings_contribution: Decimal
    snowball_amount: Decimal
    normal_savings_contribution: Decimal
    deadline_required_savings_contribution: Decimal
    snowball_before_adjustment: Decimal
    snowball_reduction: Decimal
    personal_expense_reduction: Decimal
    projected_shortfall: Decimal


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
        self.savings_balance = money(self.settings.starting_savings)
        self.extra_snowball_per_paycheck = money(extra_snowball_per_paycheck)
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
        self._record_stage_progress(active_stage, period.pay_date)
        snowball_amount = money(
            allocation.snowball_amount + self.extra_snowball_per_paycheck,
        )
        debt_engine_snowball = self._debt_engine_snowball_amount(snowball_amount)
        debt_result = self.debt_engine.process_pay_period(
            scheduled_payments=scheduled_payments,
            snowball_amount=debt_engine_snowball,
        )

        debt_minimums_paid = money(sum(debt_result["minimums"].values(), ZERO_MONEY))
        snowball_paid = money(sum(debt_result["snowball"].values(), ZERO_MONEY))
        remaining_cash = self._remaining_cash(
            bills_paid=bills_paid,
            debt_minimums=debt_minimums_paid,
            savings_contribution=allocation.savings_contribution,
            snowball_payment=snowball_paid,
            personal_expense_reduction=allocation.personal_expense_reduction,
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
            required_fixed_expenses=bills_paid,
            normal_personal_allowance=self.settings.personal_per_paycheck,
            actual_personal_allowance=money(
                self.settings.personal_per_paycheck
                - allocation.personal_expense_reduction,
            ),
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

    def _scheduled_bill_total(self, scheduled_payments) -> Decimal:
        """Return fixed expenses plus scheduled non-debt bills for the pay period."""
        return money(
            self._fixed_expense_total()
            +
            sum(
                (
                    payment.amount
                    for payment in scheduled_payments
                    if payment.payment_type != "debt"
                ),
                ZERO_MONEY,
            ),
        )

    def _fixed_expense_total(self) -> Decimal:
        """Return configured expenses that are reserved every paycheck."""
        return money(
            self.settings.rent_per_paycheck
            + self.settings.insurance_per_paycheck
            + self.settings.personal_per_paycheck,
        )

    def _scheduled_debt_minimum_total(self, scheduled_payments) -> Decimal:
        """Return scheduled debt minimum total for the pay period."""
        return money(
            sum(
                (
                    self._debt_minimum_after_interest(payment.name)
                    for payment in scheduled_payments
                    if payment.payment_type == "debt"
                ),
                ZERO_MONEY,
            )
        )

    def _debt_minimum_after_interest(self, debt_name: str) -> Decimal:
        """Return the expected minimum payment after this period's interest."""
        debt = next(
            debt for debt in self.debt_engine.debts if debt.name == debt_name
        )
        balance_after_interest = debt.balance + (debt.balance * debt.rate_per_paycheck)
        return money(min(debt.minimum, balance_after_interest))

    def _surplus_after_required_payments(
        self,
        bills_paid: Decimal,
        debt_minimums: Decimal,
    ) -> Decimal:
        """Return cash left after bills and reserved debt minimums."""
        return money(max(self.settings.paycheck - bills_paid - debt_minimums, ZERO_MONEY))

    def _split_surplus(
        self,
        surplus: Decimal,
        active_stage,
        pay_date: date,
    ) -> SavingsAllocation:
        """Apply the savings rule and return detailed allocation amounts."""
        if surplus <= ZERO_MONEY:
            return SavingsAllocation(
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
            )

        active_target = self._active_savings_target()
        if self.savings_balance >= active_target:
            snowball = money(surplus)
            return SavingsAllocation(
                ZERO_MONEY,
                snowball,
                ZERO_MONEY,
                ZERO_MONEY,
                snowball,
                ZERO_MONEY,
                ZERO_MONEY,
                ZERO_MONEY,
            )

        savings_needed = money(active_target - self.savings_balance)
        normal_savings = min(
            money(surplus * self._savings_percentage()),
            savings_needed,
        )
        funding_mode = (
            active_stage.funding_mode
            if active_stage is not None
            else SavingsFundingMode.PERCENTAGE
        )
        deadline_required = ZERO_MONEY
        projected_shortfall = ZERO_MONEY
        personal_reduction = ZERO_MONEY
        if funding_mode == SavingsFundingMode.DEADLINE_PRIORITY:
            deadline_required = self._deadline_required_savings(
                goal=active_stage,
                pay_date=pay_date,
            )
            snowball_available = money(max(surplus - normal_savings, ZERO_MONEY))
            redirected_snowball = min(
                snowball_available,
                money(max(savings_needed - normal_savings, ZERO_MONEY)),
            )
            personal_reduction = self._personal_expense_reduction(
                goal=active_stage,
                pay_date=pay_date,
                current_discretionary_savings=money(
                    normal_savings + redirected_snowball,
                ),
            )
            savings_contribution = min(
                money(normal_savings + redirected_snowball + personal_reduction),
                savings_needed,
            )
            projected_shortfall = self._projected_shortfall_after_contribution(
                goal=active_stage,
                pay_date=pay_date,
                savings_contribution=savings_contribution,
            )
        elif funding_mode == SavingsFundingMode.PRIORITY_UNTIL_FUNDED:
            savings_contribution = min(surplus, savings_needed)
        else:
            savings_contribution = normal_savings

        self.savings_balance = money(self.savings_balance + savings_contribution)
        snowball_amount = money(
            max(
                surplus - normal_savings - (savings_contribution - normal_savings),
                ZERO_MONEY,
            ),
        )
        if funding_mode == SavingsFundingMode.DEADLINE_PRIORITY:
            snowball_amount = money(
                max(
                    surplus
                    - normal_savings
                    - (savings_contribution - normal_savings - personal_reduction),
                    ZERO_MONEY,
                ),
            )
        snowball_before_adjustment = money(max(surplus - normal_savings, ZERO_MONEY))
        snowball_reduction = money(
            max(snowball_before_adjustment - snowball_amount, ZERO_MONEY),
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

    def _savings_percentage(self) -> Decimal:
        """Return the scenario-specific savings percentage, or the default rule."""
        if self.savings_percentage_override is None:
            active_stage = self._active_savings_stage_for_balance()
            if (
                active_stage is not None
                and active_stage.savings_percentage_override is not None
            ):
                return to_decimal(active_stage.savings_percentage_override)
            return Decimal("0.50")

        return to_decimal(self.savings_percentage_override)

    def _deadline_required_savings(self, goal, pay_date: date) -> Decimal:
        """Return the per-paycheck savings needed for a dated priority goal."""
        if goal is None or goal.target_date is None:
            return ZERO_MONEY

        paychecks_remaining = self._eligible_paycheck_count(pay_date, goal.target_date)
        if paychecks_remaining <= 0:
            return ZERO_MONEY

        amount_needed = max(
            money(goal.target_amount - money(self.savings_balance)),
            ZERO_MONEY,
        )
        if amount_needed == ZERO_MONEY:
            return ZERO_MONEY

        required = (amount_needed / Decimal(paychecks_remaining)).quantize(
            CENT,
            rounding=ROUND_CEILING,
        )
        return money(required)

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
        current_discretionary_savings: Decimal,
    ) -> Decimal:
        """Return the personal allowance reduction needed after snowball redirect."""
        if goal is None or goal.target_date is None:
            return ZERO_MONEY

        eligible_paychecks = self._eligible_paycheck_count(pay_date, goal.target_date)
        if eligible_paychecks <= 0:
            return ZERO_MONEY

        projected_available = self._projected_available_surplus(
            pay_date,
            goal.target_date,
        )
        projected_shortfall = max(
            money(
                goal.target_amount
                - money(self.savings_balance)
                - projected_available
            ),
            ZERO_MONEY,
        )
        if projected_shortfall == ZERO_MONEY:
            return ZERO_MONEY

        per_paycheck_gap = (projected_shortfall / Decimal(eligible_paychecks)).quantize(
            CENT,
            rounding=ROUND_CEILING,
        )
        current_remaining_need = max(
            money(
                goal.target_amount
                - money(self.savings_balance)
                - money(current_discretionary_savings)
            ),
            ZERO_MONEY,
        )
        reduction = min(
            per_paycheck_gap,
            money(self.settings.personal_per_paycheck),
            current_remaining_need,
        )
        return money(reduction)

    def _projected_available_surplus(
        self,
        start_pay_date: date,
        target_date: date,
    ) -> Decimal:
        """Project post-required cash available through a savings deadline."""
        calendar_engine = CalendarEngine(self.settings)
        total = ZERO_MONEY
        for period in calendar_engine.generate(target_date):
            if not start_pay_date <= period.pay_date <= target_date:
                continue

            scheduled_payments = self.scheduler.payments_for_period(period)
            bills_paid = self._scheduled_bill_total(scheduled_payments)
            debt_minimums = self._scheduled_debt_minimum_total(scheduled_payments)
            total += money(
                self._surplus_after_required_payments(
                    bills_paid=bills_paid,
                    debt_minimums=debt_minimums,
                )
            )

        return money(total)

    def _projected_shortfall_after_contribution(
        self,
        goal,
        pay_date: date,
        savings_contribution: Decimal,
    ) -> Decimal:
        """Return projected deadline shortfall after future eligible paychecks."""
        if goal is None or goal.target_date is None:
            return ZERO_MONEY

        projected_balance = self._projected_deadline_balance(
            goal=goal,
            pay_date=pay_date,
            balance_after_current=(
                money(self.savings_balance)
                + money(savings_contribution)
            ),
        )
        shortfall = max(
            money(goal.target_amount - projected_balance),
            ZERO_MONEY,
        )
        return money(shortfall)

    def _projected_deadline_balance(
        self,
        goal,
        pay_date: date,
        balance_after_current: Decimal,
    ) -> Decimal:
        """Project goal balance after all future eligible paychecks."""
        if goal.target_date is None or pay_date >= goal.target_date:
            return money(min(balance_after_current, goal.target_amount))

        future_capacity = ZERO_MONEY
        calendar_engine = CalendarEngine(self.settings)
        for period in calendar_engine.generate(goal.target_date):
            if not pay_date < period.pay_date <= goal.target_date:
                continue

            scheduled_payments = self.scheduler.payments_for_period(period)
            bills_paid = self._scheduled_bill_total(scheduled_payments)
            debt_minimums = self._scheduled_debt_minimum_total(scheduled_payments)
            future_capacity += money(
                self._surplus_after_required_payments(
                    bills_paid=bills_paid,
                    debt_minimums=debt_minimums,
                )
            )
            if goal.funding_mode == SavingsFundingMode.DEADLINE_PRIORITY:
                future_capacity += money(
                    self.settings.personal_per_paycheck
                )

        return money(
            min(balance_after_current + future_capacity, goal.target_amount)
        )

    def _debt_engine_snowball_amount(self, snowball_amount: Decimal) -> Decimal:
        """Return the snowball amount to pass before debt-engine rollover is added."""
        return money(
            max(snowball_amount - self.debt_engine.freed_minimum_payment, ZERO_MONEY),
        )

    def _remaining_cash(
        self,
        bills_paid: Decimal,
        debt_minimums: Decimal,
        savings_contribution: Decimal,
        snowball_payment: Decimal,
        personal_expense_reduction: Decimal = ZERO_MONEY,
    ) -> Decimal:
        """Return cash left after all budgeted outflows."""
        return money(
            self.settings.paycheck
            - bills_paid
            - debt_minimums
            - savings_contribution
            - snowball_payment
            + personal_expense_reduction,
        )

    def _debt_balances(self, debts) -> list[DebtBalance]:
        """Convert debt engine snapshots to debt balance dataclasses."""
        return [
            DebtBalance(
                name=debt["name"],
                balance=money(debt["balance"]),
                minimum=money(debt["minimum"]),
                status=debt["status"],
            )
            for debt in debts
        ]

    def savings_stage_results(self) -> list[SavingsStageResult]:
        """Return savings stage progress snapshots."""
        if not self._savings_plan_enabled():
            return []
        self._finalize_savings_stages()
        return list(self._stage_results.values())

    def planned_withdrawal_results(self) -> list[PlannedWithdrawalResult]:
        """Return planned withdrawal application results."""
        if not self._savings_plan_enabled():
            return []
        return list(self._withdrawal_results)

    def _process_savings_events(self, pay_date: date) -> dict[str, object]:
        context = {
            "balance_before_withdrawal": None,
            "withdrawal_amount": ZERO_MONEY,
            "balance_after_withdrawal": None,
            "withdrawal_name": None,
            "stage_changed": False,
        }
        if not self._savings_plan_enabled():
            return context

        self._evaluate_due_stage_deadlines(pay_date)
        for index, withdrawal in enumerate(self.savings_plan.withdrawals):
            if index in self._applied_withdrawal_indexes:
                continue
            if withdrawal.withdrawal_date > pay_date:
                continue

            before = money(self.savings_balance)
            requested = before if withdrawal.drain_balance else withdrawal.amount
            actual = min(requested, before)
            after = money(before - actual)
            shortfall = money(requested - actual)
            self.savings_balance = after
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
                    status="applied" if shortfall == ZERO_MONEY else "partial",
                )
            )
            context = {
                "balance_before_withdrawal": before,
                "withdrawal_amount": actual,
                "balance_after_withdrawal": after,
                "withdrawal_name": withdrawal.name,
                "stage_changed": True,
            }

        return context

    def _active_savings_stage(self, pay_date: date):
        if not self._savings_plan_enabled() or not self.savings_plan.goals:
            return None

        active = [
            goal
            for goal in self.savings_plan.goals
            if goal.start_date <= pay_date
            and (goal.target_date is None or pay_date <= goal.target_date)
        ]
        if not active:
            return None

        return active[-1]

    def _active_savings_stage_for_balance(self):
        if not self._savings_plan_enabled() or not self.savings_plan.goals:
            return None

        if self._last_active_stage_name is None:
            return None

        return next(
            goal
            for goal in self.savings_plan.goals
            if goal.name == self._last_active_stage_name
        )

    def _active_savings_target(self) -> Decimal:
        active_stage = self._active_savings_stage_for_balance()
        if active_stage is None:
            return self.settings.savings_goal

        return money(active_stage.target_amount)

    def _savings_plan_enabled(self) -> bool:
        """Return whether deadline-priority savings plan behavior is active."""
        return bool(
            self.savings_plan is not None
            and self.savings_plan.deadline_priority_enabled
        )

    def _ensure_stage_started(self, active_stage) -> None:
        if active_stage is None or active_stage.name in self._stage_results:
            return

        if active_stage.starting_balance_override is not None:
            self.savings_balance = money(active_stage.starting_balance_override)

        self._seen_stage_names.add(active_stage.name)
        self._stage_results[active_stage.name] = SavingsStageResult(
            goal_name=active_stage.name,
            start_date=active_stage.start_date,
            target_date=active_stage.target_date,
            target_amount=active_stage.target_amount,
            starting_balance=money(self.savings_balance),
            ending_balance=money(self.savings_balance),
            status=SavingsGoalStatus.ACTIVE,
        )

    def _record_stage_progress(self, active_stage, pay_date: date) -> None:
        if active_stage is None:
            return

        result = self._stage_results[active_stage.name]
        result.ending_balance = money(self.savings_balance)
        result.amount_needed = max(
            money(active_stage.target_amount - result.ending_balance),
            ZERO_MONEY,
        )
        result.eligible_paychecks_remaining = (
            self._eligible_paycheck_count(pay_date, active_stage.target_date)
            if active_stage.target_date is not None
            else 0
        )
        result.projected_available_contributions = money(
            result.ending_balance - result.starting_balance
        )
        if active_stage.target_date is not None:
            projected_balance = self._projected_deadline_balance(
                goal=active_stage,
                pay_date=pay_date,
                balance_after_current=result.ending_balance,
            )
            result.projected_balance_at_deadline = projected_balance
            result.projected_shortfall = max(
                money(active_stage.target_amount - projected_balance),
                ZERO_MONEY,
            )
            result.additional_funding_needed = result.projected_shortfall
            result.feasible_under_current_plan = (
                projected_balance >= active_stage.target_amount
            )
            result.projected_available_contributions = money(
                projected_balance - result.starting_balance
            )
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

            amount = money(self.savings_balance)
            result.amount_at_deadline = amount
            result.shortfall_at_deadline = max(
                money(goal.target_amount - amount),
                ZERO_MONEY,
            )
            result.amount_needed = result.shortfall_at_deadline
            result.eligible_paychecks_remaining = 0
            result.projected_balance_at_deadline = amount
            result.projected_shortfall = result.shortfall_at_deadline
            result.additional_funding_needed = result.shortfall_at_deadline
            result.feasible_under_current_plan = (
                result.shortfall_at_deadline == ZERO_MONEY
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
                    starting_balance=ZERO_MONEY,
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

    def _goal_progress_percentage(self) -> Decimal | None:
        target = self._active_savings_target()
        if target <= ZERO_MONEY:
            return None

        return min(self.savings_balance / target, Decimal("1")).quantize(
            Decimal("0.0001")
        )
