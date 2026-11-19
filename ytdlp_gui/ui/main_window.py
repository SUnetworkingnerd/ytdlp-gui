"""Main window: Download, Queue, Options and Settings tabs."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import replace

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QProgressDialog, QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core.archive import ArchiveSettings, build_archive
from ..core import signin
from ..core.cookies import BROWSERS, cookie_args
from ..core.metadata import LIVE_HINTS, PASS_THROUGH, fetch_info
from ..core import queue_store
from ..core.quality import QUALITIES, is_audio_preset, max_height, quality_args, quality_note
from ..core.formats import format_kind, format_string, pick_formats
from ..core.runner import (
    DONE, DONE_WITH_ERRORS, FAILED, FINISHED, FORCE_KILL_AFTER, RUNNER_ARGS, Job,
    base_command, clean_old_files, upgrade_runner_args, display_command, find_tool, fmt_bytes,
    YTDLP_SPEC, fmt_eta, hidden_run_kwargs, is_frozen, jobs_to_start, max_speed_settings,
)
from ..core.version import installed_version, is_outdated, latest_release, pip_update_commands
from ..core.theme import THEMES, normalize
from .archive_dialog import ArchiveDialog
from .options_panel import OptionsPanel
from .signin_dialog import SignInDialog
from .theme import apply_theme, is_dark

PRESETS = [
    ("Default (yt-dlp picks the best video + audio)", []),
    ("MP4 video  (-t mp4)", ["-t", "mp4"]),
    ("MKV video  (-t mkv)", ["-t", "mkv"]),
    ("MP3 audio  (-t mp3)", ["-t", "mp3"]),
    ("AAC audio  (-t aac)", ["-t", "aac"]),
]
LOG_MAX_AGE = 14 * 24 * 3600   # job logs on disk are deleted after two weeks

# Lines that name the file a job produced (last one wins: merge / convert come after download).
_OUTPUT_LINE = re.compile(
    r'^\[download\] Destination: (?P<a>.+)$'
    r'|^\[Merger\] Merging formats into "(?P<b>.+)"$'
    r'|^\[ExtractAudio\] Destination: (?P<c>.+)$'
    r'|^\[download\] (?P<d>.+) has already been downloaded$'
    r'|^\[\w+\] .*; Destination: (?P<e>.+)$')

FORMAT_HEADERS = ["ID", "Ext", "Resolution", "FPS", "Video codec", "Audio codec",
                  "Size", "Bitrate", "Note"]


class Bridge(QObject):
    """Carries results from worker threads to the UI thread."""
    line = Signal(object, str)
    progress = Signal(object, dict)
    state = Signal(object, str)
    info = Signal(object)
    update_output = Signal(str)
    versions = Signal(str, str)       # installed, latest ("" when unknown)


def _codec(value) -> str:
    return "" if value is None else ("-" if value == "none" else str(value))


class MainWindow(QMainWindow):
    def __init__(self, model):
        super().__init__()
        self.setWindowTitle("yt-dlp GUI")
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        self.resize(min(1150, avail.width() - 40) if avail else 1150,
                    min(840, avail.height() - 60) if avail else 840)
        self.setMinimumSize(760, 520)
        self.settings = QSettings("ytdlp-gui", "ytdlp-gui")
        self.bridge = Bridge()
        self.bridge.line.connect(self._on_line)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.state.connect(self._on_state)
        self.bridge.info.connect(self._on_info)
        self.bridge.update_output.connect(self._on_update_output)
        self.bridge.versions.connect(self._on_versions)
        self.items: dict[Job, tuple] = {}
        self.retired: list[Job] = []      # removed from the list but still shutting down
        self._ready_to_close = False
        self._closing = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save_settings)
        self.log_dir = os.path.join(signin.data_dir(), "logs")
        clean_old_files(self.log_dir, LOG_MAX_AGE)

        self.options = OptionsPanel(model)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_download_tab(), "Download")
        self.tabs.addTab(self._build_queue_tab(), "Queue")
        self.tabs.addTab(self._build_options_tab(), "Options")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.theme_button = QToolButton()
        self.theme_button.setText("Dark mode")
        self.theme_button.setCheckable(True)
        self.theme_button.setAutoRaise(True)
        self.theme_button.setToolTip("Switch between dark and light mode")
        self.theme_button.clicked.connect(self._toggle_theme)
        self.tabs.setCornerWidget(self.theme_button, Qt.TopRightCorner)
        self.theme_button.setChecked(is_dark(QApplication.instance()))

        self.options.changed.connect(self._update_preview)
        self._restore()
        self._update_preview()
        self._restore_queue()
        self.setAcceptDrops(True)
        if self.check_updates.isChecked():
            self._check_versions(latest=True)
        else:
            self._check_versions(latest=False)

    # ------------------------------------------------------------ Download tab
    @staticmethod
    def _pad(layout, margin: int = 14, spacing: int = 10) -> None:
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(spacing)

    def _build_download_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        self._pad(col)

        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("Paste one URL per line (video, playlist or live stream)")
        self.urls.setFixedHeight(92)
        self.urls.textChanged.connect(self._update_preview)
        col.addWidget(self.urls)

        fetch_row = QHBoxLayout()
        self.fetch_btn = QPushButton("Fetch info and formats")
        self.fetch_btn.clicked.connect(self._fetch)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        fetch_row.addWidget(self.fetch_btn)
        fetch_row.addWidget(self.info_label, 1)
        col.addLayout(fetch_row)

        self.formats = QTableWidget(0, len(FORMAT_HEADERS))
        self.formats.setHorizontalHeaderLabels(FORMAT_HEADERS)
        self.formats.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formats.setSelectionMode(QAbstractItemView.MultiSelection)
        self.formats.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formats.horizontalHeader().setStretchLastSection(True)
        self.formats.verticalHeader().setVisible(False)
        self.formats.setMinimumHeight(170)
        self.formats.itemSelectionChanged.connect(self._formats_selected)
        col.addWidget(self.formats, 1)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        self.preset = QComboBox()
        for label, _args in PRESETS:
            self.preset.addItem(label)
        self.preset.currentIndexChanged.connect(self._update_preview)
        self.preset.currentIndexChanged.connect(self._quality_changed)
        self.quality = QComboBox()
        for label, _height, _args in QUALITIES:
            self.quality.addItem(label)
        self.quality.setToolTip("Highest resolution to download. Lower qualities are used "
                                "when a video doesn't have the one you pick.")
        self.quality.currentIndexChanged.connect(self._update_preview)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        self.quality_hint = QLabel("")
        self.quality_hint.setWordWrap(True)
        self.quality_hint.setStyleSheet("color: gray;")
        self.quality_hint.setVisible(False)
        self._available_height = 0
        self.urls.textChanged.connect(self._forget_fetched_height)
        self.format_edit = QLineEdit()
        self.format_edit.setPlaceholderText(
            "-f format string (click rows above, e.g. 137+140) or leave empty")
        self.format_edit.textChanged.connect(self._update_preview)
        self.folder_edit = QLineEdit(os.path.expanduser("~/Downloads"))
        self.folder_edit.textChanged.connect(self._update_preview)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Preset", self.preset)
        form.addRow("Quality", self.quality)
        form.addRow("", self.quality_hint)
        form.addRow("Format (-f)", self.format_edit)
        form.addRow("Save to (-P)", folder_row)
        col.addLayout(form)

        cookies = QGroupBox("Cookies / login")
        cookies_col = QVBoxLayout(cookies)
        hint = QLabel(
            "Use this for age-restricted or members-only videos, or when a site says "
            "\"sign in to confirm you're not a bot\". Cookies stay on your computer, but "
            "they give access to your account, so never share a cookies file.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        self.cookie_none = QRadioButton("Don't use cookies")
        self.cookie_app = QRadioButton("Sign in to YouTube in this app (recommended)")
        self.cookie_browser = QRadioButton("Use my browser's login")
        self.cookie_file = QRadioButton("Use a cookies.txt file")
        self.cookie_none.setChecked(True)
        self.cookie_browser_box = QComboBox()
        self.cookie_browser_box.addItems(BROWSERS)
        self.cookie_profile = QLineEdit()
        self.cookie_profile.setPlaceholderText("profile name or path (optional)")
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("path to cookies.txt (Netscape format)")
        cookie_pick = QPushButton("Browse...")
        cookie_pick.clicked.connect(self._browse_cookies)
        self.signin_status = QLabel("")
        self.signin_btn = QPushButton("Sign in...")
        self.signin_btn.clicked.connect(self._signin_open)
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.clicked.connect(self._signin_out)
        app_row = QHBoxLayout()
        app_row.addSpacing(24)
        app_row.addWidget(self.signin_status, 1)
        app_row.addWidget(self.signin_btn)
        app_row.addWidget(self.signout_btn)
        browser_row = QHBoxLayout()
        browser_row.addSpacing(24)
        browser_row.addWidget(self.cookie_browser_box)
        browser_row.addWidget(self.cookie_profile, 1)
        file_row = QHBoxLayout()
        file_row.addSpacing(24)
        file_row.addWidget(self.cookie_file_edit, 1)
        file_row.addWidget(cookie_pick)
        self.cookie_note = QLabel("")
        self.cookie_note.setWordWrap(True)
        self.cookie_note.setStyleSheet("color: gray;")
        for widget in (hint, self.cookie_none, self.cookie_app):
            cookies_col.addWidget(widget)
        cookies_col.addLayout(app_row)
        cookies_col.addWidget(self.cookie_browser)
        cookies_col.addLayout(browser_row)
        cookies_col.addWidget(self.cookie_file)
        cookies_col.addLayout(file_row)
        cookies_col.addWidget(self.cookie_note)
        self.cookie_none.toggled.connect(self._cookie_mode_changed)
        self.cookie_app.toggled.connect(self._cookie_mode_changed)
        self.cookie_browser.toggled.connect(self._cookie_mode_changed)
        self.cookie_file.toggled.connect(self._cookie_mode_changed)
        self.cookie_browser_box.currentIndexChanged.connect(self._update_preview)
        self.cookie_profile.textChanged.connect(self._update_preview)
        self.cookie_file_edit.textChanged.connect(self._update_preview)
        self._cookie_mode_changed()
        self._signin_refresh()
        boxes = QHBoxLayout()
        boxes.setSpacing(12)
        boxes.addWidget(cookies, 3)

        live = QGroupBox("Live and scheduled streams")
        live_col = QVBoxLayout(live)
        self.live_start = QCheckBox(
            "Record from the beginning of the stream   (--live-from-start)")
        self.live_start.toggled.connect(self._update_preview)
        wait_row = QHBoxLayout()
        self.wait_chk = QCheckBox("Wait for a scheduled stream to start   (--wait-for-video)")
        self.wait_chk.toggled.connect(self._update_preview)
        self.wait_min = QSpinBox()
        self.wait_min.setRange(1, 86400)
        self.wait_min.setValue(60)
        self.wait_min.setSuffix(" s min")
        self.wait_max = QSpinBox()
        self.wait_max.setRange(0, 86400)
        self.wait_max.setSpecialValueText("no max")
        self.wait_max.setSuffix(" s max")
        for spin in (self.wait_min, self.wait_max):
            spin.valueChanged.connect(self._update_preview)
        wait_row.addSpacing(24)
        wait_row.addWidget(self.wait_min)
        wait_row.addWidget(self.wait_max)
        wait_row.addStretch(1)
        self.live_hint = QLabel("Without --live-from-start, yt-dlp records a live "
                                "stream from the moment you start (--no-live-from-start).")
        self.live_hint.setWordWrap(True)
        live_col.addWidget(self.live_start)
        live_col.addWidget(self.wait_chk)
        live_col.addLayout(wait_row)
        live_col.addWidget(self.live_hint)
        live_col.addStretch(1)
        boxes.addWidget(live, 2)
        col.addLayout(boxes)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(72)
        col.addWidget(QLabel("Command preview"))
        col.addWidget(self.preview)

        go = QPushButton("Download")
        go.clicked.connect(self._enqueue)
        archive = QPushButton("Archive full channels...")
        archive.setToolTip("Download everything one or more channels have published, resumable")
        archive.clicked.connect(self._open_archive)
        buttons = QHBoxLayout()
        buttons.addWidget(go, 2)
        buttons.addWidget(archive, 1)
        buttons.setSpacing(10)
        col.addLayout(buttons)
        # Scrollable, so small windows scroll instead of squashing the formats table.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save downloads to", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def _cookie_mode(self) -> str:
        if self.cookie_app.isChecked():
            return "app"
        if self.cookie_browser.isChecked():
            return "browser"
        return "file" if self.cookie_file.isChecked() else "none"

    def _cookie_mode_changed(self) -> None:
        mode = self._cookie_mode()
        self.cookie_browser_box.setEnabled(mode == "browser")
        self.cookie_profile.setEnabled(mode == "browser")
        self.cookie_file_edit.setEnabled(mode == "file")
        self.cookie_note.setText({
            "app": "Uses the login saved by \"Sign in...\". If YouTube starts asking you to "
                   "sign in again, or says the cookies are no longer valid, sign in again.",
            "browser": "Close the browser first if it fails to read cookies (Chrome, Edge and "
                       "Brave lock their cookie files). Firefox or a cookies.txt file are the "
                       "most reliable options.",
            "file": "Export cookies from your browser with a \"cookies.txt\" extension.",
        }.get(mode, ""))
        if hasattr(self, "preview"):
            self._update_preview()

    def _browse_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select cookies.txt", "", "Text files (*.txt);;All files (*)")
        if path:
            self.cookie_file_edit.setText(path)

    def _cookie_args(self) -> list[str]:
        mode = self._cookie_mode()
        path = signin.cookies_file() if mode == "app" else self.cookie_file_edit.text()
        return cookie_args(mode, self.cookie_browser_box.currentText(),
                           self.cookie_profile.text(), path)

    def _signin_refresh(self) -> None:
        signed = signin.is_signed_in()
        self.signin_status.setText("Signed in" if signed else "Not signed in")
        self.signin_btn.setText("Sign in again..." if signed else "Sign in...")
        self.signout_btn.setEnabled(signed)
        if hasattr(self, "preview"):
            self._update_preview()

    def _signin_open(self) -> None:
        dialog = SignInDialog(self, base_command(self.binary_edit.text().strip()))
        accepted = dialog.exec() == QDialog.Accepted
        dialog.deleteLater()
        self._signin_refresh()
        if accepted:
            self.cookie_app.setChecked(True)

    def _signin_out(self) -> None:
        signin.sign_out()
        self._signin_refresh()

    def _forget_fetched_height(self) -> None:
        if self._available_height:
            self._available_height = 0
            self._quality_changed()

    def _quality_changed(self, *_args) -> None:
        preset = PRESETS[self.preset.currentIndex()][1]
        self.quality.setEnabled(not is_audio_preset(preset))
        note = quality_note(self.quality.currentIndex(), preset,
                            bool(find_tool("ffmpeg") and find_tool("ffprobe")),
                            any(find_tool(n) for n in ("deno", "node", "bun", "qjs")),
                            self._available_height)
        self.quality_hint.setText(note)
        self.quality_hint.setVisible(bool(note))

    def _download_args(self) -> list[str]:
        preset = PRESETS[self.preset.currentIndex()][1]
        args = list(preset)
        if not is_audio_preset(preset):
            args += quality_args(self.quality.currentIndex(), preset)
        fmt = self.format_edit.text().strip()
        if fmt:
            args += ["-f", fmt]
        folder = self.folder_edit.text().strip()
        if folder:
            args += ["-P", folder]
        if self.live_start.isChecked():
            args.append("--live-from-start")
        if self.wait_chk.isChecked():
            lo, hi = self.wait_min.value(), self.wait_max.value()
            args.append(f"--wait-for-video={lo}-{hi}" if hi > lo else f"--wait-for-video={lo}")
        return args + self._cookie_args() + self.options.args()

    def _update_preview(self) -> None:
        urls = [u for u in self._url_list()] or ["URL"]
        self.preview.setPlainText(display_command(["yt-dlp", *self._download_args(), "--", *urls]))

    def _url_list(self) -> list[str]:
        return [u.strip() for u in self.urls.toPlainText().splitlines()
                if u.strip() and not u.strip().startswith("#")]

    def _fetch(self) -> None:
        urls = self._url_list()
        if not urls:
            self.info_label.setText("Paste a URL first.")
            return
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError as exc:
            self.info_label.setText(str(exc))
            return
        extra = signin.isolate_args(self._cookie_args()) + self.options.args(only=PASS_THROUGH)
        self.fetch_btn.setEnabled(False)
        self.info_label.setText("Fetching...")

        def work():
            try:
                self.bridge.info.emit(fetch_info(urls[0], base, extra))
            except Exception as exc:  # noqa: BLE001 - show any failure to the user
                self.bridge.info.emit(exc)

        threading.Thread(target=work, daemon=True).start()

    def _on_info(self, res) -> None:
        self.fetch_btn.setEnabled(True)
        if isinstance(res, Exception):
            self.info_label.setText(f"Failed: {res}")
            return
        title = res.get("title") or res.get("id") or "?"
        if res.get("_type") == "playlist":
            title = f"Playlist: {title} ({len(res.get('entries') or [])} entries)"
        status = res.get("live_status")
        self.info_label.setText(title + (f"   [{status}]" if status and status != "not_live" else ""))
        if status in LIVE_HINTS:
            self.live_hint.setText(LIVE_HINTS[status])
        self._fill_formats(res.get("formats") or [])
        self._available_height = max_height(res.get("formats") or [])
        if self._available_height:
            self.info_label.setText(f"{self.info_label.text()}   (up to {self._available_height}p)")
        self._quality_changed()

    def _fill_formats(self, formats: list) -> None:
        self.formats.setRowCount(0)
        for f in reversed(formats):                # best quality first
            row = self.formats.rowCount()
            self.formats.insertRow(row)
            size = f.get("filesize") or f.get("filesize_approx")
            res = f.get("resolution") or (f"{f['height']}p" if f.get("height") else "")
            cells = [f.get("format_id", ""), f.get("ext", ""), res, f.get("fps") or "",
                     _codec(f.get("vcodec")), _codec(f.get("acodec")),
                     fmt_bytes(size), f"{f['tbr']:.0f}k" if f.get("tbr") else "",
                     f.get("format_note") or ""]
            for col, value in enumerate(cells):
                self.formats.setItem(row, col, QTableWidgetItem(str(value)))
            self.formats.item(row, 0).setData(Qt.UserRole, format_kind(f))
        self.formats.resizeColumnsToContents()

    def _formats_selected(self) -> None:
        """Keep the selection valid: one video + one audio stream, or one combined format."""
        table = self.formats
        rows = sorted({i.row() for i in table.selectedItems()})
        selected = [(table.item(r, 0).text(), table.item(r, 0).data(Qt.UserRole)) for r in rows]
        current = table.currentRow()
        newest = table.item(current, 0).text() if current in rows else None
        keep = pick_formats(selected, newest)
        keep_ids = {fid for fid, _k in keep}
        if keep_ids != {fid for fid, _k in selected}:
            table.blockSignals(True)
            for r in rows:
                if table.item(r, 0).text() not in keep_ids:
                    for c in range(table.columnCount()):
                        table.item(r, c).setSelected(False)
            table.blockSignals(False)
            table.viewport().update()
        self.format_edit.setText(format_string(keep))

    # -------------------------------------------------------------- Queue tab
    def _build_queue_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        self._pad(col)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(2)
        self.parallel.valueChanged.connect(lambda _v: self._pump())
        top.addWidget(QLabel("Run at most"))
        top.addWidget(self.parallel)
        top.addWidget(QLabel("downloads at once"))
        fast = QPushButton("Max speed")
        fast.setToolTip("Run several downloads at once and split each one into 8 parallel "
                        "connections. Uses much more bandwidth, CPU and RAM (every running "
                        "download is its own process). Sites may throttle or block you (HTTP 429), "
                        "so for big channel archives consider a Gentle or Extra gentle pace.")
        fast.clicked.connect(self._max_speed)
        top.addWidget(fast)
        top.addStretch(1)
        for text, slot in (("Stop", self._stop_selected), ("Retry", self._retry_selected),
                           ("Remove", self._remove_selected), ("Clear finished", self._clear_finished)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            top.addWidget(btn)
        col.addLayout(top)

        self.queue = QTreeWidget()
        self.queue.setHeaderLabels(["Name", "Status", "Progress", "Speed", "ETA"])
        header = self.queue.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index, width in ((1, 190), (2, 200), (3, 110), (4, 80)):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.queue.setColumnWidth(index, width)
        self.queue.setUniformRowHeights(True)
        self.queue.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.queue.itemSelectionChanged.connect(self._show_log)
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        self.queue.itemDoubleClicked.connect(lambda item, _col: self._open_output(self._job_of(item)))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.queue)
        split.addWidget(self.log_view)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        col.addWidget(split, 1)
        return page

    def _max_speed(self) -> None:
        jobs, fragments = max_speed_settings(os.cpu_count())
        self.parallel.setValue(max(self.parallel.value(), jobs))
        ok = self.options.set_option("--concurrent-fragments", str(fragments))
        self._update_preview()
        self.statusBar().showMessage(
            f"Max speed: {self.parallel.value()} downloads at once"
            + (f", {fragments} connections each." if ok else "."), 8000)

    def _enqueue(self) -> None:
        urls = self._url_list()
        if not urls:
            self.info_label.setText("Paste at least one URL.")
            return
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError as exc:
            QMessageBox.warning(self, "yt-dlp not found", str(exc))
            return
        if not self._cookies_ok():
            return
        args = self._download_args()
        folder = self.folder_edit.text().strip()
        for url in urls:
            self._add_job(base, [*RUNNER_ARGS, *args, "--", url], url, url=url, folder=folder)
        self.tabs.setCurrentIndex(1)
        self._pump()

    def _cookies_ok(self) -> bool:
        if self._cookie_mode() == "app" and not signin.is_signed_in():
            QMessageBox.warning(self, "Not signed in",
                                "Click \"Sign in...\" in the Cookies / login box first, or "
                                "switch cookies off.")
            return False
        if self._cookie_mode() == "file" and not os.path.isfile(self.cookie_file_edit.text().strip()):
            QMessageBox.warning(self, "Cookies file not found",
                                "Choose an existing cookies.txt file, or switch cookies off.")
            return False
        return True

    def _open_archive(self) -> None:
        try:
            saved = json.loads(self.settings.value("archive", "{}"))
        except (ValueError, TypeError):
            saved = {}
        urls = self._url_list()
        defaults = ArchiveSettings.from_dict(
            {**saved, "url": "\n".join(urls), "folder": self.folder_edit.text().strip()})
        dialog = ArchiveDialog(self, defaults, self._cookie_args())
        if dialog.exec() != QDialog.Accepted:
            return
        cfg = dialog.settings()
        self.settings.setValue("archive", json.dumps(
            {k: v for k, v in cfg.to_dict().items()
            if k not in ("url", "folder", "use_cookies")}))
        self._start_archive(cfg, dialog.urls())

    def _start_archive(self, cfg: ArchiveSettings, channel_urls: list[str]) -> None:
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError as exc:
            QMessageBox.warning(self, "yt-dlp not found", str(exc))
            return
        if cfg.use_cookies and not self._cookies_ok():
            return
        try:
            os.makedirs(cfg.folder, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot use that folder", str(exc))
            return
        if cfg.pace == "max":                      # same widening as the Max speed button
            jobs, _fragments = max_speed_settings(os.cpu_count())
            self.parallel.setValue(max(self.parallel.value(), jobs))
            self.statusBar().showMessage(
                f"Max speed: up to {self.parallel.value()} channels at once.", 8000)
        # One job per channel, so each gets its own progress row, log and download archive.
        # How many run at once follows the "parallel downloads" setting; the rest wait in the queue.
        for channel in channel_urls:
            args, urls = build_archive(replace(cfg, url=channel))
            # Options-tab settings (proxy, rate limit, ...) first, so the archive's own settings win.
            job_args = [*RUNNER_ARGS, *self.options.args(),
                        *(self._cookie_args() if cfg.use_cookies else []),
                        *args, "--", *urls]
            self._add_job(base, job_args, f"Channel archive: {channel}", archive=True,
                          exclusive=cfg.exclusive, url=channel, folder=cfg.folder)
        self.tabs.setCurrentIndex(1)
        self._pump()

    def _add_job(self, base: list[str], args: list[str], label: str, archive: bool = False,
                 exclusive: bool = False, url: str = "", folder: str = "",
                 status: str = "Queued", last_error: str = "") -> Job:
        job = Job([*base, *args], label)
        job.args = list(args)             # what gets saved with the queue (no launcher prefix)
        job.url = url or label
        job.folder = folder
        job.output_path = ""
        job.resume = False
        job.is_archive = archive
        job.exclusive = exclusive         # extra-gentle archives run one at a time
        job.prepare = signin.isolate_args # fresh private cookies copy on every (re)start
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            job.log_path = os.path.join(self.log_dir, f"{job.id}.log")
        except OSError:
            pass
        job.on_line = lambda s, j=job: self.bridge.line.emit(j, s)
        job.on_progress = lambda p, j=job: self.bridge.progress.emit(j, p)
        job.on_state = lambda s, j=job: self.bridge.state.emit(j, s)
        item = QTreeWidgetItem([label, "Queued", "", "", ""])
        item.setToolTip(0, label)
        bar = QProgressBar()
        bar.setRange(0, 100)
        self.queue.addTopLevelItem(item)
        self.queue.setItemWidget(item, 2, bar)
        self.items[job] = (item, bar)
        if status != "Queued":            # restored from a previous run
            job.status = status
            job.last_error = last_error
            self._show_status(job)
        self._schedule_save()
        return job

    def _pump(self) -> None:
        if self._ready_to_close or self._closing:
            return
        self.retired = [j for j in self.retired if j.is_alive()]
        # Removed jobs that are still finishing count toward the limit too.
        jobs = [*self.retired, *self.items]
        for job in jobs_to_start(jobs, self.parallel.value()):
            job.start()

    def _job_of(self, item):
        return next((j for j, (it, _b) in self.items.items() if it is item), None)

    def _selected_jobs(self) -> list[Job]:
        return [j for j in (self._job_of(i) for i in self.queue.selectedItems()) if j]

    def _on_line(self, job, text: str) -> None:
        output = _OUTPUT_LINE.match(text)
        if output:
            job.output_path = next(g for g in output.groups() if g)
        if job in self.items and text.startswith("[gui] "):
            refresh = re.match(r"^\[gui\] Download links expired .*\(refresh (\d+) of (\d+)\)", text)
            if refresh:
                self.items[job][0].setText(1, f"Links expired, refreshing ({refresh.group(1)} "
                                              f"of {refresh.group(2)})")
            elif text == "[gui] Resuming with fresh links.":
                self.items[job][0].setText(1, "Running")
        elif job in self.items:
            if getattr(job, "is_archive", False):
                step = re.match(r"^\[download\] Downloading item (\d+) of (\d+)", text)
                if step:
                    self.items[job][0].setText(1, f"Running ({step.group(1)} of {step.group(2)})")
            elif output:
                self.items[job][0].setText(0, os.path.basename(job.output_path))
        selected = self._selected_jobs()
        if len(selected) == 1 and selected[0] is job:
            self.log_view.appendPlainText(text)

    def _on_progress(self, job, p: dict) -> None:
        if job not in self.items:
            return
        item, bar = self.items[job]
        if p["percent"] is None:
            bar.setRange(0, 0)                     # unknown total, e.g. live recording
            if p["downloaded"] and not getattr(job, "is_archive", False):
                item.setText(1, f"Recording... {fmt_bytes(p['downloaded'])}")
        else:
            bar.setRange(0, 100)
            bar.setValue(int(p["percent"]))
        item.setText(3, f"{fmt_bytes(p['speed'])}/s" if p["speed"] else "")
        item.setText(4, fmt_eta(p["eta"]))

    def _show_status(self, job) -> None:
        """Status column text, with the reason for failures."""
        item, bar = self.items[job]
        status = job.status
        error = getattr(job, "last_error", "")
        if status == DONE_WITH_ERRORS:
            count = getattr(job, "errors", 0)
            text = f"Done, {count} failed" if count else "Done with errors"
        elif status == FAILED and error:
            text = f"Failed: {error}"
        else:
            text = status
        item.setText(1, text)
        item.setToolTip(1, error if status in (FAILED, DONE_WITH_ERRORS) and error else "")
        if status != "Running":
            bar.setRange(0, 100)
            bar.setValue(100 if status in (DONE, DONE_WITH_ERRORS)
                         else bar.value() if bar.maximum() else 0)
            item.setText(3, "")
            item.setText(4, "")

    def _on_state(self, job, status: str) -> None:
        if job in self.items:
            self._show_status(job)
            self._schedule_save()
        self._pump()

    # ---- queue context menu
    def _queue_menu(self, pos) -> None:
        jobs = self._selected_jobs()
        clicked = self._job_of(self.queue.itemAt(pos))
        if clicked is not None and clicked not in jobs:
            self.queue.setCurrentItem(self.queue.itemAt(pos))
            jobs = [clicked]
        if not jobs:
            return
        one = jobs[0] if len(jobs) == 1 else None
        menu = QMenu(self)
        act = menu.addAction("Open file", lambda: self._open_output(one))
        act.setEnabled(bool(one and one.output_path and os.path.exists(one.output_path)))
        act = menu.addAction("Open folder", lambda: self._open_folder(one))
        act.setEnabled(bool(one and self._job_folder(one)))
        menu.addSeparator()
        menu.addAction("Copy URL" if one else "Copy URLs",
                       lambda: QApplication.clipboard().setText("\n".join(j.url for j in jobs)))
        act = menu.addAction("Copy command", lambda: QApplication.clipboard().setText(
            display_command(["yt-dlp", *one.args[len(RUNNER_ARGS):]])))
        act.setEnabled(one is not None)
        act = menu.addAction("Open log file", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(one.log_path)))
        act.setEnabled(bool(one and one.log_path and os.path.exists(one.log_path)))
        menu.addSeparator()
        menu.addAction("Retry", self._retry_selected).setEnabled(
            any(j.status in FINISHED for j in jobs))
        menu.addAction("Stop", self._stop_selected).setEnabled(
            any(j.status in ("Queued", "Running") for j in jobs))
        menu.addAction("Remove", self._remove_selected)
        menu.exec(self.queue.viewport().mapToGlobal(pos))

    @staticmethod
    def _job_folder(job) -> str:
        if job is None:
            return ""
        if job.output_path and os.path.isdir(os.path.dirname(os.path.abspath(job.output_path))):
            return os.path.dirname(os.path.abspath(job.output_path))
        return job.folder if job.folder and os.path.isdir(job.folder) else ""

    def _open_output(self, job) -> None:
        if job is not None and job.output_path and os.path.exists(job.output_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(job.output_path)))
        elif job is not None:
            self._open_folder(job)

    def _open_folder(self, job) -> None:
        folder = self._job_folder(job)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _show_log(self) -> None:
        selected = self._selected_jobs()
        self.log_view.setPlainText(selected[0].log_text() if len(selected) == 1 else "")

    def _stop_selected(self) -> None:
        for job in self._selected_jobs():
            job.stop()

    def _retry_selected(self) -> None:
        for job in self._selected_jobs():
            job.reset()
        self._pump()

    def _remove_selected(self) -> None:
        for job in self._selected_jobs():
            job.stop()
            if job.is_alive():
                self.retired.append(job)   # still counts toward the limit until it exits
            item, _bar = self.items.pop(job)
            self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))
        self._schedule_save()

    def _clear_finished(self) -> None:
        for job in [j for j in self.items if j.status in FINISHED]:
            item, _bar = self.items.pop(job)
            self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))
        self._schedule_save()

    # ------------------------------------------------------------ Options tab
    def _build_options_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        self._pad(col)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, slot in (("Save preset...", self._save_preset),
                           ("Load preset...", self._load_preset),
                           ("Reset all", self.options.reset)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(self.options, 1)
        return page

    def _save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save options preset", "preset.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.options.state(), fh, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save preset", str(exc))

    def _load_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load options preset", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not load preset", str(exc))
            return
        if not isinstance(state, dict):
            QMessageBox.warning(self, "Could not load preset",
                                "This file is not an options preset saved by yt-dlp GUI.")
            return
        self.options.load_state(state)

    # ----------------------------------------------------------- Settings tab
    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        self._pad(col)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        self.binary_edit = QLineEdit()
        self.binary_edit.setPlaceholderText(
            "Optional: path to a yt-dlp executable (empty = use the installed Python package)")
        pick = QPushButton("Browse...")
        pick.clicked.connect(self._browse_binary)
        row = QHBoxLayout()
        row.addWidget(self.binary_edit, 1)
        row.addWidget(pick)
        form.addRow("yt-dlp executable", row)
        self.theme_box = QComboBox()
        for key, label in THEMES:
            self.theme_box.addItem(label, key)
        saved_theme = normalize(self.settings.value("theme", "system"))
        self.theme_box.setCurrentIndex([k for k, _l in THEMES].index(saved_theme))
        self.theme_box.currentIndexChanged.connect(self._theme_changed)
        form.addRow("Theme", self.theme_box)
        self.version_label = QLabel("Checking...")
        self.version_label.setWordWrap(True)
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("yt-dlp version", self.version_label)
        self.check_updates = QCheckBox("Check GitHub for a newer yt-dlp release when the app starts")
        self.check_updates.setChecked(True)
        self.check_updates.toggled.connect(self._schedule_save)
        self.resume_queue = QCheckBox("Resume unfinished downloads when the app starts")
        self.resume_queue.setChecked(True)
        self.resume_queue.toggled.connect(self._schedule_save)
        form.addRow("", self.check_updates)
        form.addRow("", self.resume_queue)
        col.addLayout(form)

        self.deps_label = QLabel(self._deps_text())
        col.addWidget(QLabel("Dependencies"))
        col.addWidget(self.deps_label)

        update = QPushButton("Update yt-dlp from GitHub (master branch)")
        update.clicked.connect(self._update_ytdlp)
        self.update_btn = update
        self.update_log = QPlainTextEdit()
        self.update_log.setReadOnly(True)
        col.addWidget(update)
        col.addWidget(self.update_log, 1)
        return page

    def _theme_changed(self, _index: int = 0) -> None:
        choice = self.theme_box.currentData()
        self.settings.setValue("theme", choice)
        app = QApplication.instance()
        apply_theme(app, choice)
        self.theme_button.setChecked(is_dark(app))

    def _toggle_theme(self, checked: bool) -> None:
        wanted = "dark" if checked else "light"
        self.theme_box.setCurrentIndex([k for k, _l in THEMES].index(wanted))

    def _browse_binary(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select yt-dlp executable")
        if path:
            self.binary_edit.setText(path)

    @staticmethod
    def _deps_text() -> str:
        ffmpeg = "found" if find_tool("ffmpeg") and find_tool("ffprobe") else "MISSING (needed to merge and convert)"
        found = [n for n in ("deno", "node", "bun", "qjs") if find_tool(n)]
        if "deno" in found:
            js = "deno"
        elif found:
            name = "quickjs" if found[0] == "qjs" else found[0]
            js = (f"{name} found, but yt-dlp only enables deno by default. "
                  f"Add --js-runtimes {name} in the Options tab.")
        else:
            js = "MISSING (needed for full YouTube support; deno is recommended)"
        return f"ffmpeg + ffprobe: {ffmpeg}\nJavaScript runtime: {js}"

    def _update_ytdlp(self) -> None:
        if is_frozen():
            self.update_log.setPlainText(
                "This build has yt-dlp bundled inside it. To use a newer yt-dlp, either "
                "rebuild the app or download a yt-dlp executable and set its path above.")
            return
        if self.binary_edit.text().strip():
            self.update_log.setPlainText(
                "Settings > \"yt-dlp executable\" points at a separate yt-dlp, so updating the "
                "Python package would not change what downloads use. Update that executable "
                "with its own \"yt-dlp -U\", or clear the field to use the Python package.")
            return
        self.update_btn.setEnabled(False)
        self.update_log.setPlainText("Downloading yt-dlp from GitHub and installing...")
        base = base_command("")

        def work():
            log = []
            try:
                before = installed_version(base)
            except Exception:  # noqa: BLE001 - only used for the message
                before = "unknown"
            ok = True
            for cmd in pip_update_commands(sys.executable, YTDLP_SPEC):
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                          errors="replace", **hidden_run_kwargs())
                except OSError as exc:
                    log.append(f"Could not run pip: {exc}")
                    ok = False
                    break
                log.append(display_command(cmd) + "\n" + proc.stdout + proc.stderr)
                if proc.returncode != 0:
                    log.append(f"pip failed with exit code {proc.returncode}. yt-dlp was not updated.")
                    ok = False
                    break
            if ok:
                try:
                    after = installed_version(base)
                except Exception as exc:  # noqa: BLE001
                    after = f"unknown ({exc})"
                log.append(f"Updated yt-dlp: {before} -> {after}.\nNew downloads use it right away. "
                           "Restart the app to refresh the Options tab with any new options.")
            self.bridge.update_output.emit("\n".join(log))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_output(self, text: str) -> None:
        self.update_log.setPlainText(text)
        self.update_log.moveCursor(QTextCursor.End)
        self.update_btn.setEnabled(True)
        self._check_versions(latest=False)

    # ---- versions
    def _check_versions(self, latest: bool) -> None:
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError as exc:
            self.version_label.setText(str(exc))
            return
        self.version_label.setText("Checking...")

        def work():
            try:
                have = installed_version(base)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                have = f"! {exc}"
            newest = ""
            if latest:
                try:
                    newest = latest_release()
                except Exception:  # noqa: BLE001 - offline etc.: just don't show it
                    newest = ""
            self.bridge.versions.emit(have, newest)

        threading.Thread(target=work, daemon=True).start()

    def _on_versions(self, have: str, newest: str) -> None:
        if have.startswith("! "):
            self.version_label.setText(f"Could not run yt-dlp: {have[2:]}")
            return
        text = have
        if newest and is_outdated(have, newest):
            text += (f"  -  a newer release ({newest}) is available. "
                     + ("Rebuild the app or point \"yt-dlp executable\" at a newer yt-dlp."
                        if is_frozen() else "Use the update button below."))
            self.version_label.setStyleSheet("color: #c0392b;")
            self.statusBar().showMessage(
                f"yt-dlp {have} is out of date ({newest} is available). See Settings.", 15000)
        elif newest:
            text += "  (up to date)"
            self.version_label.setStyleSheet("")
        self.version_label.setText(text)

    # ---- drag and drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        data = event.mimeData()
        if data.hasUrls():
            found = [u.toString() for u in data.urls() if u.scheme() in ("http", "https")]
        else:
            found = [w for w in data.text().split() if re.match(r"^https?://", w)]
        if not found:
            return
        current = self.urls.toPlainText().rstrip("\n")
        self.urls.setPlainText("\n".join([current, *found]) if current else "\n".join(found))
        self.tabs.setCurrentIndex(0)
        event.acceptProposedAction()

    # ------------------------------------------------------------ persistence
    @staticmethod
    def _bool(value) -> bool:
        return value in (True, "true", "1", 1)

    def _restore(self) -> None:
        s = self.settings
        self.folder_edit.setText(s.value("output_dir", self.folder_edit.text()))
        self.binary_edit.setText(s.value("binary", ""))
        self.parallel.setValue(int(s.value("parallel", 2)))
        preset = int(s.value("preset", 0))
        self.preset.setCurrentIndex(preset if 0 <= preset < len(PRESETS) else 0)
        quality = int(s.value("quality", 0))
        self.quality.setCurrentIndex(quality if 0 <= quality < len(QUALITIES) else 0)
        self._quality_changed()
        self.live_start.setChecked(self._bool(s.value("live_from_start", False)))
        self.wait_chk.setChecked(self._bool(s.value("wait_for_video", False)))
        self.wait_min.setValue(int(s.value("wait_min", 60)))
        self.wait_max.setValue(int(s.value("wait_max", 0)))
        self.check_updates.setChecked(self._bool(s.value("check_updates", True)))
        self.resume_queue.setChecked(self._bool(s.value("resume_queue", True)))
        self.cookie_browser_box.setCurrentText(s.value("cookie_browser", BROWSERS[0]))
        self.cookie_profile.setText(s.value("cookie_profile", ""))
        self.cookie_file_edit.setText(s.value("cookie_file", ""))
        {"app": self.cookie_app, "browser": self.cookie_browser, "file": self.cookie_file}.get(
            s.value("cookie_mode", "none"), self.cookie_none).setChecked(True)
        try:
            self.options.load_state(json.loads(s.value("options", "{}")))
        except (ValueError, TypeError):
            pass
        # Save shortly after any change, not only on a clean exit.
        self.options.changed.connect(self._schedule_save)
        for signal in (self.folder_edit.textChanged, self.binary_edit.textChanged,
                       self.parallel.valueChanged, self.preset.currentIndexChanged,
                       self.quality.currentIndexChanged,
                       self.live_start.toggled, self.wait_chk.toggled,
                       self.wait_min.valueChanged, self.wait_max.valueChanged,
                       self.cookie_none.toggled, self.cookie_app.toggled,
                       self.cookie_browser.toggled, self.cookie_file.toggled,
                       self.cookie_browser_box.currentIndexChanged,
                       self.cookie_profile.textChanged, self.cookie_file_edit.textChanged):
            signal.connect(self._schedule_save)

    def _restore_queue(self) -> None:
        try:
            records = queue_store.load(json.loads(self.settings.value("queue", "{}")))
        except (ValueError, TypeError):
            records = []
        if not records:
            return
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError:
            return
        resume = self.resume_queue.isChecked()
        waiting = 0
        for rec in records:
            status = rec["status"]
            if status == "Queued" and not resume:
                status = "Stopped"
            waiting += status == "Queued"
            self._add_job(base, upgrade_runner_args(rec["args"]), rec["label"], archive=rec["archive"],
                          exclusive=rec["exclusive"], url=rec["url"], folder=rec["folder"],
                          status=status, last_error=rec["last_error"])
        if waiting:
            self.statusBar().showMessage(f"Resuming {waiting} unfinished download(s).", 8000)
            self._pump()

    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("output_dir", self.folder_edit.text())
        s.setValue("binary", self.binary_edit.text())
        s.setValue("parallel", self.parallel.value())
        s.setValue("preset", self.preset.currentIndex())
        s.setValue("quality", self.quality.currentIndex())
        s.setValue("live_from_start", self.live_start.isChecked())
        s.setValue("wait_for_video", self.wait_chk.isChecked())
        s.setValue("wait_min", self.wait_min.value())
        s.setValue("wait_max", self.wait_max.value())
        s.setValue("check_updates", self.check_updates.isChecked())
        s.setValue("resume_queue", self.resume_queue.isChecked())
        s.setValue("cookie_mode", self._cookie_mode())
        s.setValue("cookie_browser", self.cookie_browser_box.currentText())
        s.setValue("cookie_profile", self.cookie_profile.text())
        s.setValue("cookie_file", self.cookie_file_edit.text())
        s.setValue("options", json.dumps(self.options.state()))
        s.setValue("queue", json.dumps(queue_store.dump(list(self.items))))
        s.sync()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._ready_to_close:
            event.accept()
            return
        running = [j for j in self.items if j.status == "Running"]
        if running:
            answer = QMessageBox.question(
                self, "Downloads running",
                f"{len(running)} download(s) still running. Stop them and quit?\n\n"
                "They will resume the next time you start the app"
                + ("." if self.resume_queue.isChecked() else " if you retry them."))
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._closing = True
            for job in running:
                job.resume = True          # saved as Queued, so it continues next time
                job.stop()
        self._closing = True               # nothing new starts from here on
        self._save_timer.stop()
        self._save_settings()
        alive = [j for j in (*self.items, *self.retired) if j.is_alive()]
        if not alive:
            self._ready_to_close = True
            event.accept()
            return
        # Keep reading their output until they exit: closing now would break the pipe and
        # could kill yt-dlp while it is still finalizing a recording or merging files.
        event.ignore()
        self._wait_for_exit(alive)

    def _wait_for_exit(self, jobs: list[Job]) -> None:
        dialog = QProgressDialog("Waiting for downloads to finish saving...", "Quit now",
                                 0, 0, self)
        dialog.setWindowTitle("Stopping downloads")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        waited = {"ms": 0}
        timer = QTimer(self)

        def finish():
            timer.stop()
            for job in jobs:
                job.kill()
            dialog.close()
            self._ready_to_close = True
            self.close()

        def tick():
            waited["ms"] += 200
            left = [j for j in jobs if j.is_alive()]
            if not left:
                finish()
                return
            dialog.setLabelText(f"Waiting for {len(left)} download(s) to finish saving...")
            if waited["ms"] > (FORCE_KILL_AFTER + 5) * 1000:
                finish()

        dialog.canceled.connect(finish)
        timer.timeout.connect(tick)
        timer.start(200)
        dialog.show()
