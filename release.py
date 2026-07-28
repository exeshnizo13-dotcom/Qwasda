"""Build portable and NSIS release artifacts for Qwasda."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from qwasda.version import __version__

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"


def run(*args: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=capture_output,
        text=capture_output,
        timeout=120,
    )


def main() -> int:
    version = __version__
    if version != "1.4.0":
        raise SystemExit(f"Unexpected release version: {version}")
    ARTIFACTS.mkdir(exist_ok=True)
    for path in ARTIFACTS.glob("Qwasda-*"):
        if path.is_file():
            path.unlink()

    run(sys.executable, "-m", "PyInstaller", "Qwasda.spec", "--clean", "--noconfirm")
    portable = ARTIFACTS / f"Qwasda-{version}-x64.exe"
    shutil.copy2(ROOT / "dist" / "Qwasda.exe", portable)
    version_result = run(str(portable), "--version", capture_output=True)
    if version_result.stdout.strip() != version:
        raise SystemExit(f"Packaged version mismatch: {version_result.stdout!r}")
    run(str(portable), "--smoke-test", capture_output=True)

    makensis = os.environ.get("MAKENSIS", "makensis")
    run(
        makensis,
        f"/DAPP_VERSION={version}",
        f"/DPAYLOAD={portable}",
        f"/DOUT_DIR={ARTIFACTS}",
        str(ROOT / "installer" / "Qwasda.nsi"),
    )

    cert = os.environ.get("QWASDA_SIGNTOOL_CERT")
    password = os.environ.get("QWASDA_SIGNTOOL_PASSWORD")
    if cert and password:
        signtool = os.environ.get("SIGNTOOL", "signtool")
        for artifact in (portable, ARTIFACTS / f"Qwasda-Setup-{version}-x64.exe"):
            run(signtool, "sign", "/fd", "SHA256", "/f", cert, "/p", password, str(artifact))
            run(signtool, "verify", "/pa", str(artifact))

    lines = []
    for artifact in sorted(ARTIFACTS.glob("Qwasda-*.exe")):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    (ARTIFACTS / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
