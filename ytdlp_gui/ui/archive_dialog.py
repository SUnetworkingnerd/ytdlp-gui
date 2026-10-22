"""Dialog for the "Archive full channel" button."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..core.archive import (AUDIO_FORMATS, AUDIO_ONLY, CONTAINERS, QUALITIES,
                            ArchiveSettings, build_archive, split_channel_urls)
from ..core.runner import display_command, find_tool


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


class ArchiveDialog(QDialog):
    def __init__(self, parent, defaults: ArchiveSettings, cookie_args: list[str] | None = None):
        super().__init__(parent)
        self._cookie_args = list(cookie_args or [])
        self.setWindowTitle("Archive full channels")
        self.setMinimumWidth(680)
        col = QVBoxLayout(self)
        col.addWidget(_note(
            "Downloads everything each channel has published into one folder per channel, "
            "with a download archive so it is safe to stop and run again: videos you "
            "already have are skipped. Add as many channels as you like; each one becomes "
            "its own job in the queue, using the settings below."))

        self.url = QPlainTextEdit(defaults.url)
        self.url.setPlaceholderText(
            "One channel per line, e.g.\nhttps://www.youtube.com/@channelname\n"
            "https://www.youtube.com/@another")
        self.url.setFixedHeight(100)
        load = QPushButton("Load from file...")
        load.setToolTip("Read channel URLs from a text file (one per line, # for comments)")
        load.clicked.connect(self._load_file)
        self.url_count = QLabel()
        self.url_count.setStyleSheet("color: gray;")
        url_foot = QHBoxLayout()
        url_foot.addWidget(self.url_count, 1)
        url_foot.addWidget(load)
        url_col = QVBoxLayout()
        url_col.addWidget(self.url)
        url_col.addLayout(url_foot)
        self.folder = QLineEdit(defaults.folder)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        self.quality = QComboBox()
        for label, _args in QUALITIES:
            self.quality.addItem(label)
        self.quality.setCurrentIndex(defaults.quality if 0 <= defaults.quality < len(QUALITIES) else 0)

        self.container = QComboBox()
        for label, _v in CONTAINERS:
            self.container.addItem(label)
        self.container.setCurrentIndex(
            defaults.container if 0 <= defaults.container < len(CONTAINERS) else 0)
        self.audio_format = QComboBox()
        for label, _v in AUDIO_FORMATS:
            self.audio_format.addItem(label)
        self.audio_format.setCurrentIndex(
            defaults.audio_format if 0 <= defaults.audio_format < len(AUDIO_FORMATS) else 0)
        self.custom_format = QLineEdit(defaults.custom_format)
        self.custom_format.setPlaceholderText(
            "optional -f string, e.g. bv*[vcodec^=avc1]+ba[ext=m4a]/b (replaces Quality)")

        form = QFormLayout()
        form.addRow("Channel URLs", url_col)
        form.addRow("Save to", folder_row)
        form.addRow("Quality", self.quality)
        form.addRow("Video format", self.container)
        form.addRow("Audio format", self.audio_format)
        form.addRow("Custom format (-f)", self.custom_format)
        col.addLayout(form)

        self.shorts_live = QCheckBox("Include Shorts and live stream recordings (YouTube)")
        self.shorts_live.setChecked(defaults.include_shorts_live)
        self.metadata = QCheckBox("Save metadata: embedded tags and chapters, info.json, thumbnail")
        self.metadata.setChecked(defaults.metadata)
        self.subtitles = QCheckBox("Download subtitles (all languages, no live chat)")
        self.subtitles.setChecked(defaults.subtitles)
        self.gentle = QCheckBox("Be gentle: short random pauses between videos (recommended)")
        self.gentle.setChecked(defaults.gentle)
        self.quick = QCheckBox("Quick update: only fetch videos newer than what is already archived")
        self.quick.setChecked(defaults.quick_update)
        self.cookies = QCheckBox("Use cookies / login (the choice made on the Download tab)")
        self.cookies.setChecked(False)
        for box in (self.shorts_live, self.metadata, self.subtitles, self.gentle, self.quick,
                    self.cookies):
            col.addWidget(box)
            box.toggled.connect(self._refresh)
        if self._cookie_args:
            cookie_note = ("Off by default and reset every time. Downloading a whole channel while "
                           "logged in can get the account flagged, so only tick this if the "
                           "channel needs a login (age-restricted or members-only videos).")
        else:
            self.cookies.setEnabled(False)
            cookie_note = ("Pick \"Use my browser's login\" or a cookies.txt file on the Download "
                           "tab first to enable this.")
        col.addWidget(_note(cookie_note))
        col.addWidget(_note("Big channels take hours and can trigger rate limits. Pauses help."))
        if not (find_tool("ffmpeg") and find_tool("ffprobe")):
            col.addWidget(_note("ffmpeg was not found. It is needed to merge video and audio, "
                                "embed metadata and convert thumbnails (see the Settings tab)."))

        col.addWidget(QLabel("Command preview"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(90)
        col.addWidget(self.preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Start archive")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

        self.url.textChanged.connect(self._refresh)
        for edit in (self.folder, self.custom_format):
            edit.textChanged.connect(self._refresh)
        for combo in (self.quality, self.container, self.audio_format):
            combo.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def urls(self) -> list[str]:
        return split_channel_urls(self.url.toPlainText())

    def settings(self) -> ArchiveSettings:
        """Settings for the first channel (the others share everything but the URL)."""
        urls = self.urls()
        return ArchiveSettings(
            url=urls[0] if urls else "", folder=self.folder.text().strip(),
            quality=self.quality.currentIndex(), container=self.container.currentIndex(),
            audio_format=self.audio_format.currentIndex(),
            custom_format=self.custom_format.text().strip(), include_shorts_live=self.shorts_live.isChecked(),
            metadata=self.metadata.isChecked(), subtitles=self.subtitles.isChecked(),
            gentle=self.gentle.isChecked(), quick_update=self.quick.isChecked(),
            use_cookies=self.cookies.isChecked() and bool(self._cookie_args))

    def _refresh(self) -> None:
        cfg = self.settings()
        count = len(self.urls())
        self.url_count.setText(f"{count} channel{'s' if count != 1 else ''}")
        custom = bool(cfg.custom_format)
        audio = cfg.quality == AUDIO_ONLY and not custom
        self.quality.setEnabled(not custom)
        self.container.setEnabled(not audio)
        self.audio_format.setEnabled(audio)
        args, urls = build_archive(cfg)
        cookies = self._cookie_args if cfg.use_cookies else []
        text = display_command(["yt-dlp", *cookies, *args, "--", *(urls or ["URL"])])
        if count > 1:
            text = f"# First of {count} channels (one job each, same settings)\n{text}"
        self.preview.setPlainText(text)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save the archive to", self.folder.text())
        if path:
            self.folder.setText(path)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load channel URLs", "",
                                              "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.warning(self, "Cannot read file", str(exc))
            return
        current = self.url.toPlainText().rstrip()
        self.url.setPlainText(f"{current}\n{text}" if current else text)

    def _accept(self) -> None:
        cfg = self.settings()
        if not cfg.url:
            QMessageBox.warning(self, "Channel URL needed", "Paste at least one channel URL first.")
        elif not cfg.folder:
            QMessageBox.warning(self, "Folder needed", "Choose where to save the archive.")
        else:
            self.accept()
