"""Main window: Download, Queue, Options and Settings tabs."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core.archive import ArchiveSettings, build_archive
from ..core.cookies import BROWSERS, cookie_args
from ..core.metadata import LIVE_HINTS, PASS_THROUGH, fetch_info
from ..core.runner import (
    RUNNER_ARGS, Job, base_command, display_command, find_tool, fmt_bytes,
    YTDLP_SPEC, fmt_eta, hidden_run_kwargs, is_frozen,
)
from ..core.theme import THEMES, normalize
from .archive_dialog import ArchiveDialog
from .options_panel import OptionsPanel
from .theme import apply_theme, is_dark

PRESETS = [
    ("Default (yt-dlp picks the best video + audio)", []),
    ("MP4 video  (-t mp4)", ["-t", "mp4"]),
    ("MKV video  (-t mkv)", ["-t", "mkv"]),
    ("MP3 audio  (-t mp3)", ["-t", "mp3"]),
    ("AAC audio  (-t aac)", ["-t", "aac"]),
]
FORMAT_HEADERS = ["ID", "Ext", "Resolution", "FPS", "Video codec", "Audio codec",
                  "Size", "Bitrate", "Note"]


class Bridge(QObject):
    """Carries results from worker threads to the UI thread."""
    line = Signal(object, str)
    progress = Signal(object, dict)
    state = Signal(object, str)
    info = Signal(object)
    update_output = Signal(str)


def _codec(value) -> str:
    return "" if value is None else ("-" if value == "none" else str(value))


class MainWindow(QMainWindow):
    def __init__(self, model):
        super().__init__()
        self.setWindowTitle("yt-dlp GUI")
        self.resize(1150, 780)
        self.settings = QSettings("ytdlp-gui", "ytdlp-gui")
        self.bridge = Bridge()
        self.bridge.line.connect(self._on_line)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.state.connect(self._on_state)
        self.bridge.info.connect(self._on_info)
        self.bridge.update_output.connect(self._on_update_output)
        self.items: dict[Job, tuple] = {}

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

    # ------------------------------------------------------------ Download tab
    def _build_download_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)

        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("Paste one URL per line (video, playlist or live stream)")
        self.urls.setFixedHeight(80)
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
        self.formats.itemSelectionChanged.connect(self._formats_selected)
        col.addWidget(self.formats, 1)

        form = QFormLayout()
        self.preset = QComboBox()
        for label, _args in PRESETS:
            self.preset.addItem(label)
        self.preset.currentIndexChanged.connect(self._update_preview)
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
        for widget in (hint, self.cookie_none, self.cookie_browser):
            cookies_col.addWidget(widget)
        cookies_col.addLayout(browser_row)
        cookies_col.addWidget(self.cookie_file)
        cookies_col.addLayout(file_row)
        cookies_col.addWidget(self.cookie_note)
        self.cookie_none.toggled.connect(self._cookie_mode_changed)
        self.cookie_browser.toggled.connect(self._cookie_mode_changed)
        self.cookie_file.toggled.connect(self._cookie_mode_changed)
        self.cookie_browser_box.currentIndexChanged.connect(self._update_preview)
        self.cookie_profile.textChanged.connect(self._update_preview)
        self.cookie_file_edit.textChanged.connect(self._update_preview)
        self._cookie_mode_changed()
        col.addWidget(cookies)

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
        wait_row.addWidget(self.wait_chk)
        wait_row.addWidget(self.wait_min)
        wait_row.addWidget(self.wait_max)
        wait_row.addStretch(1)
        self.live_hint = QLabel("Without --live-from-start, yt-dlp records a live "
                                "stream from the moment you start (--no-live-from-start).")
        self.live_hint.setWordWrap(True)
        live_col.addWidget(self.live_start)
        live_col.addLayout(wait_row)
        live_col.addWidget(self.live_hint)
        col.addWidget(live)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(64)
        col.addWidget(QLabel("Command preview"))
        col.addWidget(self.preview)

        go = QPushButton("Download")
        go.clicked.connect(self._enqueue)
        archive = QPushButton("Archive full channel...")
        archive.setToolTip("Download everything a channel has published, resumable")
        archive.clicked.connect(self._open_archive)
        buttons = QHBoxLayout()
        buttons.addWidget(go, 2)
        buttons.addWidget(archive, 1)
        col.addLayout(buttons)
        return page

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save downloads to", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def _cookie_mode(self) -> str:
        if self.cookie_browser.isChecked():
            return "browser"
        return "file" if self.cookie_file.isChecked() else "none"

    def _cookie_mode_changed(self) -> None:
        mode = self._cookie_mode()
        self.cookie_browser_box.setEnabled(mode == "browser")
        self.cookie_profile.setEnabled(mode == "browser")
        self.cookie_file_edit.setEnabled(mode == "file")
        self.cookie_note.setText({
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
        return cookie_args(self._cookie_mode(), self.cookie_browser_box.currentText(),
                           self.cookie_profile.text(), self.cookie_file_edit.text())

    def _download_args(self) -> list[str]:
        args = list(PRESETS[self.preset.currentIndex()][1])
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
        extra = self._cookie_args() + self.options.args(only=PASS_THROUGH)
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
        self.formats.resizeColumnsToContents()

    def _formats_selected(self) -> None:
        rows = sorted({i.row() for i in self.formats.selectedItems()})
        self.format_edit.setText("+".join(self.formats.item(r, 0).text() for r in rows))

    # -------------------------------------------------------------- Queue tab
    def _build_queue_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        top = QHBoxLayout()
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(2)
        self.parallel.valueChanged.connect(lambda _v: self._pump())
        top.addWidget(QLabel("Run at most"))
        top.addWidget(self.parallel)
        top.addWidget(QLabel("downloads at once"))
        top.addStretch(1)
        for text, slot in (("Stop", self._stop_selected), ("Retry", self._retry_selected),
                           ("Remove", self._remove_selected), ("Clear finished", self._clear_finished)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            top.addWidget(btn)
        col.addLayout(top)

        self.queue = QTreeWidget()
        self.queue.setHeaderLabels(["Name", "Status", "Progress", "Speed", "ETA"])
        self.queue.setColumnWidth(0, 420)
        self.queue.setColumnWidth(2, 220)
        self.queue.itemSelectionChanged.connect(self._show_log)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.queue)
        split.addWidget(self.log_view)
        col.addWidget(split, 1)
        return page

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
        for url in urls:
            self._add_job([*base, *RUNNER_ARGS, *args, "--", url], url)
        self.tabs.setCurrentIndex(1)
        self._pump()

    def _cookies_ok(self) -> bool:
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
            {**saved, "url": urls[0] if urls else "", "folder": self.folder_edit.text().strip()})
        dialog = ArchiveDialog(self, defaults)
        if dialog.exec() != QDialog.Accepted:
            return
        cfg = dialog.settings()
        self.settings.setValue("archive", json.dumps(
            {k: v for k, v in cfg.to_dict().items() if k not in ("url", "folder")}))
        self._start_archive(cfg)

    def _start_archive(self, cfg: ArchiveSettings) -> None:
        try:
            base = base_command(self.binary_edit.text().strip())
        except RuntimeError as exc:
            QMessageBox.warning(self, "yt-dlp not found", str(exc))
            return
        if not self._cookies_ok():
            return
        try:
            os.makedirs(cfg.folder, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot use that folder", str(exc))
            return
        args, urls = build_archive(cfg)
        # Options-tab settings (proxy, rate limit, ...) first, so the archive's own settings win.
        cmd = [*base, *RUNNER_ARGS, *self.options.args(), *self._cookie_args(), *args, "--", *urls]
        self._add_job(cmd, f"Channel archive: {cfg.url}", archive=True)
        self.tabs.setCurrentIndex(1)
        self._pump()

    def _add_job(self, cmd: list[str], url: str, archive: bool = False) -> None:
        job = Job(cmd, url)
        job.is_archive = archive
        job.on_line = lambda s, j=job: self.bridge.line.emit(j, s)
        job.on_progress = lambda p, j=job: self.bridge.progress.emit(j, p)
        job.on_state = lambda s, j=job: self.bridge.state.emit(j, s)
        item = QTreeWidgetItem([url, "Queued", "", "", ""])
        bar = QProgressBar()
        bar.setRange(0, 100)
        self.queue.addTopLevelItem(item)
        self.queue.setItemWidget(item, 2, bar)
        self.items[job] = (item, bar)

    def _pump(self) -> None:
        running = sum(1 for j in self.items if j.status == "Running")
        for job in list(self.items):
            if running >= self.parallel.value():
                break
            if job.status == "Queued":
                job.start()
                running += 1

    def _job_of(self, item):
        return next((j for j, (it, _b) in self.items.items() if it is item), None)

    def _selected_jobs(self) -> list[Job]:
        return [j for j in (self._job_of(i) for i in self.queue.selectedItems()) if j]

    def _on_line(self, job, text: str) -> None:
        if job in self.items:
            if getattr(job, "is_archive", False):
                step = re.match(r"^\[download\] Downloading item (\d+) of (\d+)", text)
                if step:
                    self.items[job][0].setText(1, f"Running ({step.group(1)} of {step.group(2)})")
            else:
                match = re.match(r"^\[download\] Destination: (.+)$", text)
                if match:
                    self.items[job][0].setText(0, os.path.basename(match.group(1)))
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

    def _on_state(self, job, status: str) -> None:
        if job in self.items:
            item, bar = self.items[job]
            item.setText(1, status)
            if status != "Running":
                bar.setRange(0, 100)
                bar.setValue(100 if status == "Done" else bar.value() if bar.maximum() else 0)
                item.setText(3, "")
                item.setText(4, "")
        self._pump()

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
            item, _bar = self.items.pop(job)
            self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))

    def _clear_finished(self) -> None:
        for job in [j for j in self.items if j.status in ("Done", "Failed", "Stopped")]:
            item, _bar = self.items.pop(job)
            self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))

    # ------------------------------------------------------------ Options tab
    def _build_options_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        row = QHBoxLayout()
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
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.options.state(), fh, indent=2)

    def _load_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load options preset", "", "JSON (*.json)")
        if path:
            with open(path, encoding="utf-8") as fh:
                self.options.load_state(json.load(fh))

    # ----------------------------------------------------------- Settings tab
    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        form = QFormLayout()
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
        col.addLayout(form)

        self.deps_label = QLabel(self._deps_text())
        col.addWidget(QLabel("Dependencies"))
        col.addWidget(self.deps_label)

        update = QPushButton("Update yt-dlp from GitHub (master branch)")
        update.clicked.connect(self._update_ytdlp)
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
        self.update_log.setPlainText("Downloading yt-dlp from GitHub and installing...")

        def work():
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", YTDLP_SPEC]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, **hidden_run_kwargs())
                self.bridge.update_output.emit(proc.stdout + proc.stderr + "\nRestart the app to use the new version.")
            except OSError as exc:
                self.bridge.update_output.emit(f"Could not run pip: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_update_output(self, text: str) -> None:
        self.update_log.setPlainText(text)

    # ------------------------------------------------------------ persistence
    def _restore(self) -> None:
        s = self.settings
        self.folder_edit.setText(s.value("output_dir", self.folder_edit.text()))
        self.binary_edit.setText(s.value("binary", ""))
        self.parallel.setValue(int(s.value("parallel", 2)))
        self.cookie_browser_box.setCurrentText(s.value("cookie_browser", BROWSERS[0]))
        self.cookie_profile.setText(s.value("cookie_profile", ""))
        self.cookie_file_edit.setText(s.value("cookie_file", ""))
        {"browser": self.cookie_browser, "file": self.cookie_file}.get(
            s.value("cookie_mode", "none"), self.cookie_none).setChecked(True)
        try:
            self.options.load_state(json.loads(s.value("options", "{}")))
        except (ValueError, TypeError):
            pass

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        running = [j for j in self.items if j.status == "Running"]
        if running:
            answer = QMessageBox.question(
                self, "Downloads running",
                f"{len(running)} download(s) still running. Stop them and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            for job in running:
                job.stop()
        s = self.settings
        s.setValue("output_dir", self.folder_edit.text())
        s.setValue("binary", self.binary_edit.text())
        s.setValue("parallel", self.parallel.value())
        s.setValue("cookie_mode", self._cookie_mode())
        s.setValue("cookie_browser", self.cookie_browser_box.currentText())
        s.setValue("cookie_profile", self.cookie_profile.text())
        s.setValue("cookie_file", self.cookie_file_edit.text())
        s.setValue("options", json.dumps(self.options.state()))
        event.accept()
