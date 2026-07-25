"""History JSON and CSV export workflows."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.database import LATEST_SCHEMA_VERSION
from app.history.repository import HistoryRepository
from app.money import from_cents
from app.serialization import dumps_json


class PlanHistoryExporter:
    """Build portable history export files for a plan history service."""

    def __init__(
        self,
        service: Any,
        repository: HistoryRepository,
        *,
        application_version: str,
        forecast_engine_version: str,
        export_format_version: int,
        utc_timestamp,
        canonical_json,
        portable_content_fingerprint,
        portable_id,
        fingerprint,
    ) -> None:
        self.service = service
        self.repository = repository
        self.application_version = application_version
        self.forecast_engine_version = forecast_engine_version
        self.export_format_version = export_format_version
        self.utc_timestamp = utc_timestamp
        self.canonical_json = canonical_json
        self.portable_content_fingerprint = portable_content_fingerprint
        self.portable_id = portable_id
        self.fingerprint = fingerprint

    def export_plan_json(self, plan_id: int, path: str | Path) -> Path:
        """Export a saved plan to portable local JSON."""
        payload = self.build_export_payload(plan_id)
        output = Path(path)
        output.write_text(self.canonical_json(payload), encoding="utf-8")
        return output

    def build_export_payload(self, plan_id: int) -> dict[str, Any]:
        """Build the JSON-safe portable plan export payload."""
        plan = self.service.get_plan(plan_id)
        versions = self.service.list_plan_versions(plan_id)
        payload = {
            "format_version": self.export_format_version,
            "application_version": self.application_version,
            "forecast_engine_version": self.forecast_engine_version,
            "source_schema_version": LATEST_SCHEMA_VERSION,
            "exported_at": self.utc_timestamp(),
            "portable_id": self.portable_id("plan", plan.id, plan.name),
            "plan": asdict(plan),
            "versions": [asdict(version) for version in versions],
            "forecast_snapshots": self.export_forecast_snapshots(plan_id),
            "forecast_periods": self.export_forecast_periods(plan_id),
            "debt_snapshots": self.export_debt_snapshots(plan_id),
            "savings_snapshots": self.export_savings_snapshots(plan_id),
            "warnings": self.export_warnings(plan_id),
            "actual_entries": self.export_actual_transactions(plan_id),
            "balance_observations": self.export_balance_observations(plan_id),
        }
        self.attach_snapshot_history_fingerprints(payload)
        payload["portable_content_fingerprint"] = self.portable_content_fingerprint(
            payload
        )
        return payload

    def export_forecast_periods_csv(self, snapshot_id: int, path: str | Path) -> Path:
        """Export forecast periods as a flat local CSV."""
        rows = self.service._forecast_period_rows(snapshot_id)
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
        return {
            "plan_versions": self.write_csv(
                output / "plan_versions.csv",
                [asdict(version) for version in self.service.list_plan_versions(plan_id)],
            ),
            "forecast_periods": self.write_csv(
                output / "forecast_periods.csv",
                self.export_forecast_periods(plan_id),
            ),
            "debt_history": self.write_csv(
                output / "debt_history.csv",
                self.export_debt_snapshots(plan_id),
            ),
            "savings_history": self.write_csv(
                output / "savings_history.csv",
                self.export_savings_snapshots(plan_id),
            ),
            "actual_transactions": self.write_csv(
                output / "actual_transactions.csv",
                self.export_actual_transactions(plan_id),
            ),
            "forecast_vs_actual": self.write_csv(
                output / "forecast_vs_actual.csv",
                [
                    self.period_comparison_row(row)
                    for row in self.service.compare_forecast_to_actual_periods(plan_id)
                ],
            ),
            "warnings": self.write_csv(
                output / "warnings.csv",
                self.export_warnings(plan_id),
            ),
        }

    def history_report_rows(self, plan_id: int) -> dict[str, list[dict[str, Any]]]:
        """Return detailed rows for optional history workbooks."""
        return {
            "forecast_snapshots": self.export_forecast_snapshots(plan_id),
            "period_comparisons": [
                self.period_comparison_row(row)
                for row in self.service.compare_forecast_to_actual_periods(plan_id)
            ],
            "debt_history": self.export_debt_snapshots(plan_id),
            "savings_history": self.export_savings_snapshots(plan_id),
            "warnings": self.export_warnings(plan_id),
        }

    def export_forecast_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready forecast snapshot rows."""
        return self.repository.export_forecast_snapshots(plan_id)

    def export_forecast_periods(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready forecast period rows."""
        return self.repository.export_child_rows(plan_id, "forecast_periods")

    def export_debt_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready debt snapshot rows."""
        return self.repository.export_child_rows(plan_id, "debt_snapshots")

    def export_savings_snapshots(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready savings snapshot rows."""
        return self.repository.export_child_rows(plan_id, "savings_snapshots")

    def export_warnings(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready warning rows."""
        return self.repository.export_child_rows(plan_id, "data_quality_warnings")

    def export_actual_transactions(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready actual transaction rows."""
        return self.repository.export_actual_transactions(plan_id)

    def export_balance_observations(self, plan_id: int) -> list[dict[str, Any]]:
        """Return export-ready balance observation rows."""
        return self.repository.export_balance_observations(plan_id)

    def attach_snapshot_history_fingerprints(self, payload: dict[str, Any]) -> None:
        """Attach stable fingerprints for snapshot history sections."""
        for snapshot in payload["forecast_snapshots"]:
            snapshot["history_fingerprint"] = self.snapshot_history_fingerprint(
                payload,
                int(snapshot["id"]),
            )

    def snapshot_history_fingerprint(
        self,
        payload: dict[str, Any],
        snapshot_id: int,
    ) -> str:
        """Fingerprint one forecast snapshot and its child history rows."""
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

    @staticmethod
    def without_keys(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
        """Return a row without unstable keys."""
        return {key: value for key, value in row.items() if key not in keys}

    @staticmethod
    def period_comparison_row(row: Any) -> dict[str, Any]:
        """Flatten nested debt comparisons for tabular history exports."""
        output = asdict(row)
        output["debt_balance_comparisons"] = dumps_json(
            output["debt_balance_comparisons"],
            sort_keys=True,
        )
        return output

    @staticmethod
    def write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
        """Write dictionaries to a simple CSV file."""
        fieldnames = sorted({key for row in rows for key in row}) or ["empty"]
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically for exported files."""
    return dumps_json(value, sort_keys=True, separators=(",", ":"))
