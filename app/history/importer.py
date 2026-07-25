"""History JSON import workflow and validation."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from app.history.repository import HistoryRepository
from app.money import to_cents
from app.models import ActualEntryType, DataWarningSeverity, Plan


class PlanHistoryImporter:
    """Validate and import portable plan history exports."""

    def __init__(
        self,
        service: Any,
        repository: HistoryRepository,
        *,
        export_format_version: int,
        latest_schema_version: int,
        utc_timestamp,
        canonical_json,
        fingerprint,
        portable_content_fingerprint,
    ) -> None:
        self.service = service
        self.repository = repository
        self.export_format_version = export_format_version
        self.latest_schema_version = latest_schema_version
        self.utc_timestamp = utc_timestamp
        self.canonical_json = canonical_json
        self.fingerprint = fingerprint
        self.portable_content_fingerprint = portable_content_fingerprint

    def import_plan_json(self, path: str | Path, *, new_name: str | None = None) -> Plan:
        """Import a portable local JSON plan export transactionally."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format_version") != self.export_format_version:
            raise ValueError("unsupported plan export format version.")
        if "plan" not in payload or "versions" not in payload:
            raise ValueError("plan export is missing required fields.")
        self.validate_import_payload(payload)

        plan_data = payload["plan"]
        versions = payload["versions"]
        plan_name = new_name or plan_data["name"]
        import_hash = self.portable_content_fingerprint(payload)
        if self.repository.import_fingerprint_exists(import_hash):
            raise ValueError("this plan export has already been imported.")
        if self.repository.plan_name_exists(plan_name):
            raise ValueError("a nonarchived plan with this name already exists.")
        if not versions:
            raise ValueError("plan export does not contain any versions.")

        with self.repository.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO plans (name, description, created_at, updated_at,
                                   archived, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_name,
                    plan_data.get("description", ""),
                    self.utc_timestamp(),
                    self.utc_timestamp(),
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
                snapshot_json = self.canonical_json(snapshot)
                version_id = self.service._insert_plan_version(
                    conn,
                    plan_id=plan_id,
                    version_number=int(version["version_number"]),
                    config_snapshot=snapshot_json,
                    config_fingerprint=self.fingerprint(snapshot),
                    change_note=version.get("change_note", "Imported"),
                    source="import",
                    active=bool(version.get("active", False)),
                )
                version_id_map[int(version["id"])] = version_id
                if int(version["id"]) == int(active_version["id"]):
                    latest_version_id = version_id
            snapshot_id_map = self.import_forecast_snapshots(
                conn,
                payload.get("forecast_snapshots", []),
                version_id_map,
            )
            period_id_map = self.import_forecast_periods(
                conn,
                payload.get("forecast_periods", []),
                snapshot_id_map,
            )
            self.import_debt_snapshots(
                conn,
                payload.get("debt_snapshots", []),
                period_id_map,
            )
            self.import_savings_snapshots(
                conn,
                payload.get("savings_snapshots", []),
                period_id_map,
            )
            self.import_warnings(
                conn,
                payload.get("warnings", []),
                snapshot_id_map,
            )
            self.import_actual_transactions(
                conn,
                payload.get("actual_entries", []),
                plan_id,
                period_id_map,
            )
            self.import_balance_observations(
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
                (plan_id, import_hash, self.utc_timestamp()),
            )
        return self.service.get_plan(plan_id)

    def import_forecast_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        version_id_map: dict[int, int],
    ) -> dict[int, int]:
        """Import forecast snapshot rows and return old-to-new IDs."""
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

    def import_forecast_periods(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        snapshot_id_map: dict[int, int],
    ) -> dict[int, int]:
        """Import forecast period rows and return old-to-new IDs."""
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

    def import_debt_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        period_id_map: dict[int, int],
    ) -> None:
        """Import debt snapshot rows."""
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

    def import_savings_snapshots(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        period_id_map: dict[int, int],
    ) -> None:
        """Import savings snapshot rows."""
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
                    self.optional_import_cents(row.get("goal_target")),
                    row.get("goal_deadline"),
                    to_cents(row["projected_shortfall"]),
                    row["goal_feasible_status"],
                    to_cents(row.get("total_deposit", row["normal_contribution"])),
                    self.optional_import_cents(row.get("amount_required_before")),
                    self.optional_import_cents(row.get("amount_required_after")),
                    self.optional_import_cents(row.get("projected_deadline_balance")),
                    row.get("withdrawal_date"),
                    self.optional_import_cents(row.get("withdrawal_amount")),
                    row.get("post_withdrawal_allocation_state", ""),
                    row.get("reason_codes", ""),
                    row.get("explanation", ""),
                ),
            )

    def import_warnings(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        snapshot_id_map: dict[int, int],
    ) -> None:
        """Import data quality warnings."""
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

    def import_actual_transactions(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        plan_id: int,
        period_id_map: dict[int, int],
    ) -> None:
        """Import actual transactions and correction links."""
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
                    row.get("created_at", self.utc_timestamp()),
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

    def import_balance_observations(
        self,
        conn: sqlite3.Connection,
        rows: list[dict[str, Any]],
        plan_id: int,
        period_id_map: dict[int, int],
    ) -> None:
        """Import balance observations."""
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
                    row.get("created_at", self.utc_timestamp()),
                    None if old_period_id is None else period_id_map[int(old_period_id)],
                    row.get("match_method", "unmatched"),
                ),
            )

    @staticmethod
    def optional_import_cents(value: Any) -> int | None:
        """Return nullable integer cents for imported money fields."""
        if value is None:
            return None
        return to_cents(value)

    @staticmethod
    def without_keys(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
        """Return a row without unstable keys."""
        return {key: value for key, value in row.items() if key not in keys}

    def snapshot_history_fingerprint(
        self,
        payload: dict[str, Any],
        snapshot_id: int,
    ) -> str:
        """Recompute one forecast snapshot history fingerprint."""
        periods = [
            row
            for row in payload.get("forecast_periods", [])
            if int(row["forecast_snapshot_id"]) == snapshot_id
        ]
        period_ids = {int(row["id"]) for row in periods}
        return self.fingerprint(
            {
                "snapshot": self.without_keys(
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

    def validate_import_payload(self, payload: dict[str, Any]) -> None:
        """Validate a portable import payload before persisting anything."""
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
            raise ValueError(
                f"plan export is missing required section(s): {', '.join(sorted(missing))}."
            )
        if payload.get("source_schema_version", 0) > self.latest_schema_version:
            raise ValueError("plan export uses an unsupported future schema version.")
        expected_portable = payload.get("portable_content_fingerprint")
        if expected_portable and expected_portable != self.portable_content_fingerprint(payload):
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
            if (
                self.fingerprint(json.loads(version["config_snapshot"]))
                != version["config_fingerprint"]
            ):
                raise ValueError("plan export has a configuration fingerprint mismatch.")
        if len(active_versions) != 1:
            raise ValueError("plan export must contain exactly one active version.")
        if int(payload["plan"].get("current_version_id")) != int(active_versions[0]["id"]):
            raise ValueError("plan export current version does not match active version.")
        self.validate_import_relationships_and_totals(payload, version_ids)

    def validate_import_relationships_and_totals(
        self,
        payload: dict[str, Any],
        version_ids: set[int],
    ) -> None:
        """Validate imported relationships and aggregate totals."""
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
            self.validate_period_reconciliation(row)

        debts_by_period: dict[int, list[dict[str, Any]]] = {period_id: [] for period_id in period_ids}
        for row in debts:
            period_id = int(row["forecast_period_id"])
            if period_id not in period_ids:
                raise ValueError("debt_snapshots contain an orphan forecast_period_id.")
            debts_by_period[period_id].append(row)
            self.validate_debt_row(row)
        for rows in debts_by_period.values():
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
            self.validate_savings_row(row)
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
            self.validate_snapshot_totals(
                snapshot,
                periods_by_snapshot[int(snapshot["id"])],
                debts_by_period,
                savings_by_period,
                warnings_by_snapshot[int(snapshot["id"])],
                payload,
            )
        self.validate_import_actuals(payload, period_ids)

    @staticmethod
    def validate_period_reconciliation(row: dict[str, Any]) -> None:
        """Validate imported forecast-period cash reconciliation."""
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

    @staticmethod
    def validate_debt_row(row: dict[str, Any]) -> None:
        """Validate imported debt snapshot arithmetic."""
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

    @staticmethod
    def validate_savings_row(row: dict[str, Any]) -> None:
        """Validate imported savings snapshot arithmetic."""
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

    def validate_snapshot_totals(
        self,
        snapshot: dict[str, Any],
        periods: list[dict[str, Any]],
        debts_by_period: dict[int, list[dict[str, Any]]],
        savings_by_period: dict[int, dict[str, Any]],
        warnings: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """Validate imported forecast snapshot aggregate totals."""
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
            starting_savings = to_cents(
                savings_by_period[first_period_id]["starting_savings"]
            )
            ending_savings = to_cents(
                savings_by_period[last_period_id]["ending_savings"]
            )
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
        if snapshot.get("history_fingerprint") != self.snapshot_history_fingerprint(
            payload,
            snapshot_id,
        ):
            raise ValueError("forecast_snapshots history_fingerprint mismatch.")

    @staticmethod
    def validate_import_actuals(
        payload: dict[str, Any],
        period_ids: set[int],
    ) -> None:
        """Validate actual-entry import relationships."""
        actual_ids = set()
        actual_types = {}
        actual_sources = {}
        actual_amounts = {}
        correction_targets = {}
        for row in payload.get("actual_entries", []):
            row_id = int(row["id"])
            if row_id in actual_ids:
                raise ValueError("actual_entries contain duplicate identifiers.")
            actual_ids.add(row_id)
            actual_types[row_id] = ActualEntryType(row["entry_type"])
            actual_sources[row_id] = str(row.get("source", "import"))
            actual_amounts[row_id] = to_cents(row["amount"])
            date.fromisoformat(row["entry_date"])
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
                source = actual_sources[int(row["id"])]
                if source not in {"correction", "correction_replacement"}:
                    raise ValueError("actual_entries contain an invalid correction source.")
                correction_targets[int(row["id"])] = corrected_id
            elif actual_sources[int(row["id"])] in {
                "correction",
                "correction_replacement",
            }:
                raise ValueError("actual_entries contain an unlinked correction row.")
        PlanHistoryImporter._validate_correction_relationships(
            actual_sources,
            actual_amounts,
            correction_targets,
        )
        for row in payload.get("balance_observations", []):
            ActualEntryType(row["observation_type"])
            date.fromisoformat(row["observation_date"])
            to_cents(row["balance"])
            period_id = row.get("forecast_period_id")
            if period_id is not None and int(period_id) not in period_ids:
                raise ValueError("balance_observations contain an orphan forecast_period_id.")

    @staticmethod
    def _validate_correction_relationships(
        actual_sources: dict[int, str],
        actual_amounts: dict[int, int],
        correction_targets: dict[int, int],
    ) -> None:
        """Validate reversal pairs and acyclic correction replacement chains."""
        children_by_target: dict[int, list[int]] = {}
        for child_id, target_id in correction_targets.items():
            children_by_target.setdefault(target_id, []).append(child_id)

        for target_id, child_ids in children_by_target.items():
            if actual_sources[target_id] == "correction":
                raise ValueError("actual_entries cannot correct a reversal row.")
            child_sources = [actual_sources[child_id] for child_id in child_ids]
            if child_sources.count("correction") != 1:
                raise ValueError(
                    "actual_entries correction target must have one reversal."
                )
            if child_sources.count("correction_replacement") > 1 or len(child_ids) > 2:
                raise ValueError("actual_entries contain duplicate correction children.")
            if (
                "correction_replacement" in child_sources
                and len(child_sources) != 2
            ):
                raise ValueError(
                    "actual_entries correction replacement requires a reversal."
                )
            reversal_id = next(
                child_id
                for child_id in child_ids
                if actual_sources[child_id] == "correction"
            )
            if actual_amounts[reversal_id] != -actual_amounts[target_id]:
                raise ValueError(
                    "actual_entries correction reversal amount mismatch."
                )

        for child_id in correction_targets:
            visited = set()
            current = child_id
            while current in correction_targets:
                if current in visited:
                    raise ValueError("actual_entries contain a correction cycle.")
                visited.add(current)
                current = correction_targets[current]
