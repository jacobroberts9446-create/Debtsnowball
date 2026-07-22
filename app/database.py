"""
database.py

SQLite persistence for generated DebtSnowball plans.
"""

import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from app.money import from_cents, money, to_cents

LATEST_SCHEMA_VERSION = 4
HISTORY_SCHEMA_VERSION = 3
INTEGER_CENT_SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSIONS = {0, 1}


class MonetaryColumn(NamedTuple):
    """Document one SQLite monetary column and its application field."""

    table: str
    column: str
    legacy_type: str
    current_type: str
    model_field: str
    nullable: bool
    default: str


SQLITE_MONEY_SCHEMA: tuple[MonetaryColumn, ...] = (
    MonetaryColumn("paychecks", "income", "REAL", "INTEGER cents", "income", False, "required"),
    MonetaryColumn(
        "paychecks",
        "bills_paid",
        "REAL",
        "INTEGER cents",
        "bills_paid",
        False,
        "required",
    ),
    MonetaryColumn(
        "paychecks",
        "debt_minimums",
        "REAL",
        "INTEGER cents",
        "debt_minimums",
        False,
        "required",
    ),
    MonetaryColumn(
        "paychecks",
        "snowball_payment",
        "REAL",
        "INTEGER cents",
        "snowball_payment",
        False,
        "required",
    ),
    MonetaryColumn(
        "paychecks",
        "savings_added",
        "REAL",
        "INTEGER cents",
        "savings_added",
        False,
        "required",
    ),
    MonetaryColumn(
        "paychecks",
        "checking_remaining",
        "REAL",
        "INTEGER cents",
        "checking_remaining",
        False,
        "required",
    ),
    MonetaryColumn("debts", "balance", "REAL", "INTEGER cents", "balance", False, "required"),
    MonetaryColumn(
        "debts",
        "minimum_payment",
        "REAL",
        "INTEGER cents",
        "minimum",
        False,
        "required",
    ),
    MonetaryColumn(
        "debts",
        "total_paid",
        "REAL",
        "INTEGER cents",
        "total_paid",
        False,
        "required",
    ),
    MonetaryColumn(
        "debts",
        "total_interest_paid",
        "REAL",
        "INTEGER cents",
        "total_interest_paid",
        False,
        "required",
    ),
)


class DatabaseMigrationError(RuntimeError):
    """Raised when SQLite schema migration cannot complete safely."""


class Database:
    """Persist generated budget plans to SQLite."""

    def __init__(self, filename: str | Path = "output/debtsnowball.sqlite") -> None:
        self.path = Path(filename)
        self.last_backup_path: Path | None = None

    def initialize(self) -> None:
        """Create or migrate the database schema to the latest version."""
        if not self._is_memory_database():
            self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            version = self._schema_version(conn)
            if version > LATEST_SCHEMA_VERSION:
                raise DatabaseMigrationError(
                    f"Unsupported SQLite schema version {version}; "
                    f"latest supported version is {LATEST_SCHEMA_VERSION}."
                )
            if version == LATEST_SCHEMA_VERSION:
                self._ensure_current_schema_exists(conn)
                return
            if not self._has_application_tables(conn):
                with conn:
                    self._create_current_schema(conn)
                    self._set_schema_version(conn, LATEST_SCHEMA_VERSION)
                return
            if version == INTEGER_CENT_SCHEMA_VERSION:
                self._backup_before_migration("v2-integer-cents")
                self._migrate_v2_to_v3(conn)
                self._migrate_v3_to_v4(conn)
                return
            if version == HISTORY_SCHEMA_VERSION:
                self._backup_before_migration("v3-history")
                self._migrate_v3_to_v4(conn)
                return
            if version not in LEGACY_SCHEMA_VERSIONS:
                raise DatabaseMigrationError(f"Cannot migrate SQLite schema version {version}.")

            self._backup_before_migration()
            self._migrate_legacy_real_money_schema(conn)

    def save_plan(self, paychecks: list[Any], debts: list[dict[str, Any]]) -> None:
        """Replace stored paycheck and debt rows with the generated plan."""
        self.initialize()

        with closing(self._connect()) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute("DELETE FROM paychecks")
                conn.execute("DELETE FROM debts")

                conn.executemany(
                    """
                    INSERT INTO paychecks (
                        pay_date,
                        income,
                        bills_paid,
                        debt_minimums,
                        snowball_payment,
                        savings_added,
                        checking_remaining,
                        notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            paycheck.pay_date.isoformat(),
                            self._money_to_storage(paycheck.income),
                            self._money_to_storage(paycheck.bills_paid),
                            self._money_to_storage(paycheck.debt_minimums),
                            self._money_to_storage(paycheck.snowball_payment),
                            self._money_to_storage(paycheck.savings_added),
                            self._money_to_storage(paycheck.checking_remaining),
                            "\n".join(paycheck.notes),
                        )
                        for paycheck in paychecks
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO debts (
                        name,
                        balance,
                        apr,
                        minimum_payment,
                        total_paid,
                        total_interest_paid,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            debt["name"],
                            self._money_to_storage(debt["balance"]),
                            str(debt["apr"]),
                            self._money_to_storage(debt["minimum"]),
                            self._money_to_storage(debt["total_paid"]),
                            self._money_to_storage(debt["total_interest_paid"]),
                            debt["status"],
                        )
                        for debt in debts
                    ],
                )

    def load_paychecks(self) -> list[dict[str, Any]]:
        """Return stored paycheck rows with cent fields normalized to Decimal."""
        self.initialize()

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    pay_date,
                    income,
                    bills_paid,
                    debt_minimums,
                    snowball_payment,
                    savings_added,
                    checking_remaining,
                    notes
                FROM paychecks
                ORDER BY pay_date
                """
            ).fetchall()

        return [
            {
                "pay_date": row[0],
                "income": self._money_from_storage(row[1]),
                "bills_paid": self._money_from_storage(row[2]),
                "debt_minimums": self._money_from_storage(row[3]),
                "snowball_payment": self._money_from_storage(row[4]),
                "savings_added": self._money_from_storage(row[5]),
                "checking_remaining": self._money_from_storage(row[6]),
                "notes": row[7],
            }
            for row in rows
        ]

    def load_debts(self) -> list[dict[str, Any]]:
        """Return stored debt rows with cent fields normalized to Decimal."""
        self.initialize()

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    name,
                    balance,
                    apr,
                    minimum_payment,
                    total_paid,
                    total_interest_paid,
                    status
                FROM debts
                ORDER BY name
                """
            ).fetchall()

        return [
            {
                "name": row[0],
                "balance": self._money_from_storage(row[1]),
                "apr": row[2],
                "minimum": self._money_from_storage(row[3]),
                "total_paid": self._money_from_storage(row[4]),
                "total_interest_paid": self._money_from_storage(row[5]),
                "status": row[6],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def _set_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(f"PRAGMA user_version = {version}")

    def _has_application_tables(self, conn: sqlite3.Connection) -> bool:
        table_names = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name IN ('paychecks', 'debts')
                """
            )
        }
        return bool(table_names)

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None

    def _ensure_current_schema_exists(self, conn: sqlite3.Connection) -> None:
        if not self._has_application_tables(conn):
            with conn:
                self._create_current_schema(conn)
            return
        if self._table_exists(conn, "plan_versions"):
            with conn:
                self._create_history_integrity_triggers(conn)
        self._validate_current_schema(conn)

    def _create_current_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paychecks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pay_date TEXT NOT NULL UNIQUE,
                income INTEGER NOT NULL,
                bills_paid INTEGER NOT NULL,
                debt_minimums INTEGER NOT NULL,
                snowball_payment INTEGER NOT NULL,
                savings_added INTEGER NOT NULL,
                checking_remaining INTEGER NOT NULL,
                notes TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS debts (
                name TEXT PRIMARY KEY,
                balance INTEGER NOT NULL,
                apr REAL NOT NULL,
                minimum_payment INTEGER NOT NULL,
                total_paid INTEGER NOT NULL,
                total_interest_paid INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self._create_history_schema(conn)

    def _backup_before_migration(self, label: str = "v1-real-money") -> None:
        """Create a timestamped backup before changing a file-backed database."""
        self.last_backup_path = None
        if self._is_memory_database() or not self.path.exists():
            return

        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        backup_path = self.path.with_name(f"{self.path.name}.{label}.{timestamp}.bak")
        suffix = 1
        while backup_path.exists():
            backup_path = self.path.with_name(
                f"{self.path.name}.{label}.{timestamp}.{suffix}.bak"
            )
            suffix += 1
        shutil.copy2(self.path, backup_path)
        self.last_backup_path = backup_path

    def _migrate_v2_to_v3(self, conn: sqlite3.Connection) -> None:
        """Add durable history tables to an existing integer-cent database."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_current_money_schema(conn)
            self._create_history_schema(conn, include_detail=False)
            self._validate_history_schema(conn, include_detail=False)
            self._set_schema_version(conn, HISTORY_SCHEMA_VERSION)
            conn.commit()
        except (sqlite3.Error, ValueError) as exc:
            conn.rollback()
            raise DatabaseMigrationError(f"SQLite history migration failed: {exc}") from exc

    def _migrate_v3_to_v4(self, conn: sqlite3.Connection) -> None:
        """Add detailed history fields and balance observations."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._create_history_detail_schema(conn)
            self._create_history_integrity_triggers(conn)
            self._validate_history_schema(conn)
            self._set_schema_version(conn, LATEST_SCHEMA_VERSION)
            conn.commit()
        except (sqlite3.Error, ValueError) as exc:
            conn.rollback()
            raise DatabaseMigrationError(
                f"SQLite detailed history migration failed: {exc}"
            ) from exc

    def _migrate_legacy_real_money_schema(self, conn: sqlite3.Connection) -> None:
        """Migrate legacy REAL money columns to INTEGER cents transactionally."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_legacy_schema(conn)
            conn.execute("DROP TABLE IF EXISTS __migration_paychecks")
            conn.execute("DROP TABLE IF EXISTS __migration_debts")
            self._create_migration_tables(conn)
            self._copy_legacy_paychecks(conn)
            self._copy_legacy_debts(conn)
            self._validate_migrated_counts(conn)
            conn.execute("DROP TABLE paychecks")
            conn.execute("ALTER TABLE __migration_paychecks RENAME TO paychecks")
            conn.execute("DROP TABLE debts")
            conn.execute("ALTER TABLE __migration_debts RENAME TO debts")
            self._create_history_schema(conn)
            self._validate_current_schema(conn)
            self._set_schema_version(conn, LATEST_SCHEMA_VERSION)
            conn.commit()
        except (sqlite3.Error, ValueError, OverflowError) as exc:
            conn.rollback()
            self._cleanup_migration_tables(conn)
            raise DatabaseMigrationError(f"SQLite money migration failed: {exc}") from exc

    def _create_migration_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE __migration_paychecks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pay_date TEXT NOT NULL UNIQUE,
                income INTEGER NOT NULL,
                bills_paid INTEGER NOT NULL,
                debt_minimums INTEGER NOT NULL,
                snowball_payment INTEGER NOT NULL,
                savings_added INTEGER NOT NULL,
                checking_remaining INTEGER NOT NULL,
                notes TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE __migration_debts (
                name TEXT PRIMARY KEY,
                balance INTEGER NOT NULL,
                apr REAL NOT NULL,
                minimum_payment INTEGER NOT NULL,
                total_paid INTEGER NOT NULL,
                total_interest_paid INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )

    def _copy_legacy_paychecks(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT
                id,
                pay_date,
                income,
                bills_paid,
                debt_minimums,
                snowball_payment,
                savings_added,
                checking_remaining,
                notes
            FROM paychecks
            ORDER BY id
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO __migration_paychecks (
                id,
                pay_date,
                income,
                bills_paid,
                debt_minimums,
                snowball_payment,
                savings_added,
                checking_remaining,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0],
                    row[1],
                    self._legacy_money_to_cents("paychecks", "income", row[0], row[2]),
                    self._legacy_money_to_cents("paychecks", "bills_paid", row[0], row[3]),
                    self._legacy_money_to_cents(
                        "paychecks", "debt_minimums", row[0], row[4]
                    ),
                    self._legacy_money_to_cents(
                        "paychecks", "snowball_payment", row[0], row[5]
                    ),
                    self._legacy_money_to_cents(
                        "paychecks", "savings_added", row[0], row[6]
                    ),
                    self._legacy_money_to_cents(
                        "paychecks", "checking_remaining", row[0], row[7]
                    ),
                    row[8],
                )
                for row in rows
            ],
        )

    def _copy_legacy_debts(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT
                name,
                balance,
                apr,
                minimum_payment,
                total_paid,
                total_interest_paid,
                status
            FROM debts
            ORDER BY name
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO __migration_debts (
                name,
                balance,
                apr,
                minimum_payment,
                total_paid,
                total_interest_paid,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0],
                    self._legacy_money_to_cents("debts", "balance", row[0], row[1]),
                    row[2],
                    self._legacy_money_to_cents("debts", "minimum_payment", row[0], row[3]),
                    self._legacy_money_to_cents("debts", "total_paid", row[0], row[4]),
                    self._legacy_money_to_cents(
                        "debts", "total_interest_paid", row[0], row[5]
                    ),
                    row[6],
                )
                for row in rows
            ],
        )

    def _legacy_money_to_cents(
        self,
        table: str,
        column: str,
        row_id: object,
        raw_value: object,
    ) -> int:
        if raw_value is None:
            raise ValueError(f"{table}.{column} row {row_id!r} is required.")
        try:
            return to_cents(money(str(raw_value)))
        except (ValueError, OverflowError) as exc:
            raise ValueError(
                f"{table}.{column} row {row_id!r} has invalid money value {raw_value!r}."
            ) from exc

    def _validate_legacy_schema(self, conn: sqlite3.Connection) -> None:
        required_columns = {
            "paychecks": {
                "id",
                "pay_date",
                "income",
                "bills_paid",
                "debt_minimums",
                "snowball_payment",
                "savings_added",
                "checking_remaining",
                "notes",
            },
            "debts": {
                "name",
                "balance",
                "apr",
                "minimum_payment",
                "total_paid",
                "total_interest_paid",
                "status",
            },
        }
        for table, expected in required_columns.items():
            columns = self._table_columns(conn, table)
            missing = expected - columns
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(f"{table} is missing required column(s): {missing_list}.")

    def _validate_migrated_counts(self, conn: sqlite3.Connection) -> None:
        for source, target in (
            ("paychecks", "__migration_paychecks"),
            ("debts", "__migration_debts"),
        ):
            source_count = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
            target_count = conn.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
            if source_count != target_count:
                raise ValueError(
                    f"{source} migration row-count mismatch: "
                    f"{source_count} source rows, {target_count} migrated rows."
                )

    def _validate_current_schema(self, conn: sqlite3.Connection) -> None:
        self._validate_current_money_schema(conn)
        self._validate_history_schema(conn)

    def _validate_current_money_schema(self, conn: sqlite3.Connection) -> None:
        expected_integer_money = {
            "paychecks": {
                "income",
                "bills_paid",
                "debt_minimums",
                "snowball_payment",
                "savings_added",
                "checking_remaining",
            },
            "debts": {
                "balance",
                "minimum_payment",
                "total_paid",
                "total_interest_paid",
            },
        }
        for table, columns in expected_integer_money.items():
            table_info = {
                row[1]: row[2].upper()
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column in columns:
                if table_info.get(column) != "INTEGER":
                    raise ValueError(f"{table}.{column} must use INTEGER cent storage.")

    def _create_history_schema(
        self,
        conn: sqlite3.Connection,
        *,
        include_detail: bool = True,
    ) -> None:
        """Create version-3 local plan history and snapshot tables."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                current_version_id INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (current_version_id)
                    REFERENCES plan_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_active_name
            ON plans (name)
            WHERE archived = 0
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                version_number INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                change_note TEXT NOT NULL,
                config_snapshot TEXT NOT NULL,
                config_fingerprint TEXT NOT NULL,
                application_version TEXT NOT NULL,
                forecast_engine_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (plan_id) REFERENCES plans(id),
                UNIQUE (plan_id, version_number)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_versions_plan
            ON plan_versions (plan_id, version_number)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecast_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_version_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                forecast_start_date TEXT NOT NULL,
                forecast_end_date TEXT NOT NULL,
                debt_free_date TEXT,
                total_projected_interest INTEGER NOT NULL,
                total_projected_debt_payments INTEGER NOT NULL,
                starting_debt INTEGER NOT NULL,
                ending_debt INTEGER NOT NULL,
                starting_savings INTEGER NOT NULL,
                ending_savings INTEGER NOT NULL,
                pay_period_count INTEGER NOT NULL,
                forecast_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                warning_count INTEGER NOT NULL,
                FOREIGN KEY (plan_version_id) REFERENCES plan_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forecast_snapshots_version
            ON forecast_snapshots (plan_version_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecast_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_snapshot_id INTEGER NOT NULL,
                sequence_number INTEGER NOT NULL,
                pay_date TEXT NOT NULL,
                income INTEGER NOT NULL,
                fixed_expenses INTEGER NOT NULL,
                personal_allowance INTEGER NOT NULL,
                personal_expenses_used INTEGER NOT NULL,
                personal_expense_reduction INTEGER NOT NULL,
                minimum_debt_payments INTEGER NOT NULL,
                snowball_payment INTEGER NOT NULL,
                savings_deposit INTEGER NOT NULL,
                savings_withdrawal INTEGER NOT NULL,
                checking_remaining INTEGER NOT NULL,
                reconciliation_difference INTEGER NOT NULL,
                active_savings_goal TEXT,
                reason_codes TEXT NOT NULL,
                explanation TEXT NOT NULL,
                FOREIGN KEY (forecast_snapshot_id) REFERENCES forecast_snapshots(id),
                UNIQUE (forecast_snapshot_id, sequence_number)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forecast_periods_snapshot_sequence
            ON forecast_periods (forecast_snapshot_id, sequence_number)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS debt_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_period_id INTEGER NOT NULL,
                debt_identifier TEXT NOT NULL,
                debt_name TEXT NOT NULL,
                starting_balance INTEGER NOT NULL,
                interest_charged INTEGER NOT NULL,
                minimum_payment INTEGER NOT NULL,
                extra_payment INTEGER NOT NULL,
                total_payment INTEGER NOT NULL,
                principal_paid INTEGER NOT NULL,
                ending_balance INTEGER NOT NULL,
                paid_off INTEGER NOT NULL,
                payoff_date TEXT,
                FOREIGN KEY (forecast_period_id) REFERENCES forecast_periods(id),
                UNIQUE (forecast_period_id, debt_identifier)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_debt_snapshots_period
            ON debt_snapshots (forecast_period_id, debt_identifier)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS savings_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_period_id INTEGER NOT NULL UNIQUE,
                starting_savings INTEGER NOT NULL,
                normal_contribution INTEGER NOT NULL,
                redirected_snowball INTEGER NOT NULL,
                personal_expense_reduction_contribution INTEGER NOT NULL,
                other_contribution INTEGER NOT NULL,
                withdrawal INTEGER NOT NULL,
                ending_savings INTEGER NOT NULL,
                active_goal TEXT,
                goal_target INTEGER,
                goal_deadline TEXT,
                projected_shortfall INTEGER NOT NULL,
                goal_feasible_status TEXT NOT NULL,
                FOREIGN KEY (forecast_period_id) REFERENCES forecast_periods(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actual_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                entry_date TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                debt_identifier TEXT,
                amount INTEGER NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                corrected_entry_id INTEGER,
                note TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (plan_id) REFERENCES plans(id),
                FOREIGN KEY (corrected_entry_id) REFERENCES actual_transactions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_actual_transactions_plan_date
            ON actual_transactions (plan_id, entry_date)
            """
        )
        if include_detail:
            self._create_history_detail_schema(conn)
            self._create_history_integrity_triggers(conn)

    def _create_history_detail_schema(self, conn: sqlite3.Connection) -> None:
        """Create version-4 detailed history columns and tables."""
        for statement in (
            "ALTER TABLE debt_snapshots ADD COLUMN payoff_order INTEGER",
            "ALTER TABLE debt_snapshots ADD COLUMN scheduled_minimum_payment INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE debt_snapshots ADD COLUMN actual_minimum_payment INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE debt_snapshots ADD COLUMN apr TEXT NOT NULL DEFAULT '0'",
            "ALTER TABLE debt_snapshots ADD COLUMN final_payoff_tolerance_applied INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE debt_snapshots ADD COLUMN allocation_reason_code TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE debt_snapshots ADD COLUMN explanation TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE savings_snapshots ADD COLUMN total_deposit INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE savings_snapshots ADD COLUMN amount_required_before INTEGER",
            "ALTER TABLE savings_snapshots ADD COLUMN amount_required_after INTEGER",
            "ALTER TABLE savings_snapshots ADD COLUMN projected_deadline_balance INTEGER",
            "ALTER TABLE savings_snapshots ADD COLUMN withdrawal_date TEXT",
            "ALTER TABLE savings_snapshots ADD COLUMN withdrawal_amount INTEGER",
            "ALTER TABLE savings_snapshots ADD COLUMN post_withdrawal_allocation_state TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE savings_snapshots ADD COLUMN reason_codes TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE savings_snapshots ADD COLUMN explanation TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE actual_transactions ADD COLUMN forecast_period_id INTEGER",
            "ALTER TABLE actual_transactions ADD COLUMN match_method TEXT NOT NULL DEFAULT 'unmatched'",
            "ALTER TABLE actual_transactions ADD COLUMN matched_at TEXT",
            "ALTER TABLE actual_transactions ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0",
        ):
            self._try_add_column(conn, statement)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                observation_date TEXT NOT NULL,
                observation_type TEXT NOT NULL,
                debt_identifier TEXT,
                balance INTEGER NOT NULL,
                source TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                forecast_period_id INTEGER,
                match_method TEXT NOT NULL DEFAULT 'unmatched',
                FOREIGN KEY (plan_id) REFERENCES plans(id),
                FOREIGN KEY (forecast_period_id) REFERENCES forecast_periods(id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_balance_observations_plan_date
            ON balance_observations (plan_id, observation_date)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_quality_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_snapshot_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                relevant_date TEXT,
                relevant_name TEXT,
                suggested_action TEXT,
                FOREIGN KEY (forecast_snapshot_id) REFERENCES forecast_snapshots(id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_data_quality_warnings_snapshot
            ON data_quality_warnings (forecast_snapshot_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_plan_fingerprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                import_fingerprint TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_debt_snapshots_period_order
            ON debt_snapshots (forecast_period_id, payoff_order)
            """
        )

    def _create_history_integrity_triggers(self, conn: sqlite3.Connection) -> None:
        """Create guarded triggers that protect immutable plan versions."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_version_mutation_guard (
                id INTEGER PRIMARY KEY CHECK (id = 1)
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_plan_version_update
            BEFORE UPDATE ON plan_versions
            WHEN NOT EXISTS (
                SELECT 1 FROM plan_version_mutation_guard WHERE id = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'plan_versions are immutable');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_plan_version_delete
            BEFORE DELETE ON plan_versions
            WHEN NOT EXISTS (
                SELECT 1 FROM plan_version_mutation_guard WHERE id = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'plan_versions are immutable');
            END
            """
        )

    def _try_add_column(self, conn: sqlite3.Connection, statement: str) -> None:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def _validate_history_schema(
        self,
        conn: sqlite3.Connection,
        *,
        include_detail: bool = True,
    ) -> None:
        required_tables = {
            "plans",
            "plan_versions",
            "forecast_snapshots",
            "forecast_periods",
            "debt_snapshots",
            "savings_snapshots",
            "actual_transactions",
        }
        if include_detail:
            required_tables |= {
                "balance_observations",
                "data_quality_warnings",
                "imported_plan_fingerprints",
                "plan_version_mutation_guard",
            }
        existing = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
        missing = required_tables - existing
        if missing:
            raise ValueError(
                f"history schema is missing table(s): {', '.join(sorted(missing))}."
            )
        if include_detail:
            trigger_names = {
                row[0]
                for row in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'trigger'
                    """
                )
            }
            missing_triggers = {
                "prevent_plan_version_update",
                "prevent_plan_version_delete",
            } - trigger_names
            if missing_triggers:
                raise ValueError(
                    "history schema is missing trigger(s): "
                    f"{', '.join(sorted(missing_triggers))}."
                )

    def _cleanup_migration_tables(self, conn: sqlite3.Connection) -> None:
        """Remove temporary migration tables after a failed migration attempt."""
        with conn:
            conn.execute("DROP TABLE IF EXISTS __migration_paychecks")
            conn.execute("DROP TABLE IF EXISTS __migration_debts")

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not columns:
            raise ValueError(f"{table} table is missing.")
        return columns

    def _money_to_storage(self, value: object) -> int:
        """Convert Decimal money to SQLite INTEGER cents."""
        return to_cents(value)

    def _money_from_storage(self, value: object) -> Decimal:
        """Convert SQLite INTEGER cents to Decimal money."""
        return from_cents(value)

    def _is_memory_database(self) -> bool:
        return str(self.path) == ":memory:"
