"""Dialog for the "Archive full channel" button."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..core.archive import QUALITIES, ArchiveSettings, build_archive
from ..core.runner import display_command, find_tool


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


class ArchiveDialog(QDialog):
    def __init__(self, parent, defaults: ArchiveSettings):
        super().__init__(parent)
        self.setWindowTitle("Archive a full channel")
        self.setMinimumWidth(680)
        col = QVBoxLayout(self)
        col.addWidget(_note(
            "Downloads everything a channel has published into one folder per channel, "
            "with a download archive so it is safe to stop and run again: videos you "
            "already have are skipped."))

        self.url = QLineEdit(defaults.url)
        self.url.setPlaceholderText("https://www.youtube.com/@channelname")
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

        form = QFormLayout()
        form.addRow("Channel URL", self.url)
        form.addRow("Save to", folder_row)
        form.addRow("Quality", self.quality)
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
        for box in (self.shorts_live, self.metadata, self.subtitles, self.gentle, self.quick):
            col.addWidget(box)
            box.toggled.connect(self._refresh)
        col.addWidget(_note(
            "Big channels take hours and can trigger rate limits. Pauses help, and logging in "
            "with cookies (Download tab) helps too."))
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

        for edit in (self.url, self.folder):
            edit.textChanged.connect(self._refresh)
        self.quality.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def settings(self) -> ArchiveSettings:
        return ArchiveSettings(
            url=self.url.text().strip(), folder=self.folder.text().strip(),
            quality=self.quality.currentIndex(), include_shorts_live=self.shorts_live.isChecked(),
            metadata=self.metadata.isChecked(), subtitles=self.subtitles.isChecked(),
            gentle=self.gentle.isChecked(), quick_update=self.quick.isChecked())

    def _refresh(self) -> None:
        cfg = self.settings()
        args, urls = build_archive(cfg)
        self.preview.setPlainText(display_command(["yt-dlp", *args, "--", *(urls or ["URL"])]))

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save the archive to", self.folder.text())
        if path:
            self.folder.setText(path)

    def _accept(self) -> None:
        cfg = self.settings()
        if not cfg.url:
            QMessageBox.warning(self, "Channel URL needed", "Paste the channel's URL first.")
        elif not cfg.folder:
            QMessageBox.warning(self, "Folder needed", "Choose where to save the archive.")
        else:
            self.accept()
