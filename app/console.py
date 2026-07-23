"""Console formatting and optional ANSI styling helpers."""

import os
import sys
from collections.abc import Callable, Mapping
from typing import TextIO

BANNER_WIDTH = 60
ANSI_RESET = "\033[0m"
ANSI_STYLES = {
    "success": "\033[32m",
    "warning": "\033[33m",
    "error": "\033[31m",
    "heading": "\033[96m",
    "muted": "\033[2m",
}
OutputFunc = Callable[[str], None]


def ansi_color_enabled(
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether ANSI styling should be used for console output."""
    stream = stream or sys.stdout
    environ = environ or os.environ

    if "NO_COLOR" in environ:
        return False

    override = environ.get("DEBTSNOWBALL_COLOR")
    if override == "1":
        return True
    if override == "0":
        return False

    return bool(stream.isatty() and terminal_supports_ansi(environ))


def terminal_supports_ansi(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the current terminal appears to support ANSI escapes."""
    environ = environ or os.environ
    term = environ.get("TERM", "")
    if term.casefold() == "dumb":
        return False
    if os.name != "nt":
        return bool(term)

    return bool(
        environ.get("WT_SESSION")
        or environ.get("ANSICON")
        or environ.get("ConEmuANSI") == "ON"
        or "xterm" in term.casefold()
        or "ansi" in term.casefold()
        or "vt100" in term.casefold()
    )


def style_text(
    text: str,
    style: str,
    *,
    enable_color: bool | None = None,
) -> str:
    """Apply one centralized ANSI style while preserving plain text when disabled."""
    if enable_color is None:
        enable_color = ansi_color_enabled()
    if not enable_color:
        return text

    prefix = ANSI_STYLES[style]
    return f"{prefix}{text}{ANSI_RESET}"


def success_text(text: str, *, enable_color: bool | None = None) -> str:
    """Return success-styled text when color is enabled."""
    return style_text(text, "success", enable_color=enable_color)


def warning_text(text: str, *, enable_color: bool | None = None) -> str:
    """Return warning-styled text when color is enabled."""
    return style_text(text, "warning", enable_color=enable_color)


def error_text(text: str, *, enable_color: bool | None = None) -> str:
    """Return error-styled text when color is enabled."""
    return style_text(text, "error", enable_color=enable_color)


def heading_text(text: str, *, enable_color: bool | None = None) -> str:
    """Return heading-styled text when color is enabled."""
    return style_text(text, "heading", enable_color=enable_color)


def muted_text(text: str, *, enable_color: bool | None = None) -> str:
    """Return muted supporting text when color is enabled."""
    return style_text(text, "muted", enable_color=enable_color)


def print_application_banner(version: str, output_func: OutputFunc = print) -> None:
    """Print the standard DebtSnowball application banner."""
    output_func(heading_text("=" * BANNER_WIDTH))
    output_func(heading_text(f"DebtSnowball v{version}"))
    output_func(muted_text("Personal Debt Planning"))
    output_func(heading_text("=" * BANNER_WIDTH))


def print_section_header(title: str, output_func: OutputFunc = print) -> None:
    """Print a consistent plain-text section header."""
    output_func("")
    output_func(heading_text(title))
    output_func(heading_text("-" * len(title)))


def print_menu_title(title: str, output_func: OutputFunc = print) -> None:
    """Print a reusable menu title."""
    output_func("")
    output_func(heading_text(title))
    output_func(heading_text("-" * len(title)))
    output_func("")


def print_success(message: str, output_func: OutputFunc = print) -> None:
    """Print a consistently formatted success message."""
    output_func(success_text(f"Success: {message}"))


def print_warning(message: str, output_func: OutputFunc = print) -> None:
    """Print a consistently formatted warning message."""
    output_func(warning_text(f"Warning: {message}"))


def print_error(message: str, output_func: OutputFunc = print) -> None:
    """Print a consistently formatted error message."""
    output_func(error_text(f"Error: {message}"))


def print_table(
    headers: list[str],
    rows: list[list[str]],
    output_func: OutputFunc = print,
) -> None:
    """Print a simple aligned plain-text table."""
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    output_func(
        " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    )
    output_func("-+-".join("-" * width for width in widths))
    for row in rows:
        output_func(
            " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )
