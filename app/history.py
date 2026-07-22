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
from app.money import from_cents, money, to_cents
from app.models import (
    ActualEntryType,
    ActualTransaction,
    AllocationExplanation,
    AllocationReasonCode,
    DataQualityWarning,
    DataWarningSeverity,
    ForecastActualComparison,
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
        self.database = database if isinstance(database, Database) else Database(database)
        self.database.initialize()

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
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
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
        with closing(self.database._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                if active:
                    conn.execute(
                        "UPDATE plan_versions SET active = 0 WHERE plan_id = ?",
                        (plan_id,),
                    )
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
                )
        return self.get_forecast_snapshot(snapshot_id)

    def list_plans(self, *, include_archived: bool = False) -> list[Plan]:
        """Return lightweight plan summaries."""
        clause = "" if include_archived else "WHERE archived = 0"
        with closing(self.database._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, description, created_at, updated_at, archived,
                       current_version_id, notes
                FROM plans
                {clause}
                ORDER BY updated_at DESC, name
                """
            ).fetchall()
        return [self._plan_from_row(row) for row in rows]

    def get_plan(self, plan_id: int) -> Plan:
        """Return one plan by ID."""
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, name, description, created_at, updated_at, archived,
                       current_version_id, notes
                FROM plans
                WHERE id = ?
                """,
                (plan_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"plan {plan_id} was not found.")
        return self._plan_from_row(row)

    def get_plan_version(self, version_id: int) -> PlanVersion:
        """Return one immutable plan version by ID."""
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, plan_id, version_number, created_at, source, change_note,
                       config_snapshot, config_fingerprint, application_version,
                       forecast_engine_version, schema_version, active
                FROM plan_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"plan version {version_id} was not found.")
        return self._plan_version_from_row(row)

    def list_plan_versions(self, plan_id: int) -> list[PlanVersion]:
        """Return immutable versions for a plan."""
        with closing(self.database._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, plan_id, version_number, created_at, source, change_note,
                       config_snapshot, config_fingerprint, application_version,
                       forecast_engine_version, schema_version, active
                FROM plan_versions
                WHERE plan_id = ?
                ORDER BY version_number
                """,
                (plan_id,),
            ).fetchall()
        return [self._plan_version_from_row(row) for row in rows]

    def archive_plan(self, plan_id: int) -> None:
        """Soft-delete a plan from normal listings."""
        with closing(self.database._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE plans SET archived = 1, updated_at = ? WHERE id = ?",
                    (utc_timestamp(), plan_id),
                )

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

    def get_actual_entry(self, entry_id: int) -> ActualTransaction:
        """Return one actual transaction by ID."""
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, plan_id, entry_date, entry_type, debt_identifier, amount,
                       category, description, source, corrected_entry_id, note
                FROM actual_transactions
                WHERE id = ?
                """,
                (entry_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"actual entry {entry_id} was not found.")
        return self._actual_from_row(row)

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
            explanation=explanation,
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
        plan = self.get_plan(plan_id)
        versions = self.list_plan_versions(plan_id)
        payload = {
            "format_version": EXPORT_FORMAT_VERSION,
            "application_version": APPLICATION_VERSION,
            "forecast_engine_version": FORECAST_ENGINE_VERSION,
            "exported_at": utc_timestamp(),
            "plan": asdict(plan),
            "versions": [asdict(version) for version in versions],
            "actual_entries": [asdict(entry) for entry in self.list_actual_entries(plan_id)],
        }
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

        plan_data = payload["plan"]
        versions = payload["versions"]
        plan_name = new_name or plan_data["name"]
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
                latest_version_id = None
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
                    latest_version_id = version_id
                conn.execute(
                    "UPDATE plans SET current_version_id = ? WHERE id = ?",
                    (latest_version_id, plan_id),
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

    def list_actual_entries(self, plan_id: int) -> list[ActualTransaction]:
        """Return actual entries for a plan ordered by date and ID."""
        with closing(self.database._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, plan_id, entry_date, entry_type, debt_identifier, amount,
                       category, description, source, corrected_entry_id, note
                FROM actual_transactions
                WHERE plan_id = ?
                ORDER BY entry_date, id
                """,
                (plan_id,),
            ).fetchall()
        return [self._actual_from_row(row) for row in rows]

    def get_forecast_snapshot(self, snapshot_id: int) -> ForecastSnapshotRecord:
        """Return a persisted forecast snapshot record."""
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, plan_version_id, forecast_fingerprint, debt_free_date,
                       total_projected_interest, starting_debt, ending_debt,
                       starting_savings, ending_savings, warning_count
                FROM forecast_snapshots
                WHERE id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"forecast snapshot {snapshot_id} was not found.")
        return self._snapshot_from_row(row)

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
        cursor = conn.execute(
            """
            INSERT INTO plan_versions (
                plan_id, version_number, created_at, source, change_note,
                config_snapshot, config_fingerprint, application_version,
                forecast_engine_version, schema_version, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                version_number,
                utc_timestamp(),
                source,
                change_note,
                config_snapshot,
                config_fingerprint,
                APPLICATION_VERSION,
                FORECAST_ENGINE_VERSION,
                LATEST_SCHEMA_VERSION,
                int(active),
            ),
        )
        return int(cursor.lastrowid)

    def _save_forecast_periods(
        self,
        conn: sqlite3.Connection,
        snapshot_id: int,
        forecast: ForecastSummary,
        starting_savings: Decimal,
        *,
        pay_period_summaries: list[PayPeriodSummary] | None,
    ) -> None:
        previous_debt = forecast.starting_debt
        previous_savings = money(starting_savings)
        summaries_by_date = {
            summary.pay_date: summary for summary in pay_period_summaries or []
        }
        for index, period in enumerate(forecast.periods, start=1):
            summary = summaries_by_date.get(period.paycheck_date)
            explanation = explain_forecast_period(period)
            withdrawal = money(period.planned_withdrawal_amount)
            reconciliation = money(
                period.available_after_required_payments
                - period.savings_contribution
                - period.snowball_paid
            )
            income = Decimal("0.00") if summary is None else summary.income
            checking_remaining = (
                Decimal("0.00") if summary is None else summary.remaining_cash
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
            ending_debt = money(period.total_debt_balance)
            total_payment = money(period.minimums_paid + period.snowball_paid)
            conn.execute(
                """
                INSERT INTO debt_snapshots (
                    forecast_period_id, debt_identifier, debt_name, starting_balance,
                    interest_charged, minimum_payment, extra_payment, total_payment,
                    principal_paid, ending_balance, paid_off, payoff_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id,
                    "total_debt",
                    "Total Debt",
                    to_cents(previous_debt),
                    to_cents(period.interest_paid),
                    to_cents(period.minimums_paid),
                    to_cents(period.snowball_paid),
                    to_cents(total_payment),
                    to_cents(max(total_payment - period.interest_paid, Decimal("0.00"))),
                    to_cents(ending_debt),
                    int(ending_debt == Decimal("0.00")),
                    _date_value(forecast.debt_free_date)
                    if ending_debt == Decimal("0.00")
                    else None,
                ),
            )
            ending_savings = money(period.savings_balance)
            conn.execute(
                """
                INSERT INTO savings_snapshots (
                    forecast_period_id, starting_savings, normal_contribution,
                    redirected_snowball, personal_expense_reduction_contribution,
                    other_contribution, withdrawal, ending_savings, active_goal,
                    goal_target, goal_deadline, projected_shortfall,
                    goal_feasible_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    if period.active_savings_target is None
                    else to_cents(period.active_savings_target),
                    None,
                    to_cents(period.projected_savings_shortfall),
                    "review" if period.projected_savings_shortfall > 0 else "on_track",
                ),
            )
            previous_debt = ending_debt
            previous_savings = ending_savings

    def _plan_from_row(self, row: tuple[Any, ...]) -> Plan:
        return Plan(
            id=row[0],
            name=row[1],
            description=row[2],
            created_at=row[3],
            updated_at=row[4],
            archived=bool(row[5]),
            current_version_id=row[6],
            notes=row[7],
        )

    def _plan_version_from_row(self, row: tuple[Any, ...]) -> PlanVersion:
        return PlanVersion(
            id=row[0],
            plan_id=row[1],
            version_number=row[2],
            created_at=row[3],
            source=row[4],
            change_note=row[5],
            config_snapshot=row[6],
            config_fingerprint=row[7],
            application_version=row[8],
            forecast_engine_version=row[9],
            schema_version=row[10],
            active=bool(row[11]),
        )

    def _snapshot_from_row(self, row: tuple[Any, ...]) -> ForecastSnapshotRecord:
        return ForecastSnapshotRecord(
            id=row[0],
            plan_version_id=row[1],
            forecast_fingerprint=row[2],
            debt_free_date=_optional_date(row[3]),
            total_projected_interest=from_cents(row[4]),
            starting_debt=from_cents(row[5]),
            ending_debt=from_cents(row[6]),
            starting_savings=from_cents(row[7]),
            ending_savings=from_cents(row[8]),
            warning_count=row[9],
        )

    def _actual_from_row(self, row: tuple[Any, ...]) -> ActualTransaction:
        return ActualTransaction(
            id=row[0],
            plan_id=row[1],
            entry_date=date.fromisoformat(row[2]),
            entry_type=ActualEntryType(row[3]),
            debt_identifier=row[4],
            amount=from_cents(row[5]),
            category=row[6],
            description=row[7],
            source=row[8],
            corrected_entry_id=row[9],
            note=row[10],
        )

    def _latest_snapshot_for_version(self, plan_version_id: int) -> ForecastSnapshotRecord:
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                """
                SELECT id
                FROM forecast_snapshots
                WHERE plan_version_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (plan_version_id,),
            ).fetchone()
        if row is None:
            raise ValueError("plan version does not have a saved forecast snapshot.")
        return self.get_forecast_snapshot(row[0])

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
        with closing(self.database._connect()) as conn:
            return conn.execute(
                """
                SELECT id, sequence_number, pay_date, income, fixed_expenses,
                       personal_allowance, personal_expenses_used,
                       personal_expense_reduction, minimum_debt_payments,
                       snowball_payment, savings_deposit, savings_withdrawal,
                       checking_remaining
                FROM forecast_periods
                WHERE forecast_snapshot_id = ?
                ORDER BY sequence_number
                """,
                (snapshot_id,),
            ).fetchall()

    def _snapshot_debt_payments(self, snapshot_id: int) -> Decimal:
        return self._sum_snapshot_column(snapshot_id, "snowball_payment") + self._sum_snapshot_column(
            snapshot_id, "minimum_debt_payments"
        )

    def _snapshot_personal_spending(self, snapshot_id: int) -> Decimal:
        return self._sum_snapshot_column(snapshot_id, "personal_expenses_used")

    def _period_count(self, snapshot_id: int) -> int:
        return len(self._forecast_period_rows(snapshot_id))

    def _sum_snapshot_column(self, snapshot_id: int, column: str) -> Decimal:
        with closing(self.database._connect()) as conn:
            value = conn.execute(
                f"""
                SELECT COALESCE(SUM({column}), 0)
                FROM forecast_periods
                WHERE forecast_snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()[0]
        return from_cents(value)

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
        with closing(self.database._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM plans WHERE name = ? AND archived = 0",
                (name,),
            ).fetchone()
        return row is not None


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


def _date_value(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _optional_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _date_delta_days(left: date | None, right: date | None) -> int | None:
    if left is None or right is None:
        return None
    return (right - left).days


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
