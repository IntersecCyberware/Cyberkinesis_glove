import html

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


BG = "#0c0c0d"
PANEL_BG = "#161618"
CONTROL_BG = "#1c1c1f"
BORDER = "#3a3a3d"
ACCENT = "#e8e8ea"
TEXT = "#f1f1f2"
DIM = "#77777d"
WARN = "#ff5a5a"

STYLE = f"""
QDialog {{
    background-color: {PANEL_BG};
}}
QLabel#terminalStatus {{
    color: {DIM};
}}
QLabel#terminalPrompt {{
    color: {ACCENT};
    font-family: Consolas, monospace;
    font-weight: 700;
}}
QPlainTextEdit {{
    background-color: {BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit {{
    background-color: {CONTROL_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled {{
    color: {DIM};
}}
QPushButton:disabled {{
    background-color: {PANEL_BG};
    color: {DIM};
}}
"""


class CommandInput(QLineEdit):

    historyStep = pyqtSignal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            self.historyStep.emit(-1)
            return
        if event.key() == Qt.Key.Key_Down:
            self.historyStep.emit(1)
            return
        super().keyPressEvent(event)


class TerminalWindow(QDialog):

    MAX_LINES = 5000
    HISTORY_LIMIT = 100

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link
        self._history = []
        self._history_pos = 0

        self.setWindowTitle("Debug console")
        self.resize(720, 460)
        self.setMinimumSize(420, 300)
        self.setStyleSheet(STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("terminalStatus")
        self.clear_button = QPushButton("Clear")
        header.addWidget(self.status_label, 1)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(font)
        self.output.setMaximumBlockCount(self.MAX_LINES)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.output, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        prompt = QLabel(">")
        prompt.setObjectName("terminalPrompt")
        self.input = CommandInput()
        self.input.setFont(font)
        self.input.setPlaceholderText("Type a command (h for help)")
        self.send_button = QPushButton("Send")
        input_row.addWidget(prompt)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)

        for button in (self.clear_button, self.send_button):
            button.setAutoDefault(False)
            button.setDefault(False)

        self.input.returnPressed.connect(self._submit)
        self.input.historyStep.connect(self._step_history)
        self.send_button.clicked.connect(self._submit)
        self.clear_button.clicked.connect(self.output.clear)

        self.link.connected.connect(self._on_connected)
        self.link.disconnected.connect(self._on_disconnected)
        self.link.line_received.connect(self._on_line)
        self.link.command_sent.connect(self._on_command_sent)

        self._set_connected(self.link.is_connected(), "")

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _set_connected(self, connected, name):
        self.input.setEnabled(connected)
        self.send_button.setEnabled(connected)
        if connected:
            self.status_label.setText(f"Connected: {name}")
        else:
            self.status_label.setText("Glove not connected via USB.")

    def _on_connected(self, name):
        self._set_connected(True, name)
        self._append(f"-- connected: {name} --", DIM)

    def _on_disconnected(self):
        self._set_connected(False, "")
        self._append("-- disconnected --", DIM)

    def _on_line(self, line):
        color = DIM if line.startswith("@") else TEXT
        self._append(line, color)

    def _on_command_sent(self, command):
        self._append("> " + command, ACCENT)

    def _append(self, text, color):
        bar = self.output.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        escaped = html.escape(text)
        self.output.appendHtml(
            f'<span style="white-space: pre; color: {color};">{escaped}</span>'
        )

        if at_bottom:
            bar.setValue(bar.maximum())

    def _submit(self):
        text = self.input.text().strip()
        if not text:
            return

        self.input.clear()

        if not self._history or self._history[-1] != text:
            self._history.append(text)
            del self._history[:-self.HISTORY_LIMIT]
        self._history_pos = len(self._history)

        if not self.link.send(text.replace("\\t", "\t")):
            self._append("-- not connected --", WARN)

    def _step_history(self, step):
        if not self._history:
            return

        self._history_pos = max(0, min(len(self._history), self._history_pos + step))

        if self._history_pos == len(self._history):
            self.input.clear()
        else:
            self.input.setText(self._history[self._history_pos])