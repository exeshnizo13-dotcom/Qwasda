from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qwasda.config import ConfigManager
from qwasda.updater import (
    ManifestVerifier,
    ReleaseClient,
    UpdateChannel,
    UpdateError,
    Version,
    canonical_json,
    download_release,
    release_from_manifest,
)


def _signed_manifest() -> tuple[dict[str, object], bytes, bytes, dict[str, bytes]]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    manifest: dict[str, object] = {
        "schema": 1,
        "version": "1.6.0",
        "channel": "stable",
        "signing_key_id": "test-key",
        "assets": {
            "windows-x64-portable": {
                "name": "Qwasda-1.6.0-x64.exe",
                "size": 4,
                "sha256": hashlib.sha256(b"test").hexdigest(),
                "url": "https://example.invalid/update.exe",
            }
        },
    }
    data = canonical_json(manifest)
    signature = json.dumps(
        {
            "schema": 1,
            "key_id": "test-key",
            "signature": base64.b64encode(private.sign(data)).decode("ascii"),
        }
    ).encode("utf-8")
    return manifest, data, signature, {"test-key": public}


def test_version_orders_stable_after_prerelease() -> None:
    assert Version.parse("1.5.0-beta.1") < Version.parse("1.5.0")
    assert Version.parse("1.6.0") > Version.parse("1.5.0")


def test_manifest_signature_and_asset_validation() -> None:
    manifest, data, signature, keys = _signed_manifest()
    payload = ManifestVerifier(keys).verify(data, signature)
    release = release_from_manifest(payload, data, signature)
    assert release.channel is UpdateChannel.STABLE
    assert release.version == "1.6.0"


def test_manifest_tampering_is_rejected() -> None:
    _, data, signature, keys = _signed_manifest()
    with pytest.raises(UpdateError, match="підпис"):
        ManifestVerifier(keys).verify(data.replace(b"1.6.0", b"9.9.9"), signature)


def test_download_hash_size_and_atomic_ready(tmp_path: Path) -> None:
    manifest, data, signature, keys = _signed_manifest()
    release = release_from_manifest(ManifestVerifier(keys).verify(data, signature), data, signature)

    class Response(io.BytesIO):
        status = 200

    path = download_release(release, tmp_path, opener=lambda *_args, **_kwargs: Response(b"test"))
    assert path.name.endswith(".ready")
    assert path.read_bytes() == b"test"
    assert not list(tmp_path.glob("*.part"))


def test_download_mismatch_removes_partial(tmp_path: Path) -> None:
    manifest, data, signature, keys = _signed_manifest()
    release = release_from_manifest(ManifestVerifier(keys).verify(data, signature), data, signature)

    class Response(io.BytesIO):
        status = 200

    with pytest.raises(UpdateError, match="SHA-256"):
        download_release(release, tmp_path, opener=lambda *_args, **_kwargs: Response(b"bad!"))
    assert not list(tmp_path.iterdir())


def test_old_config_gets_update_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    manager = ConfigManager(str(tmp_path))
    manager.load()
    assert manager.config.update_checks_enabled is False
    assert manager.config.update_channel is UpdateChannel.STABLE


def test_update_config_round_trip_serializes_channel(tmp_path: Path) -> None:
    manager = ConfigManager(str(tmp_path))
    manager.update_config(update_checks_enabled=True, update_channel=UpdateChannel.BETA)
    reloaded = ConfigManager(str(tmp_path))
    reloaded.load()
    assert reloaded.config.update_checks_enabled is True
    assert reloaded.config.update_channel is UpdateChannel.BETA


def test_notification_marker_is_persistent_and_once(tmp_path: Path) -> None:
    client = ReleaseClient(tmp_path / "state.json", opener=lambda *_args, **_kwargs: None)
    assert client.consume_notification("1.6.0") is True
    assert client.consume_notification("1.6.0") is False
    reloaded = ReleaseClient(tmp_path / "state.json", opener=lambda *_args, **_kwargs: None)
    assert reloaded.state["offered_version"] == "1.6.0"
