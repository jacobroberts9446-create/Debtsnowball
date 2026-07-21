"""
database.py

SQLite persistence for generated DebtSnowball plans.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.money import excel_number


class Database:
    """Persist generated budget plans to SQLite."""

    def __init__(self, filename: str | Path = "output/debtsnowball.sqlite") -> None:
        self.path = Path(filename)

    def initialize(self) -> None:
        """Create the database schema if it does not already exist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paychecks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pay_date TEXT NOT NULL UNIQUE,
                        income REAL NOT NULL,
                        bills_paid REAL NOT NULL,
                        debt_minimums REAL NOT NULL,
                        snowball_payment REAL NOT NULL,
                        savings_added REAL NOT NULL,
                        checking_remaining REAL NOT NULL,
                        notes TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS debts (
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

    def save_plan(self, paychecks: list[Any], debts: list[dict[str, Any]]) -> None:
        """Replace stored paycheck and debt rows with the generated plan."""
        self.initialize()

        with closing(self._connect()) as conn:
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
                            self._storage_number(paycheck.income),
                            self._storage_number(paycheck.bills_paid),
                            self._storage_number(paycheck.debt_minimums),
                            self._storage_number(paycheck.snowball_payment),
                            self._storage_number(paycheck.savings_added),
                            self._storage_number(paycheck.checking_remaining),
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
                            self._storage_number(debt["balance"]),
                            str(debt["apr"]),
                            self._storage_number(debt["minimum"]),
                            self._storage_number(debt["total_paid"]),
                            self._storage_number(debt["total_interest_paid"]),
                            debt["status"],
                        )
                        for debt in debts
                    ],
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _storage_number(self, value: object) -> float:
        """Convert money to the existing SQLite REAL storage boundary."""
        return excel_number(value)
