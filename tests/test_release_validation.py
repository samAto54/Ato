import importlib.util
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_release.py"
SPEC = importlib.util.spec_from_file_location("validate_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_source_metadata_is_consistent() -> None:
    root = Path(__file__).parents[1]
    version, files = MODULE.validate_source(root)

    assert version == "0.1.0"
    assert "ato/ui/desktop.py" in files
    assert "ato/providers/deepseek.py" in files


def test_release_validator_checks_wheel_modules_metadata_and_scripts(tmp_path) -> None:
    root = Path(__file__).parents[1]
    version, files = MODULE.validate_source(root)
    wheel = tmp_path / "ato_agent-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in files:
            archive.writestr(name, "")
        archive.writestr(
            "ato_agent-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: ato-agent\nVersion: 0.1.0\n",
        )
        archive.writestr(
            "ato_agent-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\n"
            "ato = ato.main:main\n"
            "ato-gui = ato.ui.desktop:main\n"
            "ato-doctor = ato.doctor:main\n",
        )

    MODULE.validate_wheel(wheel, version, files)
