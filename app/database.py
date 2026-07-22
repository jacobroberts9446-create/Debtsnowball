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

LATEST_SCHEMA_VERSION = 2
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

    def _ensure_current_schema_exists(self, conn: sqlite3.Connection) -> None:
        if not self._has_application_tables(conn):
            with conn:
                self._create_current_schema(conn)
            return
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

    def _backup_before_migration(self) -> None:
        """Create a timestamped backup before changing a file-backed database."""
        self.last_backup_path = None
        if self._is_memory_database() or not self.path.exists():
            return

        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        backup_path = self.path.with_name(f"{self.path.name}.v1-real-money.{timestamp}.bak")
        suffix = 1
        while backup_path.exists():
            backup_path = self.path.with_name(
                f"{self.path.name}.v1-real-money.{timestamp}.{suffix}.bak"
            )
            suffix += 1
        shutil.copy2(self.path, backup_path)
        self.last_backup_path = backup_path

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
