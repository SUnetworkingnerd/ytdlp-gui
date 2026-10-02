import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .core.options_model import OptionsModel
from .ui.main_window import MainWindow
from .ui.theme import apply_theme

APP_ID = "ytdlp-gui"


def main() -> int:
    if os.name == "nt":
        # Give the process its own taskbar identity so the logo is shown, not Python's.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)          # lets Wayland match the .desktop entry
    apply_theme(app, QSettings("ytdlp-gui", "ytdlp-gui").value("theme", "system"))
    icon = Path(__file__).parent / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    try:
        model = OptionsModel()
    except ImportError:
        QMessageBox.critical(
            None, "yt-dlp is not installed",
            'The GUI reads its options from the yt-dlp Python package.\n\n'
            'Install it with:\n    pip install "yt-dlp[default]"')
        return 1
    window = MainWindow(model)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
