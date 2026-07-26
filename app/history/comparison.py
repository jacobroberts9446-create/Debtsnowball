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
    ActualDataCompleteness,
    ActualEntryType,
    AssumptionDifference,
    BalanceObservation,
    DebtBalanceComparison,
    ForecastActualComparison,
    ForecastActualPeriodComparison,
    ForecastSnapshotRecord,
    PlanComparison,
)

COMPLETENESS_CATEGORY_ORDER = (
    ActualEntryType.INCOME_RECEIVED,
    ActualEntryType.BILL_PAID,
    ActualEntryType.DEBT_PAYMENT,
    ActualEntryType.SAVINGS_DEPOSIT,
    ActualEntryType.PERSONAL_SPENDING,
)
NO_ACTIVE_FORECAST_MESSAGE = "No forecast is available for the active version."


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
        entries = self.repository.list_actual_entries(plan_id)
        actual = self.actual_totals(plan_id, entries=entries)
        completeness = self.actual_data_completeness(
            planned,
            {entry.entry_type for entry in entries},
        )
        debt_balances = self.latest_debt_balance_comparisons(plan_id)
        actual_remaining = money(
            actual["income"]
            - actual["bills"]
            - actual["debt"]
            - actual["savings"]
            - actual["personal"]
            + actual["adjustment"]
        )
        status = "Insufficient actual data"
        if completeness.is_complete:
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
            completeness=completeness,
            debt_balance_comparisons=debt_balances,
        )

    def compare_forecast_to_actual_periods(
        self,
        plan_id: int,
    ) -> list[ForecastActualPeriodComparison]:
        """Compare actual entries to each persisted forecast period."""
        plan = self.repository.get_plan(plan_id)
        if plan.current_version_id is None:
            raise ValueError(NO_ACTIVE_FORECAST_MESSAGE)
        snapshot = self.active_plan_snapshot(plan.current_version_id)
        periods = self.repository.forecast_period_rows(snapshot.id)
        actual_by_period = self.actual_totals_by_period(plan_id, periods)
        observations_by_period = self.balance_observations_by_period(plan_id, periods)
        comparisons = []
        for row in periods:
            period_id = row[0]
            actual = actual_by_period.get(period_id, {})
            observations = observations_by_period.get(period_id, {})
            debt_balances = observations.get("debt_balances", ())
            planned_income = from_cents(row[3])
            planned_bills = from_cents(row[4])
            planned_personal = from_cents(row[6])
            planned_debt_minimums = from_cents(row[8])
            planned_snowball = from_cents(row[9])
            planned_savings = from_cents(row[10])
            planned_withdrawal = from_cents(row[11])
            planned_remaining = from_cents(row[12])
            planned = {
                "income": planned_income,
                "bills": planned_bills,
                "bill_activity": money(
                    planned_bills - from_cents(row[5])
                ),
                "debt": money(planned_debt_minimums + planned_snowball),
                "savings": planned_savings,
                "personal": planned_personal,
            }
            completeness = self.actual_data_completeness(planned, set(actual))
            actual_income = actual.get(ActualEntryType.INCOME_RECEIVED)
            actual_bills = actual.get(ActualEntryType.BILL_PAID)
            actual_debt = actual.get(ActualEntryType.DEBT_PAYMENT)
            actual_savings = actual.get(ActualEntryType.SAVINGS_DEPOSIT)
            actual_withdrawal = actual.get(ActualEntryType.SAVINGS_WITHDRAWAL)
            actual_personal = actual.get(ActualEntryType.PERSONAL_SPENDING)
            actual_adjustment = actual.get(ActualEntryType.ADJUSTMENT)
            actual_remaining = self.actual_remaining(
                actual_income,
                actual_bills,
                actual_debt,
                actual_savings,
                actual_personal,
                actual_withdrawal,
                actual_adjustment,
                completeness.missing_categories,
            )
            status = self.period_status(
                actual,
                actual_remaining,
                planned_remaining,
                completeness.missing_categories,
            )
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
                    debt_balance_variance=self.single_debt_variance(
                        debt_balances
                    ),
                    savings_balance_variance=observations.get("savings"),
                    data_completeness=self.data_completeness(
                        actual,
                        completeness.missing_categories,
                    ),
                    status=status,
                    interpretation=self.period_interpretation(status),
                    debt_balance_comparisons=debt_balances,
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

    def active_plan_snapshot(self, plan_version_id: int) -> ForecastSnapshotRecord:
        """Return the active version's forecast or an exact-config predecessor."""
        try:
            return self.latest_snapshot_for_version(plan_version_id)
        except ValueError:
            try:
                return self.repository.get_forecast_snapshot(
                    self.repository.latest_compatible_snapshot_id_for_version(
                        plan_version_id
                    )
                )
            except ValueError as exc:
                raise ValueError(NO_ACTIVE_FORECAST_MESSAGE) from exc

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
            raise ValueError(NO_ACTIVE_FORECAST_MESSAGE)
        snapshot = self.active_plan_snapshot(plan.current_version_id)
        fixed_expenses = self.sum_snapshot_column(snapshot.id, "fixed_expenses")
        personal_allowance = self.sum_snapshot_column(
            snapshot.id,
            "personal_allowance",
        )
        return {
            "income": self.sum_snapshot_column(snapshot.id, "income"),
            "bills": fixed_expenses,
            "bill_activity": money(fixed_expenses - personal_allowance),
            "debt": self.snapshot_debt_payments(snapshot.id),
            "savings": self.sum_snapshot_column(snapshot.id, "savings_deposit"),
            "personal": self.sum_snapshot_column(snapshot.id, "personal_expenses_used"),
            "remaining": self.sum_snapshot_column(snapshot.id, "checking_remaining"),
        }

    def actual_totals(
        self,
        plan_id: int,
        *,
        entries: list[Any] | None = None,
    ) -> dict[str, Decimal]:
        """Return actual totals for one plan."""
        totals = zero_actual_totals()
        actual_entries = (
            entries
            if entries is not None
            else self.repository.list_actual_entries(plan_id)
        )
        for entry in actual_entries:
            if entry.entry_type == ActualEntryType.INCOME_RECEIVED:
                totals["income"] += entry.amount
            elif entry.entry_type == ActualEntryType.BILL_PAID:
                totals["bills"] += entry.amount
            elif entry.entry_type == ActualEntryType.DEBT_PAYMENT:
                totals["debt"] += entry.amount
            elif entry.entry_type == ActualEntryType.SAVINGS_DEPOSIT:
                totals["savings"] += entry.amount
            elif entry.entry_type == ActualEntryType.SAVINGS_WITHDRAWAL:
                totals["savings"] -= entry.amount
            elif entry.entry_type == ActualEntryType.PERSONAL_SPENDING:
                totals["personal"] += entry.amount
            elif entry.entry_type == ActualEntryType.ADJUSTMENT:
                totals["adjustment"] += entry.amount
        return {key: money(value) for key, value in totals.items()}

    @staticmethod
    def actual_data_completeness(
        planned: dict[str, Decimal],
        recorded_entry_types: set[ActualEntryType],
    ) -> ActualDataCompleteness:
        """Classify applicable actual categories from planned values and presence."""
        planned_by_category = {
            ActualEntryType.INCOME_RECEIVED: planned["income"],
            ActualEntryType.BILL_PAID: planned.get(
                "bill_activity",
                planned["bills"],
            ),
            ActualEntryType.DEBT_PAYMENT: planned["debt"],
            ActualEntryType.SAVINGS_DEPOSIT: planned["savings"],
            ActualEntryType.PERSONAL_SPENDING: planned["personal"],
        }
        expected = tuple(
            category
            for category in COMPLETENESS_CATEGORY_ORDER
            if planned_by_category[category] != Decimal("0.00")
        )
        recorded = tuple(
            category for category in expected if category in recorded_entry_types
        )
        missing = tuple(
            category for category in expected if category not in recorded_entry_types
        )
        return ActualDataCompleteness(expected, recorded, missing)

    def actual_totals_by_period(
        self,
        plan_id: int,
        periods: list[tuple[Any, ...]],
    ) -> dict[int, dict[ActualEntryType, Decimal]]:
        """Return actual totals grouped against the selected forecast periods."""
        totals: dict[int, dict[ActualEntryType, Decimal]] = {}
        for actual in self.repository.list_actual_entries(plan_id):
            period_id = self.period_id_for_date(periods, actual.entry_date)
            if period_id is None:
                continue
            bucket = totals.setdefault(period_id, {})
            bucket[actual.entry_type] = money(
                bucket.get(actual.entry_type, Decimal("0.00")) + actual.amount
            )
        return totals

    def balance_observations_by_period(
        self,
        plan_id: int,
        periods: list[tuple[Any, ...]],
    ) -> dict[int, dict[str, Any]]:
        """Return observation variances against the selected forecast periods."""
        debt_observations: dict[int, dict[str, BalanceObservation]] = {}
        savings_observations: dict[int, BalanceObservation] = {}
        for observation in self.repository.list_balance_observations(plan_id):
            period_id = self.period_id_for_date(periods, observation.observation_date)
            if period_id is None:
                continue
            if (
                observation.observation_type
                == ActualEntryType.DEBT_BALANCE_OBSERVATION
            ):
                debt_name = observation.debt_identifier or "Unnamed debt"
                debt_observations.setdefault(period_id, {})[debt_name] = observation
            else:
                savings_observations[period_id] = observation

        observations: dict[int, dict[str, Any]] = {}
        with self.repository.connection() as conn:
            for period_id, period_observations in debt_observations.items():
                bucket = observations.setdefault(period_id, {})
                bucket["debt_balances"] = tuple(
                    self.compare_debt_observation(conn, period_id, observation)
                    for _, observation in sorted(period_observations.items())
                )
            for period_id, observation in savings_observations.items():
                bucket = observations.setdefault(period_id, {})
                planned = self.planned_balance_for_period(conn, period_id, "savings")
                bucket["savings"] = money(observation.balance - planned)
        return observations

    def latest_debt_balance_comparisons(
        self,
        plan_id: int,
    ) -> tuple[DebtBalanceComparison, ...]:
        """Return each debt's latest observation against the active forecast."""
        plan = self.repository.get_plan(plan_id)
        if plan.current_version_id is None:
            return ()
        try:
            snapshot = self.active_plan_snapshot(plan.current_version_id)
        except ValueError:
            return ()
        periods = self.repository.forecast_period_rows(snapshot.id)
        by_period = self.balance_observations_by_period(plan_id, periods)
        latest: dict[str, DebtBalanceComparison] = {}
        for row in periods:
            for comparison in by_period.get(row[0], {}).get("debt_balances", ()):
                latest[comparison.debt_name] = comparison
        return tuple(latest[name] for name in sorted(latest))

    @staticmethod
    def compare_debt_observation(
        conn: sqlite3.Connection,
        period_id: int,
        observation: BalanceObservation,
    ) -> DebtBalanceComparison:
        """Compare one observed debt with an exact-name active snapshot row."""
        debt_name = observation.debt_identifier or "Unnamed debt"
        rows = conn.execute(
            """
            SELECT ending_balance
            FROM debt_snapshots
            WHERE forecast_period_id = ? AND debt_name = ?
            ORDER BY id
            """,
            (period_id, debt_name),
        ).fetchall()
        if not rows:
            return DebtBalanceComparison(
                debt_name=debt_name,
                observed_balance=observation.balance,
                observation_date=observation.observation_date,
                status="No matching debt in active forecast",
            )
        if len(rows) > 1:
            return DebtBalanceComparison(
                debt_name=debt_name,
                observed_balance=observation.balance,
                observation_date=observation.observation_date,
                status="Ambiguous debt name in active forecast",
            )
        planned = from_cents(rows[0][0])
        return DebtBalanceComparison(
            debt_name=debt_name,
            planned_balance=planned,
            observed_balance=observation.balance,
            variance=money(observation.balance - planned),
            observation_date=observation.observation_date,
            status="Matched",
        )

    @staticmethod
    def single_debt_variance(
        comparisons: tuple[DebtBalanceComparison, ...],
    ) -> Decimal | None:
        """Preserve the legacy scalar only for one unambiguous matched debt."""
        if len(comparisons) != 1 or comparisons[0].status != "Matched":
            return None
        return comparisons[0].variance

    @staticmethod
    def period_id_for_date(
        periods: list[tuple[Any, ...]],
        value: date,
    ) -> int | None:
        """Match a date to one period using paycheck-date windows."""
        for index, row in enumerate(periods):
            start_date = date.fromisoformat(row[2])
            next_start = (
                date.fromisoformat(periods[index + 1][2])
                if index + 1 < len(periods)
                else date.max
            )
            if start_date <= value < next_start:
                return int(row[0])
        return None

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
        adjustment: Decimal | None,
        missing_categories: tuple[ActualEntryType, ...],
    ) -> Decimal | None:
        """Calculate remaining cash from actual entries."""
        if missing_categories:
            return None
        return money(
            (income or Decimal("0.00"))
            - (bills or Decimal("0.00"))
            - (debt or Decimal("0.00"))
            - (savings or Decimal("0.00"))
            - (personal or Decimal("0.00"))
            + (withdrawal or Decimal("0.00"))
            + (adjustment or Decimal("0.00"))
        )

    @staticmethod
    def period_status(
        actual: dict[ActualEntryType, Decimal],
        actual_remaining: Decimal | None,
        planned_remaining: Decimal,
        missing_categories: tuple[ActualEntryType, ...],
    ) -> str:
        """Return status for one forecast-vs-actual period."""
        if not actual:
            return "No actual activity recorded"
        if missing_categories:
            return "Insufficient actual data"
        variance_value = money(actual_remaining - planned_remaining)
        if variance_value > Decimal("10.00"):
            return "Ahead of plan"
        if variance_value < Decimal("-10.00"):
            return "Needs review"
        return "On track"

    @staticmethod
    def data_completeness(
        actual: dict[ActualEntryType, Decimal],
        missing_categories: tuple[ActualEntryType, ...],
    ) -> str:
        """Return data completeness for one actual period."""
        if not actual:
            return "none"
        return "partial" if missing_categories else "complete"

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
        "adjustment": Decimal("0.00"),
    }
