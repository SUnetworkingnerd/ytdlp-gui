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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
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
    """Options tab. Each group's page is only built the first time it is shown (or
    matches a search): the full set is several hundred widgets, mostly never looked at."""
    changed = Signal()

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.rows: dict[str, OptionRow] = {}      # only the rows built so far
        self._built: set[int] = set()
        self._pending: dict[str, object] = {}     # saved values for rows not built yet
        self._keys = {it.key for g in model.groups for it in g.items}
        self._needle = ""

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all options...")
        self.search.textChanged.connect(self._filter)
        self.group_list = QListWidget()
        self.group_list.setMinimumWidth(190)
        self.group_list.setMaximumWidth(260)
        self.stack = QStackedWidget()
        for group in model.groups:
            self.stack.addWidget(QWidget())        # cheap placeholder until first shown
            self.group_list.addItem(group.title)
        self.group_list.currentRowChanged.connect(self._show_group)
        self.group_list.setCurrentRow(0)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 6, 0)
        left_col.setSpacing(8)
        left_col.addWidget(self.search)
        left_col.addWidget(self.group_list)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        split.setSizes([220, 880])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

    def _ensure_built(self, index: int) -> None:
        if index in self._built or not 0 <= index < len(self.model.groups):
            return
        self._built.add(index)
        page = QWidget()
        col = QVBoxLayout(page)
        col.setSpacing(4)
        col.setContentsMargins(10, 10, 10, 10)
        for item in self.model.groups[index].items:
            row = OptionRow(item)
            if item.key in self._pending:          # apply before connecting, so no preview churn
                row.set_value(self._pending.pop(item.key))
            row.changed.connect(self.changed)
            row.setVisible(not self._needle or self._needle in item.searchtext)
            self.rows[item.key] = row
            col.addWidget(row)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        old = self.stack.widget(index)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.insertWidget(index, scroll)
        self.stack.setCurrentIndex(max(self.group_list.currentRow(), 0))

    def _show_group(self, index: int) -> None:
        if index < 0:
            return
        self._ensure_built(index)
        self.stack.setCurrentIndex(index)

    def _filter(self, text: str) -> None:
        self._needle = needle = text.lower().strip()
        first_visible = None
        for gi, group in enumerate(self.model.groups):
            matches = [it for it in group.items if not needle or needle in it.searchtext]
            if matches and needle:
                self._ensure_built(gi)             # only groups that have a hit get built
            if gi in self._built:
                for it in group.items:
                    self.rows[it.key].setVisible(not needle or needle in it.searchtext)
            self.group_list.item(gi).setHidden(not matches)
            if matches and first_visible is None:
                first_visible = gi
        current = self.group_list.currentRow()
        if first_visible is not None and self.group_list.item(current).isHidden():
            self.group_list.setCurrentRow(first_visible)

    def state(self) -> dict:
        out = {k: r.value() for k, r in self.rows.items() if not r.is_default()}
        out.update({k: v for k, v in self._pending.items() if k not in self.rows})
        return out

    def load_state(self, state: dict) -> None:
        self.reset()
        for key, value in state.items():
            if key in self.rows:
                self.rows[key].set_value(value)
            elif key in self._keys:
                self._pending[key] = value

    def set_option(self, key: str, value) -> bool:
        """Set one option from code (works before its page has been built)."""
        if key in self.rows:
            self.rows[key].set_value(value)
        elif key in self._keys:
            self._pending[key] = value
            self.changed.emit()
        else:
            return False                           # this yt-dlp version doesn't have it
        return True

    def reset(self) -> None:
        self._pending.clear()
        for row in self.rows.values():
            row.reset()

    def args(self, only: set | None = None) -> list[str]:
        return self.model.build_args(self.state(), only)
