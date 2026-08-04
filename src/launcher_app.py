from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher_core import (
    APP_NAME,
    GAME_EXE,
    NEXUS_FILES_URL,
    STEAM_APP_ID,
    ModpackManager,
    compare_versions,
    detect_game_directories,
    fetch_latest_version,
    inspect_archive,
    install_archive,
    is_game_running,
    validate_game_directory,
)


COLORS = {
    "ink": "#131310",
    "panel": "#1c1c18",
    "panel_alt": "#24231e",
    "line": "#3d392e",
    "gold": "#e1b957",
    "gold_bright": "#f0cc72",
    "gold_soft": "#a98b47",
    "cream": "#f1e8d2",
    "muted": "#aaa38f",
    "teal": "#59b8ab",
    "red": "#d56c5e",
    "green": "#77b77a",
}


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_metadata() -> dict:
    try:
        return json.loads(
            (resource_root() / "build_metadata.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return {"launcher_version": "1.0.0.0", "build_id": "development"}


class WorkerSignals(QObject):
    progress = Signal(str, int, int)
    success = Signal(object)
    failure = Signal(str, str)


class Worker(QRunnable):
    def __init__(self, operation: Callable) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.operation(self.signals.progress.emit)
            self.signals.success.emit(result)
        except Exception as error:
            self.signals.failure.emit(str(error), traceback.format_exc())


class LauncherWindow(QMainWindow):
    def __init__(self, check_updates: bool = True) -> None:
        super().__init__()
        self.metadata = load_metadata()
        self.launcher_version = str(
            self.metadata.get("launcher_version")
            or self.metadata.get("package_version")
            or "1.0.0.0"
        )
        self.settings_root = (
            Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "EnhancedOverhaulLauncher"
        )
        self.settings_path = self.settings_root / "settings.json"
        self.thread_pool = QThreadPool.globalInstance()
        self.busy = False
        self.latest_version: str | None = None
        self.selected_package_version: str | None = None
        self.action_buttons: list[QPushButton] = []
        self.active_workers: set[Worker] = set()

        self.setWindowTitle(APP_NAME)
        self.resize(1080, 680)
        self.setMinimumSize(940, 610)
        icon_path = resource_root() / "assets" / "launcher_icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build_ui(icon_path)
        self._load_initial_game_path()
        self._load_initial_archive()
        if check_updates:
            self._begin_update_check()

    def _build_ui(self, icon_path: Path) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(310)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(36, 34, 30, 30)
        sidebar_layout.setSpacing(0)

        if icon_path.is_file():
            logo = QLabel()
            pixmap = QPixmap(str(icon_path))
            logo.setPixmap(
                pixmap.scaled(
                    102,
                    102,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            logo.setFixedSize(108, 108)
            sidebar_layout.addWidget(logo)
            sidebar_layout.addSpacing(18)

        brand = QLabel("ENHANCED\nOVERHAUL")
        brand.setObjectName("brand")
        sidebar_layout.addWidget(brand)
        subtitle = QLabel("REVAMPED")
        subtitle.setObjectName("brandSubtitle")
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(26)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setObjectName("rule")
        sidebar_layout.addWidget(rule)
        sidebar_layout.addSpacing(24)

        privacy = QLabel(
            "A safe, local modpack manager.\n\n"
            "No automatic Nexus downloads.\n"
            "No saves or personal configs touched."
        )
        privacy.setObjectName("sidebarCopy")
        privacy.setWordWrap(True)
        sidebar_layout.addWidget(privacy)
        sidebar_layout.addStretch(1)

        update_card = QFrame()
        update_card.setObjectName("updateCard")
        update_layout = QVBoxLayout(update_card)
        update_layout.setContentsMargins(15, 13, 15, 13)
        update_layout.setSpacing(6)
        update_caption = QLabel("ONLINE VERSION")
        update_caption.setObjectName("eyebrow")
        update_layout.addWidget(update_caption)
        self.update_label = QLabel("Checking GitHub for updates...")
        self.update_label.setObjectName("updateText")
        self.update_label.setWordWrap(True)
        update_layout.addWidget(self.update_label)
        self.files_button = self._button(
            "OPEN NEXUS FILES", self._open_nexus, compact=True, muted=True
        )
        update_layout.addWidget(self.files_button)
        sidebar_layout.addWidget(update_card)
        shell.addWidget(sidebar)

        accent = QFrame()
        accent.setObjectName("accent")
        accent.setFixedWidth(4)
        shell.addWidget(accent)

        content = QFrame()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(44, 34, 44, 28)
        content_layout.setSpacing(0)

        eyebrow = QLabel("MODPACK CONTROL")
        eyebrow.setObjectName("eyebrow")
        content_layout.addWidget(eyebrow)
        title = QLabel("Ready the adventure.")
        title.setObjectName("pageTitle")
        content_layout.addWidget(title)
        content_layout.addSpacing(24)

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(24, 20, 24, 20)
        status_layout.setSpacing(0)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setFixedSize(12, 12)
        status_row.addWidget(self.status_dot)
        self.install_state = QLabel("Scanning installation...")
        self.install_state.setObjectName("statusTitle")
        status_row.addWidget(self.install_state)
        status_row.addStretch(1)
        self.package_label = QLabel("Mod archive  Not selected")
        self.package_label.setObjectName("versionText")
        status_row.addWidget(self.package_label)
        status_layout.addLayout(status_row)
        status_layout.addSpacing(10)

        self.detail_label = QLabel("Locating For The King II...")
        self.detail_label.setObjectName("detailText")
        self.detail_label.setWordWrap(True)
        status_layout.addWidget(self.detail_label)
        status_layout.addSpacing(16)

        self.progress = QProgressBar()
        self.progress.setObjectName("progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        status_layout.addWidget(self.progress)
        content_layout.addWidget(status_card)
        content_layout.addSpacing(24)

        path_caption = QLabel("GAME FOLDER")
        path_caption.setObjectName("eyebrow")
        content_layout.addWidget(path_caption)
        content_layout.addSpacing(7)
        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("pathEdit")
        self.path_edit.setPlaceholderText("Select the folder containing For The King II.exe")
        path_row.addWidget(self.path_edit, 1)
        browse = self._button("BROWSE", self._browse, compact=True)
        browse.setFixedWidth(102)
        path_row.addWidget(browse)
        content_layout.addLayout(path_row)
        content_layout.addSpacing(16)

        archive_caption = QLabel("DOWNLOADED MOD ARCHIVE")
        archive_caption.setObjectName("eyebrow")
        content_layout.addWidget(archive_caption)
        content_layout.addSpacing(7)
        archive_row = QHBoxLayout()
        archive_row.setSpacing(10)
        self.archive_edit = QLineEdit()
        self.archive_edit.setObjectName("pathEdit")
        self.archive_edit.setReadOnly(True)
        self.archive_edit.setPlaceholderText(
            "Select Release.7z or Release.zip downloaded from Nexus"
        )
        archive_row.addWidget(self.archive_edit, 1)
        archive_browse = self._button(
            "SELECT FILE", self._browse_archive, compact=True
        )
        archive_browse.setFixedWidth(102)
        archive_row.addWidget(archive_browse)
        content_layout.addLayout(archive_row)
        content_layout.addSpacing(22)

        actions = QHBoxLayout()
        actions.setSpacing(14)
        install = self._button(
            "INSTALL / UPDATE", self._install, primary=True
        )
        verify = self._button("VERIFY", self._verify)
        uninstall = self._button("UNINSTALL", self._uninstall, danger=True)
        actions.addWidget(install, 1)
        actions.addWidget(verify, 1)
        actions.addWidget(uninstall, 1)
        content_layout.addLayout(actions)
        content_layout.addSpacing(15)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        launch = self._button(
            "LAUNCH FOR THE KING II", self._launch_game, primary=True
        )
        folder = self._button(
            "OPEN MOD FOLDER", self._open_mod_folder, compact=True, muted=True
        )
        lower.addWidget(launch, 1)
        lower.addWidget(folder)
        content_layout.addLayout(lower)
        content_layout.addStretch(1)

        safety = QLabel(
            "Install replaces only packaged files and records every change. "
            "Uninstall restores replaced files and preserves user edits."
        )
        safety.setObjectName("safetyText")
        safety.setWordWrap(True)
        content_layout.addWidget(safety)
        shell.addWidget(content, 1)

        self.action_buttons = [
            install,
            verify,
            uninstall,
            launch,
            browse,
            archive_browse,
        ]
        self.setStyleSheet(self._stylesheet())

    def _button(
        self,
        text: str,
        callback: Callable,
        *,
        primary: bool = False,
        danger: bool = False,
        compact: bool = False,
        muted: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        if primary:
            button.setProperty("kind", "primary")
        elif danger:
            button.setProperty("kind", "danger")
        elif muted:
            button.setProperty("kind", "muted")
        else:
            button.setProperty("kind", "standard")
        button.setProperty("compact", compact)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(36 if compact else 48)
        return button

    def _stylesheet(self) -> str:
        return f"""
            QWidget#root, QFrame#content {{
                background: {COLORS["ink"]};
                color: {COLORS["cream"]};
            }}
            QFrame#sidebar {{
                background: #191914;
            }}
            QFrame#accent {{
                background: {COLORS["gold"]};
            }}
            QLabel#brand {{
                color: {COLORS["cream"]};
                font-family: Georgia;
                font-size: 27px;
                font-weight: 700;
                line-height: 0.9;
            }}
            QLabel#brandSubtitle {{
                color: {COLORS["gold"]};
                font-family: "Segoe UI";
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 2px;
            }}
            QLabel#sidebarCopy {{
                color: {COLORS["muted"]};
                font-family: "Segoe UI";
                font-size: 10px;
            }}
            QFrame#rule {{
                background: {COLORS["line"]};
                border: none;
                max-height: 1px;
            }}
            QFrame#updateCard, QFrame#statusCard {{
                background: {COLORS["panel"]};
                border: 1px solid {COLORS["line"]};
                border-radius: 4px;
            }}
            QFrame#updateCard {{
                background: {COLORS["panel_alt"]};
            }}
            QLabel#eyebrow {{
                color: {COLORS["gold_soft"]};
                font-family: "Segoe UI";
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#updateText {{
                color: {COLORS["cream"]};
                font-family: "Segoe UI";
                font-size: 10px;
            }}
            QLabel#pageTitle {{
                color: {COLORS["cream"]};
                font-family: Georgia;
                font-size: 29px;
                font-weight: 700;
            }}
            QLabel#statusTitle {{
                color: {COLORS["cream"]};
                font-family: Georgia;
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#versionText, QLabel#detailText {{
                color: {COLORS["muted"]};
                font-family: "Segoe UI";
                font-size: 10px;
            }}
            QLabel#statusDot {{
                background: {COLORS["gold_soft"]};
                border-radius: 6px;
            }}
            QLineEdit#pathEdit {{
                background: {COLORS["panel_alt"]};
                color: {COLORS["cream"]};
                border: 1px solid {COLORS["line"]};
                border-radius: 3px;
                padding: 11px 13px;
                font-family: Consolas;
                font-size: 10px;
                selection-background-color: {COLORS["gold_soft"]};
            }}
            QLineEdit#pathEdit:focus {{
                border-color: {COLORS["gold_soft"]};
            }}
            QProgressBar#progress {{
                background: {COLORS["ink"]};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar#progress::chunk {{
                background: {COLORS["gold"]};
                border-radius: 4px;
            }}
            QPushButton {{
                border: none;
                border-radius: 3px;
                padding: 11px 15px;
                font-family: "Segoe UI";
                font-size: 9px;
                font-weight: 700;
            }}
            QPushButton[kind="primary"] {{
                background: {COLORS["gold"]};
                color: {COLORS["ink"]};
            }}
            QPushButton[kind="primary"]:hover {{
                background: {COLORS["gold_bright"]};
            }}
            QPushButton[kind="standard"] {{
                background: #302f29;
                color: {COLORS["cream"]};
            }}
            QPushButton[kind="standard"]:hover {{
                background: #3b3931;
            }}
            QPushButton[kind="danger"] {{
                background: #4a2925;
                color: #f1c4bd;
            }}
            QPushButton[kind="danger"]:hover {{
                background: #61342e;
            }}
            QPushButton[kind="muted"] {{
                background: {COLORS["panel_alt"]};
                color: {COLORS["muted"]};
                border: 1px solid {COLORS["line"]};
            }}
            QPushButton[kind="muted"]:hover {{
                color: {COLORS["cream"]};
                border-color: {COLORS["gold_soft"]};
            }}
            QPushButton:disabled {{
                background: #24241f;
                color: #66645b;
            }}
            QLabel#safetyText {{
                color: #777365;
                font-family: "Segoe UI";
                font-size: 8px;
            }}
        """

    def _settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        try:
            self.settings_root.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "game_path": self.path_edit.text(),
                        "archive_path": self.archive_edit.text(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.settings_path)
        except OSError:
            pass

    def _load_initial_game_path(self) -> None:
        saved_value = self._settings().get("game_path")
        detected = detect_game_directories(Path(saved_value) if saved_value else None)
        if detected:
            self.path_edit.setText(str(detected[0]))
            self._save_settings()
            self._refresh_status()
        else:
            self._set_status(
                "Game folder required",
                "Automatic Steam detection did not find the game. Choose the "
                "folder containing For The King II.exe.",
                COLORS["red"],
            )

    def _load_initial_archive(self) -> None:
        archive_value = self._settings().get("archive_path")
        if archive_value and Path(archive_value).is_file():
            self.archive_edit.setText(str(archive_value))
            self.package_label.setText(f"Mod archive  {Path(archive_value).name}")
        else:
            self.package_label.setText("Mod archive  Not selected")

    def _manager(self) -> ModpackManager:
        path = Path(self.path_edit.text().strip())
        if not validate_game_directory(path):
            raise ValueError("Select the folder containing For The King II.exe.")
        return ModpackManager(path)

    def _set_status(self, title: str, detail: str, color: str) -> None:
        self.install_state.setText(title)
        self.detail_label.setText(detail)
        self.status_dot.setStyleSheet(
            f"background: {color}; border-radius: 6px;"
        )

    def _refresh_status(self) -> None:
        try:
            detected = self._manager().detected_version()
        except Exception as error:
            self._set_status("Game folder required", str(error), COLORS["red"])
            return
        if detected is None:
            self._set_status(
                "Modpack not installed",
                "Select the mod archive downloaded from Nexus, then choose "
                "Install / Update.",
                COLORS["gold_soft"],
            )
        elif detected == "Existing/manual install":
            self._set_status(
                "Existing mod detected",
                "Install once to adopt this folder and create a verification manifest.",
                COLORS["gold"],
            )
        else:
            self._set_status(
                "Modpack installed",
                f"Installed version {detected}. Use Verify to check every managed file.",
                COLORS["green"],
            )

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select the For The King II folder",
            self.path_edit.text() or str(Path.home()),
        )
        if not selected:
            return
        if not validate_game_directory(Path(selected)):
            QMessageBox.critical(
                self,
                "Invalid game folder",
                "Select the folder that contains For The King II.exe.",
            )
            return
        self.path_edit.setText(selected)
        self._save_settings()
        self._refresh_status()

    def _browse_archive(self) -> None:
        starting_path = self.archive_edit.text()
        if not starting_path:
            downloads = Path.home() / "Downloads"
            starting_path = str(downloads if downloads.is_dir() else Path.home())
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Select the Enhanced Overhaul release archive",
            starting_path,
            "Enhanced Overhaul Releases (*.7z *.zip);;7-Zip Archives (*.7z);;"
            "ZIP Archives (*.zip)",
        )
        if not selected:
            return
        self.archive_edit.setText(selected)
        self.selected_package_version = None
        self.package_label.setText(f"Checking  {Path(selected).name}")
        self._save_settings()
        self._inspect_selected_archive(Path(selected))

    def _inspect_selected_archive(self, archive_path: Path) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.detail_label.setText("Validating the selected mod archive...")
        worker = Worker(lambda _progress: inspect_archive(archive_path))
        worker.signals.success.connect(self._archive_inspected)
        worker.signals.failure.connect(self._archive_inspection_failed)
        self._queue_worker(worker)

    def _archive_inspected(self, info) -> None:
        self._set_busy(False)
        self.selected_package_version = info.version
        self.package_label.setText(f"Selected version  {info.version}")
        self.detail_label.setText(
            f"{info.package_kind} package selected: {info.archive_path.name} "
            f"({info.file_count} files)."
        )

    def _archive_inspection_failed(self, message: str, trace: str) -> None:
        self._set_busy(False)
        self.selected_package_version = None
        self.package_label.setText("Mod archive  Invalid")
        QMessageBox.critical(self, "Invalid mod archive", message)
        try:
            self.settings_root.mkdir(parents=True, exist_ok=True)
            (self.settings_root / "launcher_error.log").write_text(
                trace, encoding="utf-8"
            )
        except OSError:
            pass

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        for button in self.action_buttons:
            button.setEnabled(not value)
        if not value:
            self.progress.setValue(0)

    def _run_task(
        self,
        label: str,
        operation: Callable,
        success_handler: Callable,
    ) -> None:
        if self.busy:
            return
        try:
            manager = self._manager()
        except Exception as error:
            QMessageBox.critical(self, "Cannot continue", str(error))
            return
        if is_game_running():
            QMessageBox.warning(
                self,
                "Game is running",
                "Close For The King II before changing or verifying mod files.",
            )
            return

        self._set_busy(True)
        self.detail_label.setText(label)
        worker = Worker(lambda progress: operation(manager, progress))
        worker.signals.progress.connect(self._on_progress)
        worker.signals.success.connect(
            lambda result: self._task_success(result, success_handler)
        )
        worker.signals.failure.connect(self._task_failure)
        self._queue_worker(worker)

    def _queue_worker(self, worker: Worker) -> None:
        worker.setAutoDelete(False)
        self.active_workers.add(worker)
        worker.signals.success.connect(
            lambda _result, active=worker: self.active_workers.discard(active)
        )
        worker.signals.failure.connect(
            lambda _message, _trace, active=worker: self.active_workers.discard(active)
        )
        self.thread_pool.start(worker)

    def _on_progress(self, label: str, current: int, total: int) -> None:
        self.detail_label.setText(label)
        self.progress.setValue(0 if total <= 0 else int((current / total) * 100))

    def _task_success(self, result, handler: Callable) -> None:
        self._set_busy(False)
        try:
            handler(result)
        except Exception as error:
            self._task_failure(str(error), traceback.format_exc())

    def _task_failure(self, message: str, trace: str) -> None:
        self._set_busy(False)
        self._set_status("Operation failed", message, COLORS["red"])
        QMessageBox.critical(
            self,
            "Operation failed",
            f"{message}\n\nIf the game is installed under Program Files, "
            "run the launcher as administrator.",
        )
        try:
            self.settings_root.mkdir(parents=True, exist_ok=True)
            (self.settings_root / "launcher_error.log").write_text(
                trace, encoding="utf-8"
            )
        except OSError:
            pass

    def _install(self) -> None:
        archive_path = Path(self.archive_edit.text().strip())
        if not archive_path.is_file():
            QMessageBox.warning(
                self,
                "Select the downloaded mod",
                "Choose the Enhanced Overhaul .7z or .zip file you downloaded "
                "from the Nexus Files page first.",
            )
            self._browse_archive()
            return
        self._run_task(
            f"Reading {archive_path.name}...",
            lambda manager, progress: install_archive(
                manager.game_root,
                archive_path,
                None,
                progress,
            ),
            self._install_complete,
        )

    def _install_complete(self, result) -> None:
        self._save_settings()
        self.selected_package_version = result.version
        self.package_label.setText(f"Selected version  {result.version}")
        self._refresh_status()
        QMessageBox.information(
            self,
            "Installation complete",
            f"Enhanced Overhaul Revamped {result.version} is installed.\n\n"
            f"Files installed: {result.installed_files}\n"
            f"Original files backed up: {result.backed_up_files}\n\n"
            "For multiplayer, every player must use the same release.",
        )

    def _verify(self) -> None:
        try:
            manager = self._manager()
            if manager.load_manifest() is None:
                self._verify_complete(manager.verify())
                return
        except Exception as error:
            QMessageBox.critical(self, "Cannot verify", str(error))
            return
        self._run_task(
            "Checking every managed file...",
            lambda manager, progress: manager.verify(progress),
            self._verify_complete,
        )

    def _verify_complete(self, result) -> None:
        if result.healthy:
            self._set_status(
                "Verified and ready",
                f"All {result.valid} managed files match the installed manifest.",
                COLORS["teal"],
            )
            QMessageBox.information(
                self,
                "Verification passed",
                f"All {result.valid} modpack files are present and correct.",
            )
            return
        if result.expected == 0:
            QMessageBox.warning(
                self, "Verification unavailable", "\n".join(result.extra_notes)
            )
            self._refresh_status()
            return
        self._set_status(
            "Repair recommended",
            f"{len(result.missing)} missing and {len(result.modified)} modified files.",
            COLORS["red"],
        )
        QMessageBox.warning(
            self,
            "Verification found problems",
            f"Valid: {result.valid} / {result.expected}\n"
            f"Missing: {len(result.missing)}\n"
            f"Modified: {len(result.modified)}\n\n"
            "Use Install / Update to repair the modpack.",
        )

    def _uninstall(self) -> None:
        try:
            manager = self._manager()
            if manager.load_manifest() is None:
                QMessageBox.warning(
                    self,
                    "Nothing to uninstall",
                    "No launcher-managed installation was found. The launcher "
                    "will not remove files it did not install.",
                )
                self._refresh_status()
                return
        except Exception as error:
            QMessageBox.critical(self, "Cannot uninstall", str(error))
            return
        answer = QMessageBox.question(
            self,
            "Uninstall modpack?",
            "This removes launcher-managed mod files and restores files that "
            "were replaced during installation.\n\n"
            "Saves, BepInEx configs, logs, and reports are not removed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_task(
            "Removing managed files and restoring originals...",
            lambda manager, progress: manager.uninstall(progress),
            self._uninstall_complete,
        )

    def _uninstall_complete(self, result) -> None:
        self._refresh_status()
        preservation = ""
        if result.preserved_files and result.preserve_folder:
            preservation = (
                f"\n\nPreserved {result.preserved_files} changed file(s) at:\n"
                f"{result.preserve_folder}"
            )
        QMessageBox.information(
            self,
            "Uninstall complete",
            f"Removed files: {result.removed_files}\n"
            f"Restored original files: {result.restored_files}"
            f"{preservation}",
        )

    def _launch_game(self) -> None:
        try:
            self._manager()
        except Exception as error:
            QMessageBox.critical(self, "Cannot launch", str(error))
            return
        try:
            os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
        except OSError:
            executable = Path(self.path_edit.text()) / GAME_EXE
            subprocess.Popen([str(executable)], cwd=str(executable.parent))

    def _open_mod_folder(self) -> None:
        path = Path(self.path_edit.text()) / "BepInEx" / "plugins"
        if not path.is_dir():
            path = Path(self.path_edit.text())
        try:
            os.startfile(str(path))
        except OSError as error:
            QMessageBox.critical(self, "Unable to open folder", str(error))

    def _open_nexus(self) -> None:
        QDesktopServices.openUrl(QUrl(NEXUS_FILES_URL))

    def _begin_update_check(self) -> None:
        worker = Worker(lambda _progress: fetch_latest_version())
        worker.signals.success.connect(self._handle_update)
        worker.signals.failure.connect(
            lambda _message, _trace: self.update_label.setText(
                "Could not check right now.\nThe launcher still works offline."
            )
        )
        self._queue_worker(worker)

    def closeEvent(self, event) -> None:
        if self.busy:
            QMessageBox.information(
                self,
                "Operation in progress",
                "Wait for the current file operation to finish before closing "
                "the launcher.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _handle_update(self, latest: str) -> None:
        self.latest_version = latest
        local_version = None
        try:
            detected = self._manager().detected_version()
            if detected and detected != "Existing/manual install":
                compare_versions(detected, latest)
                local_version = detected
        except (ValueError, OSError, RuntimeError):
            pass
        if local_version is None and self.selected_package_version:
            try:
                compare_versions(self.selected_package_version, latest)
                local_version = self.selected_package_version
            except ValueError:
                pass

        if local_version is None:
            self.update_label.setText(f"Latest available\nVersion {latest}")
            return

        comparison = compare_versions(latest, local_version)
        if comparison > 0:
            self.update_label.setText(
                f"Update available\n{local_version}  ->  {latest}"
            )
            answer = QMessageBox.question(
                self,
                "Enhanced Overhaul update available",
                f"Version {latest} is available. Your local version is "
                f"{local_version}.\n\n"
                "Open the Nexus Files page to manually download the latest "
                "launcher/modpack?\n\n"
                "The launcher does not download Nexus files automatically.",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._open_nexus()
        elif comparison < 0:
            self.update_label.setText(
                f"Experimental build\nLocal {local_version}"
            )
        else:
            self.update_label.setText(
                f"Up to date\nVersion {local_version}"
            )


def smoke_test() -> int:
    smoke_log = Path(tempfile.gettempdir()) / "EORLauncherSmoke.log"
    try:
        smoke_log.unlink()
    except FileNotFoundError:
        pass

    def note(value: str) -> None:
        with smoke_log.open("a", encoding="utf-8") as stream:
            stream.write(value + "\n")

    try:
        note("started")
        required = (
            resource_root() / "build_metadata.json",
            resource_root() / "assets" / "launcher_icon.png",
            resource_root() / "tools" / "7z.exe",
            resource_root() / "tools" / "7z.dll",
        )
        if any(not path.is_file() for path in required):
            note("required resource missing")
            return 2
        note("resources verified")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(sys.argv)
        window = LauncherWindow(check_updates=False)
        window.close()
        app.processEvents()
        note("passed")
        return 0
    except Exception:
        note(traceback.format_exc())
        return 3


def archive_smoke_test(archive_path: str) -> int:
    smoke_log = Path(tempfile.gettempdir()) / "EORLauncherArchiveSmoke.log"
    try:
        info = inspect_archive(Path(archive_path))
        smoke_log.write_text(
            f"passed version={info.version} files={info.file_count} "
            f"kind={info.package_kind}\n",
            encoding="utf-8",
        )
        return 0
    except Exception:
        smoke_log.write_text(traceback.format_exc(), encoding="utf-8")
        return 4


def main() -> int:
    if "--smoke-test" in sys.argv:
        return smoke_test()
    if "--archive-smoke-test" in sys.argv:
        index = sys.argv.index("--archive-smoke-test")
        if index + 1 >= len(sys.argv):
            return 4
        return archive_smoke_test(sys.argv[index + 1])
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Enhanced Overhaul Revamped")
    app.setFont(QFont("Segoe UI", 10))
    window = LauncherWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
