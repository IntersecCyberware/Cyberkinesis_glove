import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

import comm_backend as backend


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
QLabel#wifiStatus {{
    color: {DIM};
}}
QTreeWidget {{
    background-color: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 5px;
    outline: none;
}}
QTreeWidget::item {{
    padding: 5px 4px;
}}
QTreeWidget::item:selected {{
    background-color: #2a2a2e;
    color: {TEXT};
}}
QHeaderView::section {{
    background-color: {PANEL_BG};
    color: {DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 6px;
    font-weight: 600;
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

FAIL_MESSAGES = {
    "AUTH": "Connection failed - most likely the password is wrong.",
    "NOT_FOUND": "The glove can't see this network (it may be 5 GHz only or out of range).",
    "TIMEOUT": "The network didn't answer in time. Try again.",
    "FULL": "All 8 saved network slots are in use. Forget a network first.",
    "BAD": "Invalid SSID or password (a password must be 8-63 characters).",
    "ABORT": "Cancelled because the glove's mode switch was changed.",
}


def _rssi_to_percent(rssi):
    """Rough dBm -> percent conversion (common approximation used by a
    lot of WiFi tooling: -100dBm or worse = 0%, -50dBm or better = 100%,
    linear in between)."""
    return max(0, min(100, 2 * (rssi + 100)))


class WifiWindow(QDialog):

    reachable_changed = pyqtSignal(str)

    SCAN_MAX_AGE = 10.0
    WATCHDOG_MS = 120000

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link

        self._saved = {}
        self._scan = {}
        self._pending_saved = {}
        self._pending_scan = {}
        self._scan_done = False
        self._last_scan = 0.0
        self._reachable = ""
        self._busy = set()

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)

        self._build_ui()
        self._connect_signals()
        self._refresh_ui()

    def _build_ui(self):
        self.setWindowTitle("WiFi networks")
        self.setMinimumSize(540, 470)
        self.setStyleSheet(STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("wifiStatus")
        self.refresh_button = QPushButton("Rescan")
        header.addWidget(self.status_label, 1)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Network", "Status", "Signal"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        password_row = QHBoxLayout()
        password_row.setSpacing(8)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_button = QPushButton("Show")
        self.show_button.setCheckable(True)
        password_row.addWidget(self.password_input, 1)
        password_row.addWidget(self.show_button)
        layout.addLayout(password_row)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.connect_button = QPushButton("Connect")
        self.forget_button = QPushButton("Forget")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.forget_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        for button in (
            self.refresh_button,
            self.show_button,
            self.connect_button,
            self.forget_button,
            self.close_button,
        ):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _connect_signals(self):
        self.link.connected.connect(self._on_link_connected)
        self.link.disconnected.connect(self._on_link_disconnected)
        self.link.reply_received.connect(self._on_reply)

        self.tree.itemSelectionChanged.connect(self._update_controls)
        self.password_input.textChanged.connect(self._update_controls)
        self.password_input.returnPressed.connect(self._connect_selected)
        self.show_button.toggled.connect(self._toggle_password)
        self.refresh_button.clicked.connect(self._request_scan)
        self.connect_button.clicked.connect(self._connect_selected)
        self.forget_button.clicked.connect(self._forget_selected)
        self.close_button.clicked.connect(self.close)

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

        stale = time.monotonic() - self._last_scan > self.SCAN_MAX_AGE
        if self.link.is_connected() and not self._busy and stale:
            self._request_scan()

    def _send(self, op, command):
        if not self.link.send(command):
            return False

        if op == "wl":
            self._pending_saved = {}
        elif op == "ws":
            self._pending_scan = {}

        self._busy.add(op)
        self._watchdog.start(self.WATCHDOG_MS)
        self._refresh_ui()
        return True

    def _finish(self, op):
        self._busy.discard(op)
        if self._busy:
            self._watchdog.start(self.WATCHDOG_MS)
        else:
            self._watchdog.stop()
        self._refresh_ui()

    def _set_reachable(self, ssid):
        if ssid == self._reachable:
            return
        self._reachable = ssid
        self.reachable_changed.emit(ssid)

    def _on_link_connected(self, _name):
        self._saved = {}
        self._scan = {}
        self._scan_done = False
        self._last_scan = 0.0
        self._busy.clear()
        self._set_reachable("")
        self._show_result("", None)
        self._send("wl", backend.cmd_wifi_saved())
        self._send("wc", backend.cmd_wifi_probe())
        self._send("ws", backend.cmd_wifi_scan())

    def _on_link_disconnected(self):
        self._saved = {}
        self._scan = {}
        self._scan_done = False
        self._last_scan = 0.0
        self._busy.clear()
        self._watchdog.stop()
        self._set_reachable("")
        self._refresh_ui()

    def _on_watchdog(self):
        self._busy.clear()
        self._show_result("The glove didn't answer in time.", False)
        self._refresh_ui()

    def _on_reply(self, tag, fields):
        if tag == "WL":
            self._on_saved_reply(fields)
        elif tag == "WS":
            self._on_scan_reply(fields)
        elif tag == "WC":
            self._on_probe_reply(fields)
        elif tag == "WA":
            self._on_add_reply(fields)
        elif tag == "C":
            self._on_forget_reply(fields)

    def _on_saved_reply(self, fields):
        if not fields:
            return

        if fields[0] == "END":
            self._saved = self._pending_saved
            self._pending_saved = {}
            self._finish("wl")
            return

        try:
            index = int(fields[0])
        except ValueError:
            return

        self._pending_saved["\t".join(fields[1:])] = index

    def _on_scan_reply(self, fields):
        if not fields:
            return

        if fields[0] == "END":
            self._scan = self._pending_scan
            self._pending_scan = {}
            self._scan_done = True
            self._last_scan = time.monotonic()
            self._finish("ws")
            return

        if len(fields) < 3:
            return

        try:
            percent = _rssi_to_percent(int(fields[0]))
        except ValueError:
            return

        secure = fields[1] == "1"
        ssid = "\t".join(fields[2:])
        known = self._pending_scan.get(ssid)
        if known is None or percent > known[0]:
            self._pending_scan[ssid] = (percent, secure)

    def _on_probe_reply(self, fields):
        if len(fields) > 1 and fields[0] == "OK":
            self._set_reachable("\t".join(fields[1:]))
        else:
            self._set_reachable("")
        self._finish("wc")

    def _on_add_reply(self, fields):
        if len(fields) >= 3 and fields[0] == "OK":
            ssid = "\t".join(fields[1:-1])
            try:
                self._saved[ssid] = int(fields[-1])
            except ValueError:
                pass
            self.password_input.clear()
            self._set_reachable(ssid)
            self._show_result(f"Connected to {ssid} and saved it on the glove.", True)
        elif fields and fields[0] == "FAIL":
            reason = fields[1] if len(fields) > 1 else "TIMEOUT"
            self._show_result(
                FAIL_MESSAGES.get(reason, f"Connection failed ({reason})."),
                False,
            )
        self._finish("wa")

    def _on_forget_reply(self, fields):
        if len(fields) > 1 and fields[0] == "OK":
            try:
                index = int(fields[1])
            except ValueError:
                index = -1

            for ssid, slot in list(self._saved.items()):
                if slot != index:
                    continue
                del self._saved[ssid]
                if ssid == self._reachable:
                    self._set_reachable("")
                self._show_result(f"Forgot {ssid}.", True)
        self._finish("c")

    def _request_scan(self):
        if self._busy or not self.link.is_connected():
            return
        self._send("ws", backend.cmd_wifi_scan())

    def _selected_ssid(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _connect_selected(self):
        ssid = self._selected_ssid()
        if not ssid or self._busy or not self.link.is_connected():
            return
        if ssid == self._reachable or ssid not in self._scan:
            return

        secure = self._scan[ssid][1]
        password = self.password_input.text() if secure else ""
        if secure and not password:
            return

        try:
            command = backend.cmd_wifi_add(ssid, password)
        except ValueError:
            self._show_result(
                "The SSID or password contains characters that can't be sent to the glove.",
                False,
            )
            return

        self._show_result(f"Connecting to {ssid}...", None)
        self._send("wa", command)

    def _forget_selected(self):
        ssid = self._selected_ssid()
        if ssid not in self._saved or self._busy or not self.link.is_connected():
            return
        self._show_result("", None)
        self._send("c", backend.cmd_wifi_forget(self._saved[ssid]))

    def _toggle_password(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)
        self.show_button.setText("Hide" if checked else "Show")

    def _show_result(self, text, ok):
        color = DIM if ok is None else (ACCENT if ok else WARN)
        self.result_label.setStyleSheet(f"color: {color};")
        self.result_label.setText(text)

    def _status_text(self, ssid):
        if ssid == self._reachable:
            return "Connected"

        parts = ["Saved"] if ssid in self._saved else []
        if ssid in self._scan:
            parts.append("Secured" if self._scan[ssid][1] else "Open")
        elif self._scan_done:
            parts.append("Out of range")
        return " · ".join(parts)

    def _signal_text(self, ssid):
        if ssid in self._scan:
            return f"{self._scan[ssid][0]}%"
        return "-"

    def _rebuild_tree(self):
        selected = self._selected_ssid()
        self.tree.blockSignals(True)
        self.tree.clear()

        ordered = sorted(
            set(self._scan) | set(self._saved),
            key=lambda ssid: (
                ssid != self._reachable,
                ssid not in self._saved,
                -self._scan.get(ssid, (-1, False))[0],
                ssid.lower(),
            ),
        )

        for ssid in ordered:
            item = QTreeWidgetItem(
                [ssid, self._status_text(ssid), self._signal_text(ssid)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, ssid)
            item.setTextAlignment(
                2,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

            if ssid == self._reachable:
                font = item.font(0)
                font.setBold(True)
                for column in range(3):
                    item.setFont(column, font)
            elif self._scan_done and ssid not in self._scan:
                for column in range(3):
                    item.setForeground(column, QColor(DIM))

            self.tree.addTopLevelItem(item)

            if ssid == selected:
                self.tree.setCurrentItem(item)
                item.setSelected(True)

        self.tree.blockSignals(False)

    def _update_controls(self):
        connected = self.link.is_connected()
        idle = connected and not self._busy
        ssid = self._selected_ssid()

        is_reachable = ssid is not None and ssid == self._reachable
        visible = ssid in self._scan
        secure = self._scan.get(ssid, (0, True))[1]
        can_edit = idle and visible and not is_reachable

        self.password_input.setEnabled(can_edit and secure)
        self.show_button.setEnabled(can_edit and secure)
        self.connect_button.setEnabled(
            can_edit and (not secure or bool(self.password_input.text()))
        )
        self.forget_button.setEnabled(idle and ssid in self._saved)
        self.refresh_button.setEnabled(idle)

    def _update_status(self):
        if not self.link.is_connected():
            text = "Glove not connected via USB."
        elif "wa" in self._busy:
            text = "Connecting..."
        elif "wc" in self._busy:
            text = "Checking which saved network the glove can reach..."
        elif "ws" in self._busy:
            text = "Scanning..."
        elif self._busy:
            text = "Working..."
        elif self._reachable:
            text = f"Glove can reach: {self._reachable}"
        else:
            text = "No saved network is reachable."
        self.status_label.setText(text)

    def _refresh_ui(self):
        self._rebuild_tree()
        self._update_controls()
        self._update_status()