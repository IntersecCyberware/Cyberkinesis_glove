import sys

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from color_wheel import ColorWheelDialog
from glove_link import GloveLink, UdpLink
from key_bindings import BindingEngine, KeyBindingsWindow
from terminal import TerminalWindow
from wifi_window import WifiWindow


BG = "#0c0c0d"
PANEL_BG = "#161618"
CONTROL_BG = "#1c1c1f"
BORDER = "#3a3a3d"
ACCENT = "#e8e8ea"
TEXT = "#f1f1f2"
DIM = "#77777d"
FILL = "#a6a6ab"
WARN = "#ff5a5a"

STYLESHEET = f"""
QMainWindow {{
    background-color: {BG};
    color: {TEXT};
}}
QWidget {{
    color: {TEXT};
    font-family: "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QFrame#mainPanel {{
    background-color: {PANEL_BG};
    border: 2px solid {BORDER};
    border-radius: 8px;
}}
QFrame#sectionDivider {{
    background-color: {BORDER};
    border: none;
    max-height: 1px;
}}
QLabel#appTitle {{
    color: {ACCENT};
    font-size: 27px;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#secondaryText {{
    color: {DIM};
}}
QLabel#connectionStatus {{
    color: {WARN};
    font-weight: 600;
}}
QPushButton {{
    background-color: {CONTROL_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 9px 14px;
    font-weight: 600;
    text-align: left;
}}
QPushButton:hover {{
    background-color: #232326;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {BG};
}}
QPushButton:disabled {{
    color: {DIM};
}}
"""


class FingerBar(QWidget):

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = 0

        self.setMinimumSize(48, 150)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    def set_value(self, value: int) -> None:
        self.value = max(0, min(100, int(value)))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()

        label_height = 24
        value_height = 25
        bar_top = label_height
        bar_height = max(1, height - label_height - value_height)
        bar_width = max(1, width - 14)
        bar_x = (width - bar_width) / 2

        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        painter.drawText(
            QRectF(0, 0, width, label_height),
            Qt.AlignmentFlag.AlignCenter,
            self.label,
        )

        outline = QRectF(
            bar_x,
            bar_top,
            bar_width,
            bar_height,
        )
        painter.setPen(QPen(QColor(BORDER), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outline, 3, 3)

        fill_height = max(
            0,
            round((bar_height - 4) * self.value / 100),
        )

        if fill_height:
            fill_rect = QRectF(
                bar_x + 2,
                bar_top + bar_height - 2 - fill_height,
                bar_width - 4,
                fill_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(FILL)))
            painter.drawRoundedRect(fill_rect, 2, 2)

        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Consolas", 9))
        painter.drawText(
            QRectF(0, height - value_height, width, value_height),
            Qt.AlignmentFlag.AlignCenter,
            f"{self.value}%",
        )


class FingerBarsPanel(QWidget):

    FINGER_NAMES = (
        "THUMB",
        "POINTER",
        "MIDDLE",
        "RING",
        "PINKIE",
    )

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        bars_row = QHBoxLayout()
        bars_row.setContentsMargins(0, 0, 0, 0)
        bars_row.setSpacing(16)

        self.bars = []

        for name in self.FINGER_NAMES:
            bar = FingerBar(name)
            self.bars.append(bar)
            bars_row.addWidget(bar, 1)

        layout.addLayout(bars_row, 1)

    def set_values(self, values) -> None:
        for bar, value in zip(self.bars, values):
            bar.set_value(value)


class TransportBadge(QLabel):

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(58, 28)

        self.setStyleSheet(
            f"background-color: {CONTROL_BG}; "
            f"color: {DIM}; "
            f"border: 1px solid {BORDER}; "
            "border-radius: 5px; "
            "font-family: Consolas, monospace; "
            "font-size: 11px; "
            "font-weight: 700;"
        )

    def set_active(self, active: bool) -> None:
        color = ACCENT if active else DIM
        border = ACCENT if active else BORDER
        self.setStyleSheet(
            f"background-color: {CONTROL_BG}; "
            f"color: {color}; "
            f"border: 1px solid {border}; "
            "border-radius: 5px; "
            "font-family: Consolas, monospace; "
            "font-size: 11px; "
            "font-weight: 700;"
        )


class MainWindow(QMainWindow):

    led_color_selected = pyqtSignal(QColor)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Cyberkinesis Glove LaunchPad v1.0 alpha")
        self.resize(900, 620)
        self.setMinimumSize(720, 500)

        self.led_color = QColor("#000000")
        self._device_name = ""
        self._reachable_ssid = ""
        self._udp_connected = False
        self._udp_name = ""

        self._build_ui()

        self.link = GloveLink(self)
        self.udp_link = UdpLink(self)
        self.terminal = TerminalWindow(self.link, self)
        self.wifi_window = WifiWindow(self.link, self)
        self.binding_engine = BindingEngine(self)
        self.key_bindings_window = KeyBindingsWindow(self.binding_engine, self)
        self._connect_link()
        self.link.start()
        self.udp_link.start()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)

        panel = QFrame()
        panel.setObjectName("mainPanel")
        root.addWidget(panel, 1)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 16)
        panel_layout.setSpacing(16)

        top_row = QHBoxLayout()
        top_row.setSpacing(24)
        panel_layout.addLayout(top_row)

        left_column = QVBoxLayout()
        left_column.setSpacing(10)
        top_row.addLayout(left_column, 3)

        transport_row = QHBoxLayout()
        transport_row.setSpacing(7)

        self.usb_badge = TransportBadge("USB")
        self.wifi_badge = TransportBadge("WiFi")

        transport_row.addWidget(self.usb_badge)
        transport_row.addWidget(self.wifi_badge)

        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("connectionStatus")
        transport_row.addWidget(self.status_label)
        transport_row.addStretch(1)

        left_column.addLayout(transport_row)

        self.device_label = QLabel("...")
        self.device_label.setObjectName("secondaryText")
        left_column.addWidget(self.device_label)

        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(8)

        self.led_button = QPushButton("LED")
        self.network_button = QPushButton("Choose network")

        self.led_color_preview = QLabel()
        self.led_color_preview.setFixedSize(26, 26)
        self.led_color_preview.setToolTip("Selected LED color")

        left_buttons.addWidget(self.led_button)
        left_buttons.addWidget(self.led_color_preview)
        left_buttons.addWidget(self.network_button)
        left_buttons.addStretch(1)

        left_column.addLayout(left_buttons)

        right_column = QVBoxLayout()
        right_column.setSpacing(10)
        top_row.addLayout(right_column, 2)

        self.keybindings_button = QPushButton("Key bindings")
        self.console_button = QPushButton("Debug console")

        right_column.addWidget(self.keybindings_button)
        right_column.addWidget(self.console_button)
        right_column.addStretch(1)

        divider = QFrame()
        divider.setObjectName("sectionDivider")
        panel_layout.addWidget(divider)


        self.finger_panel = FingerBarsPanel()
        panel_layout.addWidget(self.finger_panel, 1)


        self.led_button.clicked.connect(self._open_color_picker)
        self.led_button.setToolTip(
            "Choose an LED color. "
            "It is saved in the glove's memory."
        )

        self._update_led_color_preview(self.led_color)

        self.network_button.setToolTip("Choose the WiFi network the glove connects to.")
        self.console_button.setToolTip("Open the serial debug console.")

        self.keybindings_button.setToolTip(
            "Bind finger positions to keyboard keys or Xbox controller buttons."
        )

    def _connect_link(self):
        self.console_button.clicked.connect(self.terminal.show_window)
        self.network_button.clicked.connect(self.wifi_window.show_window)
        self.keybindings_button.clicked.connect(self.key_bindings_window.show_window)

        self.led_button.setEnabled(False)
        self.led_color_selected.connect(self._send_led_color)
        self.link.reply_received.connect(self._on_link_reply)
        self.link.connected.connect(self._on_link_connected)
        self.link.disconnected.connect(self._on_link_disconnected)
        self.wifi_window.reachable_changed.connect(self._on_reachable_changed)
        self.udp_link.connected.connect(self._on_udp_connected)
        self.udp_link.disconnected.connect(self._on_udp_disconnected)
        self.udp_link.data_received.connect(self.finger_panel.set_values)
        self.udp_link.data_received.connect(self.binding_engine.update)
        self.udp_link.disconnected.connect(self.binding_engine.release_all)

    def _on_link_connected(self, device_name):
        self._device_name = device_name
        self.led_button.setEnabled(True)
        self.usb_badge.set_active(True)
        self._apply_status()

    def _on_link_disconnected(self):
        self._device_name = ""
        self._reachable_ssid = ""
        self.led_button.setEnabled(False)
        self.usb_badge.set_active(False)
        self._apply_status()

    def _on_link_reply(self, tag, fields):
        if tag != "LC" or not fields:
            return

        color = QColor(f"#{fields[0]}")
        if color.isValid():
            self.led_color = color
            self._update_led_color_preview(color)

    def _send_led_color(self, color):
        self.link.send(f"ls {color.name()[1:].upper()}")

    def _on_reachable_changed(self, ssid):
        self._reachable_ssid = ssid
        self._apply_status()

    def _on_udp_connected(self, device_name):
        self._udp_connected = True
        self._udp_name = device_name
        self._apply_status()

    def _on_udp_disconnected(self):
        self._udp_connected = False
        self._udp_name = ""
        self.finger_panel.set_values([0] * len(self.finger_panel.bars))
        self._apply_status()

    def _apply_status(self):
        usb = self.link.is_connected()
        self.wifi_badge.set_active(self._udp_connected or bool(self._reachable_ssid))

        if usb or self._udp_connected:
            self.status_label.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
            self.status_label.setText("Connected")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setText("Disconnected")

        self._update_device_label()

    def _update_device_label(self):
        name = self._device_name or self._udp_name
        if not name:
            self.device_label.setText("...")
            return

        text = name
        if self._reachable_ssid:
            text += f"  |  WiFi: {self._reachable_ssid}"
        self.device_label.setText(text)

    def closeEvent(self, event):
        self.terminal.close()
        self.wifi_window.close()
        self.key_bindings_window.close()
        self.binding_engine.shutdown()
        self.link.stop()
        self.udp_link.stop()
        super().closeEvent(event)

    def _open_color_picker(self):
        dialog = ColorWheelDialog(self.led_color, self)

        if dialog.exec():
            self.led_color = dialog.selected_color

            self._update_led_color_preview(self.led_color)

            self.led_color_selected.emit(QColor(self.led_color))

    def _update_led_color_preview(self, color: QColor):
        self.led_color_preview.setStyleSheet(
            f"background-color: {color.name()}; "
            f"border: 1px solid {BORDER}; "
            "border-radius: 4px;"
        )

        self.led_color_preview.setToolTip(
            f"Selected LED color: {color.name().upper()}"
        )


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()