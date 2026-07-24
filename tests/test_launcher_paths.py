"""Tests for packaged launcher and path resolution."""

from __future__ import annotations

import sys

import app_launcher
from app import paths
from app.config import Config
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
    assert paths.default_workbook_path() == paths.project_root() / "output" / "debtsnowball_plan.xlsx"


def test_packaged_paths_use_local_app_data(monkeypatch, tmp_path) -> None:
    """Packaged writable paths use the local app-data directory."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DebtSnowball.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("DEBTSNOWBALL_DATA_DIR", raising=False)

    root = tmp_path / "LocalAppData" / "DebtSnowball"
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
    monkeypatch.setenv("DEBTSNOWBALL_DATA_DIR", str(user_root))

    assert paths.default_config_path() == bundled / "config.json"

    user_config = user_root / "config.json"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("{}", encoding="utf-8")

    assert paths.default_config_path() == user_config


def test_default_objects_use_centralized_paths(monkeypatch, tmp_path) -> None:
    """Default persistence/output objects use app path helpers."""
    monkeypatch.setenv("DEBTSNOWBALL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert Config().load
    assert Database().path == tmp_path / "output" / "debtsnowball.sqlite"
    assert PlanHistoryService().database.path == tmp_path / "output" / "debtsnowball.sqlite"
    assert ExcelWriter().path == tmp_path / "output" / "debtsnowball_plan.xlsx"
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
    assert "DebtSnowball closed." in output


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
    assert f"DebtSnowball v{APP_VERSION} could not start." in text
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
