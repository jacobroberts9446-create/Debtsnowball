"""History comparison workflows."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from app.history.repository import HistoryRepository
from app.money import from_cents, money
from app.models import (
    ActualEntryType,
    AssumptionDifference,
    ForecastActualComparison,
    ForecastActualPeriodComparison,
    ForecastSnapshotRecord,
    PlanComparison,
)


class HistoryComparisonService:
    """Compare saved plans, forecasts, and actual activity."""

    def __init__(
        self,
        repository: HistoryRepository,
        *,
        format_money,
    ) -> None:
        self.repository = repository
        self.format_money = format_money

    def compare_forecast_to_actual(self, plan_id: int) -> ForecastActualComparison:
        """Compare persisted forecast totals with posted actual totals."""
        planned = self.latest_plan_forecast_totals(plan_id)
        actual = self.actual_totals(plan_id)
        actual_remaining = money(
            actual["income"]
            - actual["bills"]
            - actual["debt"]
            - actual["savings"]
            - actual["personal"]
        )
        status = "Insufficient actual data"
        if any(value != Decimal("0.00") for value in actual.values()):
            variance = money(actual_remaining - planned["remaining"])
            if variance > Decimal("10.00"):
                status = "Ahead of plan"
            elif variance < Decimal("-10.00"):
                status = "Needs review"
            else:
                status = "On track"
        return ForecastActualComparison(
            planned_income=planned["income"],
            actual_income=actual["income"],
            planned_bills=planned["bills"],
            actual_bills=actual["bills"],
            planned_debt_payments=planned["debt"],
            actual_debt_payments=actual["debt"],
            planned_savings=planned["savings"],
            actual_savings=actual["savings"],
            planned_personal_spending=planned["personal"],
            actual_personal_spending=actual["personal"],
            planned_remaining_cash=planned["remaining"],
            actual_remaining_cash=actual_remaining,
            status=status,
        )

    def compare_forecast_to_actual_periods(
        self,
        plan_id: int,
    ) -> list[ForecastActualPeriodComparison]:
        """Compare actual entries to each persisted forecast period."""
        plan = self.repository.get_plan(plan_id)
        if plan.current_version_id is None:
            return []
        snapshot = self.latest_snapshot_for_version(plan.current_version_id)
        periods = self.repository.forecast_period_rows(snapshot.id)
        actual_by_period = self.actual_totals_by_period(plan_id)
        observations_by_period = self.balance_observations_by_period(plan_id)
        comparisons = []
        for row in periods:
            period_id = row[0]
            actual = actual_by_period.get(period_id, {})
            observations = observations_by_period.get(period_id, {})
            planned_income = from_cents(row[3])
            planned_bills = from_cents(row[4])
            planned_personal = from_cents(row[6])
            planned_debt_minimums = from_cents(row[8])
            planned_snowball = from_cents(row[9])
            planned_savings = from_cents(row[10])
            planned_withdrawal = from_cents(row[11])
            planned_remaining = from_cents(row[12])
            actual_income = actual.get(ActualEntryType.INCOME_RECEIVED)
            actual_bills = actual.get(ActualEntryType.BILL_PAID)
            actual_debt = actual.get(ActualEntryType.DEBT_PAYMENT)
            actual_savings = actual.get(ActualEntryType.SAVINGS_DEPOSIT)
            actual_withdrawal = actual.get(ActualEntryType.SAVINGS_WITHDRAWAL)
            actual_personal = actual.get(ActualEntryType.PERSONAL_SPENDING)
            actual_remaining = self.actual_remaining(
                actual_income,
                actual_bills,
                actual_debt,
                actual_savings,
                actual_personal,
                actual_withdrawal,
            )
            status = self.period_status(actual, actual_remaining, planned_remaining)
            comparisons.append(
                ForecastActualPeriodComparison(
                    forecast_period_id=period_id,
                    pay_date=date.fromisoformat(row[2]),
                    planned_income=planned_income,
                    actual_income=actual_income,
                    income_variance=variance(actual_income, planned_income),
                    planned_bills=planned_bills,
                    actual_bills=actual_bills,
                    bills_variance=variance(actual_bills, planned_bills),
                    planned_debt_minimums=planned_debt_minimums,
                    actual_debt_payments=actual_debt,
                    debt_payment_variance=variance(
                        actual_debt,
                        planned_debt_minimums + planned_snowball,
                    ),
                    planned_snowball=planned_snowball,
                    actual_extra_debt_payment=None
                    if actual_debt is None
                    else money(max(actual_debt - planned_debt_minimums, Decimal("0.00"))),
                    planned_savings_deposit=planned_savings,
                    actual_savings_deposit=actual_savings,
                    planned_savings_withdrawal=planned_withdrawal,
                    actual_savings_withdrawal=actual_withdrawal,
                    planned_personal_spending=planned_personal,
                    actual_personal_spending=actual_personal,
                    planned_remaining_cash=planned_remaining,
                    actual_remaining_cash=actual_remaining,
                    debt_balance_variance=observations.get("debt"),
                    savings_balance_variance=observations.get("savings"),
                    data_completeness=self.data_completeness(actual),
                    status=status,
                    interpretation=self.period_interpretation(status),
                )
            )
        return comparisons

    def compare_plan_versions(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> PlanComparison:
        """Compare two saved forecast snapshots with neutral tradeoff wording."""
        earlier = self.latest_snapshot_for_version(earlier_version_id)
        later = self.latest_snapshot_for_version(later_version_id)
        earlier_payoffs = self.payoff_order(earlier.id)
        later_payoffs = self.payoff_order(later.id)
        assumption_differences = self.compare_assumptions(
            earlier_version_id,
            later_version_id,
        )
        date_delta = date_delta_days(earlier.debt_free_date, later.debt_free_date)
        interest_difference = money(
            later.total_projected_interest - earlier.total_projected_interest
        )
        savings_difference = money(later.ending_savings - earlier.ending_savings)
        debt_payment_difference = money(
            self.snapshot_debt_payments(later.id)
            - self.snapshot_debt_payments(earlier.id)
        )
        personal_difference = money(
            self.snapshot_personal_spending(later.id)
            - self.snapshot_personal_spending(earlier.id)
        )
        first_difference = self.first_different_period(earlier.id, later.id)
        explanation = self.comparison_explanation(date_delta, interest_difference)
        return PlanComparison(
            earlier_version=self.repository.get_plan_version(
                earlier_version_id
            ).version_number,
            later_version=self.repository.get_plan_version(later_version_id).version_number,
            debt_free_date_difference_days=date_delta,
            interest_difference=interest_difference,
            debt_payment_difference=debt_payment_difference,
            savings_difference=savings_difference,
            personal_spending_difference=personal_difference,
            pay_period_difference=self.period_count(later.id) - self.period_count(earlier.id),
            first_different_period=first_difference,
            payoff_order_changed=earlier_payoffs != later_payoffs,
            deadline_priority_changed=self.deadline_priority_changed(
                earlier_version_id,
                later_version_id,
            ),
            feasible=earlier.ending_debt == Decimal("0.00")
            and later.ending_debt == Decimal("0.00"),
            explanation=self.rich_comparison_explanation(
                explanation,
                assumption_differences,
            ),
        )

    def compare_assumptions(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> list[AssumptionDifference]:
        """Return meaningful input differences between two plan versions."""
        earlier = json.loads(
            self.repository.get_plan_version(earlier_version_id).config_snapshot
        )
        later = json.loads(
            self.repository.get_plan_version(later_version_id).config_snapshot
        )
        differences: list[AssumptionDifference] = []
        self.compare_mapping("Budget", earlier.get("budget", {}), later.get("budget", {}), differences)
        self.compare_named_collection("Bills", earlier.get("bills", []), later.get("bills", []), differences)
        self.compare_named_collection("Debts", earlier.get("debts", []), later.get("debts", []), differences)
        if earlier.get("savings_plan") != later.get("savings_plan"):
            differences.append(
                AssumptionDifference(
                    category="Savings Plan",
                    name="savings_plan",
                    earlier_value=earlier.get("savings_plan"),
                    later_value=later.get("savings_plan"),
                    direction="changed",
                    interpretation="Savings goals, deadlines, or withdrawals changed.",
                )
            )
        return differences

    def latest_snapshot_for_version(self, plan_version_id: int) -> ForecastSnapshotRecord:
        """Return latest snapshot for one plan version."""
        return self.repository.get_forecast_snapshot(
            self.repository.latest_snapshot_id_for_version(plan_version_id)
        )

    def payoff_order(self, snapshot_id: int) -> list[str]:
        """Return payoff order for a forecast snapshot."""
        with self.repository.connection() as conn:
            rows = conn.execute(
                """
                SELECT debt_name
                FROM debt_snapshots
                WHERE payoff_date IS NOT NULL
                  AND forecast_period_id IN (
                      SELECT id FROM forecast_periods WHERE forecast_snapshot_id = ?
                  )
                ORDER BY payoff_date, forecast_period_id
                """,
                (snapshot_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def snapshot_debt_payments(self, snapshot_id: int) -> Decimal:
        """Return total debt payments for a snapshot."""
        return self.sum_snapshot_column(snapshot_id, "snowball_payment") + self.sum_snapshot_column(
            snapshot_id, "minimum_debt_payments"
        )

    def snapshot_personal_spending(self, snapshot_id: int) -> Decimal:
        """Return total personal spending for a snapshot."""
        return self.sum_snapshot_column(snapshot_id, "personal_expenses_used")

    def period_count(self, snapshot_id: int) -> int:
        """Return forecast period count for a snapshot."""
        return len(self.repository.forecast_period_rows(snapshot_id))

    def sum_snapshot_column(self, snapshot_id: int, column: str) -> Decimal:
        """Return a forecast period money-column sum."""
        return from_cents(self.repository.sum_snapshot_column(snapshot_id, column))

    def first_different_period(
        self,
        earlier_snapshot_id: int,
        later_snapshot_id: int,
    ) -> date | None:
        """Return first pay date where two snapshots differ."""
        earlier = self.repository.forecast_period_rows(earlier_snapshot_id)
        later = self.repository.forecast_period_rows(later_snapshot_id)
        for left, right in zip(earlier, later, strict=False):
            if left[3:] != right[3:]:
                return date.fromisoformat(left[2])
        if len(earlier) != len(later):
            extra = (
                earlier[min(len(earlier), len(later))]
                if len(earlier) > len(later)
                else later[min(len(earlier), len(later))]
            )
            return date.fromisoformat(extra[2])
        return None

    def deadline_priority_changed(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> bool:
        """Return whether deadline-priority savings changed."""
        earlier = json.loads(
            self.repository.get_plan_version(earlier_version_id).config_snapshot
        )
        later = json.loads(
            self.repository.get_plan_version(later_version_id).config_snapshot
        )
        earlier_savings_plan = earlier.get("savings_plan") or {}
        later_savings_plan = later.get("savings_plan") or {}
        return (
            earlier_savings_plan.get("deadline_priority_enabled")
            != later_savings_plan.get("deadline_priority_enabled")
        )

    def comparison_explanation(
        self,
        date_delta: int | None,
        interest_delta: Decimal,
    ) -> str:
        """Return plain-language plan comparison explanation."""
        parts = []
        if date_delta is not None:
            direction = "later" if date_delta > 0 else "earlier"
            parts.append(f"The later plan reaches debt-free {abs(date_delta)} day(s) {direction}.")
        if interest_delta > Decimal("0.00"):
            parts.append(f"It projects {self.format_money(interest_delta)} more interest.")
        elif interest_delta < Decimal("0.00"):
            parts.append(f"It projects {self.format_money(abs(interest_delta))} less interest.")
        return " ".join(parts) or "The selected plans have no major forecast difference."

    def latest_plan_forecast_totals(self, plan_id: int) -> dict[str, Decimal]:
        """Return latest planned totals for one plan."""
        plan = self.repository.get_plan(plan_id)
        if plan.current_version_id is None:
            return zero_totals()
        try:
            snapshot = self.latest_snapshot_for_version(plan.current_version_id)
        except ValueError:
            return zero_totals()
        return {
            "income": self.sum_snapshot_column(snapshot.id, "income"),
            "bills": self.sum_snapshot_column(snapshot.id, "fixed_expenses"),
            "debt": self.snapshot_debt_payments(snapshot.id),
            "savings": self.sum_snapshot_column(snapshot.id, "savings_deposit"),
            "personal": self.sum_snapshot_column(snapshot.id, "personal_expenses_used"),
            "remaining": self.sum_snapshot_column(snapshot.id, "checking_remaining"),
        }

    def actual_totals(self, plan_id: int) -> dict[str, Decimal]:
        """Return actual totals for one plan."""
        totals = zero_actual_totals()
        for entry in self.repository.list_actual_entries(plan_id):
            if entry.entry_type == ActualEntryType.INCOME_RECEIVED:
                totals["income"] += entry.amount
            elif entry.entry_type == ActualEntryType.BILL_PAID:
                totals["bills"] += entry.amount
            elif entry.entry_type == ActualEntryType.DEBT_PAYMENT:
                totals["debt"] += entry.amount
            elif entry.entry_type in {
                ActualEntryType.SAVINGS_DEPOSIT,
                ActualEntryType.SAVINGS_WITHDRAWAL,
            }:
                totals["savings"] += entry.amount
            elif entry.entry_type == ActualEntryType.PERSONAL_SPENDING:
                totals["personal"] += entry.amount
        return {key: money(value) for key, value in totals.items()}

    def actual_totals_by_period(
        self,
        plan_id: int,
    ) -> dict[int, dict[ActualEntryType, Decimal]]:
        """Return actual totals grouped by forecast period."""
        totals: dict[int, dict[ActualEntryType, Decimal]] = {}
        with self.repository.connection() as conn:
            rows = conn.execute(
                """
                SELECT forecast_period_id, entry_type, amount
                FROM actual_transactions
                WHERE plan_id = ? AND forecast_period_id IS NOT NULL
                """,
                (plan_id,),
            ).fetchall()
        for period_id, entry_type, amount in rows:
            bucket = totals.setdefault(period_id, {})
            entry = ActualEntryType(entry_type)
            bucket[entry] = money(bucket.get(entry, Decimal("0.00")) + from_cents(amount))
        return totals

    def balance_observations_by_period(
        self,
        plan_id: int,
    ) -> dict[int, dict[str, Decimal]]:
        """Return balance observation variances grouped by period."""
        observations: dict[int, dict[str, Decimal]] = {}
        with self.repository.connection() as conn:
            rows = conn.execute(
                """
                SELECT bo.forecast_period_id, bo.observation_type, bo.balance,
                       fp.id
                FROM balance_observations bo
                LEFT JOIN forecast_periods fp ON fp.id = bo.forecast_period_id
                WHERE bo.plan_id = ? AND bo.forecast_period_id IS NOT NULL
                """,
                (plan_id,),
            ).fetchall()
            for period_id, observation_type, balance, _ in rows:
                bucket = observations.setdefault(period_id, {})
                key = (
                    "debt"
                    if observation_type == ActualEntryType.DEBT_BALANCE_OBSERVATION.value
                    else "savings"
                )
                planned = self.planned_balance_for_period(conn, period_id, key)
                bucket[key] = money(from_cents(balance) - planned)
        return observations

    @staticmethod
    def planned_balance_for_period(
        conn: sqlite3.Connection,
        period_id: int,
        key: str,
    ) -> Decimal:
        """Return planned debt or savings balance for one period."""
        if key == "debt":
            value = conn.execute(
                """
                SELECT COALESCE(SUM(ending_balance), 0)
                FROM debt_snapshots
                WHERE forecast_period_id = ?
                """,
                (period_id,),
            ).fetchone()[0]
            return from_cents(value)
        value = conn.execute(
            "SELECT ending_savings FROM savings_snapshots WHERE forecast_period_id = ?",
            (period_id,),
        ).fetchone()
        return Decimal("0.00") if value is None else from_cents(value[0])

    @staticmethod
    def actual_remaining(
        income: Decimal | None,
        bills: Decimal | None,
        debt: Decimal | None,
        savings: Decimal | None,
        personal: Decimal | None,
        withdrawal: Decimal | None,
    ) -> Decimal | None:
        """Calculate remaining cash from actual entries."""
        values = [income, bills, debt, savings, personal]
        if any(value is None for value in values):
            return None
        return money(
            income
            - bills
            - debt
            - savings
            - personal
            + (withdrawal or Decimal("0.00"))
        )

    @staticmethod
    def period_status(
        actual: dict[ActualEntryType, Decimal],
        actual_remaining: Decimal | None,
        planned_remaining: Decimal,
    ) -> str:
        """Return status for one forecast-vs-actual period."""
        if not actual:
            return "No actual activity recorded"
        required = {
            ActualEntryType.INCOME_RECEIVED,
            ActualEntryType.BILL_PAID,
            ActualEntryType.DEBT_PAYMENT,
            ActualEntryType.SAVINGS_DEPOSIT,
            ActualEntryType.PERSONAL_SPENDING,
        }
        if not required.issubset(actual):
            return "Insufficient actual data"
        variance_value = money(actual_remaining - planned_remaining)
        if variance_value > Decimal("10.00"):
            return "Ahead of plan"
        if variance_value < Decimal("-10.00"):
            return "Needs review"
        return "On track"

    @staticmethod
    def data_completeness(actual: dict[ActualEntryType, Decimal]) -> str:
        """Return data completeness for one actual period."""
        if not actual:
            return "none"
        required = {
            ActualEntryType.INCOME_RECEIVED,
            ActualEntryType.BILL_PAID,
            ActualEntryType.DEBT_PAYMENT,
            ActualEntryType.SAVINGS_DEPOSIT,
            ActualEntryType.PERSONAL_SPENDING,
        }
        return "complete" if required.issubset(actual) else "partial"

    @staticmethod
    def period_interpretation(status: str) -> str:
        """Return plain-language interpretation for a period status."""
        if status == "No actual activity recorded":
            return "No actual transactions were recorded for this period."
        if status == "Insufficient actual data":
            return "Some actual activity is recorded, but more entries are needed for a complete comparison."
        if status == "Ahead of plan":
            return "Recorded activity leaves more cash than planned for this period."
        if status == "Needs review":
            return "Recorded activity leaves less cash than planned for this period."
        return "Recorded activity is close to the forecast for this period."

    @staticmethod
    def compare_mapping(
        category: str,
        earlier: dict[str, Any],
        later: dict[str, Any],
        differences: list[AssumptionDifference],
    ) -> None:
        """Append mapping-level assumption differences."""
        for key in sorted(set(earlier) | set(later)):
            if earlier.get(key) == later.get(key):
                continue
            differences.append(
                AssumptionDifference(
                    category=category,
                    name=key,
                    earlier_value=earlier.get(key),
                    later_value=later.get(key),
                    direction="changed",
                    interpretation=f"{category} assumption '{key}' changed.",
                )
            )

    @staticmethod
    def compare_named_collection(
        category: str,
        earlier: list[dict[str, Any]],
        later: list[dict[str, Any]],
        differences: list[AssumptionDifference],
    ) -> None:
        """Append named collection assumption differences."""
        earlier_by_name = {item.get("name"): item for item in earlier}
        later_by_name = {item.get("name"): item for item in later}
        for name in sorted(set(earlier_by_name) | set(later_by_name)):
            if name not in earlier_by_name:
                direction = "added"
            elif name not in later_by_name:
                direction = "removed"
            elif earlier_by_name[name] != later_by_name[name]:
                direction = "changed"
            else:
                continue
            differences.append(
                AssumptionDifference(
                    category=category,
                    name=str(name),
                    earlier_value=earlier_by_name.get(name),
                    later_value=later_by_name.get(name),
                    direction=direction,
                    interpretation=f"{category} item '{name}' was {direction}.",
                )
            )

    @staticmethod
    def rich_comparison_explanation(
        base: str,
        differences: list[AssumptionDifference],
    ) -> str:
        """Add input-difference context to a comparison explanation."""
        if not differences:
            return base
        first = differences[0]
        return f"{base} Input changes include {first.category} {first.name} ({first.direction})."


def date_delta_days(left: date | None, right: date | None) -> int | None:
    """Return date delta in days when both dates exist."""
    if left is None or right is None:
        return None
    return (right - left).days


def variance(actual: Decimal | None, planned: Decimal) -> Decimal | None:
    """Return actual minus planned variance when actual exists."""
    if actual is None:
        return None
    return money(actual - planned)


def zero_totals() -> dict[str, Decimal]:
    """Return empty planned totals."""
    return {
        "income": Decimal("0.00"),
        "bills": Decimal("0.00"),
        "debt": Decimal("0.00"),
        "savings": Decimal("0.00"),
        "personal": Decimal("0.00"),
        "remaining": Decimal("0.00"),
    }


def zero_actual_totals() -> dict[str, Decimal]:
    """Return empty actual totals."""
    return {
        "income": Decimal("0.00"),
        "bills": Decimal("0.00"),
        "debt": Decimal("0.00"),
        "savings": Decimal("0.00"),
        "personal": Decimal("0.00"),
    }
