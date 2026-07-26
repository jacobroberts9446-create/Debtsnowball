"""Build the per-user Windows installer for DebtPilot."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_SCRIPT = PROJECT_ROOT / "installer" / "DebtPilot.iss"
PORTABLE_EXE = PROJECT_ROOT / "dist" / "DebtPilot" / "DebtPilot.exe"
INSTALLER_OUTPUT = PROJECT_ROOT / "dist" / "installer" / "DebtPilot-Setup-1.2.0.exe"
COMPILER_ENV = "INNO_SETUP_COMPILER"
KNOWN_COMPILERS = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Inno Setup 7"
    / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Inno Setup 6"
    / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    / "Inno Setup 7"
    / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    / "Inno Setup 6"
    / "ISCC.exe",
)


def main(argv: list[str] | None = None) -> int:
    """Build the portable app when needed, then compile its installer."""
    parser = argparse.ArgumentParser(description="Build the DebtPilot Windows installer.")
    parser.add_argument(
        "--skip-app-build",
        action="store_true",
        help="Use the existing dist/DebtPilot portable application.",
    )
    parser.add_argument(
        "--compiler",
        type=Path,
        help=f"Path to ISCC.exe (or set {COMPILER_ENV}).",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("This build script must be run on Windows.", file=sys.stderr)
        return 1

    if not args.skip_app_build:
        run_command([sys.executable, str(PROJECT_ROOT / "scripts" / "build_windows.py")])
    if not PORTABLE_EXE.is_file():
        print(
            f"Portable application not found: {PORTABLE_EXE}",
            file=sys.stderr,
        )
        return 1

    compiler = find_compiler(args.compiler)
    if compiler is None:
        print(
            "Inno Setup compiler not found. Install Inno Setup 6 or 7, "
            f"or set {COMPILER_ENV}.",
            file=sys.stderr,
        )
        return 1

    run_command([str(compiler), str(INSTALLER_SCRIPT)])
    if not INSTALLER_OUTPUT.is_file() or INSTALLER_OUTPUT.stat().st_size == 0:
        print(
            f"Installer build failed: expected output was not created at "
            f"{INSTALLER_OUTPUT}.",
            file=sys.stderr,
        )
        return 1

    print(f"Built installer: {INSTALLER_OUTPUT}")
    return 0


def find_compiler(explicit_path: Path | None = None) -> Path | None:
    """Find an explicitly configured or normally installed ISCC compiler."""
    candidates = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    configured = os.environ.get(COMPILER_ENV)
    if configured:
        candidates.append(Path(configured))
    candidates.extend(KNOWN_COMPILERS)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def run_command(command: list[str]) -> None:
    """Run one installer build command from the project root."""
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
