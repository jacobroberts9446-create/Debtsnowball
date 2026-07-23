"""Persistent local plan history, comparison, and export services."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.database import Database, LATEST_SCHEMA_VERSION
from app.budget_engine import PayPeriodSummary
from app.history.repository import HistoryRepository
from app.money import from_cents, money, to_cents
from app.models import (
    ActualEntryType,
    ActualTransaction,
    AssumptionDifference,
    BalanceObservation,
    AllocationExplanation,
    AllocationReasonCode,
    DataQualityWarning,
    DataWarningSeverity,
    ForecastActualComparison,
    ForecastActualPeriodComparison,
    ForecastPeriod,
    ForecastSnapshotRecord,
    ForecastSummary,
    Plan,
    PlanComparison,
    PlanVersion,
)
from app.serialization import dumps_json, to_json_ready

APPLICATION_VERSION = "1.0.0"
FORECAST_ENGINE_VERSION = "2"
EXPORT_FORMAT_VERSION = 1

MONEY_CONFIG_KEYS = {
    "paycheck",
    "rent_per_paycheck",
    "insurance_per_paycheck",
    "personal_per_paycheck",
    "starting_savings",
    "savings_goal",
    "amount",
    "balance",
    "minimum",
    "target_amount",
    "starting_balance",
    "starting_balance_override",
    "extra_per_paycheck",
    "maximum_extra_per_paycheck",
}


def utc_timestamp() -> str:
    """Return an ISO timestamp without machine-specific local time."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically for snapshots and fingerprints."""
    return dumps_json(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    """Return a SHA-256 fingerprint of deterministic JSON content."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_config_snapshot(config: Any) -> dict[str, Any]:
    """Return a stable, JSON-safe configuration snapshot without local paths."""
    if hasattr(config, "settings") and hasattr(config, "debts"):
        return _normalize_config_object(config)
    return _normalize_config_value(config)


def config_fingerprint(config: Any) -> str:
    """Return a deterministic fingerprint for normalized configuration inputs."""
    return fingerprint(normalized_config_snapshot(config))


def forecast_fingerprint(forecast: ForecastSummary) -> str:
    """Return a deterministic fingerprint for stable forecast result data."""
    payload = {
        "forecast_start_date": forecast.forecast_start_date,
        "forecast_end_date": forecast.forecast_end_date,
        "debt_free_date": forecast.debt_free_date,
        "savings_goal_date": forecast.savings_goal_date,
        "starting_debt": forecast.starting_debt,
        "total_interest_paid": forecast.total_interest_paid,
        "total_minimum_payments": forecast.total_minimum_payments,
        "total_snowball_payments": forecast.total_snowball_payments,
        "ending_savings": forecast.ending_savings,
        "remaining_debt": forecast.remaining_debt,
        "completed": forecast.completed,
        "debt_payoffs": forecast.debt_payoffs,
        "periods": forecast.periods,
        "savings_stage_results": forecast.savings_stage_results,
        "planned_withdrawal_results": forecast.planned_withdrawal_results,
    }
    return fingerprint(payload)


def portable_content_fingerprint(payload: dict[str, Any]) -> str:
    """Fingerprint stable portable export content without local IDs or timestamps."""
    return fingerprint(_portable_content(payload))


def explain_forecast_period(period: ForecastPeriod) -> AllocationExplanation:
    """Build reason codes and a neutral plain-language allocation explanation."""
    codes: list[AllocationReasonCode] = []
    messages: list[str] = []

    if period.minimums_paid > Decimal("0.00"):
        codes.append(AllocationReasonCode.MINIMUM_DEBT_PAYMENT)
        messages.append("Required debt minimum payments were reserved first.")
    if period.savings_contribution > Decimal("0.00"):
        codes.append(AllocationReasonCode.NORMAL_SAVINGS)
        messages.append(
            f"{_fmt(period.savings_contribution)} was allocated to savings."
        )
    if period.snowball_reduction > Decimal("0.00"):
        codes.append(AllocationReasonCode.DEADLINE_SAVINGS_REDIRECTION)
        messages.append(
            f"{_fmt(period.snowball_reduction)} was redirected from the debt "
            "snowball because an active savings goal needed funding."
        )
    if period.personal_expense_reduction > Decimal("0.00"):
        codes.append(AllocationReasonCode.PERSONAL_EXPENSE_REDUCTION)
        messages.append(
            f"Personal spending was reduced by {_fmt(period.personal_expense_reduction)} "
            "to support the active savings deadline."
        )
    if period.snowball_paid > Decimal("0.00"):
        codes.append(AllocationReasonCode.DEBT_SNOWBALL)
        messages.append(
            f"{_fmt(period.snowball_paid)} was applied to the debt snowball."
        )
    if period.planned_withdrawal_amount > Decimal("0.00"):
        codes.append(AllocationReasonCode.WITHDRAWAL_PROCESSED)
        messages.append(
            f"A planned savings withdrawal of {_fmt(period.planned_withdrawal_amount)} "
            "was processed."
        )
    if not codes:
        codes.append(AllocationReasonCode.NO_AVAILABLE_SURPLUS)
        messages.append("No available surplus was left after required allocations.")

    return AllocationExplanation(reason_codes=codes, explanation=" ".join(messages))


def build_warnings(
    forecast: ForecastSummary,
    *,
    high_apr_debts: list[tuple[str, Decimal]] | None = None,
) -> list[DataQualityWarning]:
    """Create nonjudgmental review warnings for a forecast."""
    warnings: list[DataQualityWarning] = []
    for period in forecast.periods:
        if period.available_after_required_payments < Decimal("0.00"):
            warnings.append(
                DataQualityWarning(
                    code="EXPENSES_EXCEED_INCOME",
                    severity=DataWarningSeverity.IMPORTANT,
                    message="Required expenses exceeded income for this pay period.",
                    relevant_date=period.paycheck_date,
                    suggested_action="Review income, bills, and minimum payment timing.",
                )
            )
        if period.projected_savings_shortfall > Decimal("0.00"):
            warnings.append(
                DataQualityWarning(
                    code="SAVINGS_DEADLINE_NEEDS_REVIEW",
                    severity=DataWarningSeverity.REVIEW,
                    message="A savings deadline may need additional funding.",
                    relevant_date=period.paycheck_date,
                    relevant_name=period.active_savings_goal_name,
                    suggested_action="Review the savings target, date, or available cash.",
                )
            )
        if period.personal_expense_reduction > Decimal("0.00"):
            warnings.append(
                DataQualityWarning(
                    code="PERSONAL_SPENDING_REDUCTION_PROJECTED",
                    severity=DataWarningSeverity.REVIEW,
                    message="The forecast projects a temporary personal spending reduction.",
                    relevant_date=period.paycheck_date,
                    suggested_action="Confirm this temporary reduction is realistic.",
                )
            )

    for name, apr in high_apr_debts or []:
        if apr >= Decimal("30"):
            warnings.append(
                DataQualityWarning(
                    code="HIGH_APR_DEBT",
                    severity=DataWarningSeverity.REVIEW,
                    message="A debt has a high APR and may deserve extra review.",
                    relevant_name=name,
                    suggested_action="Confirm the APR and minimum payment are current.",
                )
            )

    if not forecast.completed:
        warnings.append(
            DataQualityWarning(
                code="FORECAST_HORIZON_REACHED",
                severity=DataWarningSeverity.REVIEW,
                message="The payoff forecast did not complete within the configured horizon.",
                suggested_action="Review assumptions or extend the forecast horizon.",
            )
        )
    return warnings


class PlanHistoryService:
    """High-level API for durable local plan history."""

    def __init__(self, database: Database | str | Path = "output/debtsnowball.sqlite") -> None:
        self.repository = HistoryRepository(database)
        self.database = self.repository.database
        self.repository.initialize()

    def create_plan(
        self,
        name: str,
        config: Any,
        *,
        description: str = "",
        change_note: str = "Initial version",
        source: str = "manual",
        notes: str = "",
    ) -> Plan:
        """Create a plan and immutable version 1 from the supplied inputs."""
        timestamp = utc_timestamp()
        snapshot = normalized_config_snapshot(config)
        snapshot_json = canonical_json(snapshot)
        config_hash = fingerprint(snapshot)
        with self.repository.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO plans (name, description, created_at, updated_at, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, description, timestamp, timestamp, notes),
            )
            plan_id = int(cursor.lastrowid)
            version_id = self._insert_plan_version(
                conn,
                plan_id=plan_id,
                version_number=1,
                config_snapshot=snapshot_json,
                config_fingerprint=config_hash,
                change_note=change_note,
                source=source,
                active=True,
            )
            conn.execute(
                "UPDATE plans SET current_version_id = ? WHERE id = ?",
                (version_id, plan_id),
            )
        return self.get_plan(plan_id)

    def save_plan_version(
        self,
        plan_id: int,
        config: Any,
        *,
        change_note: str = "",
        source: str = "manual",
        force: bool = False,
        active: bool = True,
    ) -> PlanVersion:
        """Save changed inputs as the next immutable version."""
        snapshot = normalized_config_snapshot(config)
        snapshot_json = canonical_json(snapshot)
        config_hash = fingerprint(snapshot)
        versions = self.list_plan_versions(plan_id)
        if versions and versions[-1].config_fingerprint == config_hash and not force:
            return versions[-1]

        version_number = (versions[-1].version_number if versions else 0) + 1
        with self.repository.transaction() as conn:
            if active:
                self._enable_plan_version_mutation(conn)
                conn.execute(
                    "UPDATE plan_versions SET active = 0 WHERE plan_id = ?",
                    (plan_id,),
                )
                self._disable_plan_version_mutation(conn)
            version_id = self._insert_plan_version(
                conn,
                plan_id=plan_id,
                version_number=version_number,
                config_snapshot=snapshot_json,
                config_fingerprint=config_hash,
                change_note=change_note,
                source=source,
                active=active,
            )
            if active:
                conn.execute(
                    """
                    UPDATE plans
                    SET current_version_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, utc_timestamp(), plan_id),
                )
        return self.get_plan_version(version_id)

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
        warnings = warnings or []
        forecast_hash = forecast_fingerprint(forecast)
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
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
                        utc_timestamp(),
                        forecast.forecast_start_date.isoformat(),
                        forecast.forecast_end_date.isoformat(),
                        _date_value(forecast.debt_free_date),
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
                self._save_forecast_periods(
                    conn,
                    snapshot_id,
                    forecast,
                    starting_savings,
                    pay_period_summaries=pay_period_summaries,
                    starting_debts=starting_debts,
                )
                self._save_warnings(conn, snapshot_id, warnings)
                self._sync_snapshot_totals(conn, snapshot_id)
        return self.get_forecast_snapshot(snapshot_id)

    def list_plans(self, *, include_archived: bool = False) -> list[Plan]:
        """Return lightweight plan summaries."""
        return self.repository.list_plans(include_archived=include_archived)

    def get_plan(self, plan_id: int) -> Plan:
        """Return one plan by ID."""
        return self.repository.get_plan(plan_id)

    def get_plan_version(self, version_id: int) -> PlanVersion:
        """Return one immutable plan version by ID."""
        return self.repository.get_plan_version(version_id)

    def list_plan_versions(self, plan_id: int) -> list[PlanVersion]:
        """Return immutable versions for a plan."""
        return self.repository.list_plan_versions(plan_id)

    def archive_plan(self, plan_id: int) -> None:
        """Soft-delete a plan from normal listings."""
        self.repository.archive_plan(plan_id, utc_timestamp())

    def restore_plan_version(self, version_id: int, *, change_note: str = "Restored") -> PlanVersion:
        """Restore an older version by creating a new immutable version."""
        version = self.get_plan_version(version_id)
        snapshot = json.loads(version.config_snapshot)
        return self.save_plan_version(
            version.plan_id,
            snapshot,
            change_note=change_note,
            source="restore",
            force=True,
        )

    def add_actual_entry(
        self,
        plan_id: int,
        entry_date: date,
        entry_type: ActualEntryType | str,
        amount: Decimal,
        *,
        category: str = "",
        description: str = "",
        source: str = "manual",
        debt_identifier: str | None = None,
        corrected_entry_id: int | None = None,
        note: str = "",
    ) -> ActualTransaction:
        """Post an immutable actual financial activity entry."""
        entry_type = ActualEntryType(str(entry_type))
        period_id, match_method = self._match_period_for_date(plan_id, entry_date)
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO actual_transactions (
                        plan_id, entry_date, entry_type, debt_identifier, amount,
                        category, description, source, created_at,
                        corrected_entry_id, note
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        entry_date.isoformat(),
                        entry_type.value,
                        debt_identifier,
                        to_cents(amount),
                        category,
                        description,
                        source,
                        utc_timestamp(),
                        corrected_entry_id,
                        note,
                    ),
                )
                if period_id is not None:
                    conn.execute(
                        """
                        UPDATE actual_transactions
                        SET forecast_period_id = ?, match_method = ?, matched_at = ?
                        WHERE id = ?
                        """,
                        (period_id, match_method, utc_timestamp(), int(cursor.lastrowid)),
                    )
        return self.get_actual_entry(int(cursor.lastrowid))

    def reverse_actual_entry(self, entry_id: int, *, note: str = "Correction") -> ActualTransaction:
        """Reverse a posted entry while preserving the audit trail."""
        original = self.get_actual_entry(entry_id)
        return self.add_actual_entry(
            original.plan_id,
            original.entry_date,
            original.entry_type,
            -original.amount,
            category=original.category,
            description=f"Reversal: {original.description}",
            source="correction",
            debt_identifier=original.debt_identifier,
            corrected_entry_id=entry_id,
            note=note,
        )

    def add_balance_observation(
        self,
        plan_id: int,
        observation_date: date,
        observation_type: ActualEntryType | str,
        balance: Decimal,
        *,
        source: str = "manual",
        debt_identifier: str | None = None,
        note: str = "",
    ) -> BalanceObservation:
        """Record an optional observed debt or savings balance."""
        observation_type = ActualEntryType(str(observation_type))
        if observation_type not in {
            ActualEntryType.DEBT_BALANCE_OBSERVATION,
            ActualEntryType.SAVINGS_BALANCE_OBSERVATION,
        }:
            raise ValueError("balance observation type must be debt or savings balance.")
        period_id, method = self._match_period_for_date(plan_id, observation_date)
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO balance_observations (
                        plan_id, observation_date, observation_type, debt_identifier,
                        balance, source, note, created_at, forecast_period_id,
                        match_method
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        observation_date.isoformat(),
                        observation_type.value,
                        debt_identifier,
                        to_cents(balance),
                        source,
                        note,
                        utc_timestamp(),
                        period_id,
                        method,
                    ),
                )
        return self.get_balance_observation(int(cursor.lastrowid))

    def get_balance_observation(self, observation_id: int) -> BalanceObservation:
        """Return one balance observation by ID."""
        return self.repository.get_balance_observation(observation_id)

    def get_actual_entry(self, entry_id: int) -> ActualTransaction:
        """Return one actual transaction by ID."""
        return self.repository.get_actual_entry(entry_id)

    def compare_forecast_to_actual(self, plan_id: int) -> ForecastActualComparison:
        """Compare persisted forecast totals with posted actual totals."""
        planned = self._latest_plan_forecast_totals(plan_id)
        actual = self._actual_totals(plan_id)
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
        plan = self.get_plan(plan_id)
        if plan.current_version_id is None:
            return []
        snapshot = self._latest_snapshot_for_version(plan.current_version_id)
        periods = self._forecast_period_rows(snapshot.id)
        actual_by_period = self._actual_totals_by_period(plan_id)
        observations_by_period = self._balance_observations_by_period(plan_id)
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
            actual_remaining = self._actual_remaining(
                actual_income,
                actual_bills,
                actual_debt,
                actual_savings,
                actual_personal,
                actual_withdrawal,
            )
            status = self._period_status(actual, actual_remaining, planned_remaining)
            comparisons.append(
                ForecastActualPeriodComparison(
                    forecast_period_id=period_id,
                    pay_date=date.fromisoformat(row[2]),
                    planned_income=planned_income,
                    actual_income=actual_income,
                    income_variance=_variance(actual_income, planned_income),
                    planned_bills=planned_bills,
                    actual_bills=actual_bills,
                    bills_variance=_variance(actual_bills, planned_bills),
                    planned_debt_minimums=planned_debt_minimums,
                    actual_debt_payments=actual_debt,
                    debt_payment_variance=_variance(
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
                    data_completeness=self._data_completeness(actual),
                    status=status,
                    interpretation=self._period_interpretation(status),
                )
            )
        return comparisons

    def compare_plan_versions(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> PlanComparison:
        """Compare two saved forecast snapshots with neutral tradeoff wording."""
        earlier = self._latest_snapshot_for_version(earlier_version_id)
        later = self._latest_snapshot_for_version(later_version_id)
        earlier_payoffs = self._payoff_order(earlier.id)
        later_payoffs = self._payoff_order(later.id)
        assumption_differences = self.compare_assumptions(
            earlier_version_id,
            later_version_id,
        )
        date_delta = _date_delta_days(earlier.debt_free_date, later.debt_free_date)
        interest_difference = money(
            later.total_projected_interest - earlier.total_projected_interest
        )
        savings_difference = money(later.ending_savings - earlier.ending_savings)
        debt_payment_difference = money(
            self._snapshot_debt_payments(later.id)
            - self._snapshot_debt_payments(earlier.id)
        )
        personal_difference = money(
            self._snapshot_personal_spending(later.id)
            - self._snapshot_personal_spending(earlier.id)
        )
        first_difference = self._first_different_period(earlier.id, later.id)
        explanation = self._comparison_explanation(date_delta, interest_difference)
        return PlanComparison(
            earlier_version=self.get_plan_version(earlier_version_id).version_number,
            later_version=self.get_plan_version(later_version_id).version_number,
            debt_free_date_difference_days=date_delta,
            interest_difference=interest_difference,
            debt_payment_difference=debt_payment_difference,
            savings_difference=savings_difference,
            personal_spending_difference=personal_difference,
            pay_period_difference=self._period_count(later.id) - self._period_count(earlier.id),
            first_different_period=first_difference,
            payoff_order_changed=earlier_payoffs != later_payoffs,
            deadline_priority_changed=self._deadline_priority_changed(
                earlier_version_id,
                later_version_id,
            ),
            feasible=earlier.ending_debt == Decimal("0.00") and later.ending_debt == Decimal("0.00"),
            explanation=self._rich_comparison_explanation(
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
        earlier = json.loads(self.get_plan_version(earlier_version_id).config_snapshot)
        later = json.loads(self.get_plan_version(later_version_id).config_snapshot)
        differences: list[AssumptionDifference] = []
        self._compare_mapping("Budget", earlier.get("budget", {}), later.get("budget", {}), differences)
        self._compare_named_collection("Bills", earlier.get("bills", []), later.get("bills", []), differences)
        self._compare_named_collection("Debts", earlier.get("debts", []), later.get("debts", []), differences)
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

    def plan_summary(self, plan_version_id: int) -> str:
        """Return a plain-language summary for a saved plan version."""
        snapshot = self._latest_snapshot_for_version(plan_version_id)
        first_payoff = self._payoff_order(snapshot.id)
        first_debt = first_payoff[0] if first_payoff else "no debt"
        return (
            f"The forecast estimates starting debt of {_fmt(snapshot.starting_debt)} "
            f"and starting savings of {_fmt(snapshot.starting_savings)}. "
            f"Based on the current assumptions, the debt-free date is "
            f"{snapshot.debt_free_date or 'not reached in the forecast horizon'}. "
            f"Projected interest is {_fmt(snapshot.total_projected_interest)}. "
            f"The first expected payoff is {first_debt}. This may change if income, "
            "expenses, rates, or payments change."
        )

    def export_plan_json(self, plan_id: int, path: str | Path) -> Path:
        """Export a saved plan to portable local JSON."""
        plan = self.get_plan(plan_id)
        versions = self.list_plan_versions(plan_id)
        payload = {
            "format_version": EXPORT_FORMAT_VERSION,
            "application_version": APPLICATION_VERSION,
            "forecast_engine_version": FORECAST_ENGINE_VERSION,
            "source_schema_version": LATEST_SCHEMA_VERSION,
            "exported_at": utc_timestamp(),
            "portable_id": _portable_id("plan", plan.id, plan.name),
            "plan": asdict(plan),
            "versions": [asdict(version) for version in versions],
            "forecast_snapshots": self._export_forecast_snapshots(plan_id),
            "forecast_periods": self._export_forecast_periods(plan_id),
            "debt_snapshots": self._export_debt_snapshots(plan_id),
            "savings_snapshots": self._export_savings_snapshots(plan_id),
            "warnings": self._export_warnings(plan_id),
            "actual_entries": self._export_actual_transactions(plan_id),
            "balance_observations": self._export_balance_observations(plan_id),
        }
        self._attach_snapshot_history_fingerprints(payload)
        payload["portable_content_fingerprint"] = portable_content_fingerprint(payload)
        output = Path(path)
        output.write_text(canonical_json(payload), encoding="utf-8")
        return output

    def import_plan_json(self, path: str | Path, *, new_name: str | None = None) -> Plan:
        """Import a portable local JSON plan export transactionally."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format_version") != EXPORT_FORMAT_VERSION:
            raise ValueError("unsupported plan export format version.")
        if "plan" not in payload or "versions" not in payload:
            raise ValueError("plan export is missing required fields.")
        self._validate_import_payload(payload)

        plan_data = payload["plan"]
        versions = payload["versions"]
        plan_name = new_name or plan_data["name"]
        import_hash = portable_content_fingerprint(payload)
        if self._import_fingerprint_exists(import_hash):
            raise ValueError("this plan export has already been imported.")
        if self._plan_name_exists(plan_name):
            raise ValueError("a nonarchived plan with this name already exists.")
        if not versions:
            raise ValueError("plan export does not contain any versions.")

        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO plans (name, description, created_at, updated_at,
                                       archived, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_name,
                        plan_data.get("description", ""),
                        utc_timestamp(),
                        utc_timestamp(),
                        0,
                        plan_data.get("notes", ""),
                    ),
                )
                plan_id = int(cursor.lastrowid)
                active_version = next(version for version in versions if version["active"])
                latest_version_id = None
                version_id_map: dict[int, int] = {}
                for version in versions:
                    snapshot = json.loads(version["config_snapshot"])
                    snapshot_json = canonical_json(snapshot)
                    version_id = self._insert_plan_version(
                        conn,
                        plan_id=plan_id,
                        version_number=int(version["version_number"]),
                        config_snapshot=snapshot_json,
                        config_fingerprint=fingerprint(snapshot),
                        change_note=version.get("change_note", "Imported"),
                        source="import",
                        active=bool(version.get("active", False)),
                    )
                    version_id_map[int(version["id"])] = version_id
                    if int(version["id"]) == int(active_version["id"]):
                        latest_version_id = version_id
                snapshot_id_map = self._import_forecast_snapshots(
                    conn,
                    payload.get("forecast_snapshots", []),
                    version_id_map,
                )
                period_id_map = self._import_forecast_periods(
                    conn,
                    payload.get("forecast_periods", []),
                    snapshot_id_map,
                )
                self._import_debt_snapshots(
                    conn,
                    payload.get("debt_snapshots", []),
                    period_id_map,
                )
                self._import_savings_snapshots(
                    conn,
                    payload.get("savings_snapshots", []),
                    period_id_map,
                )
                self._import_warnings(
                    conn,
                    payload.get("warnings", []),
                    snapshot_id_map,
                )
                self._import_actual_transactions(
                    conn,
                    payload.get("actual_entries", []),
                    plan_id,
                    period_id_map,
                )
                self._import_balance_observations(
                    conn,
                    payload.get("balance_observations", []),
                    plan_id,
                    period_id_map,
                )
                conn.execute(
                    "UPDATE plans SET current_version_id = ? WHERE id = ?",
                    (latest_version_id, plan_id),
                )
                conn.execute(
                    """
                    INSERT INTO imported_plan_fingerprints (
                        plan_id, import_fingerprint, imported_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (plan_id, import_hash, utc_timestamp()),
                )
        return self.get_plan(plan_id)

    def export_forecast_periods_csv(self, snapshot_id: int, path: str | Path) -> Path:
        """Export forecast periods as a flat local CSV."""
        rows = self._forecast_period_rows(snapshot_id)
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["sequence", "pay_date", "income", "savings", "snowball"],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "sequence": row[1],
                        "pay_date": row[2],
                        "income": f"{from_cents(row[3]):.2f}",
                        "savings": f"{from_cents(row[10]):.2f}",
                        "snowball": f"{from_cents(row[9]):.2f}",
                    }
                )
        return output

    def export_csv_bundle(self, plan_id: int, directory: str | Path) -> dict[str, Path]:
        """Export all useful history tables to user-facing CSV files."""
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        paths = {
            "plan_versions": self._write_csv(
                output / "plan_versions.csv",
                [asdict(version) for version in self.list_plan_versions(plan_id)],
            ),
            "forecast_periods": self._write_csv(
                output / "forecast_periods.csv",
                self._export_forecast_periods(plan_id),
            ),
            "debt_history": self._write_csv(
                output / "debt_history.csv",
                self._export_debt_snapshots(plan_id),
            ),
            "savings_history": self._write_csv(
                output / "savings_history.csv",
                self._export_savings_snapshots(plan_id),
            ),
            "actual_transactions": self._write_csv(
                output / "actual_transactions.csv",
                self._export_actual_transactions(plan_id),
            ),
            "forecast_vs_actual": self._write_csv(
                output / "forecast_vs_actual.csv",
                [asdict(row) for row in self.compare_forecast_to_actual_periods(plan_id)],
            ),
            "warnings": self._write_csv(output / "warnings.csv", self._export_warnings(plan_id)),
        }
        return paths

    def history_report_rows(self, plan_id: int) -> dict[str, list[dict[str, Any]]]:
        """Return detailed rows for optional history workbooks."""
        return {
            "forecast_snapshots": self._export_forecast_snapshots(plan_id),
            "period_comparisons": [
                asdict(row) for row in self.compare_forecast_to_actual_periods(plan_id)
            ],
            "debt_history": self._export_debt_snapshots(plan_id),
            "savings_history": self._export_savings_snapshots(plan_id),
            "warnings": self._export_warnings(plan_id),
        }

    def delete_plan_permanently(
        self,
        plan_id: int,
        *,
        confirmation_name: str,
        export_path: str | Path | None = None,
    ) -> None:
        """Permanently delete an archived plan after exact confirmation."""
        plan = self.get_plan(plan_id)
        if not plan.archived:
            raise ValueError("archive the plan before permanent deletion.")
        if confirmation_name != plan.name:
            raise ValueError("confirmation name does not match the plan name.")
        if export_path is not None:
            self.export_plan_json(plan_id, export_path)
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                self._enable_plan_version_mutation(conn)
                conn.execute(
                    "UPDATE plans SET current_version_id = NULL WHERE id = ?",
                    (plan_id,),
                )
                snapshot_ids = [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT fs.id
                        FROM forecast_snapshots fs
                        JOIN plan_versions pv ON pv.id = fs.plan_version_id
                        WHERE pv.plan_id = ?
                        """,
                        (plan_id,),
                    )
                ]
                for snapshot_id in snapshot_ids:
                    period_ids = [
                        row[0]
                        for row in conn.execute(
                            "SELECT id FROM forecast_periods WHERE forecast_snapshot_id = ?",
                            (snapshot_id,),
                        )
                    ]
                    for period_id in period_ids:
                        conn.execute(
                            "DELETE FROM debt_snapshots WHERE forecast_period_id = ?",
                            (period_id,),
                        )
                        conn.execute(
                            "DELETE FROM savings_snapshots WHERE forecast_period_id = ?",
                            (period_id,),
                        )
                    conn.execute(
                        "DELETE FROM forecast_periods WHERE forecast_snapshot_id = ?",
                        (snapshot_id,),
                    )
                    conn.execute(
                        "DELETE FROM data_quality_warnings WHERE forecast_snapshot_id = ?",
                        (snapshot_id,),
                    )
                conn.execute(
                    """
                    DELETE FROM forecast_snapshots
                    WHERE plan_version_id IN (
                        SELECT id FROM plan_versions WHERE plan_id = ?
                    )
                    """,
                    (plan_id,),
                )
                conn.execute("DELETE FROM actual_transactions WHERE plan_id = ?", (plan_id,))
                conn.execute("DELETE FROM balance_observations WHERE plan_id = ?", (plan_id,))
                conn.execute(
                    "DELETE FROM imported_plan_fingerprints WHERE plan_id = ?",
                    (plan_id,),
                )
                conn.execute("DELETE FROM plan_versions WHERE plan_id = ?", (plan_id,))
                self._disable_plan_version_mutation(conn)
                conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))

    def list_actual_entries(self, plan_id: int) -> list[ActualTransaction]:
        """Return actual entries for a plan ordered by date and ID."""
        return self.repository.list_actual_entries(plan_id)

    def list_balance_observations(self, plan_id: int) -> list[BalanceObservation]:
        """Return balance observations for a plan ordered by date and ID."""
        return self.repository.list_balance_observations(plan_id)

    def get_forecast_snapshot(self, snapshot_id: int) -> ForecastSnapshotRecord:
        """Return a persisted forecast snapshot record."""
        return self.repository.get_forecast_snapshot(snapshot_id)

    def _insert_plan_version(
        self,
        conn: sqlite3.Connection,
        *,
        plan_id: int,
        version_number: int,
        config_snapshot: str,
        config_fingerprint: str,
        change_note: str,
        source: str,
        active: bool,
    ) -> int:
        return self.repository.insert_plan_version(
            conn,
            plan_id=plan_id,
            version_number=version_number,
            created_at=utc_timestamp(),
            config_snapshot=config_snapshot,
            config_fingerprint=config_fingerprint,
            change_note=change_note,
            source=source,
            application_version=APPLICATION_VERSION,
            forecast_engine_version=FORECAST_ENGINE_VERSION,
            schema_version=LATEST_SCHEMA_VERSION,
            active=active,
        )

    def _enable_plan_version_mutation(self, conn: sqlite3.Connection) -> None:
        """Allow controlled version activation/deletion inside this transaction."""
        self.repository.enable_plan_version_mutation(conn)

    def _disable_plan_version_mutation(self, conn: sqlite3.Connection) -> None:
        """Re-enable database-level plan-version immutability triggers."""
        self.repository.disable_plan_version_mutation(conn)

    def _save_forecast_periods(
        self,
        conn: sqlite3.Connection,
        snapshot_id: int,
        forecast: ForecastSummary,
        starting_savings: Decimal,
        *,
        pay_period_summaries: list[PayPeriodSummary] | None,
        starting_debts: list[Any] | None = None,
    ) -> None:
        previous_savings = money(starting_savings)
        summaries_by_date = {
            summary.pay_date: summary for summary in pay_period_summaries or []
        }
        debt_states = self._initial_debt_states(starting_debts)
        payoff_dates = {
            payoff.debt_name: payoff.payoff_date for payoff in forecast.debt_payoffs
        }
        for index, period in enumerate(forecast.periods, start=1):
            summary = summaries_by_date.get(period.paycheck_date)
            explanation = explain_forecast_period(period)
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
            debt_states = self._save_debt_snapshots(
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
                    self._goal_deadline_for_period(forecast, period),
                    to_cents(period.projected_savings_shortfall),
                    "review" if period.projected_savings_shortfall > 0 else "on_track",
                    to_cents(period.savings_contribution),
                    self._nullable_cents(period.active_savings_target if has_active_goal else None),
                    self._nullable_cents(
                        None
                        if period.active_savings_target is None or not has_active_goal
                        else max(period.active_savings_target - ending_savings, Decimal("0.00"))
                    ),
                    self._nullable_cents(ending_savings if has_active_goal else None),
                    _date_value(period.paycheck_date) if withdrawal > Decimal("0.00") else None,
                    to_cents(withdrawal) if withdrawal > Decimal("0.00") else None,
                    "post_withdrawal"
                    if period.planned_withdrawal_amount > Decimal("0.00")
                    else "normal",
                    ",".join(code.value for code in explanation.reason_codes),
                    explanation.explanation,
                ),
            )
            previous_savings = ending_savings

    def _save_debt_snapshots(
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
            return self._save_aggregate_debt_snapshot(conn, period_id, period)

        interest_by_name = summary.debt_interest_by_name or {} if summary else {}
        minimums_by_name = summary.debt_minimums_by_name or {} if summary else {}
        snowball_by_name = summary.debt_snowball_by_name or {} if summary else {}
        paid_names = {debt.name for debt in summary.paid_off_debts} if summary else set()
        ending_by_name = {}
        if summary is not None:
            ending_by_name.update(
                {debt.name: money(debt.balance) for debt in summary.active_debt_balances}
            )
            ending_by_name.update({debt.name: Decimal("0.00") for debt in summary.paid_off_debts})
        use_engine_payment_detail = summary is not None and (
            interest_by_name or minimums_by_name or snowball_by_name
        )

        active_start = {
            name: state for name, state in debt_states.items() if state["balance"] > Decimal("0.00")
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
                    _stable_identifier(name),
                    name,
                    to_cents(starting),
                    to_cents(interest),
                    to_cents(actual_minimum),
                    to_cents(extra_payment),
                    to_cents(total_payment),
                    to_cents(principal_paid),
                    to_cents(ending),
                    int(ending == Decimal("0.00")),
                    _date_value(payoff_dates.get(name)) if ending == Decimal("0.00") else None,
                    state["order"],
                    to_cents(scheduled_minimum),
                    to_cents(actual_minimum),
                    str(state["apr"]),
                    int(tolerance_applied),
                    reason_code.value,
                    self._debt_explanation(name, total_payment, ending, reason_code),
                ),
            )
            new_states[name]["balance"] = ending
        return new_states

    def _save_aggregate_debt_snapshot(
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

    def _initial_debt_states(self, starting_debts: list[Any] | None) -> dict[str, dict[str, Any]]:
        states = {}
        for debt in starting_debts or []:
            states[debt.name] = {
                "balance": money(debt.balance),
                "minimum": money(debt.minimum),
                "apr": debt.apr,
                "order": debt.snowball_order,
            }
        return states

    def _goal_deadline_for_period(
        self,
        forecast: ForecastSummary,
        period: ForecastPeriod,
    ) -> str | None:
        if period.active_savings_goal_name is None:
            return None
        for stage in forecast.savings_stage_results:
            if stage.goal_name == period.active_savings_goal_name:
                return _date_value(stage.target_date)
        return None

    def _nullable_cents(self, value: object | None) -> int | None:
        return None if value is None else to_cents(value)

    def _save_warnings(
        self,
        conn: sqlite3.Connection,
        snapshot_id: int,
        warnings: list[DataQualityWarning],
    ) -> None:
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
                    _date_value(warning.relevant_date),
                    warning.relevant_name,
                    warning.suggested_action,
                )
                for warning in warnings
            ],
        )

    def _sync_snapshot_totals(self, conn: sqlite3.Connection, snapshot_id: int) -> None:
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

    def _debt_explanation(
        self,
        debt_name: str,
        total_payment: Decimal,
        ending: Decimal,
        reason_code: AllocationReasonCode,
    ) -> str:
        if reason_code == AllocationReasonCode.FINAL_DEBT_PAYOFF:
            return f"{debt_name} was paid off with a total payment of {_fmt(total_payment)}."
        if reason_code == AllocationReasonCode.DEBT_SNOWBALL:
            return f"{debt_name} received snowball funding this period."
        if ending == Decimal("0.00"):
            return f"{debt_name} has no remaining balance."
        return f"{debt_name} received its scheduled debt payment."

    def _plan_from_row(self, row: tuple[Any, ...]) -> Plan:
        return self.repository.plan_from_row(row)

    def _plan_version_from_row(self, row: tuple[Any, ...]) -> PlanVersion:
        return self.repository.plan_version_from_row(row)

    def _snapshot_from_row(self, row: tuple[Any, ...]) -> ForecastSnapshotRecord:
        return self.repository.snapshot_from_row(row)

    def _actual_from_row(self, row: tuple[Any, ...]) -> ActualTransaction:
        return self.repository.actual_from_row(row)

    def _latest_snapshot_for_version(self, plan_version_id: int) -> ForecastSnapshotRecord:
        return self.get_forecast_snapshot(
            self.repository.latest_snapshot_id_for_version(plan_version_id)
        )

    def _payoff_order(self, snapshot_id: int) -> list[str]:
        with closing(self.database._connect()) as conn:
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

    def _forecast_period_rows(self, snapshot_id: int) -> list[tuple[Any, ...]]:
        return self.repository.forecast_period_rows(snapshot_id)

    def _snapshot_debt_payments(self, snapshot_id: int) -> Decimal:
        return self._sum_snapshot_column(snapshot_id, "snowball_payment") + self._sum_snapshot_column(
            snapshot_id, "minimum_debt_payments"
        )

    def _snapshot_personal_spending(self, snapshot_id: int) -> Decimal:
        return self._sum_snapshot_column(snapshot_id, "personal_expenses_used")

    def _period_count(self, snapshot_id: int) -> int:
        return len(self._forecast_period_rows(snapshot_id))

    def _sum_snapshot_column(self, snapshot_id: int, column: str) -> Decimal:
        return from_cents(self.repository.sum_snapshot_column(snapshot_id, column))

    def _first_different_period(self, earlier_snapshot_id: int, later_snapshot_id: int) -> date | None:
        earlier = self._forecast_period_rows(earlier_snapshot_id)
        later = self._forecast_period_rows(later_snapshot_id)
        for left, right in zip(earlier, later, strict=False):
            if left[3:] != right[3:]:
                return date.fromisoformat(left[2])
        if len(earlier) != len(later):
            extra = earlier[min(len(earlier), len(later))] if len(earlier) > len(later) else later[min(len(earlier), len(later))]
            return date.fromisoformat(extra[2])
        return None

    def _deadline_priority_changed(self, earlier_version_id: int, later_version_id: int) -> bool:
        earlier = json.loads(self.get_plan_version(earlier_version_id).config_snapshot)
        later = json.loads(self.get_plan_version(later_version_id).config_snapshot)
        return (
            earlier.get("savings_plan", {}).get("deadline_priority_enabled")
            != later.get("savings_plan", {}).get("deadline_priority_enabled")
        )

    def _comparison_explanation(self, date_delta: int | None, interest_delta: Decimal) -> str:
        parts = []
        if date_delta is not None:
            direction = "later" if date_delta > 0 else "earlier"
            parts.append(f"The later plan reaches debt-free {abs(date_delta)} day(s) {direction}.")
        if interest_delta > Decimal("0.00"):
            parts.append(f"It projects {_fmt(interest_delta)} more interest.")
        elif interest_delta < Decimal("0.00"):
            parts.append(f"It projects {_fmt(abs(interest_delta))} less interest.")
        return " ".join(parts) or "The selected plans have no major forecast difference."

    def _latest_plan_forecast_totals(self, plan_id: int) -> dict[str, Decimal]:
        plan = self.get_plan(plan_id)
        if plan.current_version_id is None:
            return _zero_totals()
        try:
            snapshot = self._latest_snapshot_for_version(plan.current_version_id)
        except ValueError:
            return _zero_totals()
        return {
            "income": self._sum_snapshot_column(snapshot.id, "income"),
            "bills": self._sum_snapshot_column(snapshot.id, "fixed_expenses"),
            "debt": self._snapshot_debt_payments(snapshot.id),
            "savings": self._sum_snapshot_column(snapshot.id, "savings_deposit"),
            "personal": self._sum_snapshot_column(snapshot.id, "personal_expenses_used"),
            "remaining": self._sum_snapshot_column(snapshot.id, "checking_remaining"),
        }

    def _actual_totals(self, plan_id: int) -> dict[str, Decimal]:
        totals = _zero_actual_totals()
        for entry in self.list_actual_entries(plan_id):
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

    def _plan_name_exists(self, name: str) -> bool:
        return self.repository.plan_name_exists(name)

    def _match_period_for_date(self, plan_id: int, value: date) -> tuple[int | None, str]:
        plan = self.get_plan(plan_id)
        if plan.current_version_id is None:
            return None, "unmatched"
        try:
            snapshot = self._latest_snapshot_for_version(plan.current_version_id)
        except ValueError:
            return None, "unmatched"
        rows = self._forecast_period_rows(snapshot.id)
        for index, row in enumerate(rows):
            start_date = date.fromisoformat(row[2])
            next_start = (
                date.fromisoformat(rows[index + 1][2])
                if index + 1 < len(rows)
                else date.max
            )
            if start_date <= value < next_start:
                return row[0], "date_window"
        return None, "unmatched"

    def _actual_totals_by_period(
        self,
        plan_id: int,
    ) -> dict[int, dict[ActualEntryType, Decimal]]:
        totals: dict[int, dict[ActualEntryType, Decimal]] = {}
        with closing(self.database._connect()) as conn:
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

    def _balance_observations_by_period(
        self,
        plan_id: int,
    ) -> dict[int, dict[str, Decimal]]:
        observations: dict[int, dict[str, Decimal]] = {}
        with closing(self.database._connect()) as conn:
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
                planned = self._planned_balance_for_period(conn, period_id, key)
                bucket[key] = money(from_cents(balance) - planned)
        return observations

    def _planned_balance_for_period(
        self,
        conn: sqlite3.Connection,
        period_id: int,
        key: str,
    ) -> Decimal:
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

    def _actual_remaining(
        self,
        income: Decimal | None,
        bills: Decimal | None,
        debt: Decimal | None,
        savings: Decimal | None,
        personal: Decimal | None,
        withdrawal: Decimal | None,
    ) -> Decimal | None:
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

    def _period_status(
        self,
        actual: dict[ActualEntryType, Decimal],
        actual_remaining: Decimal | None,
        planned_remaining: Decimal,
    ) -> str:
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
        variance = money(actual_remaining - planned_remaining)
        if variance > Decimal("10.00"):
            return "Ahead of plan"
        if variance < Decimal("-10.00"):
            return "Needs review"
        return "On track"

    def _data_completeness(self, actual: dict[ActualEntryType, Decimal]) -> str:
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

    def _period_interpretation(self, status: str) -> str:
        if status == "No actual activity recorded":
            return "No actual transactions were recorded for this period."
        if status == "Insufficient actual data":
            return "Some actual activity is recorded, but more entries are needed for a complete comparison."
        if status == "Ahead of plan":
            return "Recorded activity leaves more cash than planned for this period."
        if status == "Needs review":
            return "Recorded activity leaves less cash than planned for this period."
        return "Recorded activity is close to the forecast for this period."

    def _compare_mapping(
        self,
        category: str,
        earlier: dict[str, Any],
        later: dict[str, Any],
        differences: list[AssumptionDifference],
    ) -> None:
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

    def _compare_named_collection(
        self,
        category: str,
        earlier: list[dict[str, Any]],
        later: list[dict[str, Any]],
        differences: list[AssumptionDifference],
    ) -> None:
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

    def _rich_comparison_explanation(
        self,
        base: str,
        differences: list[AssumptionDifference],
    ) -> str:
        if not differences:
            return base
        first = differences[0]
        return f"{base} Input changes include {first.category} {first.name} ({first.direction})."

    def _export_forecast_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        return self.repository.export_forecast_snapshots(plan_id)

    def _export_forecast_periods(self, plan_id: int) -> list[dict[str, Any]]:
        return self._export_child_rows(plan_id, "forecast_periods")

    def _export_debt_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        return self._export_child_rows(plan_id, "debt_snapshots")

    def _export_savings_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        return self._export_child_rows(plan_id, "savings_snapshots")

    def _export_warnings(self, plan_id: int) -> list[dict[str, Any]]:
        return self._export_child_rows(plan_id, "data_quality_warnings")

    def _export_actual_transactions(self, plan_id: int) -> list[dict[str, Any]]:
        return self.repository.export_actual_transactions(plan_id)

    def _export_balance_observations(self, plan_id: int) -> list[dict[str, Any]]:
        return self.repository.export_balance_observations(plan_id)

    def _export_child_rows(self, plan_id: int, table: str) -> list[dict[str, Any]]:
        return self.repository.export_child_rows(plan_id, table)

    def _import_forecast_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        version_id_map: dict[int, int],
    ) -> dict[int, int]:
        id_map = {}
        for row in rows:
            old_id = int(row["id"])
            cursor = conn.execute(
                """
                INSERT INTO forecast_snapshots (
                    plan_version_id, created_at, forecast_start_date,
                    forecast_end_date, debt_free_date, total_projected_interest,
                    total_projected_debt_payments, starting_debt, ending_debt,
                    starting_savings, ending_savings, pay_period_count,
                    forecast_fingerprint, status, warning_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id_map[int(row["plan_version_id"])],
                    row["created_at"],
                    row["forecast_start_date"],
                    row["forecast_end_date"],
                    row.get("debt_free_date"),
                    to_cents(row["total_projected_interest"]),
                    to_cents(row["total_projected_debt_payments"]),
                    to_cents(row["starting_debt"]),
                    to_cents(row["ending_debt"]),
                    to_cents(row["starting_savings"]),
                    to_cents(row["ending_savings"]),
                    int(row["pay_period_count"]),
                    row["forecast_fingerprint"],
                    row["status"],
                    int(row["warning_count"]),
                ),
            )
            id_map[old_id] = int(cursor.lastrowid)
        return id_map

    def _import_forecast_periods(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        snapshot_id_map: dict[int, int],
    ) -> dict[int, int]:
        id_map = {}
        for row in rows:
            old_id = int(row["id"])
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
                    snapshot_id_map[int(row["forecast_snapshot_id"])],
                    int(row["sequence_number"]),
                    row["pay_date"],
                    to_cents(row["income"]),
                    to_cents(row["fixed_expenses"]),
                    to_cents(row["personal_allowance"]),
                    to_cents(row["personal_expenses_used"]),
                    to_cents(row["personal_expense_reduction"]),
                    to_cents(row["minimum_debt_payments"]),
                    to_cents(row["snowball_payment"]),
                    to_cents(row["savings_deposit"]),
                    to_cents(row["savings_withdrawal"]),
                    to_cents(row["checking_remaining"]),
                    to_cents(row["reconciliation_difference"]),
                    row.get("active_savings_goal"),
                    row.get("reason_codes", ""),
                    row.get("explanation", ""),
                ),
            )
            id_map[old_id] = int(cursor.lastrowid)
        return id_map

    def _import_debt_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        period_id_map: dict[int, int],
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO debt_snapshots (
                    forecast_period_id, debt_identifier, debt_name,
                    starting_balance, interest_charged, minimum_payment,
                    extra_payment, total_payment, principal_paid, ending_balance,
                    paid_off, payoff_date, payoff_order,
                    scheduled_minimum_payment, actual_minimum_payment, apr,
                    final_payoff_tolerance_applied, allocation_reason_code,
                    explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id_map[int(row["forecast_period_id"])],
                    row["debt_identifier"],
                    row["debt_name"],
                    to_cents(row["starting_balance"]),
                    to_cents(row["interest_charged"]),
                    to_cents(row["minimum_payment"]),
                    to_cents(row["extra_payment"]),
                    to_cents(row["total_payment"]),
                    to_cents(row["principal_paid"]),
                    to_cents(row["ending_balance"]),
                    int(bool(row["paid_off"])),
                    row.get("payoff_date"),
                    row.get("payoff_order"),
                    to_cents(row.get("scheduled_minimum_payment", "0.00")),
                    to_cents(row.get("actual_minimum_payment", "0.00")),
                    row.get("apr", "0"),
                    int(bool(row.get("final_payoff_tolerance_applied", 0))),
                    row.get("allocation_reason_code", ""),
                    row.get("explanation", ""),
                ),
            )

    def _import_savings_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        period_id_map: dict[int, int],
    ) -> None:
        for row in rows:
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
                    period_id_map[int(row["forecast_period_id"])],
                    to_cents(row["starting_savings"]),
                    to_cents(row["normal_contribution"]),
                    to_cents(row["redirected_snowball"]),
                    to_cents(row["personal_expense_reduction_contribution"]),
                    to_cents(row["other_contribution"]),
                    to_cents(row["withdrawal"]),
                    to_cents(row["ending_savings"]),
                    row.get("active_goal"),
                    self._optional_import_cents(row.get("goal_target")),
                    row.get("goal_deadline"),
                    to_cents(row["projected_shortfall"]),
                    row["goal_feasible_status"],
                    to_cents(row.get("total_deposit", row["normal_contribution"])),
                    self._optional_import_cents(row.get("amount_required_before")),
                    self._optional_import_cents(row.get("amount_required_after")),
                    self._optional_import_cents(row.get("projected_deadline_balance")),
                    row.get("withdrawal_date"),
                    self._optional_import_cents(row.get("withdrawal_amount")),
                    row.get("post_withdrawal_allocation_state", ""),
                    row.get("reason_codes", ""),
                    row.get("explanation", ""),
                ),
            )

    def _import_warnings(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        snapshot_id_map: dict[int, int],
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO data_quality_warnings (
                    forecast_snapshot_id, code, severity, message,
                    relevant_date, relevant_name, suggested_action
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id_map[int(row["forecast_snapshot_id"])],
                    row["code"],
                    row["severity"],
                    row["message"],
                    row.get("relevant_date"),
                    row.get("relevant_name"),
                    row.get("suggested_action"),
                ),
            )

    def _import_actual_transactions(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        plan_id: int,
        period_id_map: dict[int, int],
    ) -> None:
        id_map = {}
        corrections = {}
        for row in rows:
            old_id = int(row["id"])
            old_period_id = row.get("forecast_period_id")
            cursor = conn.execute(
                """
                INSERT INTO actual_transactions (
                    plan_id, entry_date, entry_type, debt_identifier, amount,
                    category, description, source, created_at, corrected_entry_id,
                    note, forecast_period_id, match_method, matched_at,
                    manual_override
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    row["entry_date"],
                    row["entry_type"],
                    row.get("debt_identifier"),
                    to_cents(row["amount"]),
                    row.get("category", ""),
                    row.get("description", ""),
                    row.get("source", "import"),
                    row.get("created_at", utc_timestamp()),
                    None,
                    row.get("note", ""),
                    None if old_period_id is None else period_id_map[int(old_period_id)],
                    row.get("match_method", "unmatched"),
                    row.get("matched_at"),
                    int(bool(row.get("manual_override", 0))),
                ),
            )
            id_map[old_id] = int(cursor.lastrowid)
            corrections[old_id] = row.get("corrected_entry_id")
        for old_id, corrected_old_id in corrections.items():
            if corrected_old_id is not None:
                conn.execute(
                    "UPDATE actual_transactions SET corrected_entry_id = ? WHERE id = ?",
                    (id_map[int(corrected_old_id)], id_map[old_id]),
                )

    def _import_balance_observations(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        plan_id: int,
        period_id_map: dict[int, int],
    ) -> None:
        for row in rows:
            old_period_id = row.get("forecast_period_id")
            conn.execute(
                """
                INSERT INTO balance_observations (
                    plan_id, observation_date, observation_type, debt_identifier,
                    balance, source, note, created_at, forecast_period_id,
                    match_method
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    row["observation_date"],
                    row["observation_type"],
                    row.get("debt_identifier"),
                    to_cents(row["balance"]),
                    row.get("source", "import"),
                    row.get("note", ""),
                    row.get("created_at", utc_timestamp()),
                    None if old_period_id is None else period_id_map[int(old_period_id)],
                    row.get("match_method", "unmatched"),
                ),
            )

    def _optional_import_cents(self, value: Any) -> int | None:
        if value is None:
            return None
        return to_cents(value)

    def _attach_snapshot_history_fingerprints(self, payload: dict[str, Any]) -> None:
        for snapshot in payload["forecast_snapshots"]:
            snapshot["history_fingerprint"] = self._snapshot_history_fingerprint(
                payload,
                int(snapshot["id"]),
            )

    def _snapshot_history_fingerprint(
        self,
        payload: dict[str, Any],
        snapshot_id: int,
    ) -> str:
        periods = [
            row
            for row in payload.get("forecast_periods", [])
            if int(row["forecast_snapshot_id"]) == snapshot_id
        ]
        period_ids = {int(row["id"]) for row in periods}
        return fingerprint(
            {
                "snapshot": self._without_keys(
                    next(
                        row
                        for row in payload.get("forecast_snapshots", [])
                        if int(row["id"]) == snapshot_id
                    ),
                    {"history_fingerprint"},
                ),
                "periods": periods,
                "debts": [
                    row
                    for row in payload.get("debt_snapshots", [])
                    if int(row["forecast_period_id"]) in period_ids
                ],
                "savings": [
                    row
                    for row in payload.get("savings_snapshots", [])
                    if int(row["forecast_period_id"]) in period_ids
                ],
                "warnings": [
                    row
                    for row in payload.get("warnings", [])
                    if int(row["forecast_snapshot_id"]) == snapshot_id
                ],
            }
        )

    def _without_keys(self, row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key not in keys}

    def _validate_import_payload(self, payload: dict[str, Any]) -> None:
        required = {
            "plan",
            "versions",
            "forecast_snapshots",
            "forecast_periods",
            "debt_snapshots",
            "savings_snapshots",
            "warnings",
            "actual_entries",
            "balance_observations",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"plan export is missing required section(s): {', '.join(sorted(missing))}.")
        if payload.get("source_schema_version", 0) > LATEST_SCHEMA_VERSION:
            raise ValueError("plan export uses an unsupported future schema version.")
        expected_portable = payload.get("portable_content_fingerprint")
        if expected_portable and expected_portable != portable_content_fingerprint(payload):
            raise ValueError("plan export portable content fingerprint mismatch.")
        if not isinstance(payload["versions"], list) or not payload["versions"]:
            raise ValueError("plan export does not contain any versions.")
        version_ids = set()
        version_numbers = set()
        active_versions = []
        for version in payload["versions"]:
            version_id = int(version["id"])
            version_number = int(version["version_number"])
            if version_id in version_ids:
                raise ValueError("versions contain duplicate identifiers.")
            if version_number <= 0 or version_number in version_numbers:
                raise ValueError("versions contain invalid or duplicate version numbers.")
            version_ids.add(version_id)
            version_numbers.add(version_number)
            if bool(version.get("active", False)):
                active_versions.append(version)
            if fingerprint(json.loads(version["config_snapshot"])) != version["config_fingerprint"]:
                raise ValueError("plan export has a configuration fingerprint mismatch.")
        if len(active_versions) != 1:
            raise ValueError("plan export must contain exactly one active version.")
        if int(payload["plan"].get("current_version_id")) != int(active_versions[0]["id"]):
            raise ValueError("plan export current version does not match active version.")
        self._validate_import_relationships_and_totals(payload, version_ids)

    def _validate_import_relationships_and_totals(
        self,
        payload: dict[str, Any],
        version_ids: set[int],
    ) -> None:
        snapshots = payload["forecast_snapshots"]
        periods = payload["forecast_periods"]
        debts = payload["debt_snapshots"]
        savings_rows = payload["savings_snapshots"]
        warnings = payload["warnings"]
        snapshot_ids = set()
        for snapshot in snapshots:
            snapshot_id = int(snapshot["id"])
            if snapshot_id in snapshot_ids:
                raise ValueError("forecast_snapshots contain duplicate identifiers.")
            if int(snapshot["plan_version_id"]) not in version_ids:
                raise ValueError("forecast_snapshots contain an orphan plan_version_id.")
            snapshot_ids.add(snapshot_id)
        period_ids = set()
        sequences_by_snapshot: dict[int, set[int]] = {}
        periods_by_snapshot: dict[int, list[dict[str, Any]]] = {
            snapshot_id: [] for snapshot_id in snapshot_ids
        }
        for row in periods:
            period_id = int(row["id"])
            snapshot_id = int(row["forecast_snapshot_id"])
            if period_id in period_ids:
                raise ValueError("forecast_periods contain duplicate identifiers.")
            if snapshot_id not in snapshot_ids:
                raise ValueError("forecast_periods contain an orphan forecast_snapshot_id.")
            sequence = int(row["sequence_number"])
            sequence_bucket = sequences_by_snapshot.setdefault(snapshot_id, set())
            if sequence in sequence_bucket:
                raise ValueError("forecast_periods contain duplicate sequence numbers.")
            sequence_bucket.add(sequence)
            period_ids.add(period_id)
            periods_by_snapshot[snapshot_id].append(row)
            self._validate_period_reconciliation(row)

        debts_by_period: dict[int, list[dict[str, Any]]] = {period_id: [] for period_id in period_ids}
        for row in debts:
            period_id = int(row["forecast_period_id"])
            if period_id not in period_ids:
                raise ValueError("debt_snapshots contain an orphan forecast_period_id.")
            debts_by_period[period_id].append(row)
            self._validate_debt_row(row)
        for period_id, rows in debts_by_period.items():
            identifiers = [row["debt_identifier"] for row in rows]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("debt_snapshots contain duplicate debt identifiers.")

        savings_by_period: dict[int, dict[str, Any]] = {}
        for row in savings_rows:
            period_id = int(row["forecast_period_id"])
            if period_id not in period_ids:
                raise ValueError("savings_snapshots contain an orphan forecast_period_id.")
            if period_id in savings_by_period:
                raise ValueError("savings_snapshots contain duplicate forecast periods.")
            savings_by_period[period_id] = row
            self._validate_savings_row(row)
        if set(savings_by_period) != period_ids:
            raise ValueError("savings_snapshots must contain one row per forecast period.")

        warnings_by_snapshot: dict[int, list[dict[str, Any]]] = {
            snapshot_id: [] for snapshot_id in snapshot_ids
        }
        for row in warnings:
            snapshot_id = int(row["forecast_snapshot_id"])
            if snapshot_id not in snapshot_ids:
                raise ValueError("warnings contain an orphan forecast_snapshot_id.")
            DataWarningSeverity(row["severity"])
            warnings_by_snapshot[snapshot_id].append(row)

        for snapshot in snapshots:
            self._validate_snapshot_totals(
                snapshot,
                periods_by_snapshot[int(snapshot["id"])],
                debts_by_period,
                savings_by_period,
                warnings_by_snapshot[int(snapshot["id"])],
                payload,
            )
        self._validate_import_actuals(payload, period_ids)

    def _validate_period_reconciliation(self, row: dict[str, Any]) -> None:
        income = to_cents(row["income"])
        fixed = to_cents(row["fixed_expenses"])
        personal = to_cents(row["personal_expenses_used"])
        non_personal_fixed = fixed - personal
        expected = (
            income
            - non_personal_fixed
            - personal
            - to_cents(row["minimum_debt_payments"])
            - to_cents(row["snowball_payment"])
            - to_cents(row["savings_deposit"])
            + to_cents(row["savings_withdrawal"])
            - to_cents(row["checking_remaining"])
        )
        if expected != to_cents(row["reconciliation_difference"]):
            raise ValueError("forecast_periods reconciliation_difference mismatch.")
        if expected != 0:
            raise ValueError("forecast_periods do not reconcile to zero.")

    def _validate_debt_row(self, row: dict[str, Any]) -> None:
        principal = to_cents(row["principal_paid"])
        interest = to_cents(row["interest_charged"])
        total = to_cents(row["total_payment"])
        starting = to_cents(row["starting_balance"])
        ending = to_cents(row["ending_balance"])
        tolerance = bool(int(row.get("final_payoff_tolerance_applied", 0)))
        paid_off = bool(int(row.get("paid_off", 0)))
        if principal + interest != total:
            raise ValueError("debt_snapshots principal plus interest mismatch.")
        difference = starting + interest - total - ending
        if difference != 0:
            if not (tolerance and paid_off and 0 < difference <= 1):
                raise ValueError("debt_snapshots balance equation mismatch.")
        if tolerance and not (paid_off and 0 <= difference <= 1):
            raise ValueError("debt_snapshots final payoff tolerance flag mismatch.")

    def _validate_savings_row(self, row: dict[str, Any]) -> None:
        total = to_cents(row["total_deposit"])
        components = (
            to_cents(row["normal_contribution"])
            + to_cents(row["redirected_snowball"])
            + to_cents(row["personal_expense_reduction_contribution"])
            + to_cents(row["other_contribution"])
        )
        if total != components:
            raise ValueError("savings_snapshots total_deposit mismatch.")
        if (
            to_cents(row["starting_savings"])
            + total
            - to_cents(row["withdrawal"])
            != to_cents(row["ending_savings"])
        ):
            raise ValueError("savings_snapshots balance equation mismatch.")
        target = row.get("goal_target")
        if row.get("active_goal") is None and target is not None:
            raise ValueError("savings_snapshots goal target requires an active goal.")
        if row.get("goal_deadline") is not None:
            date.fromisoformat(row["goal_deadline"])
        if row.get("withdrawal_date") is not None:
            date.fromisoformat(row["withdrawal_date"])

    def _validate_snapshot_totals(
        self,
        snapshot: dict[str, Any],
        periods: list[dict[str, Any]],
        debts_by_period: dict[int, list[dict[str, Any]]],
        savings_by_period: dict[int, dict[str, Any]],
        warnings: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        snapshot_id = int(snapshot["id"])
        ordered_periods = sorted(periods, key=lambda row: int(row["sequence_number"]))
        if len(ordered_periods) != int(snapshot["pay_period_count"]):
            raise ValueError("forecast_snapshots pay_period_count mismatch.")
        if len(warnings) != int(snapshot["warning_count"]):
            raise ValueError("forecast_snapshots warning_count mismatch.")
        if ordered_periods:
            if snapshot["forecast_start_date"] != ordered_periods[0]["pay_date"]:
                raise ValueError("forecast_snapshots forecast_start_date mismatch.")
            if snapshot["forecast_end_date"] != ordered_periods[-1]["pay_date"]:
                raise ValueError("forecast_snapshots forecast_end_date mismatch.")
            first_period_id = int(ordered_periods[0]["id"])
            last_period_id = int(ordered_periods[-1]["id"])
            starting_debt = sum(
                to_cents(row["starting_balance"])
                for row in debts_by_period.get(first_period_id, [])
            )
            ending_debt = sum(
                to_cents(row["ending_balance"])
                for row in debts_by_period.get(last_period_id, [])
            )
            starting_savings = to_cents(savings_by_period[first_period_id]["starting_savings"])
            ending_savings = to_cents(savings_by_period[last_period_id]["ending_savings"])
        else:
            starting_debt = ending_debt = starting_savings = ending_savings = 0
        snapshot_period_ids = {int(row["id"]) for row in ordered_periods}
        total_interest = sum(
            to_cents(row["interest_charged"])
            for period_id, rows in debts_by_period.items()
            if period_id in snapshot_period_ids
            for row in rows
        )
        total_debt_payment = sum(
            to_cents(row["total_payment"])
            for period_id, rows in debts_by_period.items()
            if period_id in snapshot_period_ids
            for row in rows
        )
        expected = {
            "starting_debt": starting_debt,
            "ending_debt": ending_debt,
            "starting_savings": starting_savings,
            "ending_savings": ending_savings,
            "total_projected_interest": total_interest,
            "total_projected_debt_payments": total_debt_payment,
        }
        for field, cents in expected.items():
            if to_cents(snapshot[field]) != cents:
                raise ValueError(f"forecast_snapshots {field} mismatch.")
        if snapshot.get("history_fingerprint") != self._snapshot_history_fingerprint(
            payload,
            snapshot_id,
        ):
            raise ValueError("forecast_snapshots history_fingerprint mismatch.")

    def _validate_import_actuals(
        self,
        payload: dict[str, Any],
        period_ids: set[int],
    ) -> None:
        actual_ids = set()
        actual_types = {}
        for row in payload.get("actual_entries", []):
            row_id = int(row["id"])
            if row_id in actual_ids:
                raise ValueError("actual_entries contain duplicate identifiers.")
            actual_ids.add(row_id)
            actual_types[row_id] = ActualEntryType(row["entry_type"])
            date.fromisoformat(row["entry_date"])
            to_cents(row["amount"])
            period_id = row.get("forecast_period_id")
            if period_id is not None and int(period_id) not in period_ids:
                raise ValueError("actual_entries contain an orphan forecast_period_id.")
        for row in payload.get("actual_entries", []):
            corrected = row.get("corrected_entry_id")
            if corrected is not None:
                corrected_id = int(corrected)
                if corrected_id not in actual_ids:
                    raise ValueError("actual_entries contain an invalid reversal reference.")
                if actual_types[int(row["id"])] != actual_types[corrected_id]:
                    raise ValueError("actual_entries reversal type mismatch.")
        for row in payload.get("balance_observations", []):
            ActualEntryType(row["observation_type"])
            date.fromisoformat(row["observation_date"])
            to_cents(row["balance"])
            period_id = row.get("forecast_period_id")
            if period_id is not None and int(period_id) not in period_ids:
                raise ValueError("balance_observations contain an orphan forecast_period_id.")


    def _import_fingerprint_exists(self, import_hash: str) -> bool:
        return self.repository.import_fingerprint_exists(import_hash)

    def _write_csv(self, path: Path, rows: list[dict[str, Any]]) -> Path:
        fieldnames = sorted({key for row in rows for key in row}) or ["empty"]
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path


def _normalize_config_object(config: Any) -> dict[str, Any]:
    settings = config.settings
    return {
        "budget": {
            "paycheck": settings.paycheck,
            "first_paycheck": settings.first_paycheck,
            "rent_per_paycheck": settings.rent_per_paycheck,
            "insurance_per_paycheck": settings.insurance_per_paycheck,
            "personal_per_paycheck": settings.personal_per_paycheck,
            "starting_savings": settings.starting_savings,
            "savings_goal": settings.savings_goal,
            "snowball_split": settings.snowball_split,
        },
        "bills": sorted((asdict(bill) for bill in config.bills), key=lambda item: item["name"]),
        "debts": sorted(
            (asdict(debt) for debt in config.debts),
            key=lambda item: (item["snowball_order"], item["name"]),
        ),
        "scenarios": to_json_ready(config.scenarios),
        "debt_free_target": to_json_ready(config.debt_free_target),
        "savings_plan": to_json_ready(config.savings_plan),
    }


def _normalize_config_value(value: Any, key: str | None = None) -> Any:
    if is_dataclass(value):
        return _normalize_config_value(asdict(value), key)
    if isinstance(value, dict):
        return {
            str(item_key): _normalize_config_value(item_value, str(item_key))
            for item_key, item_value in sorted(value.items())
        }
    if isinstance(value, list | tuple):
        return [_normalize_config_value(item) for item in value]
    if isinstance(value, Decimal):
        if key in MONEY_CONFIG_KEYS:
            return f"{money(value):.2f}"
        return str(value.normalize())
    if isinstance(value, int | float | str) and key in MONEY_CONFIG_KEYS:
        return f"{money(value):.2f}"
    if isinstance(value, date):
        return value.isoformat()
    return value


def _portable_content(payload: dict[str, Any]) -> dict[str, Any]:
    sections = {
        "plan": _strip_unstable_keys(
            payload.get("plan", {}),
            {"id", "current_version_id", "created_at", "updated_at"},
        ),
        "versions": _normalize_rows(
            payload.get("versions", []),
            {"id", "plan_id", "created_at"},
            ("version_number",),
        ),
        "forecast_snapshots": _normalize_rows(
            payload.get("forecast_snapshots", []),
            {"id", "plan_version_id", "created_at"},
            ("forecast_start_date", "forecast_end_date", "forecast_fingerprint"),
        ),
        "forecast_periods": _normalize_rows(
            payload.get("forecast_periods", []),
            {"id", "forecast_snapshot_id"},
            ("pay_date", "sequence_number"),
        ),
        "debt_snapshots": _normalize_rows(
            payload.get("debt_snapshots", []),
            {"id", "forecast_period_id"},
            ("debt_identifier", "payoff_order", "debt_name"),
        ),
        "savings_snapshots": _normalize_rows(
            payload.get("savings_snapshots", []),
            {"id", "forecast_period_id"},
            ("goal_deadline", "active_goal", "ending_savings"),
        ),
        "warnings": _normalize_rows(
            payload.get("warnings", []),
            {"id", "forecast_snapshot_id"},
            ("code", "severity", "relevant_date", "relevant_name"),
        ),
        "actual_entries": _normalize_rows(
            payload.get("actual_entries", []),
            {"id", "plan_id", "created_at", "corrected_entry_id", "forecast_period_id", "matched_at"},
            ("entry_date", "entry_type", "amount", "description"),
        ),
        "balance_observations": _normalize_rows(
            payload.get("balance_observations", []),
            {"id", "plan_id", "created_at", "forecast_period_id"},
            ("observation_date", "observation_type", "debt_identifier", "balance"),
        ),
    }
    return {
        "format_version": payload.get("format_version"),
        "application_version": payload.get("application_version"),
        "forecast_engine_version": payload.get("forecast_engine_version"),
        "source_schema_version": payload.get("source_schema_version"),
        **sections,
    }


def _normalize_rows(
    rows: list[dict[str, Any]],
    unstable_keys: set[str],
    sort_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    normalized = [_strip_unstable_keys(row, unstable_keys) for row in rows]
    return sorted(
        normalized,
        key=lambda row: tuple("" if row.get(key) is None else str(row.get(key)) for key in sort_keys),
    )


def _strip_unstable_keys(row: dict[str, Any], unstable_keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in unstable_keys
        and key not in {"exported_at", "portable_content_fingerprint", "portable_id"}
    }


def _date_value(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _optional_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _date_delta_days(left: date | None, right: date | None) -> int | None:
    if left is None or right is None:
        return None
    return (right - left).days


def _variance(actual: Decimal | None, planned: Decimal) -> Decimal | None:
    if actual is None:
        return None
    return money(actual - planned)


def _stable_identifier(value: str) -> str:
    return value.strip().casefold().replace(" ", "_")


def _portable_id(kind: str, local_id: int, name: str) -> str:
    return f"{kind}:{local_id}:{_stable_identifier(name)}"


def _fmt(value: Decimal) -> str:
    return f"${money(value):,.2f}"


def _zero_totals() -> dict[str, Decimal]:
    return {
        "income": Decimal("0.00"),
        "bills": Decimal("0.00"),
        "debt": Decimal("0.00"),
        "savings": Decimal("0.00"),
        "personal": Decimal("0.00"),
        "remaining": Decimal("0.00"),
    }


def _zero_actual_totals() -> dict[str, Decimal]:
    return {
        "income": Decimal("0.00"),
        "bills": Decimal("0.00"),
        "debt": Decimal("0.00"),
        "savings": Decimal("0.00"),
        "personal": Decimal("0.00"),
    }
