"""Regression tests for the DebtPilot product identity."""

from pathlib import Path

from app.cli import build_parser
from app.console import print_application_banner
from app.version import APP_NAME, APP_TAGLINE, APP_VERSION
from scripts import build_windows

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_application_identity_constants() -> None:
    """The central product identity uses the DebtPilot brand."""
    assert APP_NAME == "DebtPilot"
    assert APP_TAGLINE == "Debt Planning & Progress Tracking"


def test_banner_uses_debtpilot_branding() -> None:
    """The interactive banner displays the new name and positioning."""
    output = []

    print_application_banner(APP_VERSION, output.append)

    assert f"DebtPilot v{APP_VERSION}" in output
    assert APP_TAGLINE in output
    assert not any("DebtSnowball v" in line for line in output)


def test_cli_help_uses_debtpilot_branding() -> None:
    """Argparse help identifies the application as DebtPilot."""
    help_text = build_parser().format_help()

    assert "DebtPilot" in help_text
    assert "DebtSnowball" not in help_text


def test_windows_build_outputs_are_branded_debtpilot() -> None:
    """The build script and PyInstaller spec create DebtPilot.exe."""
    assert build_windows.SPEC_PATH.name == "DebtPilot.spec"
    assert build_windows.DIST_APP_DIR.name == "DebtPilot"
    assert build_windows.EXE_PATH.name == "DebtPilot.exe"

    spec = (PROJECT_ROOT / "DebtPilot.spec").read_text(encoding="utf-8")
    version_info = (
        PROJECT_ROOT / "scripts" / "windows_version_info.txt"
    ).read_text(encoding="utf-8")
    assert 'name="DebtPilot"' in spec
    assert 'version=str(ROOT / "scripts" / "windows_version_info.txt")' in spec
    assert 'StringStruct("ProductName", "DebtPilot")' in version_info
    assert 'StringStruct("OriginalFilename", "DebtPilot.exe")' in version_info


def test_readme_uses_current_commands_and_paths() -> None:
    """First-time-user documentation contains current DebtPilot examples."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# DebtPilot")
    assert "python run.py" in readme
    assert "dist/DebtPilot/DebtPilot.exe" in readme
    assert "%LOCALAPPDATA%/DebtPilot" in readme
    assert "debtpilot_plan.xlsx" in readme
