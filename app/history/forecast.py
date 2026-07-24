"""Forecast history persistence workflow."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from app.budget_engine import PayPeriodSummary
from app.history.repository import HistoryRepository
from app.money import money, to_cents
from app.models import (
    AllocationReasonCode,
    AllocationExplanation,
    DataQualityWarning,
    ForecastPeriod,
    ForecastSnapshotRecord,
    ForecastSummary,
)


class HistoryForecastService:
    """Persist forecast snapshots and detailed forecast history rows."""

    def __init__(
        self,
        repository: HistoryRepository,
        *,
        get_forecast_snapshot: Callable[[int], ForecastSnapshotRecord],
        forecast_fingerprint: Callable[[ForecastSummary], str],
        explain_forecast_period: Callable[[ForecastPeriod], AllocationExplanation],
        utc_timestamp: Callable[[], str],
        date_value: Callable[[date | None], str | None],
        stable_identifier: Callable[[str], str],
        format_money: Callable[[Decimal], str],
    ) -> None:
        self.repository = repository
        self.get_forecast_snapshot = get_forecast_snapshot
        self.forecast_fingerprint = forecast_fingerprint
        self.explain_forecast_period = explain_forecast_period
        self.utc_timestamp = utc_timestamp
        self.date_value = date_value
        self.stable_identifier = stable_identifier
        self.format_money = format_money

    def generate_and_save_forecast(
        self,
        plan_version_id: int,
        forecast: ForecastSummary,
        *,
        starting_savings: Decimal,
        pay_period_summaries: list[PayPeriodSummary] | None = None,
        starting_debts: list[Any] | None = None,
        warnings: list[DataQualityWarning] | None = None,
    ) -> ForecastSnapshotRecord:
        """Persist a forecast snapshot without mutating the plan version."""
        with self.repository.transaction() as conn:
            snapshot_id = self.save_forecast_snapshot(
                conn,
                plan_version_id,
                forecast,
                starting_savings=starting_savings,
                pay_period_summaries=pay_period_summaries,
                starting_debts=starting_debts,
                warnings=warnings,
            )
        return self.get_forecast_snapshot(snapshot_id)

    def save_forecast_snapshot(
        self,
        conn: sqlite3.Connection,
        plan_version_id: int,
        forecast: ForecastSummary,
        *,
        starting_savings: Decimal,
        pay_period_summaries: list[PayPeriodSummary] | None = None,
        starting_debts: list[Any] | None = None,
        warnings: list[DataQualityWarning] | None = None,
    ) -> int:
        """Persist a forecast snapshot using the caller's transaction."""
        warnings = warnings or []
        forecast_hash = self.forecast_fingerprint(forecast)
        cursor = conn.execute(
            """
            INSERT INTO forecast_snapshots (
                plan_version_id,
                created_at,
                forecast_start_date,
                forecast_end_date,
                debt_free_date,
                total_projected_interest,
                total_projected_debt_payments,
                starting_debt,
                ending_debt,
                starting_savings,
                ending_savings,
                pay_period_count,
                forecast_fingerprint,
                status,
                warning_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_version_id,
                self.utc_timestamp(),
                forecast.forecast_start_date.isoformat(),
                forecast.forecast_end_date.isoformat(),
                self.date_value(forecast.debt_free_date),
                to_cents(forecast.total_interest_paid),
                to_cents(
                    forecast.total_minimum_payments
                    + forecast.total_snowball_payments
                ),
                to_cents(forecast.starting_debt),
                to_cents(forecast.remaining_debt),
                to_cents(starting_savings),
                to_cents(forecast.ending_savings),
                len(forecast.periods),
                forecast_hash,
                "completed" if forecast.completed else "needs_review",
                len(warnings),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        self.save_forecast_periods(
            conn,
            snapshot_id,
            forecast,
            starting_savings,
            pay_period_summaries=pay_period_summaries,
            starting_debts=starting_debts,
        )
        self.save_warnings(conn, snapshot_id, warnings)
        self.sync_snapshot_totals(conn, snapshot_id)
        return snapshot_id

    def save_forecast_periods(
        self,
        conn: sqlite3.Connection,
        snapshot_id: int,
        forecast: ForecastSummary,
        starting_savings: Decimal,
        *,
        pay_period_summaries: list[PayPeriodSummary] | None,
        starting_debts: list[Any] | None = None,
    ) -> None:
        """Persist detailed period, debt, and savings rows for a forecast."""
        previous_savings = money(starting_savings)
        summaries_by_date = {
            summary.pay_date: summary for summary in pay_period_summaries or []
        }
        debt_states = self.initial_debt_states(starting_debts)
        payoff_dates = {
            payoff.debt_name: payoff.payoff_date for payoff in forecast.debt_payoffs
        }
        for index, period in enumerate(forecast.periods, start=1):
            summary = summaries_by_date.get(period.paycheck_date)
            explanation = self.explain_forecast_period(period)
            withdrawal = money(period.planned_withdrawal_amount)
            income = Decimal("0.00") if summary is None else summary.income
            checking_remaining = (
                Decimal("0.00") if summary is None else summary.remaining_cash
            )
            non_personal_fixed_expenses = money(
                period.required_fixed_expenses - period.actual_personal_allowance
            )
            reconciliation = money(
                income
                - non_personal_fixed_expenses
                - period.actual_personal_allowance
                - period.minimums_paid
                - period.snowball_paid
                - period.savings_contribution
                + withdrawal
                - checking_remaining
            )
            cursor = conn.execute(
                """
                INSERT INTO forecast_periods (
                    forecast_snapshot_id, sequence_number, pay_date, income,
                    fixed_expenses, personal_allowance, personal_expenses_used,
                    personal_expense_reduction, minimum_debt_payments,
                    snowball_payment, savings_deposit, savings_withdrawal,
                    checking_remaining, reconciliation_difference,
                    active_savings_goal, reason_codes, explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    index,
                    period.paycheck_date.isoformat(),
                    to_cents(income),
                    to_cents(period.required_fixed_expenses),
                    to_cents(period.normal_personal_allowance),
                    to_cents(period.actual_personal_allowance),
                    to_cents(period.personal_expense_reduction),
                    to_cents(period.minimums_paid),
                    to_cents(period.snowball_paid),
                    to_cents(period.savings_contribution),
                    to_cents(withdrawal),
                    to_cents(checking_remaining),
                    to_cents(reconciliation),
                    period.active_savings_goal_name,
                    ",".join(code.value for code in explanation.reason_codes),
                    explanation.explanation,
                ),
            )
            period_id = int(cursor.lastrowid)
            debt_states = self.save_debt_snapshots(
                conn,
                period_id,
                summary,
                period,
                debt_states,
                payoff_dates,
            )
            ending_savings = money(period.savings_balance)
            has_active_goal = period.active_savings_goal_name is not None
            conn.execute(
                """
                INSERT INTO savings_snapshots (
                    forecast_period_id, starting_savings, normal_contribution,
                    redirected_snowball, personal_expense_reduction_contribution,
                    other_contribution, withdrawal, ending_savings, active_goal,
                    goal_target, goal_deadline, projected_shortfall,
                    goal_feasible_status, total_deposit, amount_required_before,
                    amount_required_after, projected_deadline_balance,
                    withdrawal_date, withdrawal_amount,
                    post_withdrawal_allocation_state, reason_codes, explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id,
                    to_cents(previous_savings),
                    to_cents(period.normal_savings_contribution),
                    to_cents(period.snowball_reduction),
                    to_cents(period.personal_expense_reduction),
                    to_cents(
                        period.savings_contribution
                        - period.normal_savings_contribution
                        - period.snowball_reduction
                        - period.personal_expense_reduction
                    ),
                    to_cents(withdrawal),
                    to_cents(ending_savings),
                    period.active_savings_goal_name,
                    None
                    if period.active_savings_target is None or not has_active_goal
                    else to_cents(period.active_savings_target),
                    self.goal_deadline_for_period(forecast, period),
                    to_cents(period.projected_savings_shortfall),
                    "review" if period.projected_savings_shortfall > 0 else "on_track",
                    to_cents(period.savings_contribution),
                    self.nullable_cents(
                        period.active_savings_target if has_active_goal else None
                    ),
                    self.nullable_cents(
                        None
                        if period.active_savings_target is None or not has_active_goal
                        else max(
                            period.active_savings_target - ending_savings,
                            Decimal("0.00"),
                        )
                    ),
                    self.nullable_cents(ending_savings if has_active_goal else None),
                    self.date_value(period.paycheck_date)
                    if withdrawal > Decimal("0.00")
                    else None,
                    to_cents(withdrawal) if withdrawal > Decimal("0.00") else None,
                    "post_withdrawal"
                    if period.planned_withdrawal_amount > Decimal("0.00")
                    else "normal",
                    ",".join(code.value for code in explanation.reason_codes),
                    explanation.explanation,
                ),
            )
            previous_savings = ending_savings

    def save_debt_snapshots(
        self,
        conn: sqlite3.Connection,
        period_id: int,
        summary: PayPeriodSummary | None,
        period: ForecastPeriod,
        debt_states: dict[str, dict[str, Any]],
        payoff_dates: dict[str, date | None],
    ) -> dict[str, dict[str, Any]]:
        """Persist one row per debt for a forecast period."""
        if not debt_states:
            return self.save_aggregate_debt_snapshot(conn, period_id, period)

        interest_by_name = summary.debt_interest_by_name or {} if summary else {}
        minimums_by_name = summary.debt_minimums_by_name or {} if summary else {}
        snowball_by_name = summary.debt_snowball_by_name or {} if summary else {}
        paid_names = {debt.name for debt in summary.paid_off_debts} if summary else set()
        ending_by_name = {}
        if summary is not None:
            ending_by_name.update(
                {debt.name: money(debt.balance) for debt in summary.active_debt_balances}
            )
            ending_by_name.update(
                {debt.name: Decimal("0.00") for debt in summary.paid_off_debts}
            )
        use_engine_payment_detail = summary is not None and (
            interest_by_name or minimums_by_name or snowball_by_name
        )

        active_start = {
            name: state
            for name, state in debt_states.items()
            if state["balance"] > Decimal("0.00")
        }
        active_starting_total = money(
            sum((state["balance"] for state in active_start.values()), Decimal("0.00"))
        )
        new_states = {name: dict(state) for name, state in debt_states.items()}
        for name, state in debt_states.items():
            starting = money(state["balance"])
            interest = money(interest_by_name.get(name, Decimal("0.00")))
            actual_minimum = money(minimums_by_name.get(name, Decimal("0.00")))
            extra_payment = money(snowball_by_name.get(name, Decimal("0.00")))
            total_payment = money(actual_minimum + extra_payment)
            if use_engine_payment_detail:
                ending = money(
                    ending_by_name.get(
                        name,
                        max(starting + interest - total_payment, Decimal("0.00")),
                    )
                )
                reported_difference = money(starting + interest - total_payment - ending)
                tolerance_applied = (
                    ending == Decimal("0.00")
                    and Decimal("0.00") < reported_difference <= Decimal("0.01")
                )
                if not tolerance_applied and reported_difference != Decimal("0.00"):
                    interest = money(ending + total_payment - starting)
            else:
                ending = starting
            if starting == Decimal("0.00") and ending == Decimal("0.00"):
                continue

            scheduled_minimum = money(state["minimum"])
            if not use_engine_payment_detail:
                share = Decimal("0.00")
                if active_starting_total > Decimal("0.00") and starting > Decimal("0.00"):
                    share = starting / active_starting_total
                interest = money(period.interest_paid * share)
                total_payment = money(max(starting + interest - ending, Decimal("0.00")))
                actual_minimum = min(total_payment, scheduled_minimum)
                extra_payment = money(max(total_payment - actual_minimum, Decimal("0.00")))
            principal_paid = money(total_payment - interest)
            if not use_engine_payment_detail:
                tolerance_applied = (
                    name in paid_names
                    and starting + interest - total_payment > Decimal("0.00")
                    and starting + interest - total_payment <= Decimal("0.01")
                )
            reason_code = (
                AllocationReasonCode.FINAL_DEBT_PAYOFF
                if name in paid_names
                else AllocationReasonCode.DEBT_SNOWBALL
                if extra_payment > Decimal("0.00")
                else AllocationReasonCode.MINIMUM_DEBT_PAYMENT
            )
            conn.execute(
                """
                INSERT INTO debt_snapshots (
                    forecast_period_id, debt_identifier, debt_name, starting_balance,
                    interest_charged, minimum_payment, extra_payment, total_payment,
                    principal_paid, ending_balance, paid_off, payoff_date,
                    payoff_order, scheduled_minimum_payment, actual_minimum_payment,
                    apr, final_payoff_tolerance_applied, allocation_reason_code,
                    explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id,
                    self.stable_identifier(name),
                    name,
                    to_cents(starting),
                    to_cents(interest),
                    to_cents(actual_minimum),
                    to_cents(extra_payment),
                    to_cents(total_payment),
                    to_cents(principal_paid),
                    to_cents(ending),
                    int(ending == Decimal("0.00")),
                    self.date_value(payoff_dates.get(name))
                    if ending == Decimal("0.00")
                    else None,
                    state["order"],
                    to_cents(scheduled_minimum),
                    to_cents(actual_minimum),
                    str(state["apr"]),
                    int(tolerance_applied),
                    reason_code.value,
                    self.debt_explanation(name, total_payment, ending, reason_code),
                ),
            )
            new_states[name]["balance"] = ending
        return new_states

    def save_aggregate_debt_snapshot(
        self,
        conn: sqlite3.Connection,
        period_id: int,
        period: ForecastPeriod,
    ) -> dict[str, dict[str, Any]]:
        """Fallback summary row when caller did not provide debt inputs."""
        total_payment = money(period.minimums_paid + period.snowball_paid)
        conn.execute(
            """
            INSERT INTO debt_snapshots (
                forecast_period_id, debt_identifier, debt_name, starting_balance,
                interest_charged, minimum_payment, extra_payment, total_payment,
                principal_paid, ending_balance, paid_off, payoff_date,
                payoff_order, scheduled_minimum_payment, actual_minimum_payment,
                apr, final_payoff_tolerance_applied, allocation_reason_code,
                explanation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                period_id,
                "total_debt_summary",
                "Total Debt Summary",
                0,
                to_cents(period.interest_paid),
                to_cents(period.minimums_paid),
                to_cents(period.snowball_paid),
                to_cents(total_payment),
                to_cents(total_payment - period.interest_paid),
                to_cents(period.total_debt_balance),
                int(period.total_debt_balance == Decimal("0.00")),
                None,
                None,
                to_cents(period.minimums_paid),
                to_cents(period.minimums_paid),
                "0",
                0,
                AllocationReasonCode.DEBT_SNOWBALL.value,
                "Debt totals were saved because individual starting debts were not provided.",
            ),
        )
        return {}

    @staticmethod
    def initial_debt_states(starting_debts: list[Any] | None) -> dict[str, dict[str, Any]]:
        """Build mutable debt state for detailed forecast persistence."""
        states = {}
        for debt in starting_debts or []:
            states[debt.name] = {
                "balance": money(debt.balance),
                "minimum": money(debt.minimum),
                "apr": debt.apr,
                "order": debt.snowball_order,
            }
        return states

    def goal_deadline_for_period(
        self,
        forecast: ForecastSummary,
        period: ForecastPeriod,
    ) -> str | None:
        """Return the active savings goal deadline for a period."""
        if period.active_savings_goal_name is None:
            return None
        for stage in forecast.savings_stage_results:
            if stage.goal_name == period.active_savings_goal_name:
                return self.date_value(stage.target_date)
        return None

    @staticmethod
    def nullable_cents(value: object | None) -> int | None:
        """Return nullable integer cents for optional monetary values."""
        return None if value is None else to_cents(value)

    def save_warnings(
        self,
        conn: sqlite3.Connection,
        snapshot_id: int,
        warnings: list[DataQualityWarning],
    ) -> None:
        """Persist data quality warnings for one forecast snapshot."""
        conn.executemany(
            """
            INSERT INTO data_quality_warnings (
                forecast_snapshot_id, code, severity, message, relevant_date,
                relevant_name, suggested_action
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    warning.code,
                    warning.severity.value,
                    warning.message,
                    self.date_value(warning.relevant_date),
                    warning.relevant_name,
                    warning.suggested_action,
                )
                for warning in warnings
            ],
        )

    @staticmethod
    def sync_snapshot_totals(conn: sqlite3.Connection, snapshot_id: int) -> None:
        """Update snapshot summary fields from persisted child rows."""
        periods = conn.execute(
            """
            SELECT id, pay_date
            FROM forecast_periods
            WHERE forecast_snapshot_id = ?
            ORDER BY sequence_number
            """,
            (snapshot_id,),
        ).fetchall()
        if not periods:
            return

        first_period_id = periods[0][0]
        last_period_id = periods[-1][0]
        starting_debt = conn.execute(
            """
            SELECT COALESCE(SUM(starting_balance), 0)
            FROM debt_snapshots
            WHERE forecast_period_id = ?
            """,
            (first_period_id,),
        ).fetchone()[0]
        ending_debt = conn.execute(
            """
            SELECT COALESCE(SUM(ending_balance), 0)
            FROM debt_snapshots
            WHERE forecast_period_id = ?
            """,
            (last_period_id,),
        ).fetchone()[0]
        debt_totals = conn.execute(
            """
            SELECT COALESCE(SUM(ds.interest_charged), 0),
                   COALESCE(SUM(ds.total_payment), 0)
            FROM debt_snapshots ds
            JOIN forecast_periods fp ON fp.id = ds.forecast_period_id
            WHERE fp.forecast_snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        savings = conn.execute(
            """
            SELECT
                (SELECT starting_savings FROM savings_snapshots WHERE forecast_period_id = ?),
                (SELECT ending_savings FROM savings_snapshots WHERE forecast_period_id = ?)
            """,
            (first_period_id, last_period_id),
        ).fetchone()
        warning_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM data_quality_warnings
            WHERE forecast_snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE forecast_snapshots
            SET forecast_start_date = ?,
                forecast_end_date = ?,
                total_projected_interest = ?,
                total_projected_debt_payments = ?,
                starting_debt = ?,
                ending_debt = ?,
                starting_savings = ?,
                ending_savings = ?,
                pay_period_count = ?,
                warning_count = ?
            WHERE id = ?
            """,
            (
                periods[0][1],
                periods[-1][1],
                debt_totals[0],
                debt_totals[1],
                starting_debt,
                ending_debt,
                savings[0],
                savings[1],
                len(periods),
                warning_count,
                snapshot_id,
            ),
        )

    def debt_explanation(
        self,
        debt_name: str,
        total_payment: Decimal,
        ending: Decimal,
        reason_code: AllocationReasonCode,
    ) -> str:
        """Return the plain-language reason for one persisted debt payment."""
        if reason_code == AllocationReasonCode.FINAL_DEBT_PAYOFF:
            return (
                f"{debt_name} was paid off with a total payment of "
                f"{self.format_money(total_payment)}."
            )
        if reason_code == AllocationReasonCode.DEBT_SNOWBALL:
            return f"{debt_name} received snowball funding this period."
        if ending == Decimal("0.00"):
            return f"{debt_name} has no remaining balance."
        return f"{debt_name} received its scheduled debt payment."
