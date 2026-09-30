"""Options tab: one control per yt-dlp option, generated from the OptionsModel."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)


class OptionRow(QFrame):
    changed = Signal()

    def __init__(self, item):
        super().__init__()
        self.item = item
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        top = QHBoxLayout()
        layout.addLayout(top)

        kind = item.kind
        if kind == "flag":
            self.w = QCheckBox(item.title)
            self.w.toggled.connect(self.changed)
            top.addWidget(self.w, 1)
        else:
            label = QLabel(item.title)
            label.setMinimumWidth(240)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            top.addWidget(label)
            if kind == "pair":
                self.w = QComboBox()
                self.w.addItems(["(default)", item.pos.flag, item.neg.flag])
                self.w.currentIndexChanged.connect(self.changed)
            elif kind == "choice":
                self.w = QComboBox()
                self.w.addItems([""] + list(item.choices))
                self.w.currentIndexChanged.connect(self.changed)
            elif kind == "multi":
                self.w = QPlainTextEdit()
                self.w.setFixedHeight(56)
                self.w.setPlaceholderText("one entry per line")
                self.w.textChanged.connect(self.changed)
            else:
                self.w = QLineEdit()
                self.w.setPlaceholderText(item.metavar)
                self.w.textChanged.connect(self.changed)
            top.addWidget(self.w, 1)

        help_label = QLabel(item.help)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray;")
        layout.addWidget(help_label)

    def value(self):
        w = self.w
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentIndex() if self.item.kind == "pair" else w.currentText()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText()
        return w.text()

    def set_value(self, value) -> None:
        w = self.w
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QComboBox):
            if self.item.kind == "pair":
                w.setCurrentIndex(int(value))
            else:
                w.setCurrentText(str(value))
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(str(value))
        else:
            w.setText(str(value))

    def is_default(self) -> bool:
        v = self.value()
        return (not v) or (isinstance(v, str) and not v.strip())

    def reset(self) -> None:
        w = self.w
        if isinstance(w, QCheckBox):
            w.setChecked(False)
        elif isinstance(w, QComboBox):
            w.setCurrentIndex(0)
        elif isinstance(w, QPlainTextEdit):
            w.clear()
        else:
            w.clear()


class OptionsPanel(QWidget):
    changed = Signal()

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.rows: dict[str, OptionRow] = {}

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all options...")
        self.search.textChanged.connect(self._filter)
        self.group_list = QListWidget()
        self.group_list.setMaximumWidth(230)
        self.stack = QStackedWidget()

        for group in model.groups:
            page = QWidget()
            col = QVBoxLayout(page)
            for item in group.items:
                row = OptionRow(item)
                row.changed.connect(self.changed)
                self.rows[item.key] = row
                col.addWidget(row)
            col.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
            self.group_list.addItem(group.title)
        self.group_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.group_list.setCurrentRow(0)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.addWidget(self.search)
        left_col.addWidget(self.group_list)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

    def _filter(self, text: str) -> None:
        needle = text.lower().strip()
        first_visible = None
        for gi, group in enumerate(self.model.groups):
            any_visible = False
            for item in group.items:
                visible = not needle or needle in item.searchtext
                self.rows[item.key].setVisible(visible)
                any_visible |= visible
            self.group_list.item(gi).setHidden(not any_visible)
            if any_visible and first_visible is None:
                first_visible = gi
        current = self.group_list.currentRow()
        if first_visible is not None and self.group_list.item(current).isHidden():
            self.group_list.setCurrentRow(first_visible)

    def state(self) -> dict:
        return {k: r.value() for k, r in self.rows.items() if not r.is_default()}

    def load_state(self, state: dict) -> None:
        self.reset()
        for key, value in state.items():
            if key in self.rows:
                self.rows[key].set_value(value)

    def reset(self) -> None:
        for row in self.rows.values():
            row.reset()

    def args(self, only: set | None = None) -> list[str]:
        return self.model.build_args(self.state(), only)
