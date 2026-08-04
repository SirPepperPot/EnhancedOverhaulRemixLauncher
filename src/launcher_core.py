from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

try:
    import winreg
except ImportError:  # pragma: no cover - Windows release only
    winreg = None


APP_NAME = "Enhanced Overhaul Launcher"
GAME_NAME = "For The King II"
GAME_EXE = "For The King II.exe"
STEAM_APP_ID = "1676840"
PLUGIN_RELATIVE_PATH = Path("BepInEx/plugins/EnhancedOverhaulRemix.dll")
STATE_RELATIVE_PATH = Path("BepInEx/EnhancedOverhaulLauncher")
MANIFEST_NAME = "install_manifest.json"
PACKAGE_METADATA_NAME = "EOR_PACKAGE.json"
PACKAGE_ID = "sirpepperpot.enhanced-overhaul-revamped"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_KINDS = {
    "full": "Full (includes BepInEx)",
    "mod-only": "Mod only",
}
VERSION_URL = (
    "https://raw.githubusercontent.com/"
    "SirPepperPot/EnhancedOverhaulRemixVersion/main/latest.txt"
)
NEXUS_FILES_URL = "https://www.nexusmods.com/fortheking2/mods/29?tab=files"
SCHEMA_VERSION = 1
MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class VerificationResult:
    expected: int
    valid: int
    missing: tuple[str, ...]
    modified: tuple[str, ...]
    extra_notes: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.expected > 0 and not self.missing and not self.modified


@dataclass(frozen=True)
class InstallResult:
    installed_files: int
    backed_up_files: int
    removed_obsolete_files: int
    version: str


@dataclass(frozen=True)
class UninstallResult:
    removed_files: int
    restored_files: int
    preserved_files: int
    preserve_folder: Optional[Path]


@dataclass(frozen=True)
class PackageInfo:
    archive_path: Path
    version: str
    package_kind: str
    file_count: int


def normalize_version(value: str) -> tuple[int, ...]:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", value or "")
    if not match:
        raise ValueError(f"No version number was found in {value!r}.")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def compare_versions(left: str, right: str) -> int:
    left_parts = normalize_version(left)
    right_parts = normalize_version(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def validate_game_directory(path: Path) -> bool:
    path = Path(path)
    return (
        path.is_dir()
        and (path / GAME_EXE).is_file()
        and (path / "For The King II_Data").is_dir()
    )


def _read_steam_registry_paths() -> list[Path]:
    if winreg is None:
        return []

    results: list[Path] = []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    )
    for hive, key_name in keys:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        value, _ = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if value:
                        results.append(Path(str(value).replace("/", "\\")))
        except OSError:
            continue
    return results


def _steam_library_paths(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        contents = vdf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries

    for raw_path in re.findall(r'"path"\s+"([^"]+)"', contents, flags=re.IGNORECASE):
        decoded = raw_path.replace("\\\\", "\\")
        libraries.append(Path(decoded))
    return libraries


def detect_game_directories(saved_path: Optional[Path] = None) -> list[Path]:
    candidates: list[Path] = []
    if saved_path:
        candidates.append(Path(saved_path))

    steam_roots = _read_steam_registry_paths()
    steam_roots.extend(
        [
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Steam",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
        ]
    )

    for steam_root in steam_roots:
        for library in _steam_library_paths(steam_root):
            candidates.append(library / "steamapps" / "common" / GAME_NAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            key = str(candidate.absolute()).casefold()
        if key in seen or not validate_game_directory(candidate):
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def fetch_latest_version(timeout_seconds: int = 8) -> str:
    request = urllib.request.Request(
        f"{VERSION_URL}?launcher={int(time.time())}",
        headers={
            "User-Agent": "EnhancedOverhaulLauncher/1.0",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read(4096).decode("utf-8", errors="replace").strip()
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", body)
    if not match:
        raise ValueError("The online version file did not contain a valid version.")
    return match.group(1)


def is_game_running() -> bool:
    if os.name != "nt":
        return False
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return GAME_EXE.casefold() in result.stdout.casefold()


def _safe_relative_path(value: str | Path) -> Path:
    normalized = str(value).replace("\\", "/")
    path = Path(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(f"Unsafe package path: {value}")
    return path


def _payload_files(payload_root: Path) -> list[Path]:
    return sorted(
        (
            path.relative_to(payload_root)
            for path in payload_root.rglob("*")
            if path.is_file() and path.name != PACKAGE_METADATA_NAME
        ),
        key=lambda path: str(path).casefold(),
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.eor-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_archive_members(
    members: Iterable[tuple[str, int, bool]],
) -> tuple[str, ...]:
    names: list[str] = []
    total_bytes = 0
    for name, size, is_link in members:
        _safe_relative_path(name)
        if is_link:
            raise ValueError(f"Archive links are not allowed: {name}")
        if size < 0:
            raise ValueError(f"Archive entry has an invalid size: {name}")
        total_bytes += size
        if total_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError("The selected archive expands beyond the 2 GB safety limit.")
        names.append(name)
        if len(names) > MAX_ARCHIVE_FILES:
            raise ValueError("The selected archive contains too many files.")
    return tuple(names)


def _extract_archive(archive_path: Path, destination: Path) -> int:
    suffix = archive_path.suffix.casefold()
    if suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = []
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0o170000
                is_link = mode == 0o120000
                members.append((info.filename, info.file_size, is_link))
            names = _validate_archive_members(members)
            archive.extractall(destination)
            return sum(1 for name in names if not name.endswith("/"))

    if suffix == ".7z":
        seven_zip = _find_seven_zip()
        members = _list_seven_zip_members(seven_zip, archive_path)
        names = _validate_archive_members(members)
        result = _run_seven_zip(
            seven_zip,
            ["x", "-y", f"-o{destination}", str(archive_path)],
            timeout_seconds=180,
        )
        if result.returncode != 0:
            detail = (result.stdout + "\n" + result.stderr).strip()
            raise RuntimeError(
                "7-Zip could not extract the selected archive.\n"
                + "\n".join(detail.splitlines()[-12:])
            )
        return sum(1 for name in names if not name.endswith("/"))

    raise ValueError("Select an Enhanced Overhaul release archive (.7z or .zip).")


def _find_seven_zip() -> Path:
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        / "tools"
        / "7z.exe",
        Path(__file__).resolve().parent / "tools" / "7z.exe",
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ]
    for candidate in roots:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "The launcher's 7-Zip extractor is missing. Download the latest "
        "launcher or select a .zip release."
    )


def _run_seven_zip(
    executable: Path,
    arguments: list[str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [str(executable), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        creationflags=creation_flags,
    )


def _list_seven_zip_members(
    executable: Path,
    archive_path: Path,
) -> tuple[tuple[str, int, bool], ...]:
    result = _run_seven_zip(
        executable,
        ["l", "-slt", "-ba", str(archive_path)],
        timeout_seconds=60,
    )
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise ValueError(
            "The selected .7z file is invalid or damaged.\n"
            + "\n".join(detail.splitlines()[-12:])
        )

    members: list[tuple[str, int, bool]] = []
    current: dict[str, str] = {}
    for raw_line in result.stdout.splitlines() + [""]:
        line = raw_line.strip()
        if not line:
            if "Path" in current:
                name = current["Path"]
                is_directory = (
                    current.get("Folder") == "+"
                    or current.get("Attributes", "").startswith("D")
                )
                if is_directory:
                    name = name.rstrip("/\\") + "/"
                size_text = current.get("Size", "0")
                try:
                    size = int(size_text)
                except ValueError:
                    size = 0
                is_link = bool(
                    current.get("Symbolic Link")
                    or current.get("Hard Link")
                    or current.get("Alternate Stream")
                )
                members.append((name, size, is_link))
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value

    if not members:
        raise ValueError("The selected .7z archive is empty.")
    return tuple(members)


def _find_payload_root(extraction_root: Path) -> Path:
    direct = extraction_root / PLUGIN_RELATIVE_PATH
    if direct.is_file():
        return extraction_root

    candidates = sorted(
        {
            plugin.parents[len(PLUGIN_RELATIVE_PATH.parts) - 1]
            for plugin in extraction_root.rglob(PLUGIN_RELATIVE_PATH.name)
            if plugin.is_file()
            and str(plugin).replace("\\", "/").casefold().endswith(
                str(PLUGIN_RELATIVE_PATH).replace("\\", "/").casefold()
            )
        },
        key=lambda value: len(value.parts),
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(
            "The archive contains multiple modpack roots. Select an official "
            "Enhanced Overhaul release archive."
        )
    raise ValueError(
        "EnhancedOverhaulRemix.dll was not found in the expected "
        "BepInEx/plugins folder."
    )


def _read_package_metadata(payload_root: Path) -> dict:
    metadata_path = payload_root / PACKAGE_METADATA_NAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise ValueError(
            f"{PACKAGE_METADATA_NAME} is missing. Select an official current "
            "Enhanced Overhaul release archive."
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{PACKAGE_METADATA_NAME} is not valid JSON.") from error

    if not isinstance(metadata, dict):
        raise ValueError(f"{PACKAGE_METADATA_NAME} must contain a JSON object.")

    package_id = str(metadata.get("package_id", "")).strip()
    if package_id != PACKAGE_ID:
        raise ValueError(
            "The selected archive is not an Enhanced Overhaul Revamped package."
        )

    try:
        format_version = int(metadata.get("package_format_version"))
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{PACKAGE_METADATA_NAME} has an invalid package format version."
        ) from error
    if format_version != PACKAGE_FORMAT_VERSION:
        raise ValueError(
            "This package format is not supported by this launcher. "
            "Download the current launcher from Nexus Mods."
        )

    version = str(metadata.get("package_version", "")).strip()
    try:
        normalize_version(version)
    except ValueError as error:
        raise ValueError(
            f"{PACKAGE_METADATA_NAME} has an invalid mod version."
        ) from error

    package_kind = str(metadata.get("package_kind", "")).strip().casefold()
    if package_kind not in PACKAGE_KINDS:
        raise ValueError(
            f"{PACKAGE_METADATA_NAME} has an unsupported package type."
        )

    metadata["package_version"] = version
    metadata["package_kind"] = package_kind
    return metadata


def inspect_archive(archive_path: Path) -> PackageInfo:
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise FileNotFoundError("Select the mod release archive downloaded from Nexus.")

    with tempfile.TemporaryDirectory(prefix="EOR-Package-") as temporary:
        extraction_root = Path(temporary)
        file_count = _extract_archive(archive_path, extraction_root)
        payload_root = _find_payload_root(extraction_root)
        metadata = _read_package_metadata(payload_root)
        version = metadata["package_version"]
        package_kind_value = metadata["package_kind"]
        includes_bepinex = (payload_root / "winhttp.dll").is_file()
        if (package_kind_value == "full") != includes_bepinex:
            raise ValueError(
                f"{PACKAGE_METADATA_NAME} does not match the archive contents."
            )
        package_kind = PACKAGE_KINDS[package_kind_value]
        return PackageInfo(
            archive_path=archive_path,
            version=version,
            package_kind=package_kind,
            file_count=file_count,
        )


def install_archive(
    game_root: Path,
    archive_path: Path,
    vanilla_restore_root: Optional[Path] = None,
    progress: Optional[ProgressCallback] = None,
) -> InstallResult:
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise FileNotFoundError("Select the mod release archive downloaded from Nexus.")

    if progress:
        progress(f"Reading {archive_path.name}", 0, 1)
    with tempfile.TemporaryDirectory(prefix="EOR-Install-") as temporary:
        extraction_root = Path(temporary)
        _extract_archive(archive_path, extraction_root)
        payload_root = _find_payload_root(extraction_root)
        metadata = _read_package_metadata(payload_root)
        version = metadata["package_version"]

        is_full_package = (payload_root / "winhttp.dll").is_file()
        if (metadata["package_kind"] == "full") != is_full_package:
            raise ValueError(
                f"{PACKAGE_METADATA_NAME} does not match the archive contents."
            )
        existing_bepinex = (
            Path(game_root) / "BepInEx" / "core" / "BepInEx.dll"
        ).is_file()
        if not is_full_package and not existing_bepinex:
            raise RuntimeError(
                "This is the Mod Only package, but BepInEx is not installed. "
                "Select the full Enhanced Overhaul release instead."
            )

        manager = ModpackManager(
            game_root,
            payload_root,
            version,
            vanilla_restore_root,
        )
        return manager.install(progress)


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class ModpackManager:
    def __init__(
        self,
        game_root: Path,
        payload_root: Optional[Path] = None,
        package_version: str = "Unknown",
        vanilla_restore_root: Optional[Path] = None,
    ) -> None:
        self.game_root = Path(game_root)
        self.payload_root = Path(payload_root) if payload_root else None
        self.package_version = package_version
        self.vanilla_restore_root = (
            Path(vanilla_restore_root) if vanilla_restore_root else None
        )
        self.state_root = self.game_root / STATE_RELATIVE_PATH
        self.manifest_path = self.state_root / MANIFEST_NAME

    def validate(self) -> None:
        if not validate_game_directory(self.game_root):
            raise ValueError("Select the folder containing For The King II.exe.")
        if self.payload_root is None or not self.payload_root.is_dir():
            raise FileNotFoundError("The selected mod archive payload is missing.")
        if not (self.payload_root / PLUGIN_RELATIVE_PATH).is_file():
            raise FileNotFoundError("The selected archive's mod DLL is missing.")

    def load_manifest(self) -> Optional[dict]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Install manifest could not be read: {error}") from error
        if value.get("schema") != SCHEMA_VERSION:
            raise RuntimeError("The installed launcher manifest is not supported.")
        return value

    def detected_version(self) -> Optional[str]:
        manifest = self.load_manifest()
        if manifest:
            return str(manifest.get("package_version") or "Unknown")
        if (self.game_root / PLUGIN_RELATIVE_PATH).is_file():
            return "Existing/manual install"
        return None

    def _backup_for(
        self,
        relative_path: Path,
        destination: Path,
        backup_root: Path,
    ) -> tuple[str, Optional[str]]:
        if not destination.exists():
            return "created", None

        if self.payload_root is None:
            raise RuntimeError("No package payload is loaded.")
        source_hash = sha256_file(self.payload_root / relative_path)
        destination_hash = sha256_file(destination)

        if (
            str(relative_path).replace("\\", "/").casefold()
            == "for the king ii_data/streamingassets/assets/configs/json~/characters.json"
            and destination_hash == source_hash
            and self.vanilla_restore_root
        ):
            vanilla = self.vanilla_restore_root / relative_path
            if vanilla.is_file():
                backup = backup_root / relative_path
                _atomic_copy(vanilla, backup)
                return "replaced", str(backup.relative_to(self.state_root))

        if destination_hash == source_hash:
            return "shared", None

        backup = backup_root / relative_path
        _atomic_copy(destination, backup)
        return "replaced", str(backup.relative_to(self.state_root))

    def install(
        self,
        progress: Optional[ProgressCallback] = None,
    ) -> InstallResult:
        self.validate()
        old_manifest = self.load_manifest()
        old_entries = {
            entry["path"].casefold(): entry
            for entry in (old_manifest or {}).get("files", [])
        }
        install_id = time.strftime("%Y%m%d-%H%M%S")
        backup_root = self.state_root / "Backups" / install_id
        payload_files = _payload_files(self.payload_root)
        new_entries: list[dict] = []
        backed_up = 0

        for index, relative_path in enumerate(payload_files, start=1):
            relative_path = _safe_relative_path(relative_path)
            source = self.payload_root / relative_path
            destination = self.game_root / relative_path
            normalized_path = str(relative_path).replace("\\", "/")
            old_entry = old_entries.get(normalized_path.casefold())

            if progress:
                progress(f"Installing {relative_path.name}", index, len(payload_files))

            # Preserve an existing proxy loader. Security products commonly hold or
            # rescan this DLL, and an already working Doorstop/BepInEx loader does
            # not need to be replaced by every mod update.
            if normalized_path.casefold() == "winhttp.dll" and destination.is_file():
                if progress:
                    progress(
                        "Keeping existing winhttp.dll",
                        index,
                        len(payload_files),
                    )
                new_entries.append(
                    {
                        "path": normalized_path,
                        "sha256": (old_entry or {}).get("sha256"),
                        "size": destination.stat().st_size,
                        "ownership": (old_entry or {}).get("ownership", "shared"),
                        "backup": (old_entry or {}).get("backup"),
                        "verification": "presence-only",
                    }
                )
                continue

            if old_entry:
                ownership = old_entry.get("ownership", "created")
                backup = old_entry.get("backup")
            else:
                ownership, backup = self._backup_for(
                    relative_path, destination, backup_root
                )
                if backup:
                    backed_up += 1

            source_hash = sha256_file(source)
            destination_matches = (
                destination.is_file() and sha256_file(destination) == source_hash
            )
            if not destination_matches:
                try:
                    _atomic_copy(source, destination)
                except OSError as error:
                    raise OSError(
                        f"Could not install '{relative_path}' to "
                        f"'{destination}': {error}"
                    ) from error
            entry = {
                "path": normalized_path,
                "sha256": source_hash,
                "size": source.stat().st_size,
                "ownership": ownership,
                "backup": backup,
            }
            if normalized_path.casefold() == "winhttp.dll":
                entry["verification"] = "presence-only"
            new_entries.append(entry)

        new_paths = {entry["path"].casefold() for entry in new_entries}
        obsolete = [
            entry
            for key, entry in old_entries.items()
            if key not in new_paths
        ]
        removed_obsolete = self._remove_entries(obsolete, preserve_changes=True)[0]

        manifest = {
            "schema": SCHEMA_VERSION,
            "package_name": "Enhanced Overhaul Revamped",
            "package_version": self.package_version,
            "installed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": new_entries,
        }
        _write_json_atomic(self.manifest_path, manifest)
        return InstallResult(
            installed_files=len(new_entries),
            backed_up_files=backed_up,
            removed_obsolete_files=removed_obsolete,
            version=self.package_version,
        )

    def verify(
        self,
        progress: Optional[ProgressCallback] = None,
    ) -> VerificationResult:
        manifest = self.load_manifest()
        if not manifest:
            if (self.game_root / PLUGIN_RELATIVE_PATH).is_file():
                return VerificationResult(
                    expected=0,
                    valid=0,
                    missing=(),
                    modified=(),
                    extra_notes=(
                        "An existing manual installation was found, but it has no "
                        "launcher manifest. Use Install to adopt and verify it.",
                    ),
                )
            return VerificationResult(
                expected=0,
                valid=0,
                missing=(),
                modified=(),
                extra_notes=("The modpack is not installed by this launcher.",),
            )

        entries = manifest.get("files", [])
        missing: list[str] = []
        modified: list[str] = []
        valid = 0
        for index, entry in enumerate(entries, start=1):
            relative_path = _safe_relative_path(entry["path"])
            destination = self.game_root / relative_path
            if progress:
                progress(f"Verifying {relative_path.name}", index, len(entries))
            if not destination.is_file():
                missing.append(entry["path"])
                continue
            if (
                entry.get("verification") == "presence-only"
                or str(relative_path).replace("\\", "/").casefold() == "winhttp.dll"
            ):
                valid += 1
                continue
            if sha256_file(destination) != entry["sha256"]:
                modified.append(entry["path"])
                continue
            valid += 1

        return VerificationResult(
            expected=len(entries),
            valid=valid,
            missing=tuple(missing),
            modified=tuple(modified),
        )

    def _preserve_folder(self) -> Path:
        documents = Path.home() / "Documents"
        return (
            documents
            / "Enhanced Overhaul Preserved Files"
            / time.strftime("%Y%m%d-%H%M%S")
        )

    def _remove_entries(
        self,
        entries: Iterable[dict],
        preserve_changes: bool,
        progress: Optional[ProgressCallback] = None,
    ) -> tuple[int, int, int, Optional[Path]]:
        removed = 0
        restored = 0
        preserved = 0
        preserve_root: Optional[Path] = None

        reversed_entries = list(reversed(list(entries)))
        for index, entry in enumerate(reversed_entries, start=1):
            relative_path = _safe_relative_path(entry["path"])
            destination = self.game_root / relative_path
            backup_value = entry.get("backup")
            backup = self.state_root / backup_value if backup_value else None
            ownership = entry.get("ownership", "created")
            normalized_path = str(relative_path).replace("\\", "/").casefold()

            if progress:
                progress(
                    f"Removing {relative_path.name}",
                    index,
                    len(reversed_entries),
                )

            if normalized_path == "winhttp.dll":
                if ownership == "replaced" and backup and backup.is_file():
                    _atomic_copy(backup, destination)
                    restored += 1
                elif ownership == "created" and destination.is_file():
                    destination.unlink()
                    removed += 1
                continue

            if destination.is_file():
                current_hash = sha256_file(destination)
                changed = current_hash != entry.get("sha256")
                if changed and preserve_changes:
                    preserve_root = preserve_root or self._preserve_folder()
                    _atomic_copy(destination, preserve_root / relative_path)
                    preserved += 1

            if ownership == "replaced" and backup and backup.is_file():
                _atomic_copy(backup, destination)
                restored += 1
            elif ownership == "created":
                if destination.is_file():
                    destination.unlink()
                    removed += 1
            elif ownership == "shared":
                continue

        self._remove_empty_managed_directories()
        return removed, restored, preserved, preserve_root

    def _remove_empty_managed_directories(self) -> None:
        roots = (
            self.game_root / "BepInEx" / "plugins" / "EnhancedOverhaulRevamped",
            self.game_root / "BepInEx" / "plugins" / "FTK2_EnhancedMercenaries",
            self.game_root / "BepInEx" / "plugins" / "FTK2_EnhancedPets",
        )
        for root in roots:
            if not root.exists():
                continue
            directories = sorted(
                (path for path in root.rglob("*") if path.is_dir()),
                key=lambda value: len(value.parts),
                reverse=True,
            )
            directories.append(root)
            for directory in directories:
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def uninstall(
        self,
        progress: Optional[ProgressCallback] = None,
    ) -> UninstallResult:
        manifest = self.load_manifest()
        if not manifest:
            raise RuntimeError(
                "No launcher-managed installation was found. "
                "Install once before using launcher uninstall."
            )
        entries = manifest.get("files", [])
        if progress:
            progress("Preparing uninstall", 0, max(1, len(entries)))
        removed, restored, preserved, preserve_root = self._remove_entries(
            entries,
            preserve_changes=True,
            progress=progress,
        )

        if progress:
            progress("Cleaning launcher backup files", len(entries), len(entries))
        try:
            self.manifest_path.unlink()
        except FileNotFoundError:
            pass

        backup_root = self.state_root / "Backups"
        if backup_root.is_dir():
            shutil.rmtree(backup_root)
        try:
            self.state_root.rmdir()
        except OSError:
            pass

        return UninstallResult(
            removed_files=removed,
            restored_files=restored,
            preserved_files=preserved,
            preserve_folder=preserve_root,
        )


def temporary_fake_game(root: Path) -> Path:
    """Create the minimum valid game layout used by automated launcher tests."""
    game = Path(root) / GAME_NAME
    (game / "For The King II_Data").mkdir(parents=True)
    (game / GAME_EXE).write_bytes(b"test")
    return game
