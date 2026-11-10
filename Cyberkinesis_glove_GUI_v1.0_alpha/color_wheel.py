"""Standalone color-wheel widget and dialog for the Cyberkinesis GUI.

This module only lets the user choose a color. It does not communicate with
or send commands to the glove.
"""

import math

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


BORDER = "#3a3a3d"
TEXT = "#f1f1f2"
DIM = "#77777d"


class ColorWheel(QWidget):
    """Circular hue selector; angle selects hue and radius selects brightness."""

    colorChanged = pyqtSignal(QColor)

    INNER_RADIUS = 0.8
    WHITE_RADIUS = 0.95

    def __init__(self, initial_color=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(240, 240)
        self._hue = 0.0
        self._value = 1.0
        self._saturation = 1.0
        self._wheel_image = self._build_wheel_image(self.width())
        if initial_color is not None:
            self.set_color(QColor(initial_color))

    @staticmethod
    def _saturation_value(ratio):
        if ratio <= ColorWheel.INNER_RADIUS:
            return 1.0, ratio / ColorWheel.INNER_RADIUS
        fade = (ratio - ColorWheel.INNER_RADIUS) / (ColorWheel.WHITE_RADIUS - ColorWheel.INNER_RADIUS)
        return max(0.0, 1.0 - fade), 1.0

    @staticmethod
    def _radius_ratio(saturation, value):
        if value >= 0.999:
            return ColorWheel.INNER_RADIUS + (1.0 - saturation) * (ColorWheel.WHITE_RADIUS - ColorWheel.INNER_RADIUS)
        return value * ColorWheel.INNER_RADIUS

    @staticmethod
    def _build_wheel_image(side):
        image = QImage(side, side, QImage.Format.Format_ARGB32)
        image.fill(0)
        center_x = center_y = side / 2
        radius = side / 2 - 1

        for y in range(side):
            dy = y - center_y
            for x in range(side):
                dx = x - center_x
                distance = math.hypot(dx, dy)
                if distance > radius:
                    continue

                angle = math.atan2(-dy, dx)
                if angle < 0:
                    angle += 2 * math.pi
                hue = angle / (2 * math.pi)
                ratio = min(1.0, distance / radius) if radius else 0.0
                saturation, brightness = ColorWheel._saturation_value(ratio)
                image.setPixelColor(x, y, QColor.fromHsvF(hue, saturation, brightness))

        return image

    def set_color(self, color: QColor):
        if not color.isValid():
            return
        hue, saturation, value, _alpha = color.getHsvF()
        # Grayscale colors have no defined hue. Retain the current hue in
        # that case while adopting the requested brightness.
        if hue >= 0:
            self._hue = hue
        self._value = max(0.0, min(1.0, value))
        self._saturation = max(0.0, min(1.0, saturation))
        self.update()

    def current_color(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._saturation, self._value)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, self._wheel_image)

        center_x = center_y = self.width() / 2
        radius = self.width() / 2 - 1
        angle = self._hue * 2 * math.pi
        marker_radius = self._radius_ratio(self._saturation, self._value) * radius
        marker_x = center_x + math.cos(angle) * marker_radius
        marker_y = center_y - math.sin(angle) * marker_radius

        painter.setPen(QPen(QColor("#000000"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(marker_x, marker_y), 6, 6)
        painter.setPen(QPen(QColor(TEXT), 1))
        painter.drawEllipse(QPointF(marker_x, marker_y), 7, 7)

    def _pick_at(self, position):
        center_x = center_y = self.width() / 2
        radius = self.width() / 2 - 1
        dx = position.x() - center_x
        dy = position.y() - center_y
        distance = math.hypot(dx, dy)
        if radius <= 0:
            return

        angle = math.atan2(-dy, dx)
        if angle < 0:
            angle += 2 * math.pi
        self._hue = angle / (2 * math.pi)
        self._saturation, self._value = self._saturation_value(min(1.0, distance / radius))
        self.update()
        self.colorChanged.emit(self.current_color())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_at(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_at(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)


class ColorWheelDialog(QDialog):
    """Color picker with a live swatch and explicit Apply/Cancel buttons."""

    def __init__(self, initial_color=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose LED Color")
        self.setModal(True)
        self.setFixedWidth(290)

        if initial_color is None:
            initial_color = QColor("#00ff00")
        initial_color = QColor(initial_color)
        self.wheel = ColorWheel(initial_color, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        layout.addWidget(self.wheel, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.preview = QLabel()
        self.preview.setFixedHeight(32)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)

        self.hex_label = QLabel()
        self.hex_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hex_label.setStyleSheet(f"color: {DIM}; font-family: Consolas, monospace;")
        layout.addWidget(self.hex_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel_button = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        buttons.addWidget(cancel_button)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)

        self.wheel.colorChanged.connect(self._update_preview)
        cancel_button.clicked.connect(self.reject)
        apply_button.clicked.connect(self.accept)
        self._update_preview(self.wheel.current_color())

    @property
    def selected_color(self) -> QColor:
        """Return the color currently selected in the wheel."""
        return QColor(self.wheel.current_color())

    def _update_preview(self, color: QColor):
        color = QColor(color)
        self.preview.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid {BORDER}; border-radius: 4px;"
        )
        self.hex_label.setText(color.name().upper())