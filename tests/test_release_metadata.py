from __future__ import annotations

from pathlib import Path

from qwasda.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_installer_contract():
    assert __version__ == "1.5.0"
    installer = (ROOT / "installer" / "Qwasda.nsi").read_text(encoding="utf-8")
    assert "${APP_VERSION}" in installer
    assert "/AUTOSTART" in installer
    assert "/PURGEUSERDATA" in installer
    assert "SHA256SUMS.txt" in (ROOT / "release.py").read_text(encoding="utf-8")
    assert "update-manifest.json" in (ROOT / "release.py").read_text(encoding="utf-8")
    assert "QWASDA_UPDATE_SIGNING_KEY" in (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")


def test_release_toolchain_is_pinned():
    assert (ROOT / "tools" / "nsis-version.txt").read_text(encoding="utf-8").strip() == "3.12"
    assert "pyinstaller==6.20.0" in (ROOT / "requirements-release.txt").read_text(encoding="utf-8")
