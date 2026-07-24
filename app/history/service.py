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

from app.budget_engine import PayPeriodSummary
from app.database import Database, LATEST_SCHEMA_VERSION
from app.history.comparison import HistoryComparisonService
from app.history.exporter import PlanHistoryExporter
from app.history.forecast import HistoryForecastService
from app.history.importer import PlanHistoryImporter
from app.history.repository import HistoryRepository
from app.money import money, to_cents
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
    GeneratedPlanSaveRecord,
    Plan,
    PlanComparison,
    PlanVersion,
)
from app.serialization import dumps_json, to_json_ready
from app.version import APP_VERSION

APPLICATION_VERSION = APP_VERSION
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

    def _forecast_service(self) -> HistoryForecastService:
        """Build the focused forecast persistence workflow component."""
        return HistoryForecastService(
            self.repository,
            get_forecast_snapshot=self.get_forecast_snapshot,
            forecast_fingerprint=forecast_fingerprint,
            explain_forecast_period=explain_forecast_period,
            utc_timestamp=utc_timestamp,
            date_value=_date_value,
            stable_identifier=_stable_identifier,
            format_money=_fmt,
        )

    def _comparison_service(self) -> HistoryComparisonService:
        """Build the focused history comparison workflow component."""
        return HistoryComparisonService(self.repository, format_money=_fmt)

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

    def save_generated_plan(
        self,
        *,
        name: str,
        config: Any,
        forecast: ForecastSummary,
        starting_savings: Decimal,
        starting_debts: list[Any] | None = None,
        plan_id: int | None = None,
        description: str = "",
        change_note: str = "Saved generated plan",
        source: str = "interactive",
        notes: str = "",
        pay_period_summaries: list[PayPeriodSummary] | None = None,
        warnings: list[DataQualityWarning] | None = None,
        force: bool = False,
    ) -> GeneratedPlanSaveRecord:
        """Save generated inputs and their existing forecast atomically."""
        snapshot = normalized_config_snapshot(config)
        snapshot_json = canonical_json(snapshot)
        config_hash = fingerprint(snapshot)
        forecast_service = self._forecast_service()
        created_new_plan = plan_id is None
        snapshot_id = None

        if plan_id is not None:
            versions = self.list_plan_versions(plan_id)
            if versions and versions[-1].config_fingerprint == config_hash and not force:
                return GeneratedPlanSaveRecord(
                    plan=self.get_plan(plan_id),
                    version=versions[-1],
                    snapshot=None,
                    created_new_plan=False,
                    created_new_version=False,
                )
            version_number = (versions[-1].version_number if versions else 0) + 1

        with self.repository.transaction() as conn:
            if plan_id is None:
                timestamp = utc_timestamp()
                cursor = conn.execute(
                    """
                    INSERT INTO plans (name, description, created_at, updated_at, notes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, description, timestamp, timestamp, notes),
                )
                plan_id = int(cursor.lastrowid)
                version_number = 1
            else:
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
                active=True,
            )
            conn.execute(
                """
                UPDATE plans
                SET current_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (version_id, utc_timestamp(), plan_id),
            )
            snapshot_id = forecast_service.save_forecast_snapshot(
                conn,
                version_id,
                forecast,
                starting_savings=starting_savings,
                pay_period_summaries=pay_period_summaries,
                starting_debts=starting_debts,
                warnings=warnings,
            )

        return GeneratedPlanSaveRecord(
            plan=self.get_plan(plan_id),
            version=self.get_plan_version(version_id),
            snapshot=self.get_forecast_snapshot(snapshot_id),
            created_new_plan=created_new_plan,
            created_new_version=True,
        )

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
        return self._forecast_service().generate_and_save_forecast(
            plan_version_id,
            forecast,
            starting_savings=starting_savings,
            pay_period_summaries=pay_period_summaries,
            starting_debts=starting_debts,
            warnings=warnings,
        )

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
        return self._comparison_service().compare_forecast_to_actual(plan_id)

    def compare_forecast_to_actual_periods(
        self,
        plan_id: int,
    ) -> list[ForecastActualPeriodComparison]:
        """Compare actual entries to each persisted forecast period."""
        return self._comparison_service().compare_forecast_to_actual_periods(plan_id)

    def compare_plan_versions(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> PlanComparison:
        """Compare two saved forecast snapshots with neutral tradeoff wording."""
        return self._comparison_service().compare_plan_versions(
            earlier_version_id,
            later_version_id,
        )

    def compare_assumptions(
        self,
        earlier_version_id: int,
        later_version_id: int,
    ) -> list[AssumptionDifference]:
        """Return meaningful input differences between two plan versions."""
        return self._comparison_service().compare_assumptions(
            earlier_version_id,
            later_version_id,
        )

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
        return self._comparison_service().payoff_order(snapshot_id)

    def _forecast_period_rows(self, snapshot_id: int) -> list[tuple[Any, ...]]:
        return self.repository.forecast_period_rows(snapshot_id)

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
            "savings_percentage_override": _normalize_config_value(
                getattr(settings, "savings_percentage_override", None),
                "savings_percentage_override",
            ),
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


def _stable_identifier(value: str) -> str:
    return value.strip().casefold().replace(" ", "_")


def _portable_id(kind: str, local_id: int, name: str) -> str:
    return f"{kind}:{local_id}:{_stable_identifier(name)}"


def _fmt(value: Decimal) -> str:
    return f"${money(value):,.2f}"


