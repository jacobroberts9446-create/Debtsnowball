import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.database import Database, DatabaseMigrationError, LATEST_SCHEMA_VERSION
from app.money import from_cents, money, to_cents


def paycheck_record(summary):
    return SimpleNamespace(
        pay_date=summary.pay_date,
        income=summary.income,
        bills_paid=summary.bills_paid,
        debt_minimums=summary.debt_minimums,
        snowball_payment=summary.snowball_payment,
        savings_added=summary.savings_contribution,
        checking_remaining=summary.remaining_cash,
        notes=[],
    )


def create_legacy_database(db_path, *, duplicate_debt_names=False, nullable_income=False):
    income_definition = "income REAL" if nullable_income else "income REAL NOT NULL"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            f"""
            CREATE TABLE paychecks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pay_date TEXT NOT NULL UNIQUE,
                {income_definition},
                bills_paid REAL NOT NULL,
                debt_minimums REAL NOT NULL,
                snowball_payment REAL NOT NULL,
                savings_added REAL NOT NULL,
                checking_remaining REAL NOT NULL,
                notes TEXT NOT NULL
            )
            """
        )
        if duplicate_debt_names:
            conn.execute(
                """
                CREATE TABLE debts (
                    name TEXT NOT NULL,
                    balance REAL NOT NULL,
                    apr REAL NOT NULL,
                    minimum_payment REAL NOT NULL,
                    total_paid REAL NOT NULL,
                    total_interest_paid REAL NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE debts (
                    name TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    apr REAL NOT NULL,
                    minimum_payment REAL NOT NULL,
                    total_paid REAL NOT NULL,
                    total_interest_paid REAL NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
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
                ("2026-07-17", 0.0, -0.0, 0.01, 0.02, 215.0, 568.5, "zero"),
                (
                    "2026-07-31",
                    1500.0,
                    2400.0,
                    568.4999999999999,
                    "1.005",
                    "1.004",
                    0.0,
                    "rounding",
                ),
            ],
        )
        debts = [
            ("Card", 999999999.99, 26.5, 0.02, 215.0, 23.455, "active"),
            ("Loan", 2400.0, 0.0, 568.5, 1500.0, -0.0, "active"),
        ]
        if duplicate_debt_names:
            debts.append(("Card", 1.00, 0.0, 1.0, 1.0, 0.0, "duplicate"))
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
            debts,
        )
        conn.commit()


def table_column_types(conn, table):
    return {row[1]: row[2].upper() for row in conn.execute(f"PRAGMA table_info({table})")}


def table_names(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    }


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_new_database_uses_latest_integer_cent_schema(tmp_path):
    db_path = tmp_path / "plan.sqlite"

    Database(db_path).initialize()

    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == LATEST_SCHEMA_VERSION
        paycheck_types = table_column_types(conn, "paychecks")
        debt_types = table_column_types(conn, "debts")
        assert paycheck_types["income"] == "INTEGER"
        assert paycheck_types["snowball_payment"] == "INTEGER"
        assert debt_types["balance"] == "INTEGER"
        assert debt_types["total_interest_paid"] == "INTEGER"
        assert debt_types["apr"] == "REAL"


def test_database_round_trip_stores_raw_integer_cents_and_loads_decimal_money(tmp_path):
    db_path = tmp_path / "plan.sqlite"
    paychecks = [
        SimpleNamespace(
            pay_date=date(2026, 1, 2),
            income=Decimal("1500.00"),
            bills_paid=Decimal("215.00"),
            debt_minimums=Decimal("0.01"),
            snowball_payment=Decimal("568.50"),
            savings_added=Decimal("2400.00"),
            checking_remaining=Decimal("-0.00"),
            notes=["ok"],
        )
    ]
    debts = [
        {
            "name": "Card",
            "balance": Decimal("999999999.99"),
            "apr": Decimal("26"),
            "minimum": Decimal("0.02"),
            "total_paid": Decimal("215.00"),
            "total_interest_paid": Decimal("23.46"),
            "status": "active",
        }
    ]

    database = Database(db_path)
    database.save_plan(paychecks, debts)

    with closing(sqlite3.connect(db_path)) as conn:
        raw_paycheck = conn.execute(
            """
            SELECT income, bills_paid, debt_minimums, snowball_payment,
                   savings_added, checking_remaining
            FROM paychecks
            """
        ).fetchone()
        raw_debt = conn.execute(
            """
            SELECT balance, minimum_payment, total_paid, total_interest_paid
            FROM debts
            """
        ).fetchone()

    assert raw_paycheck == (150000, 21500, 1, 56850, 240000, 0)
    assert raw_debt == (99999999999, 2, 21500, 2346)
    assert all(isinstance(value, int) for value in raw_paycheck)
    assert all(isinstance(value, int) for value in raw_debt)

    saved_paycheck = database.load_paychecks()[0]
    saved_debt = database.load_debts()[0]
    assert saved_paycheck["income"] == Decimal("1500.00")
    assert saved_paycheck["bills_paid"] == Decimal("215.00")
    assert saved_paycheck["debt_minimums"] == Decimal("0.01")
    assert saved_paycheck["snowball_payment"] == Decimal("568.50")
    assert saved_paycheck["savings_added"] == Decimal("2400.00")
    assert saved_paycheck["checking_remaining"] == Decimal("0.00")
    assert isinstance(saved_paycheck["income"], Decimal)
    assert not saved_paycheck["checking_remaining"].is_signed()
    assert saved_debt["balance"] == Decimal("999999999.99")
    assert saved_debt["minimum"] == Decimal("0.02")
    assert saved_debt["total_interest_paid"] == Decimal("23.46")
    assert isinstance(saved_debt["balance"], Decimal)


@pytest.mark.parametrize(
    ("source", "stored_cents", "loaded"),
    [
        (Decimal("0.00"), 0, Decimal("0.00")),
        (Decimal("-0.00"), 0, Decimal("0.00")),
        (Decimal("0.01"), 1, Decimal("0.01")),
        (Decimal("0.02"), 2, Decimal("0.02")),
        (Decimal("215.00"), 21500, Decimal("215.00")),
        (Decimal("568.50"), 56850, Decimal("568.50")),
        (Decimal("2400.00"), 240000, Decimal("2400.00")),
        (Decimal("23.455"), 2346, Decimal("23.46")),
    ],
)
def test_decimal_to_sqlite_to_decimal_round_trip_is_exact(tmp_path, source, stored_cents, loaded):
    db_path = tmp_path / "round_trip.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE values_to_test (amount INTEGER NOT NULL)")
        conn.execute("INSERT INTO values_to_test VALUES (?)", (to_cents(source),))
        raw_value = conn.execute("SELECT amount FROM values_to_test").fetchone()[0]

    assert raw_value == stored_cents
    assert isinstance(raw_value, int)
    assert from_cents(raw_value) == loaded


def test_legacy_real_database_migrates_to_integer_cents_with_backup(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    create_legacy_database(db_path)

    database = Database(db_path)
    database.initialize()
    database.initialize()

    backups = list(tmp_path.glob("legacy.sqlite.v1-real-money.*.bak"))
    assert len(backups) == 1
    assert database.last_backup_path in backups

    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == LATEST_SCHEMA_VERSION
        paycheck_types = table_column_types(conn, "paychecks")
        debt_types = table_column_types(conn, "debts")
        assert paycheck_types["income"] == "INTEGER"
        assert debt_types["balance"] == "INTEGER"
        assert debt_types["apr"] == "REAL"
        assert conn.execute("SELECT COUNT(*) FROM paychecks").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM debts").fetchone()[0] == 2
        assert conn.execute("SELECT income, bills_paid FROM paychecks WHERE id = 1").fetchone() == (
            0,
            0,
        )
        assert conn.execute(
            "SELECT debt_minimums, snowball_payment FROM paychecks WHERE id = 2"
        ).fetchone() == (56850, 101)
        assert conn.execute("SELECT savings_added FROM paychecks WHERE id = 2").fetchone()[0] == 100
        assert conn.execute(
            "SELECT total_interest_paid FROM debts WHERE name = 'Card'"
        ).fetchone()[0] == 2346
        conn.execute(
            "INSERT INTO paychecks (pay_date, income, bills_paid, debt_minimums, "
            "snowball_payment, savings_added, checking_remaining, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-08-14", 1, 2, 3, 4, 5, 6, "unique works"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO paychecks (pay_date, income, bills_paid, debt_minimums, "
                "snowball_payment, savings_added, checking_remaining, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-08-14", 1, 2, 3, 4, 5, 6, "duplicate"),
            )

    reloaded = database.load_paychecks()
    assert reloaded[0]["savings_added"] == Decimal("215.00")
    assert reloaded[1]["snowball_payment"] == Decimal("1.01")
    assert reloaded[1]["savings_added"] == Decimal("1.00")
    assert database.load_debts()[0]["total_interest_paid"] == Decimal("23.46")


def test_current_version_database_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "plan.sqlite"
    database = Database(db_path)
    database.initialize()
    with closing(sqlite3.connect(db_path)) as conn:
        before_tables = table_names(conn)
        before_version = schema_version(conn)

    database.initialize()

    with closing(sqlite3.connect(db_path)) as conn:
        assert table_names(conn) == before_tables
        assert schema_version(conn) == before_version == LATEST_SCHEMA_VERSION
        assert list(tmp_path.glob("*.bak")) == []


def test_unknown_future_schema_version_fails_safely(tmp_path):
    db_path = tmp_path / "future.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA user_version = 999")
        conn.commit()

    with pytest.raises(DatabaseMigrationError, match="Unsupported SQLite schema version"):
        Database(db_path).initialize()

    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == 999


def test_current_version_with_real_money_columns_fails_validation(tmp_path):
    db_path = tmp_path / "bad_current.sqlite"
    create_legacy_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
        conn.commit()

    with pytest.raises(ValueError, match="must use INTEGER cent storage"):
        Database(db_path).initialize()


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("income", "not-money", "paychecks.income"),
        ("income", None, "paychecks.income"),
        ("income", "92233720368547758.08", "paychecks.income"),
    ],
)
def test_invalid_legacy_money_rolls_back_and_preserves_source_data(
    tmp_path, column, value, message
):
    db_path = tmp_path / "bad_legacy.sqlite"
    create_legacy_database(db_path, nullable_income=value is None)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(f"UPDATE paychecks SET {column} = ? WHERE id = 1", (value,))
        conn.commit()

    with pytest.raises(DatabaseMigrationError, match=message):
        Database(db_path).initialize()

    backups = list(tmp_path.glob("bad_legacy.sqlite.v1-real-money.*.bak"))
    assert len(backups) == 1
    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == 1
        assert table_column_types(conn, "paychecks")["income"] == "REAL"
        assert conn.execute("SELECT COUNT(*) FROM paychecks").fetchone()[0] == 2
        assert "__migration_paychecks" not in table_names(conn)


def test_missing_required_legacy_column_rolls_back(tmp_path):
    db_path = tmp_path / "missing.sqlite"
    create_legacy_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("ALTER TABLE paychecks DROP COLUMN notes")
        conn.commit()

    with pytest.raises(DatabaseMigrationError, match="missing required column"):
        Database(db_path).initialize()

    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == 1
        assert table_column_types(conn, "paychecks")["income"] == "REAL"


def test_duplicate_key_failure_rolls_back_legacy_migration(tmp_path):
    db_path = tmp_path / "duplicate.sqlite"
    create_legacy_database(db_path, duplicate_debt_names=True)

    with pytest.raises(DatabaseMigrationError):
        Database(db_path).initialize()

    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == 1
        assert table_column_types(conn, "debts")["balance"] == "REAL"
        assert conn.execute("SELECT COUNT(*) FROM debts WHERE name = 'Card'").fetchone()[0] == 2


def test_injected_migration_failure_rolls_back_and_leaves_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "interrupted.sqlite"
    create_legacy_database(db_path)

    def fail_counts(self, conn):
        raise ValueError("injected interruption")

    monkeypatch.setattr(Database, "_validate_migrated_counts", fail_counts)

    with pytest.raises(DatabaseMigrationError, match="injected interruption"):
        Database(db_path).initialize()

    assert list(tmp_path.glob("interrupted.sqlite.v1-real-money.*.bak"))
    with closing(sqlite3.connect(db_path)) as conn:
        assert schema_version(conn) == 1
        assert table_column_types(conn, "paychecks")["income"] == "REAL"
        assert "__migration_paychecks" not in table_names(conn)


def test_database_reload_preserves_cash_flow_reconciliation_for_generated_plan(tmp_path):
    config = Config().load("config.json")
    config.savings_plan.deadline_priority_enabled = True
    periods = CalendarEngine(config.settings).generate(date(2026, 8, 28))
    summaries = BudgetEngine(config).build_plan(periods)
    paychecks = [paycheck_record(summary) for summary in summaries]
    final_summary = summaries[-1]
    debts = [
        {
            "name": debt.name,
            "balance": debt.balance,
            "apr": Decimal("0.00"),
            "minimum": debt.minimum,
            "total_paid": Decimal("0.00"),
            "total_interest_paid": Decimal("0.00"),
            "status": debt.status,
        }
        for debt in final_summary.active_debt_balances
    ]

    database = Database(tmp_path / "generated.sqlite")
    database.save_plan(paychecks, debts)
    loaded = database.load_paychecks()

    assert loaded[0]["savings_added"] == Decimal("430.00")
    assert loaded[1]["savings_added"] == Decimal("470.00")
    assert loaded[2]["snowball_payment"] == Decimal("215.00")
    for original, reloaded in zip(summaries, loaded, strict=True):
        allocated = (
            reloaded["bills_paid"]
            + reloaded["debt_minimums"]
            + reloaded["savings_added"]
            + reloaded["snowball_payment"]
            + reloaded["checking_remaining"]
        )
        assert money(reloaded["income"] - allocated) == Decimal("0.00")
        assert reloaded["income"] == original.income
        assert reloaded["checking_remaining"] == original.remaining_cash
