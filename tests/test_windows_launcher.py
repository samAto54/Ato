from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_double_click_launcher_is_relative_and_secret_free() -> None:
    launcher = (ROOT / "start-ato.cmd").read_text(encoding="utf-8")

    assert "%~dp0" in launcher
    assert ".venv\\Scripts\\pythonw.exe" in launcher
    assert "-m ato.ui.desktop" in launcher
    assert "DEEPSEEK_API_KEY" not in launcher
    assert "C:\\Users\\" not in launcher


def test_shortcut_installer_refuses_overwrite_and_uses_dynamic_project_root() -> None:
    installer = (ROOT / "scripts" / "install_windows_shortcut.ps1").read_text(encoding="utf-8")

    assert "Split-Path -Parent $PSScriptRoot" in installer
    assert "Test-Path -LiteralPath $shortcutPath" in installer
    assert "$shortcut.TargetPath = $launcherPath" in installer
    assert "DEEPSEEK_API_KEY" not in installer
    assert "C:\\Users\\" not in installer
