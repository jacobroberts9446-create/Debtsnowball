"""Non-destructive migration of packaged DebtSnowball data to DebtPilot."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from app.paths import (
    DATA_DIR_ENV,
    LEGACY_DATA_DIR_ENV,
    is_packaged,
    legacy_packaged_user_data_dir,
    packaged_user_data_dir,
)


class DataMigrationStatus(str, Enum):
    """Possible outcomes of a legacy local-data migration check."""

    MIGRATED = "migrated"
    DESTINATION_EXISTS = "destination_exists"
    LEGACY_DATA_NOT_FOUND = "legacy_data_not_found"
    NOT_APPLICABLE = "not_applicable"
    OVERRIDE_CONFIGURED = "override_configured"


@dataclass(frozen=True)
class DataMigrationResult:
    """Outcome and paths associated with one migration check."""

    status: DataMigrationStatus
    source: Path | None = None
    destination: Path | None = None

    @property
    def migrated(self) -> bool:
        """Return whether legacy data was copied successfully."""
        return self.status is DataMigrationStatus.MIGRATED


class DataMigrationError(RuntimeError):
    """Raised when legacy local data cannot be migrated safely."""


def migrate_legacy_user_data(
    *,
    legacy_dir: str | Path | None = None,
    destination_dir: str | Path | None = None,
) -> DataMigrationResult:
    """Copy legacy packaged data into the new location without overwriting.

    The source remains untouched. A temporary sibling receives the full copy
    before an atomic rename publishes it as the DebtPilot data directory.
    """
    explicit_paths = legacy_dir is not None or destination_dir is not None
    if explicit_paths and (legacy_dir is None or destination_dir is None):
        raise ValueError("legacy_dir and destination_dir must be provided together.")

    if not explicit_paths:
        if os.environ.get(DATA_DIR_ENV) or os.environ.get(LEGACY_DATA_DIR_ENV):
            return DataMigrationResult(DataMigrationStatus.OVERRIDE_CONFIGURED)
        if not is_packaged():
            return DataMigrationResult(DataMigrationStatus.NOT_APPLICABLE)
        source = legacy_packaged_user_data_dir()
        destination = packaged_user_data_dir()
    else:
        source = Path(legacy_dir)
        destination = Path(destination_dir)

    if destination.exists():
        return DataMigrationResult(
            DataMigrationStatus.DESTINATION_EXISTS,
            source,
            destination,
        )
    if not source.exists():
        return DataMigrationResult(
            DataMigrationStatus.LEGACY_DATA_NOT_FOUND,
            source,
            destination,
        )
    if not source.is_dir():
        raise DataMigrationError(f"Legacy data path is not a directory: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.migration-{uuid4().hex}",
    )
    try:
        shutil.copytree(source, temporary)
        if destination.exists():
            shutil.rmtree(temporary)
            return DataMigrationResult(
                DataMigrationStatus.DESTINATION_EXISTS,
                source,
                destination,
            )
        temporary.rename(destination)
    except (OSError, shutil.Error) as exc:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise DataMigrationError(
            f"Could not migrate existing data from {source} to {destination}: {exc}",
        ) from exc

    return DataMigrationResult(DataMigrationStatus.MIGRATED, source, destination)
