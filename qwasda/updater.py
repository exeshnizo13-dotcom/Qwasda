"""Secure, opt-in updater primitives for Qwasda.

The module keeps network and file replacement code separate from the engine so
that verification and recovery can be tested without a running tray process.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from .version import __version__

Ed25519PublicKey: Any
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore[import-not-found]
        Ed25519PublicKey as _Ed25519PublicKey,
    )

    Ed25519PublicKey = _Ed25519PublicKey
except ImportError:  # pragma: no cover - dependency is present in packaged builds
    Ed25519PublicKey = None


REPOSITORY = "exeshnizo13-dotcom/Qwasda"
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)" r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


class UpdateError(RuntimeError):
    """A user-facing update failure."""


class UpdateChannel(StrEnum):
    STABLE = "stable"
    BETA = "beta"


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[tuple[int, str], ...] = field(compare=False, default=())

    @classmethod
    def parse(cls, value: str) -> Version:
        match = _SEMVER.fullmatch(value.removeprefix("v"))
        if not match:
            raise UpdateError(f"Некоректна версія релізу: {value}")
        raw = match.group(4)
        parts: list[tuple[int, str]] = []
        if raw:
            for item in raw.split("."):
                parts.append((0, item) if item.isdigit() else (1, item))
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), tuple(parts))

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        base = (self.major, self.minor, self.patch)
        other_base = (other.major, other.minor, other.patch)
        if base != other_base:
            return base < other_base
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left[0] != right[0]:
                return left[0] < right[0]
            if left[1] != right[1]:
                return left[1] < right[1]
        return len(self.prerelease) < len(other.prerelease)

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(part for _, part in self.prerelease)
        return value


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    channel: UpdateChannel
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    asset_sha256: str
    manifest: bytes
    signature: bytes
    package_type: str = "exe"
    release_url: str = ""


@dataclass(frozen=True)
class UpdateProgress:
    phase: str
    received: int = 0
    total: int = 0
    message: str = ""


@dataclass(frozen=True)
class UpdateSnapshot:
    status: str = "idle"
    current_version: str = __version__
    available: UpdateRelease | None = None
    downloaded_path: str | None = None
    progress: UpdateProgress = field(default_factory=lambda: UpdateProgress("idle"))
    error: str | None = None
    last_checked_at: str | None = None


def canonical_json(data: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


class ManifestVerifier:
    """Verify signed manifests against an allowlist of public keys."""

    def __init__(self, public_keys: Mapping[str, bytes] | None = None):
        self.public_keys = dict(public_keys or _configured_public_keys())

    def verify(self, manifest_bytes: bytes, signature_bytes: bytes) -> dict[str, Any]:
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            signature = json.loads(signature_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("Пошкоджений update manifest або підпис") from exc
        if not isinstance(manifest, dict) or manifest.get("schema") not in (1, 2):
            raise UpdateError("Непідтримувана схема update manifest")
        key_id = manifest.get("signing_key_id")
        if not isinstance(key_id, str) or signature.get("key_id") != key_id:
            raise UpdateError("Невідповідний signing key id")
        key = self.public_keys.get(key_id)
        encoded = signature.get("signature")
        if key is None or not isinstance(encoded, str):
            raise UpdateError("Update manifest підписаний невідомим ключем")
        try:
            sig = base64.b64decode(encoded, validate=True)
            if Ed25519PublicKey is None:
                raise UpdateError("Відсутня криптографічна підтримка updater")
            Ed25519PublicKey.from_public_bytes(key).verify(sig, manifest_bytes)
        except (ValueError, TypeError) as exc:
            raise UpdateError("Некоректний ключ або підпис update manifest") from exc
        except Exception as exc:
            raise UpdateError("Недійсний підпис update manifest") from exc
        return manifest


def _configured_public_keys() -> dict[str, bytes]:
    """Read public keys injected into a packaged build by release.py."""
    raw = os.environ.get("QWASDA_UPDATE_PUBLIC_KEYS", "")
    if getattr(sys, "frozen", False):
        try:
            bundle = Path(sys._MEIPASS) / "update-public-keys.json"  # type: ignore[attr-defined]
            data = json.loads(bundle.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = ",".join(f"{key}:{value}" for key, value in data.items())
        except (AttributeError, OSError, json.JSONDecodeError):
            pass
    result: dict[str, bytes] = {}
    for item in raw.split(","):
        if not item or ":" not in item:
            continue
        key_id, encoded = item.split(":", 1)
        try:
            result[key_id] = base64.b64decode(encoded, validate=True)
        except ValueError:
            continue
    return result


def release_from_manifest(
    payload: Mapping[str, Any],
    manifest_bytes: bytes,
    signature: bytes,
    release_url: str = "",
) -> UpdateRelease:
    """Validate the signed manifest payload and create an immutable release."""
    version = payload.get("version")
    channel = payload.get("channel")
    assets = payload.get("assets")
    schema = payload.get("schema")
    asset_key = "windows-x64-portable" if schema == 1 else "windows-x64-portable-zip"
    asset = assets.get(asset_key) if isinstance(assets, dict) else None
    if not isinstance(version, str) or not isinstance(channel, str):
        raise UpdateError("Manifest не містить версії або каналу")
    try:
        parsed_channel = UpdateChannel(channel)
        Version.parse(version)
    except ValueError as exc:
        raise UpdateError("Невідомий канал оновлення") from exc
    if not isinstance(asset, dict):
        raise UpdateError("Manifest не містить Windows x64 asset")
    name, size, digest = asset.get("name"), asset.get("size"), asset.get("sha256")
    url = asset.get("url", "")
    if (
        not isinstance(name, str)
        or not re.fullmatch(
            r"Qwasda-[0-9A-Za-z.+-]+-x64\.(exe|zip)" if schema == 2 else r"Qwasda-[0-9A-Za-z.+-]+-x64\.exe",
            name,
        )
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_DOWNLOAD_BYTES
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(url, str)
        or not url.startswith("https://")
    ):
        raise UpdateError("Некоректний Windows x64 asset у manifest")
    return UpdateRelease(
        version=version,
        channel=parsed_channel,
        notes=str(payload.get("release_notes", ""))[:16_384],
        asset_name=name,
        asset_url=url,
        asset_size=size,
        asset_sha256=digest,
        manifest=manifest_bytes,
        signature=signature,
        package_type="zip" if schema == 2 else "exe",
        release_url=release_url,
    )


class ReleaseClient:
    """Read-only GitHub release discovery with ETag support."""

    def __init__(self, state_path: Path, opener: Any = urllib.request.urlopen):
        self.state_path = state_path
        self.opener = opener
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def consume_notification(self, version: str) -> bool:
        """Return true once per version and persist the notification marker."""
        notified = self.state.get("notified_versions", [])
        if not isinstance(notified, list):
            notified = []
        if version in notified:
            return False
        notified.append(version)
        self.state["notified_versions"] = notified[-20:]
        self.state["offered_version"] = version
        self._save_state()
        return True

    def discover(self, channel: UpdateChannel) -> UpdateRelease | None:
        endpoint = f"{API_ROOT}/repos/{REPOSITORY}/releases/latest"
        if channel is UpdateChannel.BETA:
            endpoint = f"{API_ROOT}/repos/{REPOSITORY}/releases?per_page=20"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "Qwasda-Updater/1",
        }
        etag = self.state.get(f"etag:{channel.value}")
        if isinstance(etag, str):
            headers["If-None-Match"] = etag
        request = urllib.request.Request(endpoint, headers=headers)
        try:
            response = self.opener(request, timeout=15)
            status = getattr(response, "status", 200)
            if status == 304:
                return None
            body = response.read()
            if hasattr(response, "headers") and response.headers.get("ETag"):
                self.state[f"etag:{channel.value}"] = response.headers["ETag"]
            self.state["last_checked_at"] = datetime.now(UTC).isoformat()
            self._save_state()
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return None
            raise UpdateError(f"GitHub не повернув реліз ({exc.code})") from exc
        except (OSError, TimeoutError) as exc:
            raise UpdateError("Не вдалося перевірити оновлення") from exc
        try:
            releases = json.loads(body)
        except json.JSONDecodeError as exc:
            raise UpdateError("GitHub повернув некоректні дані релізу") from exc
        candidates = releases if isinstance(releases, list) else [releases]
        parsed: list[tuple[Version, dict[str, Any]]] = []
        for item in candidates:
            if not isinstance(item, dict) or item.get("draft"):
                continue
            try:
                parsed.append((Version.parse(str(item["tag_name"])), item))
            except (KeyError, UpdateError):
                continue
        if channel is UpdateChannel.STABLE:
            parsed = [item for item in parsed if not item[0].is_prerelease]
        if not parsed:
            return None
        _, release = max(parsed, key=lambda item: item[0])
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("Реліз не містить assets")
        by_name = {item.get("name"): item for item in assets if isinstance(item, dict)}
        manifest_asset = by_name.get("update-manifest.json")
        signature_asset = by_name.get("update-manifest.json.sig")
        if not all(isinstance(item, dict) for item in (manifest_asset, signature_asset)):
            raise UpdateError("Реліз не підтримує безпечне оновлення")
        assert isinstance(manifest_asset, dict)
        assert isinstance(signature_asset, dict)
        manifest_bytes = self._read_asset(manifest_asset)
        signature = self._read_asset(signature_asset)
        manifest = ManifestVerifier().verify(manifest_bytes, signature)
        manifest_assets = manifest.get("assets")
        asset_key = "windows-x64-portable-zip" if manifest.get("schema") == 2 else "windows-x64-portable"
        portable_data = manifest_assets.get(asset_key) if isinstance(manifest_assets, dict) else None
        portable_name = portable_data.get("name") if isinstance(portable_data, dict) else None
        portable = by_name.get(portable_name)
        if not isinstance(portable, dict):
            raise UpdateError("Manifest portable asset відсутній у GitHub release")
        asset_url = portable.get("browser_download_url")
        if not isinstance(asset_url, str):
            raise UpdateError("Portable asset не має URL")
        if isinstance(manifest_assets, dict) and isinstance(portable_data, dict):
            portable_data = dict(portable_data)
            portable_data["url"] = asset_url
            manifest_assets = dict(manifest_assets)
            manifest_assets[asset_key] = portable_data
            manifest = dict(manifest)
            manifest["assets"] = manifest_assets
        result = release_from_manifest(
            manifest, manifest_bytes, signature, str(release.get("html_url", ""))
        )
        if result.version != str(release.get("tag_name", "")).removeprefix("v"):
            raise UpdateError("Версія manifest не збігається з GitHub tag")
        if not (Version.parse(__version__) < Version.parse(result.version)):
            return None
        return result

    def _read_asset(self, asset: Mapping[str, Any]) -> bytes:
        url = asset.get("browser_download_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise UpdateError("Asset має небезпечний URL")
        request = urllib.request.Request(url, headers={"User-Agent": "Qwasda-Updater/1"})
        try:
            response = self.opener(request, timeout=15)
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
        except (OSError, TimeoutError) as exc:
            raise UpdateError("Не вдалося завантажити manifest") from exc
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise UpdateError("Manifest перевищує допустимий розмір")
        return cast(bytes, data)


def download_release(
    release: UpdateRelease,
    destination: Path,
    progress: Callable[[UpdateProgress], None] | None = None,
    opener: Any = urllib.request.urlopen,
    cancel: threading.Event | None = None,
) -> Path:
    """Stream, hash and atomically finalize a portable update."""
    if not release.asset_url.startswith("https://"):
        raise UpdateError("Оновлення дозволені лише через HTTPS")
    destination.mkdir(parents=True, exist_ok=True)
    part = destination / f"{release.asset_name}.part"
    ready = destination / f"{release.asset_name}.ready"
    request = urllib.request.Request(release.asset_url, headers={"User-Agent": "Qwasda-Updater/1"})
    received = 0
    digest = hashlib.sha256()
    try:
        response = opener(request, timeout=30)
        with part.open("wb") as handle:
            while True:
                if cancel and cancel.is_set():
                    raise UpdateError("Завантаження скасовано")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES or received > release.asset_size:
                    raise UpdateError("Завантажений файл перевищує розмір manifest")
                handle.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(UpdateProgress("download", received, release.asset_size))
    except Exception:
        part.unlink(missing_ok=True)
        raise
    if received != release.asset_size or digest.hexdigest() != release.asset_sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("SHA-256 або розмір оновлення не збігається")
    os.replace(part, ready)
    if progress:
        progress(UpdateProgress("downloaded", received, release.asset_size))
    return ready


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _replace_file(source: Path, target: Path, backup: Path | None = None) -> None:
    """Replace a file with a same-volume backup on Windows."""
    if os.name == "nt":
        replace = ctypes.windll.kernel32.ReplaceFileW
        replace.restype = ctypes.wintypes.BOOL
        replace.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        backup_name = str(backup) if backup else None
        if not replace(str(target), str(source), backup_name, 0, None, None):
            raise OSError(f"ReplaceFileW failed: {ctypes.GetLastError()}")
        return
    if backup is not None:
        os.replace(target, backup)
    os.replace(source, target)


def _safe_extract_zip(archive: Path, staging: Path) -> Path:
    """Extract a package without allowing traversal or links."""
    staging.mkdir(parents=True, exist_ok=False)
    extracted = 0
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            relative = Path(name)
            if not name or relative.is_absolute() or ".." in relative.parts:
                raise UpdateError("Небезпечний шлях у ZIP оновлення")
            if member.external_attr >> 16 & 0o170000 == 0o120000:
                raise UpdateError("ZIP оновлення містить symlink")
            destination = (staging / relative).resolve()
            if staging.resolve() not in destination.parents and destination != staging.resolve():
                raise UpdateError("Небезпечний шлях у ZIP оновлення")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                extracted += member.file_size
                if extracted > MAX_DOWNLOAD_BYTES * 4:
                    raise UpdateError("Розпакований пакет оновлення завеликий")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    root = staging / "Qwasda"
    return root if (root / "Qwasda.exe").is_file() else staging


def _apply_zip_package(ready: Path, target: Path, expected: str) -> None:
    staging = ready.with_name(f"{ready.stem}.staging")
    root = _safe_extract_zip(ready, staging)
    manifest_path = root / "package-manifest.json"
    if not manifest_path.is_file() or not (root / "Qwasda.exe").is_file():
        raise UpdateError("ZIP оновлення не містить package manifest або Qwasda.exe")
    package = json.loads(manifest_path.read_text(encoding="utf-8"))
    if package.get("version") != expected or not isinstance(package.get("files"), dict):
        raise UpdateError("Невідповідний package manifest")
    for relative, digest in package["files"].items():
        path = (root / str(relative)).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise UpdateError("Некоректний файл у package manifest")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise UpdateError("SHA-256 файла оновлення не збігається")
    backup_root = ready.with_name(f"{ready.stem}.backup")
    try:
        for relative in package["files"]:
            source = root / str(relative)
            destination = target.parent / str(relative)
            backup = backup_root / str(relative)
            if destination.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(backup))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        result = subprocess.run([str(target), "--version"], timeout=30, capture_output=True, text=True)
        if result.returncode != 0 or result.stdout.strip() != expected:
            raise UpdateError("Нова версія не пройшла перевірку")
    except Exception:
        for relative in package["files"]:
            destination = target.parent / str(relative)
            backup = backup_root / str(relative)
            if destination.exists():
                destination.unlink()
            if backup.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(destination))
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup_root, ignore_errors=True)


def apply_update(journal_path: Path, timeout: float = 30.0) -> int:
    """Apply a preflighted update from a journal; intended for the helper."""
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        ready = Path(str(journal["ready"])).resolve()
        target = Path(str(journal["target"])).resolve()
        backup = Path(str(journal["backup"])).resolve()
        expected = str(journal["version"])
        if ready.suffix.lower() != ".ready" or target.suffix.lower() != ".exe":
            raise UpdateError("Некоректні шляхи update journal")
        if not ready.is_file() or not target.is_file():
            raise UpdateError("Файл оновлення або target відсутній")
        journal["state"] = "applying"
        _write_json(journal_path, journal)
        if str(journal.get("package_type", "exe")) == "zip":
            _apply_zip_package(ready, target, expected)
        else:
            _replace_file(ready, target, backup)
            result = subprocess.run([str(target), "--version"], timeout=timeout, capture_output=True, text=True)
            if result.returncode != 0 or result.stdout.strip() != expected:
                raise UpdateError("Нова версія не пройшла перевірку")
            smoke = subprocess.run([str(target), "--smoke-test"], timeout=timeout)
            if smoke.returncode != 0:
                raise UpdateError("Smoke-test нової версії завершився помилкою")
        journal["state"] = "applied"
        _write_json(journal_path, journal)
        backup.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        subprocess.Popen([str(target)])
        return 0
    except Exception as exc:
        try:
            if "backup" in locals() and backup.is_file():
                _replace_file(backup, target)
            journal["state"] = "rolled_back"
            journal["error"] = str(exc)
            _write_json(journal_path, journal)
        except Exception:
            pass
        return 1


class UpdateManager:
    """Thread-safe coordinator used by the engine and settings UI."""

    def __init__(
        self,
        app_dir: str,
        enabled: bool = False,
        channel: UpdateChannel = UpdateChannel.STABLE,
        callback: Callable[[UpdateSnapshot], None] | None = None,
    ):
        local_root = Path(os.environ.get("LOCALAPPDATA", app_dir)) / "Qwasda" / "updates"
        self.root = local_root
        self.client = ReleaseClient(local_root / "state.json")
        self.enabled = enabled
        self.channel = channel
        self.callback = callback
        self.snapshot = UpdateSnapshot()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _publish(self, snapshot: UpdateSnapshot) -> None:
        with self._lock:
            self.snapshot = snapshot
        if self.callback:
            self.callback(snapshot)

    def check(self, automatic: bool = False) -> None:
        if automatic and not self.enabled:
            return
        if automatic:
            raw = self.client.state.get("last_checked_at")
            if isinstance(raw, str):
                try:
                    age = datetime.now(UTC) - datetime.fromisoformat(raw)
                    if age.total_seconds() < CHECK_INTERVAL_SECONDS:
                        return
                except ValueError:
                    pass
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._check_worker, name="qwasda-updates", daemon=True
        )
        self._thread.start()

    def _check_worker(self) -> None:
        self._publish(UpdateSnapshot("checking", current_version=__version__))
        try:
            release = self.client.discover(self.channel)
            self._publish(
                UpdateSnapshot(
                    "available" if release else "up-to-date",
                    available=release,
                    last_checked_at=self.client.state.get("last_checked_at"),
                )
            )
        except UpdateError as exc:
            self._publish(UpdateSnapshot("error", error=str(exc)))

    def cancel(self) -> None:
        self._cancel.set()

    def download(self) -> None:
        release = self.snapshot.available
        if release is None:
            return
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._download_worker,
            args=(release,),
            name="qwasda-update-download",
            daemon=True,
        )
        self._thread.start()

    def _download_worker(self, release: UpdateRelease) -> None:
        try:
            path = download_release(
                release, self.root / release.version, self._publish_progress, cancel=self._cancel
            )
            self._publish(
                UpdateSnapshot("downloaded", available=release, downloaded_path=str(path))
            )
        except UpdateError as exc:
            self._publish(UpdateSnapshot("error", available=release, error=str(exc)))

    def _publish_progress(self, progress: UpdateProgress) -> None:
        self._publish(
            UpdateSnapshot("downloading", available=self.snapshot.available, progress=progress)
        )

    def stop(self) -> None:
        self.cancel()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def start_apply(self, target: Path) -> str:
        release = self.snapshot.available
        ready = Path(self.snapshot.downloaded_path or "")
        if release is None or not ready.is_file():
            raise UpdateError("Оновлення ще не завантажене")
        if not getattr(sys, "frozen", False):
            raise UpdateError("Самозаміна доступна лише у packaged EXE")
        if release.package_type == "zip":
            try:
                with zipfile.ZipFile(ready) as bundle:
                    names = set(bundle.namelist())
                    manifest_name = "Qwasda/package-manifest.json" if "Qwasda/package-manifest.json" in names else "package-manifest.json"
                    package = json.loads(bundle.read(manifest_name).decode("utf-8"))
                if package.get("version") != release.version:
                    raise UpdateError("ZIP версія не пройшла preflight")
            except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise UpdateError("Завантажений ZIP не пройшов preflight") from exc
        else:
            version_result = subprocess.run([str(ready), "--version"], timeout=30, capture_output=True, text=True)
            if version_result.returncode != 0 or version_result.stdout.strip() != release.version:
                raise UpdateError("Завантажена версія не пройшла preflight")
            smoke_result = subprocess.run([str(ready), "--smoke-test"], timeout=30)
            if smoke_result.returncode != 0:
                raise UpdateError("Завантажений EXE не пройшов smoke-test")
        backup = ready.with_suffix(".backup.exe")
        journal = ready.with_suffix(".json")
        _write_json(
            journal,
            {
                "state": "downloaded",
                "ready": str(ready),
                "target": str(target),
                "backup": str(backup),
                "version": release.version,
                "package_type": release.package_type,
            },
        )
        if release.package_type == "zip" and getattr(sys, "frozen", False):
            # A one-dir executable needs its adjacent _internal directory.
            # Copy the current runnable bundle outside the install directory
            # so the helper can replace the live package safely.
            helper_root = ready.with_name("Qwasda-Updater")
            if helper_root.exists():
                shutil.rmtree(helper_root, ignore_errors=True)
            shutil.copytree(Path(sys.executable).parent, helper_root)
            helper = helper_root / Path(sys.executable).name
        else:
            helper = ready.with_name("Qwasda-Updater.exe")
            shutil.copy2(sys.executable, helper)
        subprocess.Popen([str(helper), "--apply-update", str(journal)])
        return "Оновлення запущено; Qwasda буде перезапущено після перевірки."
