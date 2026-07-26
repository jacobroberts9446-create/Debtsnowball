"""Path helpers for source and packaged DebtPilot execution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.version import APP_NAME, LEGACY_APP_NAME

APP_DIR_NAME = APP_NAME
LEGACY_APP_DIR_NAME = LEGACY_APP_NAME
DATA_DIR_ENV = "DEBTPILOT_DATA_DIR"
LEGACY_DATA_DIR_ENV = "DEBTSNOWBALL_DATA_DIR"


def is_packaged() -> bool:
    """Return whether the app is running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Return the repository root when running from source."""
    return Path(__file__).resolve().parent.parent


def bundled_resource_dir() -> Path:
    """Return the read-only resource location for source or packaged execution."""
    if is_packaged():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return project_root()


def resource_path(*parts: str) -> Path:
    """Return a bundled read-only resource path."""
    return bundled_resource_dir().joinpath(*parts)


def user_data_dir() -> Path:
    """Return the safe writable application data directory."""
    override = os.environ.get(DATA_DIR_ENV) or os.environ.get(LEGACY_DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if is_packaged():
        return packaged_user_data_dir()
    return project_root()


def packaged_user_data_dir() -> Path:
    """Return the standard writable directory for a packaged DebtPilot app."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME
    return Path.home() / "Documents" / APP_DIR_NAME


def legacy_packaged_user_data_dir() -> Path:
    """Return the former DebtSnowball packaged-data directory."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / LEGACY_APP_DIR_NAME
    return Path.home() / "Documents" / LEGACY_APP_DIR_NAME


def output_dir() -> Path:
    """Return the writable output directory."""
    return user_data_dir() / "output"


def default_config_path() -> Path:
    """Return the default configuration path for the current runtime."""
    if is_packaged():
        user_config = user_data_dir() / "config.json"
        if user_config.exists():
            return user_config
        return resource_path("config.json")
    return resource_path("config.json")


def default_database_path() -> Path:
    """Return the default SQLite database path."""
    return output_dir() / "debtsnowball.sqlite"


def default_workbook_path() -> Path:
    """Return the default generated workbook path."""
    return output_dir() / "debtpilot_plan.xlsx"


def default_preferences_path() -> Path:
    """Return the default interactive preferences path."""
    return output_dir() / "preferences.json"
