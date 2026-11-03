"""Light and dark themes. Always uses the Fusion style so both look the same on
Linux and Windows. "System" follows the OS colour scheme and reacts when it changes."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QProxyStyle, QStyle

from ..core.theme import effective, normalize

ACCENT = "#d62828"          # close to the red of the logo

_LIGHT = dict(window="#f0f0f0", base="#ffffff", alt="#f6f6f6", text="#1a1a1a", button="#e6e6e6",
              tip="#ffffdc", link="#b01e1e", disabled="#9a9a9a", placeholder="#8a8a8a")
_DARK = dict(window="#26262a", base="#1b1b1e", alt="#2b2b30", text="#e8e8e8", button="#35353b",
             tip="#3a3a40", link="#ff7a7a", disabled="#7c7c82", placeholder="#8c8c92")

_state = {"choice": "system", "initial_dark": None, "busy": False, "wired": False, "style": None}

# Extra (width, height) added to the default Fusion sizes, so controls are not cramped.
_EXTRA = {
    QStyle.CT_PushButton: (18, 10),
    QStyle.CT_TabBarTab: (26, 12),
    QStyle.CT_LineEdit: (0, 8),
    QStyle.CT_ComboBox: (6, 8),
    QStyle.CT_SpinBox: (0, 8),
    QStyle.CT_CheckBox: (0, 6),
    QStyle.CT_RadioButton: (0, 6),
    QStyle.CT_ItemViewItem: (8, 8),
}


class _RoomyStyle(QProxyStyle):
    """Fusion with a little more padding. Looks identical, just less squeezed."""

    def __init__(self):
        super().__init__("Fusion")

    def sizeFromContents(self, ctype, option, size, widget=None):
        base = super().sizeFromContents(ctype, option, size, widget)
        extra = _EXTRA.get(ctype)
        return base + QSize(*extra) if extra else base


def _palette(colors: dict) -> QPalette:
    c = {k: QColor(v) for k, v in colors.items()}
    p = QPalette(c["button"])           # derives the light/mid/dark shades from the button colour
    roles = {
        QPalette.Window: c["window"], QPalette.WindowText: c["text"],
        QPalette.Base: c["base"], QPalette.AlternateBase: c["alt"],
        QPalette.Text: c["text"], QPalette.Button: c["button"], QPalette.ButtonText: c["text"],
        QPalette.ToolTipBase: c["tip"], QPalette.ToolTipText: c["text"],
        QPalette.BrightText: QColor("#ffffff"), QPalette.Link: c["link"],
        QPalette.Highlight: QColor(ACCENT), QPalette.HighlightedText: QColor("#ffffff"),
        QPalette.PlaceholderText: c["placeholder"],
    }
    for role, color in roles.items():
        p.setColor(role, color)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c["disabled"])
    return p


def _system_is_dark(app) -> bool:
    hints = app.styleHints()
    if hasattr(hints, "colorScheme"):                       # Qt 6.5+
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    return bool(_state["initial_dark"])


def is_dark(app) -> bool:
    return app.palette().color(QPalette.Window).lightness() < 128


def apply_theme(app, choice) -> None:
    """choice: "system", "light" or "dark"."""
    if _state["busy"]:
        return
    _state["busy"] = True
    try:
        choice = normalize(choice)
        _state["choice"] = choice
        if _state["initial_dark"] is None:
            _state["initial_dark"] = app.palette().color(QPalette.Window).lightness() < 128
        hints = app.styleHints()
        if hasattr(hints, "setColorScheme"):                # Qt 6.8+: also themes the title bar
            scheme = {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(
                choice, Qt.ColorScheme.Unknown)
            try:
                hints.setColorScheme(scheme)
            except Exception:  # noqa: BLE001 - purely cosmetic
                pass
        dark = effective(choice, _system_is_dark(app)) == "dark"
        if _state["style"] is None:                         # set once; Qt owns it afterwards
            _state["style"] = _RoomyStyle()
            app.setStyle(_state["style"])
        app.setPalette(_palette(_DARK if dark else _LIGHT))
        if not _state["wired"] and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_args: _on_system_change(app))
            _state["wired"] = True
    finally:
        _state["busy"] = False


def _on_system_change(app) -> None:
    if _state["choice"] == "system":
        apply_theme(app, "system")
