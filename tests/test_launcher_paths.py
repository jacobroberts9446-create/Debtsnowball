"""Tests for packaged launcher and path resolution."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import app_launcher
from app import paths
from app.config import Config
from app.data_migration import (
    DataMigrationError,
    DataMigrationResult,
    DataMigrationStatus,
    migrate_legacy_user_data,
)
from app.database import Database
from app.excel_writer import ExcelWriter
from app.history import PlanHistoryService
from app.preferences import RecentPlanPreferences
from app.version import APP_VERSION


def clear_packaged_state(monkeypatch) -> None:
    """Remove PyInstaller runtime markers for path tests."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def test_source_paths_do_not_depend_on_current_working_directory(monkeypatch, tmp_path) -> None:
    """Source defaults resolve from the project root, not cwd."""
    clear_packaged_state(monkeypatch)
    monkeypatch.chdir(tmp_path)

    assert paths.default_config_path() == paths.project_root() / "config.json"
    assert paths.default_database_path() == paths.project_root() / "output" / "debtsnowball.sqlite"
    assert paths.default_workbook_path() == paths.project_root() / "output" / "debtpilot_plan.xlsx"


def test_packaged_paths_use_local_app_data(monkeypatch, tmp_path) -> None:
    """Packaged writable paths use the local app-data directory."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DebtPilot.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("DEBTPILOT_DATA_DIR", raising=False)
    monkeypatch.delenv("DEBTSNOWBALL_DATA_DIR", raising=False)

    root = tmp_path / "LocalAppData" / "DebtPilot"
    assert paths.user_data_dir() == root
    assert paths.default_database_path() == root / "output" / "debtsnowball.sqlite"
    assert paths.default_preferences_path() == root / "output" / "preferences.json"


def test_packaged_config_prefers_user_copy_then_bundled(monkeypatch, tmp_path) -> None:
    """Packaged config reads user config when present, otherwise bundled config."""
    user_root = tmp_path / "data"
    bundled = tmp_path / "bundle"
    bundled.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)
    monkeypatch.setenv("DEBTPILOT_DATA_DIR", str(user_root))

    assert paths.default_config_path() == bundled / "config.json"

    user_config = user_root / "config.json"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("{}", encoding="utf-8")

    assert paths.default_config_path() == user_config


def test_default_objects_use_centralized_paths(monkeypatch, tmp_path) -> None:
    """Default persistence/output objects use app path helpers."""
    monkeypatch.setenv("DEBTPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert Config().load
    assert Database().path == tmp_path / "output" / "debtsnowball.sqlite"
    assert PlanHistoryService().database.path == tmp_path / "output" / "debtsnowball.sqlite"
    assert ExcelWriter().path == tmp_path / "output" / "debtpilot_plan.xlsx"
    assert RecentPlanPreferences().path == tmp_path / "output" / "preferences.json"


def test_launcher_dispatches_to_existing_interactive_workflow() -> None:
    """The executable entry point delegates to the existing menu runner."""
    calls = []

    assert app_launcher.main(menu_runner=lambda: calls.append("menu")) == 0
    assert calls == ["menu"]


def test_launcher_handles_ctrl_c_cleanly() -> None:
    """Ctrl+C exits cleanly without a fatal traceback."""
    output = []

    result = app_launcher.main(
        menu_runner=lambda: (_ for _ in ()).throw(KeyboardInterrupt),
        error_func=output.append,
    )

    assert result == 130
    assert "DebtPilot closed." in output


def test_launcher_reports_fatal_startup_error_and_pauses() -> None:
    """Unexpected startup failures are readable and can pause before closing."""
    output = []
    prompts = []

    result = app_launcher.main(
        menu_runner=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        error_func=output.append,
        input_func=lambda prompt: prompts.append(prompt) or "",
        pause_on_error=True,
    )

    text = "\n".join(output)
    assert result == 1
    assert f"DebtPilot v{APP_VERSION} could not start." in text
    assert "RuntimeError: boom" in text
    assert prompts == ["Press Enter to close..."]


def test_launcher_does_not_pause_after_normal_exit() -> None:
    """Normal exits return immediately."""
    prompts = []

    result = app_launcher.main(
        menu_runner=lambda: None,
        input_func=lambda prompt: prompts.append(prompt) or "",
        pause_on_error=True,
    )

    assert result == 0
    assert prompts == []


def test_legacy_data_dir_override_remains_supported(monkeypatch, tmp_path) -> None:
    """The former data-directory environment override remains compatible."""
    monkeypatch.setenv("DEBTSNOWBALL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DEBTPILOT_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert paths.user_data_dir() == tmp_path


def test_new_data_dir_override_takes_precedence(monkeypatch, tmp_path) -> None:
    """The DebtPilot override wins when both environment variables are present."""
    new_root = tmp_path / "new"
    legacy_root = tmp_path / "legacy"
    monkeypatch.setenv("DEBTPILOT_DATA_DIR", str(new_root))
    monkeypatch.setenv("DEBTSNOWBALL_DATA_DIR", str(legacy_root))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert paths.user_data_dir() == new_root


def test_legacy_data_migration_preserves_complete_tree_and_source(tmp_path) -> None:
    """Migration copies every file and leaves the legacy directory untouched."""
    legacy = tmp_path / "DebtSnowball"
    destination = tmp_path / "DebtPilot"
    files = {
        "config.json": b'{"sample": true}',
        "output/debtsnowball.sqlite": b"database",
        "output/preferences.json": b'{"recent_plans": []}',
        "output/debtsnowball_plan.xlsx": b"workbook",
        "history/plan.json": b"history",
    }
    for relative_path, content in files.items():
        path = legacy / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    result = migrate_legacy_user_data(
        legacy_dir=legacy,
        destination_dir=destination,
    )

    assert result.status is DataMigrationStatus.MIGRATED
    assert result.source == legacy
    assert result.destination == destination
    for relative_path, content in files.items():
        assert (legacy / relative_path).read_bytes() == content
        assert (destination / relative_path).read_bytes() == content


def test_legacy_data_migration_is_idempotent_and_does_not_overwrite(tmp_path) -> None:
    """A populated DebtPilot directory prevents duplicate or destructive migration."""
    legacy = tmp_path / "DebtSnowball"
    destination = tmp_path / "DebtPilot"
    legacy.mkdir()
    destination.mkdir()
    (legacy / "plans.db").write_bytes(b"legacy")
    existing = destination / "plans.db"
    existing.write_bytes(b"current")

    first = migrate_legacy_user_data(
        legacy_dir=legacy,
        destination_dir=destination,
    )
    second = migrate_legacy_user_data(
        legacy_dir=legacy,
        destination_dir=destination,
    )

    assert first.status is DataMigrationStatus.DESTINATION_EXISTS
    assert second.status is DataMigrationStatus.DESTINATION_EXISTS
    assert existing.read_bytes() == b"current"
    assert (legacy / "plans.db").read_bytes() == b"legacy"
    assert not list(tmp_path.glob(".DebtPilot.migration-*"))


def test_legacy_data_migration_skips_when_source_is_absent(tmp_path) -> None:
    """A clean installation does not create an empty migration destination."""
    destination = tmp_path / "DebtPilot"

    result = migrate_legacy_user_data(
        legacy_dir=tmp_path / "DebtSnowball",
        destination_dir=destination,
    )

    assert result.status is DataMigrationStatus.LEGACY_DATA_NOT_FOUND
    assert not destination.exists()


def test_legacy_data_migration_requires_both_explicit_paths(tmp_path) -> None:
    """Callers cannot accidentally mix an explicit path with runtime defaults."""
    try:
        migrate_legacy_user_data(legacy_dir=tmp_path / "DebtSnowball")
    except ValueError as exc:
        assert "must be provided together" in str(exc)
    else:
        raise AssertionError("incomplete migration paths should fail")


def test_legacy_data_migration_rejects_non_directory_source(tmp_path) -> None:
    """A malformed legacy path fails without creating a destination."""
    legacy = tmp_path / "DebtSnowball"
    legacy.write_bytes(b"not a directory")
    destination = tmp_path / "DebtPilot"

    try:
        migrate_legacy_user_data(
            legacy_dir=legacy,
            destination_dir=destination,
        )
    except DataMigrationError as exc:
        assert "not a directory" in str(exc)
    else:
        raise AssertionError("a non-directory source should fail")

    assert legacy.read_bytes() == b"not a directory"
    assert not destination.exists()


def test_legacy_data_migration_skips_with_explicit_runtime_override(
    monkeypatch,
    tmp_path,
) -> None:
    """An explicit data path prevents implicit packaged-directory migration."""
    monkeypatch.setenv("DEBTPILOT_DATA_DIR", str(tmp_path / "custom"))

    result = migrate_legacy_user_data()

    assert result.status is DataMigrationStatus.OVERRIDE_CONFIGURED


def test_legacy_data_migration_does_not_overwrite_destination_created_during_copy(
    monkeypatch,
    tmp_path,
) -> None:
    """A concurrently created destination wins without being overwritten."""
    legacy = tmp_path / "DebtSnowball"
    destination = tmp_path / "DebtPilot"
    legacy.mkdir()
    (legacy / "plans.db").write_bytes(b"legacy")
    original_copytree = shutil.copytree

    def copy_then_create_destination(source: Path, temporary: Path) -> Path:
        copied = original_copytree(source, temporary)
        destination.mkdir()
        (destination / "plans.db").write_bytes(b"current")
        return copied

    monkeypatch.setattr(
        "app.data_migration.shutil.copytree",
        copy_then_create_destination,
    )

    result = migrate_legacy_user_data(
        legacy_dir=legacy,
        destination_dir=destination,
    )

    assert result.status is DataMigrationStatus.DESTINATION_EXISTS
    assert (destination / "plans.db").read_bytes() == b"current"
    assert (legacy / "plans.db").read_bytes() == b"legacy"
    assert not list(tmp_path.glob(".DebtPilot.migration-*"))


def test_failed_legacy_data_migration_leaves_source_and_no_partial_target(
    monkeypatch,
    tmp_path,
) -> None:
    """A copy failure preserves legacy data and removes temporary output."""
    legacy = tmp_path / "DebtSnowball"
    destination = tmp_path / "DebtPilot"
    legacy.mkdir()
    source_file = legacy / "plans.db"
    source_file.write_bytes(b"safe")

    def fail_copytree(_source: Path, temporary: Path) -> None:
        temporary.mkdir()
        (temporary / "partial").write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr("app.data_migration.shutil.copytree", fail_copytree)

    try:
        migrate_legacy_user_data(
            legacy_dir=legacy,
            destination_dir=destination,
        )
    except DataMigrationError as exc:
        assert "disk full" in str(exc)
    else:
        raise AssertionError("migration should fail")

    assert source_file.read_bytes() == b"safe"
    assert not destination.exists()
    assert not list(tmp_path.glob(".DebtPilot.migration-*"))


def test_launcher_reports_successful_data_migration(tmp_path) -> None:
    """The packaged launcher reports a completed legacy-data migration."""
    output = []
    destination = tmp_path / "DebtPilot"
    result = DataMigrationResult(
        DataMigrationStatus.MIGRATED,
        tmp_path / "DebtSnowball",
        destination,
    )

    exit_code = app_launcher.main(
        menu_runner=lambda: output.append("menu"),
        output_func=output.append,
        migration_func=lambda: result,
    )

    assert exit_code == 0
    assert "Success: Existing DebtSnowball data migrated to DebtPilot." in output
    assert f"Location: {destination}" in output
    assert output[-1] == "menu"


def test_packaged_migration_keeps_saved_plans_available(
    monkeypatch,
    tmp_path,
) -> None:
    """A saved plan in the legacy database is available from the new location."""
    local_app_data = tmp_path / "LocalAppData"
    legacy = local_app_data / "DebtSnowball"
    legacy_database = legacy / "output" / "debtsnowball.sqlite"
    service = PlanHistoryService(legacy_database)
    service.create_plan("Migrated Household", Config().load("config.json"))

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DebtPilot.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("DEBTPILOT_DATA_DIR", raising=False)
    monkeypatch.delenv("DEBTSNOWBALL_DATA_DIR", raising=False)
    output = []
    observed_names = []

    exit_code = app_launcher.main(
        menu_runner=lambda: observed_names.extend(
            plan.name for plan in PlanHistoryService().list_plans()
        ),
        output_func=output.append,
    )

    assert exit_code == 0
    assert observed_names == ["Migrated Household"]
    assert "Success: Existing DebtSnowball data migrated to DebtPilot." in output
    assert legacy_database.exists()
    assert (
        local_app_data / "DebtPilot" / "output" / "debtsnowball.sqlite"
    ).exists()
