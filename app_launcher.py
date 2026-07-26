"""Packaged executable entry point for DebtPilot."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable

from app.console import print_success
from app.data_migration import DataMigrationResult, migrate_legacy_user_data
from app.version import APP_NAME, APP_VERSION, LEGACY_APP_NAME
from run import run_main_menu


def main(
    menu_runner: Callable[[], None] = run_main_menu,
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    error_func: Callable[[str], None] | None = None,
    pause_on_error: bool | None = None,
    migration_func: Callable[[], DataMigrationResult] = migrate_legacy_user_data,
) -> int:
    """Launch the interactive menu for packaged Windows users."""
    error_func = error_func or (lambda message: print(message, file=sys.stderr))
    try:
        migration = migration_func()
        if migration.migrated:
            print_success(
                f"Existing {LEGACY_APP_NAME} data migrated to {APP_NAME}.",
                output_func,
            )
            output_func(f"Location: {migration.destination}")
            output_func("")
        menu_runner()
    except KeyboardInterrupt:
        error_func("")
        error_func(f"{APP_NAME} closed.")
        return 130
    except Exception:
        error_func(f"{APP_NAME} v{APP_VERSION} could not start.")
        error_func("Unexpected startup error:")
        error_func(traceback.format_exc().rstrip())
        if pause_on_error is None:
            pause_on_error = bool(sys.stdin and sys.stdin.isatty())
        if pause_on_error:
            try:
                input_func("Press Enter to close...")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
