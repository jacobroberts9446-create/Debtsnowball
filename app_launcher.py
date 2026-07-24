"""Packaged executable entry point for DebtSnowball."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable

from app.version import APP_VERSION
from run import run_main_menu


def main(
    menu_runner: Callable[[], None] = run_main_menu,
    *,
    input_func: Callable[[str], str] = input,
    error_func: Callable[[str], None] | None = None,
    pause_on_error: bool | None = None,
) -> int:
    """Launch the interactive menu for packaged Windows users."""
    error_func = error_func or (lambda message: print(message, file=sys.stderr))
    try:
        menu_runner()
    except KeyboardInterrupt:
        error_func("")
        error_func("DebtSnowball closed.")
        return 130
    except Exception:
        error_func(f"DebtSnowball v{APP_VERSION} could not start.")
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
