"""Persistent local plan history, comparison, and export services."""

from __future__ import annotations

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
from app.history.exporter import PlanHistoryExporter
from app.history.importer import PlanHistoryImporter
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

    def _exporter(self) -> PlanHistoryExporter:
        """Build the focused export workflow component."""
        return PlanHistoryExporter(
            self,
            self.repository,
            application_version=APPLICATION_VERSION,
            forecast_engine_version=FORECAST_ENGINE_VERSION,
            export_format_version=EXPORT_FORMAT_VERSION,
            utc_timestamp=utc_timestamp,
            canonical_json=canonical_json,
            portable_content_fingerprint=portable_content_fingerprint,
            portable_id=_portable_id,
            fingerprint=fingerprint,
        )

    def _importer(self) -> PlanHistoryImporter:
        """Build the focused import workflow component."""
        return PlanHistoryImporter(
            self,
            self.repository,
            export_format_version=EXPORT_FORMAT_VERSION,
            latest_schema_version=LATEST_SCHEMA_VERSION,
            utc_timestamp=utc_timestamp,
            canonical_json=canonical_json,
            fingerprint=fingerprint,
            portable_content_fingerprint=portable_content_fingerprint,
        )

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
        return self._exporter().export_plan_json(plan_id, path)

    def import_plan_json(self, path: str | Path, *, new_name: str | None = None) -> Plan:
        """Import a portable local JSON plan export transactionally."""
        return self._importer().import_plan_json(path, new_name=new_name)

    def export_forecast_periods_csv(self, snapshot_id: int, path: str | Path) -> Path:
        """Export forecast periods as a flat local CSV."""
        return self._exporter().export_forecast_periods_csv(snapshot_id, path)

    def export_csv_bundle(self, plan_id: int, directory: str | Path) -> dict[str, Path]:
        """Export all useful history tables to user-facing CSV files."""
        return self._exporter().export_csv_bundle(plan_id, directory)

    def history_report_rows(self, plan_id: int) -> dict[str, list[dict[str, Any]]]:
        """Return detailed rows for optional history workbooks."""
        return self._exporter().history_report_rows(plan_id)

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
