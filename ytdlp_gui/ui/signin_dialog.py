"""Sign-in window: sign in to Google in a real browser, and the app keeps the login.

Google refuses sign-in from embedded browsers, so this opens a real, installed browser on a
fresh, separate profile (your normal browser profile is never touched). When that browser
is closed, yt-dlp reads the new profile's cookies once, core/signin.py keeps the YouTube
ones in the app's own cookies file, and the temporary profile is deleted.
"""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QProcess, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from ..core import signin
from ..core.runner import hidden_run_kwargs

# Some browsers hand the window over to another process and exit straight away. If the
# browser "closes" faster than this, don't assume the sign-in is finished.
QUICK_EXIT_SECONDS = 5


class SignInDialog(QDialog):
    _exported = Signal(bool, str)          # emitted from the worker thread

    def __init__(self, parent=None, ytdlp_cmd: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to YouTube")
        self.resize(520, 360)
        self.ytdlp_cmd = list(ytdlp_cmd or [])
        self._browsers = signin.find_login_browsers()
        self._proc: QProcess | None = None
        self._started = 0.0
        self._exporting = False
        self._key = ""

        steps = QLabel(
            "<b>1.</b> Pick a browser and click <b>Open browser</b>. It opens on a fresh, "
            "separate profile, so your normal browser and its logins are not touched.<br>"
            "<b>2.</b> Sign in to Google there until YouTube's home page shows.<br>"
            "<b>3.</b> Close that browser window. This window finishes by itself.")
        steps.setWordWrap(True)
        hint = QLabel(
            "Your password is never stored, only the login cookies, on this computer. Heavy "
            "downloading while signed in can get an account banned (temporarily or "
            "permanently), so use a secondary account.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        self.combo = QComboBox()
        for _key, label, _exe in self._browsers:
            self.combo.addItem(label)
        self.open_btn = QPushButton("Open browser")
        self.open_btn.clicked.connect(self._open)
        row = QHBoxLayout()
        row.addWidget(self.combo, 1)
        row.addWidget(self.open_btn)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        if not self._browsers:
            self.status.setText(
                "No supported browser was found. Install Firefox (it works best), or use "
                "\"Use my browser's login\" or a cookies.txt file instead.")
            self.open_btn.setEnabled(False)

        self.done_btn = QPushButton("I've signed in and closed the browser")
        self.done_btn.setEnabled(False)
        self.done_btn.clicked.connect(self._done_pressed)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.done_btn)
        buttons.addWidget(self.cancel_btn)

        col = QVBoxLayout(self)
        col.addWidget(steps)
        col.addLayout(row)
        col.addWidget(self.status)
        col.addStretch(1)
        col.addWidget(hint)
        col.addLayout(buttons)

        self._exported.connect(self._export_finished)

    # ------------------------------------------------------------------ browser
    def _browser_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def _open(self) -> None:
        if not self._browsers or self._browser_running():
            return
        self._key, label, exe = self._browsers[self.combo.currentIndex()]
        profile = signin.login_profile_dir()
        try:
            os.makedirs(profile, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Could not prepare the browser profile", str(exc))
            return
        cmd = signin.browser_command(self._key, exe, profile)
        self._proc = QProcess(self)
        self._proc.finished.connect(self._browser_closed)
        self._proc.errorOccurred.connect(
            lambda err, name=label: self._browser_failed(err, name))
        self._started = time.monotonic()
        self._proc.start(cmd[0], cmd[1:])
        self.open_btn.setEnabled(False)
        self.done_btn.setEnabled(True)
        self.status.setText(f"{label} is open. Sign in to Google there, then close that browser window.")

    def _browser_failed(self, error, name: str) -> None:
        if error == QProcess.FailedToStart:
            self.open_btn.setEnabled(True)
            self.done_btn.setEnabled(False)
            self.status.setText("")
            QMessageBox.warning(self, "Could not start the browser",
                                f"{name} could not be started. Try another browser.")

    def _browser_closed(self, _code: int, _status) -> None:
        self.open_btn.setEnabled(not self._exporting)
        if self._exporting:
            return
        if time.monotonic() - self._started < QUICK_EXIT_SECONDS:
            self.status.setText(
                "If the browser is still open, finish signing in and close it, then press "
                "\"I've signed in and closed the browser\".")
            return
        self._export()

    def _done_pressed(self) -> None:
        if self._browser_running():
            QMessageBox.information(
                self, "Close the browser first",
                "Close the browser window first. The login is only saved to disk when the "
                "browser closes.")
            return
        self._export()

    # ------------------------------------------------------------------ export
    def _export(self) -> None:
        if self._exporting:
            return
        self._exporting = True
        for button in (self.open_btn, self.done_btn, self.cancel_btn):
            button.setEnabled(False)
        self.status.setText("Reading your login...")
        key, profile = self._key, signin.login_profile_dir()
        cmd, kwargs = self.ytdlp_cmd, hidden_run_kwargs()

        def work() -> None:
            try:
                ok, message = signin.export_login(key, profile, cmd, run_kwargs=kwargs)
            except Exception as exc:           # never leave the dialog stuck on "Reading..."
                ok, message = False, str(exc)
            self._exported.emit(ok, message)

        threading.Thread(target=work, daemon=True).start()

    def _export_finished(self, ok: bool, message: str) -> None:
        self._exporting = False
        if ok:
            signin.reset_profile()             # the session is discarded, only the cookies file stays
            self.accept()
            return
        self.open_btn.setEnabled(True)
        self.done_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.status.setText("Not signed in yet. You can reopen the browser and try again.")
        QMessageBox.warning(self, "Sign-in not finished", message)

    # ------------------------------------------------------------------ cleanup
    def done(self, result: int) -> None:
        if self._browser_running():
            self._proc.kill()
            self._proc.waitForFinished(3000)
        if result != QDialog.Accepted:
            signin.reset_profile()
        super().done(result)
