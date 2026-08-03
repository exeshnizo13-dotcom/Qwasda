"""Build portable and NSIS release artifacts for Qwasda."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from qwasda.updater import ManifestVerifier, canonical_json
from qwasda.version import __version__

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # pragma: no cover
    Ed25519PrivateKey = None  # type: ignore[assignment,misc]

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
    if version != "1.5.0":
        raise SystemExit(f"Unexpected release version: {version}")
    ARTIFACTS.mkdir(exist_ok=True)
    for path in ARTIFACTS.glob("Qwasda-*"):
        if path.is_file():
            path.unlink()

    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)
    public_keys = os.environ.get("QWASDA_UPDATE_PUBLIC_KEYS", "")
    signing_key_raw = os.environ.get("QWASDA_UPDATE_SIGNING_KEY")
    signing_key_id = os.environ.get("QWASDA_UPDATE_KEY_ID", "qwasda-2026-01")
    if signing_key_raw and Ed25519PrivateKey is not None:
        try:
            signing_private = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(signing_key_raw, validate=True)
            )
            derived = base64.b64encode(signing_private.public_key().public_bytes_raw()).decode(
                "ascii"
            )
            public_keys = (
                f"{public_keys},{signing_key_id}:{derived}"
                if public_keys
                else f"{signing_key_id}:{derived}"
            )
        except (ValueError, TypeError) as exc:
            raise SystemExit("QWASDA_UPDATE_SIGNING_KEY must be base64 Ed25519 seed") from exc
    key_payload: dict[str, str] = {}
    for item in public_keys.split(","):
        if ":" in item:
            key_id, encoded = item.split(":", 1)
            key_payload[key_id] = encoded
    (build_dir / "update-public-keys.json").write_text(
        json.dumps(key_payload, sort_keys=True), encoding="utf-8"
    )
    run(sys.executable, "-m", "PyInstaller", "Qwasda.spec", "--clean", "--noconfirm")
    dist_dir = ROOT / "dist" / "Qwasda"
    if not (dist_dir / "Qwasda.exe").is_file():
        raise SystemExit("PyInstaller one-dir output is missing Qwasda.exe")
    package_manifest: dict[str, object] = {"schema": 1, "version": version, "files": {}}
    files: dict[str, str] = {}
    for path in sorted(dist_dir.rglob("*")):
        if path.is_file() and path.name != "package-manifest.json":
            files[str(path.relative_to(dist_dir)).replace(os.sep, "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    package_manifest["files"] = files
    (dist_dir / "package-manifest.json").write_bytes(canonical_json(package_manifest))
    portable = ARTIFACTS / f"Qwasda-{version}-x64.zip"
    shutil.make_archive(str(portable.with_suffix("")), "zip", root_dir=ROOT / "dist", base_dir="Qwasda")
    version_result = run(str(dist_dir / "Qwasda.exe"), "--version", capture_output=True)
    if version_result.stdout.strip() != version:
        raise SystemExit(f"Packaged version mismatch: {version_result.stdout!r}")
    run(str(dist_dir / "Qwasda.exe"), "--smoke-test", capture_output=True)

    makensis = os.environ.get("MAKENSIS", "makensis")
    run(
        makensis,
        f"/DAPP_VERSION={version}",
        f"/DPAYLOAD_DIR={dist_dir}",
        f"/DOUT_DIR={ARTIFACTS}",
        str(ROOT / "installer" / "Qwasda.nsi"),
    )

    cert = os.environ.get("QWASDA_SIGNTOOL_CERT")
    password = os.environ.get("QWASDA_SIGNTOOL_PASSWORD")
    if cert and password:
        signtool = os.environ.get("SIGNTOOL", "signtool")
        for artifact in (ARTIFACTS / f"Qwasda-Setup-{version}-x64.exe",):
            run(signtool, "sign", "/fd", "SHA256", "/f", cert, "/p", password, str(artifact))
            run(signtool, "verify", "/pa", str(artifact))

    lines = []
    for artifact in sorted(ARTIFACTS.glob("Qwasda-*.zip")) + sorted(ARTIFACTS.glob("Qwasda-*.exe")):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    (ARTIFACTS / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    signing_key = signing_key_raw
    require_signature = os.environ.get("QWASDA_REQUIRE_UPDATE_SIGNATURE") == "1"
    if require_signature and not signing_key:
        raise SystemExit("QWASDA_UPDATE_SIGNING_KEY is required for a production release")
    if signing_key:
        if Ed25519PrivateKey is None:
            raise SystemExit("cryptography is required to sign update manifests")
        key_id = signing_key_id
        try:
            seed = base64.b64decode(signing_key, validate=True)
            private_key = Ed25519PrivateKey.from_private_bytes(seed)
        except (ValueError, TypeError) as exc:
            raise SystemExit("QWASDA_UPDATE_SIGNING_KEY must be base64 Ed25519 seed") from exc
        manifest = {
            "schema": 2,
            "version": version,
            "channel": "beta" if "-" in version else "stable",
            "published_at": os.environ.get("QWASDA_RELEASE_DATE", ""),
            "minimum_updater_version": "1.5.0",
            "signing_key_id": key_id,
            "release_notes": "Безпечні opt-in автоматичні оновлення Qwasda.",
            "assets": {
                "windows-x64-portable-zip": {
                    "name": portable.name,
                    "size": portable.stat().st_size,
                    "sha256": hashlib.sha256(portable.read_bytes()).hexdigest(),
                }
            },
        }
        manifest_bytes = canonical_json(manifest)
        signature = private_key.sign(manifest_bytes)
        signature_payload = {
            "schema": 2,
            "key_id": key_id,
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        (ARTIFACTS / "update-manifest.json").write_bytes(manifest_bytes)
        (ARTIFACTS / "update-manifest.json.sig").write_text(
            json.dumps(signature_payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        configured = {key_id: private_key.public_key().public_bytes_raw()}
        ManifestVerifier(configured).verify(
            manifest_bytes, (ARTIFACTS / "update-manifest.json.sig").read_bytes()
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
