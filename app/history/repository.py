"""SQLite persistence boundary for plan history data."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from app.database import Database
from app.money import from_cents
from app.models import (
    ActualEntryType,
    ActualTransaction,
    BalanceObservation,
    ForecastSnapshotRecord,
    Plan,
    PlanVersion,
)


class HistoryRepository:
    """Direct SQLite persistence operations for history services."""

    def __init__(self, database: Database | str | Path) -> None:
        self.database = database if isinstance(database, Database) else Database(database)

    def initialize(self) -> None:
        """Initialize or migrate the backing database."""
        self.database.initialize()

    @contextmanager
    def connection(self, *, foreign_keys: bool = False) -> Iterator[sqlite3.Connection]:
        """Open a database connection for read or caller-managed write work."""
        with closing(self.database._connect()) as conn:
            if foreign_keys:
                conn.execute("PRAGMA foreign_keys = ON")
            yield conn

    @contextmanager
    def transaction(self, *, foreign_keys: bool = True) -> Iterator[sqlite3.Connection]:
        """Open a database connection and wrap operations in a transaction."""
        with self.connection(foreign_keys=foreign_keys) as conn:
            with conn:
                yield conn

    def insert_plan_version(
        self,
        conn: sqlite3.Connection,
        *,
        plan_id: int,
        version_number: int,
        created_at: str,
        config_snapshot: str,
        config_fingerprint: str,
        change_note: str,
        source: str,
        application_version: str,
        forecast_engine_version: str,
        schema_version: int,
        active: bool,
    ) -> int:
        """Insert one immutable plan version row and return its ID."""
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
                created_at,
                source,
                change_note,
                config_snapshot,
                config_fingerprint,
                application_version,
                forecast_engine_version,
                schema_version,
                int(active),
            ),
        )
        return int(cursor.lastrowid)

    def enable_plan_version_mutation(self, conn: sqlite3.Connection) -> None:
        """Allow controlled version activation/deletion inside a transaction."""
        conn.execute("INSERT OR IGNORE INTO plan_version_mutation_guard (id) VALUES (1)")

    def disable_plan_version_mutation(self, conn: sqlite3.Connection) -> None:
        """Re-enable database-level plan-version immutability triggers."""
        conn.execute("DELETE FROM plan_version_mutation_guard WHERE id = 1")

    def list_plans(self, *, include_archived: bool = False) -> list[Plan]:
        """Return saved plans ordered by creation."""
        clause = "" if include_archived else "WHERE archived = 0"
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, description, created_at, updated_at, archived,
                       current_version_id, notes
                FROM plans
                {clause}
                ORDER BY updated_at DESC, name
                """
            ).fetchall()
        return [self.plan_from_row(row) for row in rows]

    def get_plan(self, plan_id: int) -> Plan:
        """Return one plan by ID."""
        with self.connection() as conn:
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
        return self.plan_from_row(row)

    def get_plan_version(self, version_id: int) -> PlanVersion:
        """Return one plan version by ID."""
        with self.connection() as conn:
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
        return self.plan_version_from_row(row)

    def list_plan_versions(self, plan_id: int) -> list[PlanVersion]:
        """Return versions for one plan ordered by version number."""
        with self.connection() as conn:
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
        return [self.plan_version_from_row(row) for row in rows]

    def archive_plan(self, plan_id: int, updated_at: str) -> None:
        """Mark a plan archived."""
        with self.transaction(foreign_keys=False) as conn:
            conn.execute(
                "UPDATE plans SET archived = 1, updated_at = ? WHERE id = ?",
                (updated_at, plan_id),
            )

    def get_forecast_snapshot(self, snapshot_id: int) -> ForecastSnapshotRecord:
        """Return one persisted forecast snapshot record."""
        with self.connection() as conn:
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
        return self.snapshot_from_row(row)

    def get_actual_entry(self, entry_id: int) -> ActualTransaction:
        """Return one actual transaction by ID."""
        with self.connection() as conn:
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
        return self.actual_from_row(row)

    def get_balance_observation(self, observation_id: int) -> BalanceObservation:
        """Return one balance observation by ID."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT id, plan_id, observation_date, observation_type, debt_identifier,
                       balance, source, note, forecast_period_id, match_method
                FROM balance_observations
                WHERE id = ?
                """,
                (observation_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"balance observation {observation_id} was not found.")
        return BalanceObservation(
            id=row[0],
            plan_id=row[1],
            observation_date=date.fromisoformat(row[2]),
            observation_type=ActualEntryType(row[3]),
            debt_identifier=row[4],
            balance=from_cents(row[5]),
            source=row[6],
            note=row[7],
            forecast_period_id=row[8],
            match_method=row[9],
        )

    def list_actual_entries(self, plan_id: int) -> list[ActualTransaction]:
        """Return actual transactions for one plan."""
        with self.connection() as conn:
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
        return [self.actual_from_row(row) for row in rows]

    def list_balance_observations(self, plan_id: int) -> list[BalanceObservation]:
        """Return balance observations for one plan."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, plan_id, observation_date, observation_type, debt_identifier,
                       balance, source, note, forecast_period_id, match_method
                FROM balance_observations
                WHERE plan_id = ?
                ORDER BY observation_date, id
                """,
                (plan_id,),
            ).fetchall()
        return [
            BalanceObservation(
                id=row[0],
                plan_id=row[1],
                observation_date=date.fromisoformat(row[2]),
                observation_type=ActualEntryType(row[3]),
                debt_identifier=row[4],
                balance=from_cents(row[5]),
                source=row[6],
                note=row[7],
                forecast_period_id=row[8],
                match_method=row[9],
            )
            for row in rows
        ]

    def latest_snapshot_id_for_version(self, plan_version_id: int) -> int:
        """Return the latest forecast snapshot ID for a plan version."""
        with self.connection() as conn:
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
        return int(row[0])

    def forecast_period_rows(self, snapshot_id: int) -> list[tuple[Any, ...]]:
        """Return raw forecast period rows for comparison workflows."""
        with self.connection() as conn:
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

    def sum_snapshot_column(self, snapshot_id: int, column: str) -> int:
        """Return a raw cent total from an allowed forecast period column."""
        allowed = {
            "income",
            "fixed_expenses",
            "personal_expenses_used",
            "minimum_debt_payments",
            "snowball_payment",
            "savings_deposit",
            "checking_remaining",
        }
        if column not in allowed:
            raise ValueError(f"unsupported snapshot total column: {column}")
        with self.connection() as conn:
            return conn.execute(
                f"""
                SELECT COALESCE(SUM({column}), 0)
                FROM forecast_periods
                WHERE forecast_snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()[0]

    def plan_name_exists(self, name: str) -> bool:
        """Return whether an active plan name exists."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM plans WHERE name = ? AND archived = 0",
                (name,),
            ).fetchone()
        return row is not None

    def import_fingerprint_exists(self, import_hash: str) -> bool:
        """Return whether a portable import fingerprint already exists."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM imported_plan_fingerprints WHERE import_fingerprint = ?",
                (import_hash,),
            ).fetchone()
        return row is not None

    def export_forecast_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready forecast snapshot rows for one plan."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT fs.*
                FROM forecast_snapshots fs
                JOIN plan_versions pv ON pv.id = fs.plan_version_id
                WHERE pv.plan_id = ?
                ORDER BY fs.id
                """,
                (plan_id,),
            ).fetchall()
            columns = self.table_columns(conn, "forecast_snapshots")
        return [self.export_row(columns, row) for row in rows]

    def export_actual_transactions(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready actual transaction rows for one plan."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM actual_transactions
                WHERE plan_id = ?
                ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
            columns = self.table_columns(conn, "actual_transactions")
        return [self.export_row(columns, row) for row in rows]

    def export_balance_observations(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready balance observation rows for one plan."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM balance_observations
                WHERE plan_id = ?
                ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
            columns = self.table_columns(conn, "balance_observations")
        return [self.export_row(columns, row) for row in rows]

    def export_child_rows(self, plan_id: int, table: str) -> list[dict[str, Any]]:
        """Return export-ready forecast child rows for one plan."""
        join_sql = {
            "forecast_periods": """
                SELECT fp.*
                FROM forecast_periods fp
                JOIN forecast_snapshots fs ON fs.id = fp.forecast_snapshot_id
                JOIN plan_versions pv ON pv.id = fs.plan_version_id
                WHERE pv.plan_id = ?
                ORDER BY fp.id
            """,
            "debt_snapshots": """
                SELECT ds.*
                FROM debt_snapshots ds
                JOIN forecast_periods fp ON fp.id = ds.forecast_period_id
                JOIN forecast_snapshots fs ON fs.id = fp.forecast_snapshot_id
                JOIN plan_versions pv ON pv.id = fs.plan_version_id
                WHERE pv.plan_id = ?
                ORDER BY ds.id
            """,
            "savings_snapshots": """
                SELECT ss.*
                FROM savings_snapshots ss
                JOIN forecast_periods fp ON fp.id = ss.forecast_period_id
                JOIN forecast_snapshots fs ON fs.id = fp.forecast_snapshot_id
                JOIN plan_versions pv ON pv.id = fs.plan_version_id
                WHERE pv.plan_id = ?
                ORDER BY ss.id
            """,
            "data_quality_warnings": """
                SELECT w.*
                FROM data_quality_warnings w
                JOIN forecast_snapshots fs ON fs.id = w.forecast_snapshot_id
                JOIN plan_versions pv ON pv.id = fs.plan_version_id
                WHERE pv.plan_id = ?
                ORDER BY w.id
            """,
        }
        with self.connection() as conn:
            rows = conn.execute(join_sql[table], (plan_id,)).fetchall()
            columns = self.table_columns(conn, table)
        return [self.export_row(columns, row) for row in rows]

    @staticmethod
    def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
        """Return column names for a table."""
        return [column[0] for column in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]

    @staticmethod
    def export_row(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
        """Map a raw SQLite row into portable export values."""
        money_columns = {
            "income", "fixed_expenses", "personal_allowance",
            "personal_expenses_used", "personal_expense_reduction",
            "minimum_debt_payments", "snowball_payment", "savings_deposit",
            "savings_withdrawal", "checking_remaining", "reconciliation_difference",
            "starting_balance", "interest_charged", "minimum_payment",
            "extra_payment", "total_payment", "principal_paid", "ending_balance",
            "scheduled_minimum_payment", "actual_minimum_payment", "starting_savings",
            "normal_contribution", "redirected_snowball",
            "personal_expense_reduction_contribution", "other_contribution",
            "withdrawal", "ending_savings", "goal_target", "projected_shortfall",
            "total_deposit", "amount_required_before", "amount_required_after",
            "projected_deadline_balance", "withdrawal_amount", "amount", "balance",
            "total_projected_interest", "total_projected_debt_payments",
            "starting_debt", "ending_debt",
        }
        output = {}
        for column, value in zip(columns, row, strict=True):
            if value is not None and column in money_columns:
                output[column] = f"{from_cents(value):.2f}"
            else:
                output[column] = value
        return output

    @staticmethod
    def plan_from_row(row: tuple[Any, ...]) -> Plan:
        """Map a plan row to its public model."""
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

    @staticmethod
    def plan_version_from_row(row: tuple[Any, ...]) -> PlanVersion:
        """Map a plan version row to its public model."""
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

    @staticmethod
    def snapshot_from_row(row: tuple[Any, ...]) -> ForecastSnapshotRecord:
        """Map a forecast snapshot row to its public model."""
        return ForecastSnapshotRecord(
            id=row[0],
            plan_version_id=row[1],
            forecast_fingerprint=row[2],
            debt_free_date=None if row[3] is None else date.fromisoformat(row[3]),
            total_projected_interest=from_cents(row[4]),
            starting_debt=from_cents(row[5]),
            ending_debt=from_cents(row[6]),
            starting_savings=from_cents(row[7]),
            ending_savings=from_cents(row[8]),
            warning_count=row[9],
        )

    @staticmethod
    def actual_from_row(row: tuple[Any, ...]) -> ActualTransaction:
        """Map an actual transaction row to its public model."""
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
