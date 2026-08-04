from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from launcher_core import (
    GAME_EXE,
    ModpackManager,
    compare_versions,
    inspect_archive,
    install_archive,
    sha256_file,
    temporary_fake_game,
    validate_game_directory,
)
from launcher_app import LauncherWindow


class VersionTests(unittest.TestCase):
    def test_four_part_versions(self) -> None:
        self.assertEqual(compare_versions("0.7.0.61", "0.7.0.60"), 1)
        self.assertEqual(compare_versions("0.7.0.60", "0.7.0.60"), 0)
        self.assertEqual(compare_versions("0.7.0.9", "0.7.0.60"), -1)

    def test_version_text_is_tolerated(self) -> None:
        self.assertEqual(compare_versions("Version 0.7.1", "0.7.0.60"), 1)


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = temporary_fake_game(self.root)
        self.payload = self.root / "payload"
        self.plugin = (
            self.payload / "BepInEx" / "plugins" / "EnhancedOverhaulRemix.dll"
        )
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_bytes(b"version-one")
        self.data_file = (
            self.payload
            / "BepInEx"
            / "plugins"
            / "EnhancedOverhaulRevamped"
            / "Localization"
            / "en.json"
        )
        self.data_file.parent.mkdir(parents=True)
        self.data_file.write_text('{"hello":"world"}', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_game_validation(self) -> None:
        self.assertTrue(validate_game_directory(self.game))
        (self.game / GAME_EXE).unlink()
        self.assertFalse(validate_game_directory(self.game))

    def test_install_verify_and_uninstall(self) -> None:
        manager = ModpackManager(self.game, self.payload, "0.7.0.60")
        install = manager.install()
        self.assertEqual(install.installed_files, 2)
        self.assertTrue(manager.verify().healthy)
        self.assertEqual(
            sha256_file(self.game / self.plugin.relative_to(self.payload)),
            sha256_file(self.plugin),
        )

        uninstall = manager.uninstall()
        self.assertEqual(uninstall.removed_files, 2)
        self.assertFalse(
            (self.game / self.plugin.relative_to(self.payload)).exists()
        )

    def test_replaced_file_is_restored(self) -> None:
        destination = self.game / self.data_file.relative_to(self.payload)
        destination.parent.mkdir(parents=True)
        destination.write_text('{"user":"original"}', encoding="utf-8")

        manager = ModpackManager(self.game, self.payload, "0.7.0.60")
        manager.install()
        self.assertEqual(
            destination.read_text(encoding="utf-8"), '{"hello":"world"}'
        )
        manager.uninstall()
        self.assertEqual(
            destination.read_text(encoding="utf-8"), '{"user":"original"}'
        )

    def test_verify_detects_modified_file(self) -> None:
        manager = ModpackManager(self.game, self.payload, "0.7.0.60")
        manager.install()
        destination = self.game / self.plugin.relative_to(self.payload)
        destination.write_bytes(b"changed")
        result = manager.verify()
        self.assertFalse(result.healthy)
        self.assertEqual(len(result.modified), 1)

    def test_downloaded_zip_is_inspected_and_installed(self) -> None:
        metadata = self.payload / "EOR_PACKAGE.json"
        metadata.write_text(
            json.dumps(
                {
                    "package_id": "sirpepperpot.enhanced-overhaul-revamped",
                    "package_format_version": 1,
                    "package_name": "Enhanced Overhaul Revamped",
                    "package_version": "0.7.0.61",
                    "package_kind": "mod-only",
                }
            ),
            encoding="utf-8",
        )
        (self.game / "BepInEx" / "core").mkdir(parents=True)
        (self.game / "BepInEx" / "core" / "BepInEx.dll").write_bytes(b"core")
        archive_path = self.root / "EnhancedOverhaul_0.7.0.61.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for source in self.payload.rglob("*"):
                if source.is_file():
                    archive.write(source, source.relative_to(self.payload))

        info = inspect_archive(archive_path)
        self.assertEqual(info.version, "0.7.0.61")
        self.assertEqual(info.package_kind, "Mod only")

        result = install_archive(self.game, archive_path)
        self.assertEqual(result.version, "0.7.0.61")
        manager = ModpackManager(self.game)
        self.assertTrue(manager.verify().healthy)

    def test_archive_without_package_metadata_is_rejected(self) -> None:
        archive_path = self.root / "LegacyRelease.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for source in self.payload.rglob("*"):
                if source.is_file():
                    archive.write(source, source.relative_to(self.payload))

        with self.assertRaisesRegex(ValueError, "EOR_PACKAGE.json is missing"):
            inspect_archive(archive_path)

    def test_identical_framework_file_is_not_rewritten(self) -> None:
        framework = self.payload / "winhttp.dll"
        framework.write_bytes(b"same-framework")
        destination = self.game / "winhttp.dll"
        destination.write_bytes(b"same-framework")
        before = destination.stat().st_mtime_ns

        manager = ModpackManager(self.game, self.payload, "0.7.0.60")
        manager.install()
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_created_winhttp_uses_presence_verification_and_uninstalls(self) -> None:
        framework = self.payload / "winhttp.dll"
        framework.write_bytes(b"launcher-created-framework")
        manager = ModpackManager(self.game, self.payload, "0.7.0.60")
        manager.install()

        manifest = manager.load_manifest()
        entry = next(item for item in manifest["files"] if item["path"] == "winhttp.dll")
        self.assertEqual(entry["verification"], "presence-only")
        self.assertTrue(manager.verify().healthy)

        manager.uninstall()
        self.assertFalse((self.game / "winhttp.dll").exists())


class WindowOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = temporary_fake_game(self.root)
        self.window = LauncherWindow(check_updates=False)
        self.window.path_edit.setText(str(self.game))

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _wait_for_idle(self) -> None:
        deadline = time.monotonic() + 5
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.window.busy)

    def test_verify_without_manifest_finishes_synchronously(self) -> None:
        with patch.object(
            QMessageBox,
            "warning",
            return_value=QMessageBox.StandardButton.Ok,
        ):
            self.window._verify()
        self.assertFalse(self.window.busy)
        self.assertFalse(self.window.active_workers)

    def test_verify_worker_is_retained_until_completion(self) -> None:
        payload = self.root / "payload"
        plugin = payload / "BepInEx" / "plugins" / "EnhancedOverhaulRemix.dll"
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b"plugin")
        ModpackManager(self.game, payload, "0.7.0.60").install()

        with patch.object(
            QMessageBox,
            "information",
            return_value=QMessageBox.StandardButton.Ok,
        ):
            self.window._verify()
            self.assertTrue(self.window.active_workers)
            self._wait_for_idle()
        self.assertFalse(self.window.active_workers)

    def test_uninstall_worker_finishes_and_keeps_window_alive(self) -> None:
        payload = self.root / "payload"
        plugin = payload / "BepInEx" / "plugins" / "EnhancedOverhaulRemix.dll"
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b"plugin")
        (payload / "winhttp.dll").write_bytes(b"proxy")
        ModpackManager(self.game, payload, "0.7.0.60").install()

        with (
            patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(
                QMessageBox,
                "information",
                return_value=QMessageBox.StandardButton.Ok,
            ),
        ):
            self.window._uninstall()
            self.assertTrue(self.window.active_workers)
            self._wait_for_idle()
        self.assertFalse(self.window.active_workers)
        self.assertIsNotNone(self.window.centralWidget())


if __name__ == "__main__":
    unittest.main(verbosity=2)
