"""Tests for the Windows installer build support."""

from pathlib import Path

from scripts import build_installer


def test_inno_script_defines_per_user_install_and_shortcuts() -> None:
    """Installer configuration provides the required non-admin UX."""
    script = build_installer.INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in script
    assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in script
    assert r'Name: "{group}\DebtPilot"' in script
    assert r'Name: "{autodesktop}\DebtPilot"' in script
    assert "Tasks: desktopicon" in script
    assert "UninstallDisplayIcon=" in script
    assert r'Source: "..\dist\DebtPilot\*"' in script


def test_find_compiler_prefers_explicit_path(tmp_path, monkeypatch) -> None:
    """An explicit compiler path takes precedence over environment discovery."""
    explicit = tmp_path / "ISCC.exe"
    explicit.write_bytes(b"compiler")
    configured = tmp_path / "configured.exe"
    configured.write_bytes(b"configured")
    monkeypatch.setenv(build_installer.COMPILER_ENV, str(configured))

    assert build_installer.find_compiler(explicit) == explicit


def test_find_compiler_uses_environment_path(tmp_path, monkeypatch) -> None:
    """Build agents can provide the compiler without a machine-wide install."""
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"compiler")
    monkeypatch.setenv(build_installer.COMPILER_ENV, str(compiler))
    monkeypatch.setattr(build_installer, "KNOWN_COMPILERS", ())

    assert build_installer.find_compiler() == compiler


def test_find_compiler_returns_none_when_no_candidate_exists(
    tmp_path,
    monkeypatch,
) -> None:
    """A missing compiler is reported by the build entrypoint."""
    monkeypatch.delenv(build_installer.COMPILER_ENV, raising=False)
    monkeypatch.setattr(
        build_installer,
        "KNOWN_COMPILERS",
        (tmp_path / "missing.exe",),
    )

    assert build_installer.find_compiler() is None


def test_installer_output_path_is_versioned_executable() -> None:
    """Release output has a stable product and version name."""
    assert build_installer.INSTALLER_OUTPUT.name == "DebtPilot-Setup-1.2.0.exe"
    assert build_installer.INSTALLER_OUTPUT.parent == Path(
        build_installer.PROJECT_ROOT,
        "dist",
        "installer",
    )
