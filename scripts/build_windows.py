"""Build the portable Windows DebtSnowball executable."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = PROJECT_ROOT / "DebtSnowball.spec"
DIST_APP_DIR = PROJECT_ROOT / "dist" / "DebtSnowball"
EXE_PATH = DIST_APP_DIR / "DebtSnowball.exe"
KNOWN_BUILD_OUTPUTS = (PROJECT_ROOT / "build" / "DebtSnowball", DIST_APP_DIR)


def main(argv: list[str] | None = None) -> int:
    """Run checks and build the one-folder Windows executable."""
    parser = argparse.ArgumentParser(description="Build DebtSnowball.exe with PyInstaller.")
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip pytest and Ruff before building.",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("This build script must be run on Windows.", file=sys.stderr)
        return 1

    if not pyinstaller_available():
        print(
            "PyInstaller is not installed. Run: python -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return 1

    if not args.skip_checks:
        run_command([sys.executable, "-m", "pytest"])
        run_command([sys.executable, "-m", "ruff", "check", "."])

    clean_known_outputs()
    run_command([sys.executable, "-m", "PyInstaller", str(SPEC_PATH), "--noconfirm"])

    if not EXE_PATH.exists():
        print(f"Build failed: expected executable was not created at {EXE_PATH}.", file=sys.stderr)
        return 1

    print(f"Built executable: {EXE_PATH}")
    return 0


def pyinstaller_available() -> bool:
    """Return whether PyInstaller can be imported."""
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def clean_known_outputs() -> None:
    """Remove only the build outputs this script owns."""
    for path in KNOWN_BUILD_OUTPUTS:
        if path.exists():
            shutil.rmtree(path)


def run_command(command: list[str]) -> None:
    """Run one build command from the project root."""
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
