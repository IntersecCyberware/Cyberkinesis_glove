"""
glove_gui.py - PyQt6 front-end for the Cyberkinesis glove.

Layout is modeled on the hand-drawn mockup:
    - connection panel (USB / WiFi icon, conn. status, device name)
    - LED button -> a real polar colour wheel to set the glove's RGB LED
    - Choose network button -> WiFi scan/connect dialog (USB only)
    - Key bindings button -> virtual Xbox controller analog editor
    - Debug console button -> live serial console (send/receive text)
    - finger status bar -> 5 vertical bars driven by the UDP sensor stream
    - Gyro -> XYZ axis indicator

Threading model
---------------
glove_backend.py's handshake/serial/UDP functions are the same blocking
calls the original console script used - they're just run inside QThreads
here instead of a `while True` console loop, and they report back to the
GUI thread with Qt signals instead of print().

    ConnectionWorker : runs the udp_hs()/ser_hs() handshake loop until a
                       device answers, then emits `connected`.
    UdpDataWorker    : runs the UDP receive loop, emits `sensor_data` for
                       every finger-sensor packet.
    SerialWorker     : runs the serial read loop (text console mode) AND
                       polls the UDP socket for a mode-switch handshake,
                       exactly like serial_console() did.

IMPORTANT / PROTOCOL NOTES
---------------------------
Confirmed from the device's own `h` help output, and cross-checked
against the firmware source itself:
    w <index> <ssid> <password>   - write WiFi credentials (index 0..7)
    r                              - show saved credentials
    c <index>                      - clear a saved network
    wifi_status                    - scan + try every saved network,
                                      report reachability, then
                                      disconnect again (NOT a quick
                                      status query - see below)
    led_set <color in HEX>         - set LED colour, hex digits, NO '#'
    adc_test                       - read 100 raw ADC datapoints

led_set takes bare hex digits, not a "#rrggbb" string - sending the '#'
made the device's hex parser (strtoul(..., 16)) read 0 every time. We
send color.name()[1:] to strip it.

The firmware only has MAX_NETWORKS=8 credential slots (0..7) and
silently replies "Invalid index" (saving nothing) if asked to write
outside that range. NetworkIndexStore hands a new SSID the lowest
currently-unused slot - reusing ones freed by "Forget" - rather than an
ever-increasing counter, so it can never run past slot 7 no matter how
many add/forget cycles happen.

"wifi_status" is a full connectivity TEST, not a status query: per
wifi_connect() in the firmware, it disconnects, scans, then tries the
last-known-good network first and (if that fails or isn't set) every
other saved network in slot order that's visible in the scan - up to
WIFI_TIMEOUT_MS (5s) *each* - before disconnecting again regardless of
the outcome. Worst case (several saved-but-unreachable networks in
range) is comfortably under a minute. Three things follow from that:
  1. It's sent exactly once per check, never on a repeating timer - the
     firmware processes serial commands one at a time and blocks for
     the whole test, so a second "wifi_status" sent while the first is
     still running just queues up *another* full scan-and-try-
     everything pass behind it rather than refreshing anything.
  2. Because it can try several networks in one run, the SSID that ends
     up connected isn't necessarily the one we just wrote - an already-
     saved, already-working network can easily answer first. See
     _on_wifi_check_line(): it tracks which SSID is currently being
     attempted ("Connecting to: X ..." / "Trying last network: X") so a
     "Connected to: X !" or "Failed, trying next..." only resolves our
     check when X is actually the network we asked about; a different
     network succeeding is reported as such rather than misread as ours.
  3. Because it always disconnects again at the end, _watch_for_ssid()
     - used for the *live* "what network are we on" status, e.g. after
     a real boot-time UDP-mode connection - only trusts a confirmed
     "Connected to: X !" line, never "Trying last network: X" (which is
     just an announcement of an attempt that may still fail).

wifi_connect() does persist a successful *new* connection as the
firmware's own "last known good" network (NVM "last_ssid"), even though
it disconnects again immediately after a wifi_status test - so on a
confirmed success we still record that SSID as current_ssid: it's what
the glove will actually try first next time it's put in WiFi mode, even
though it isn't live at that exact moment.

Each SSID we successfully write credentials for is remembered locally
against the WiFi-slot index it was written to (see NetworkIndexStore),
so connecting to a *new* network hands out the lowest free index instead
of always overwriting slot 0 like the original script did; reconnecting
to an already-known network reuses its existing index, and the "Forget
network" button clears a specific slot with "c <index>" and frees it for
reuse. This mapping is local bookkeeping only - the device is never
asked to enumerate its saved networks (the "r" command's output format
is unconfirmed), so it can drift out of sync if credentials are changed
some other way (a different GUI instance, a factory reset, etc).
Deleting NetworkIndexStore's file resets it.

WiFi *scanning* is local: it lists networks visible to this computer's
own WiFi radio (see wifi_scan.py), not something asked of the glove -
so Scan works even without the glove connected. Sending the chosen
network's credentials to the glove ("w <index> ...") still requires USB.

Writing WiFi credentials ("w <index> ...") only ever happens over
USB/serial - you configure WiFi while plugged in, before the glove
joins the network and switches to its UDP data stream. The glove keeps
its own WiFi credentials, so this GUI does not save network passwords
anywhere (it does keep a small local file mapping SSID -> slot index,
see NetworkIndexStore, but never the password itself).

Sending any command over UDP (not just wifi_status) is a dead end: the
firmware's UDP-mode loop only ever reads incoming UDP packets looking
for "ping_ok" (see udp_watchdog()) - cmd_exec(), which actually
understands commands like "wifi_status" or "led_set", only ever runs in
the Serial-mode branch of loop(). LED / network / debug-console are
correctly USB-only already (see _update_button_availability), but user-
defined Key Bindings shortcuts stay active in WiFi mode and would
previously fire straight into the void with no feedback; send_command()
now says so in the debug console instead of pretending it worked.

The custom LED colour (led_set) only ever visibly applies while the
glove is in WiFi/UDP mode (led_state only reaches LED_SETTING inside
loop()'s UDP branch, after a successful udp_handshake()) - while on USB
it always shows solid blue (LED_BLUE) regardless of what was last sent.
Picking a colour still saves correctly either way; it just won't be
visible until you switch to WiFi mode. The LED button's tooltip says so.

Mode switches used to leak the old serial port (SerialWorker returned on
a switch without closing it) - repeated switching would eventually leave
a port locked and unable to reopen, which is what made things flaky
after switching modes a few times. It's closed immediately now, in the
same thread, before the switch signal is even emitted.

UdpDataWorker used to give up on the WiFi link after just 1 full second
of socket silence - only ~2 ping intervals of margin over the firmware's
500ms watchdog ping, so a single dropped/delayed UDP datagram (routine
on real WiFi) was often enough to trip a false "connection lost" and
bounce back to the listening state even though the glove was still
there. See UDP_CONNECTION_TIMEOUT for that fix - but the bigger cause of
"UDP mode suddenly drops" turned out to be structural: the firmware's
own udp_watchdog() gives up after just 1s without a "ping_ok" reply and
drops back into udp_handshake(), re-broadcasting its ID+UDP_MODE
handshake packet every 500ms and blocking *forever* until it gets
"udp_ok" back. Nothing here used to recognise that packet once
UdpDataWorker (rather than ConnectionWorker) owned the socket, so it was
silently mis-decoded as garbage text and dropped - the glove would then
sit stuck re-broadcasting its handshake indefinitely, which looks to the
user exactly like a frozen/dropped connection, until the mode switch was
flipped. UdpDataWorker now recognises that packet (backend.
is_udp_handshake_packet()) and replies "udp_ok" so the glove resumes on
its own within one 500ms cycle instead of getting stuck.

The last LED colour is kept in a small plain-text file (just the hex
colour, e.g. "#a1b2c3") so it survives restarts - see SETTINGS_PATH
below for exactly where.
"""

import sys
import os
import re
import socket
import struct
import math
import time
import serial

try:
    import vgamepad as vg
except ImportError:
    vg = None
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPolygonF,
    QImage,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFrame,
    QVBoxLayout, QHBoxLayout, QDialog, QPlainTextEdit,
    QLineEdit, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QMessageBox, QSizePolicy, QInputDialog,
)

import comm_backend as backend
import wifi_scan


# ── Theme: black / dark grey / grey / white ─────────────────────────────

BG = "#0c0c0d"          # window background
PANEL_BG = "#161618"    # panel background
BORDER = "#3a3a3d"      # borders / outlines
ACCENT = "#e8e8ea"      # near-white, active/highlighted elements
TEXT = "#f1f1f2"        # primary text
DIM = "#6f6f74"         # inactive/secondary elements
FILL = "#a6a6ab"        # progress-bar fill
WARN = "#ff5a5a"        # kept only for the disconnected/error status text

# The gyro's X/Y/Z axes keep distinct hues on purpose - they're encoding
# three different data series, not decoration, so collapsing them to
# greyscale would make the three axes hard to tell apart at a glance.
AXIS_X = "#3ddc84"
AXIS_Y = "#4aa3ff"
AXIS_Z = WARN

# Same green as the gyro's X axis, reused as this app's general
# "good/connected" accent - the network dialog's "Connected" label and
# signal-strength bars use this rather than introducing a second green.
CONNECTED_GREEN = AXIS_X

STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {BG};
    color: {TEXT};
}}
QLabel {{
    color: {TEXT};
}}
QFrame#panel {{
    background-color: {PANEL_BG};
    border: 2px solid {BORDER};
    border-radius: 6px;
}}
QPushButton {{
    background-color: #1c1c1f;
    color: {TEXT};
    border: 2px solid {BORDER};
    border-radius: 5px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #232326;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: #0c0c0d;
}}
QPushButton:disabled {{
    background-color: #131315;
    color: {DIM};
    border-color: {DIM};
}}
QLineEdit, QPlainTextEdit, QListWidget, QTableWidget {{
    background-color: #0a0a0b;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {ACCENT};
    selection-color: #0c0c0d;
    font-family: "Consolas", "Monaco", monospace;
}}
QHeaderView::section {{
    background-color: #1c1c1f;
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
"""


# ── WiFi status line parsing (confirmed against the firmware source) ───

# wifi_connect() only ever prints "Connected to: X !" once WiFi.status()
# has actually confirmed the association - so this is the one line that
# means "X is genuinely connected right now", and is what both the live
# status tracker (_watch_for_ssid) and the wifi_status check
# (_on_wifi_check_line) treat as an authoritative success for X.
_SSID_CONNECTED_RE = re.compile(r"Connected to:\s*(.+?)\s*!\s*$", re.IGNORECASE)


def _extract_ssid_announcement(line):
    match = _SSID_CONNECTED_RE.search(line.strip())
    return match.group(1).strip() if match else None


# "Connecting to: X ..." / "Trying last network: X" only announce that an
# attempt is *starting* - the attempt can still fail, so on their own
# they must never be read as "we're on X now" (that was the bug: the old
# code's SSID-announcement regex matched "Trying last network: X" too,
# so a merely-attempted-and-then-failed network could still get shown as
# the current one). They're only useful for tracking which SSID a
# wifi_status check is *currently* trying, so a later "Connected to:" or
# "Failed, trying next..." line can be attributed to the right network.
_WIFI_ATTEMPT_RE = re.compile(
    r"(?:Connecting to|Trying last network):\s*(.+)$", re.IGNORECASE
)


def _extract_wifi_attempt_ssid(line):
    match = _WIFI_ATTEMPT_RE.search(line.strip())
    if not match:
        return None
    ssid = match.group(1).rstrip(" .").strip()
    return ssid or None


# Lines wifi_connect()/cmd_exec() print when NOTHING it tried worked -
# these are unambiguous failures regardless of which network we were
# hoping to confirm, since if nothing connected, our target certainly
# didn't either.
_WIFI_TOTAL_FAILURE_HINTS = (
    "could not connect to any stored network",
    "no wifi networks found",
)

# Printed immediately after a "Connecting to: X ..." / "Trying last
# network: X" line when that specific attempt didn't pan out.
_WIFI_ATTEMPT_FAILURE_HINTS = ("failed, trying next", "last network failed")




# ── Local settings (LED colour only - WiFi creds live on the glove) ────

# Saved next to the project's own files (this script's folder), not the
# user's home directory, so it's easy to find alongside the rest of the
# project rather than tucked away as a hidden dotfile elsewhere.
SETTINGS_PATH = Path(__file__).resolve().parent / "config.txt"


class SettingsStore:
    """
    Tiny local settings file for the last LED colour, stored as a plain
    hex line (e.g. "#a1b2c3") - the same format sent to the glove, so
    it's easy to open and check by hand. Errors are printed (not
    swallowed) so a failed save/load is visible instead of just quietly
    not happening.
    """

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self._led_color = None  # QColor or None
        self.load()

    def load(self):
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return
        except OSError as exc:
            print(f"[settings] could not read {self.path}: {exc}")
            return

        color = QColor(text)
        if color.isValid():
            self._led_color = color
        else:
            print(f"[settings] ignoring malformed contents of {self.path}: {text!r}")

    def save(self):
        if self._led_color is None:
            return
        try:
            self.path.write_text(self._led_color.name() + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[settings] could not write {self.path}: {exc}")

    @property
    def led_color(self):
        return self._led_color

    @led_color.setter
    def led_color(self, color: QColor):
        self._led_color = QColor(color)
        self.save()


# ── Local WiFi-slot index bookkeeping ───────────────────────────────────

# Saved next to SETTINGS_PATH, for the same reason (easy to find and to
# open by hand rather than tucked away as a hidden dotfile).
NETWORKS_PATH = Path(__file__).resolve().parent / "networks.txt"

# Matches MAX_NETWORKS in the firmware: wifi_cred_write()/wifi_cred_clear()
# both reject (Serial.println("Invalid index"), nothing saved) any index
# outside 0..MAX_SAVED_NETWORKS-1. An earlier version of this store used
# an ever-increasing counter that never reused a slot even after
# "Forget" - harmless-looking, but it would eventually count past 7 and
# start sending writes the firmware just silently drops. Slots are
# reused (lowest free first) instead, so it can never run out as long as
# at most MAX_SAVED_NETWORKS networks are saved at once.
MAX_SAVED_NETWORKS = 8


class NetworkIndexStore:
    """
    Tracks which WiFi slot index (as used by the device's "w <index> ssid
    password" / "c <index>" commands) each SSID we've connected through
    this GUI was saved to, so a new network gets a free index instead of
    always overwriting slot 0 like the original script did.

    This is purely local bookkeeping, not read back from the device -
    there's no confirmed command to list "ssid at index N" (only "r",
    whose output format is unconfirmed). So this can drift out of sync
    with the glove's actual saved list if credentials are changed by
    some other means (a different GUI instance, manual serial commands,
    a factory reset, etc). If that happens, the fix is to edit or delete
    this file.

    Stored as plain "ssid=index" lines, one per network, for the same
    "easy to open and check by hand" reason as SettingsStore's LED file.
    """

    def __init__(self, path=NETWORKS_PATH):
        self.path = path
        self._index_by_ssid = {}   # ssid -> int, 0..MAX_SAVED_NETWORKS-1
        self.load()

    def load(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            print(f"[settings] could not read {self.path}: {exc}")
            return

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            ssid, sep, index_str = line.rpartition("=")
            if not sep:
                print(f"[settings] ignoring malformed line {line_no} in {self.path}: {raw_line!r}")
                continue
            try:
                index = int(index_str.strip())
            except ValueError:
                print(f"[settings] ignoring malformed line {line_no} in {self.path}: {raw_line!r}")
                continue
            if not (0 <= index < MAX_SAVED_NETWORKS):
                print(f"[settings] ignoring out-of-range slot in {self.path}: {raw_line!r}")
                continue
            self._index_by_ssid[ssid] = index

    def save(self):
        try:
            lines = [f"{ssid}={index}" for ssid, index in self._index_by_ssid.items()]
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except OSError as exc:
            print(f"[settings] could not write {self.path}: {exc}")

    def known_ssids(self):
        return set(self._index_by_ssid.keys())

    def index_for(self, ssid):
        return self._index_by_ssid.get(ssid)

    def assign_index(self, ssid):
        """Returns the existing slot index for `ssid` if we already have
        one, otherwise allocates and persists the lowest free slot in
        0..MAX_SAVED_NETWORKS-1. Returns None if all slots are taken -
        callers must check for that rather than sending an out-of-range
        index the firmware will just reject."""
        if ssid in self._index_by_ssid:
            return self._index_by_ssid[ssid]
        used = set(self._index_by_ssid.values())
        for index in range(MAX_SAVED_NETWORKS):
            if index not in used:
                self._index_by_ssid[ssid] = index
                self.save()
                return index
        return None

    def forget(self, ssid):
        if ssid in self._index_by_ssid:
            del self._index_by_ssid[ssid]
            self.save()


# ── Finger status bar widgets ───────────────────────────────────────────

# The firmware sends calibrated finger percentages. The thumb calibration
# currently tops out at about 42%, while the physical thumb range is meant
# to represent the full 0-100% input range. Compensate for that here so the
# displayed bar and the virtual controller use the same corrected value.
# Change this single value if the thumb firmware calibration is changed.
FINGER_RAW_MAX = 100
THUMB_MAX_PERCENT = 42.0


def normalize_finger_values(values):
    """Return glove finger values normalized to the GUI's 0-100% range.

    The four non-thumb sensors already use their full 0-100% range.
    The current thumb calibration reaches only THUMB_MAX_PERCENT, so its
    value is scaled up and clipped to 100%.
    """
    normalized = []
    for index, value in enumerate(values):
        value = float(value)
        if index == 0:  # thumb
            value = value * (100.0 / THUMB_MAX_PERCENT)
        normalized.append(max(0.0, min(100.0, value)))
    return tuple(normalized)


class FingerBar(QWidget):
    """A single vertical bar showing one finger's flex-sensor reading,
    displayed as a 0-100 percentage rather than the raw sensor units."""

    def __init__(self, label, max_value=100, parent=None):
        super().__init__(parent)
        self.label = label
        self.max_value = max_value
        self.value = 0
        self.setMinimumSize(36, 140)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def set_value(self, value):
        self.value = max(0, min(self.max_value, value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        label_h = 18
        bar_rect_top = label_h
        bar_h = h - label_h - 16

        # Outline
        painter.setPen(QPen(QColor(BORDER), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(2, bar_rect_top, w - 4, bar_h)

        # Fill, proportional to value. Solid (not hatched) so 100% reads
        # as a fully, solidly coloured bar rather than a pattern with
        # gaps in it.
        frac = self.value / self.max_value if self.max_value else 0
        fill_h = round(bar_h * frac)
        fill_rect_y = bar_rect_top + (bar_h - fill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(FILL)))
        painter.drawRect(2, fill_rect_y - 1, w - 4, fill_h + 1)

        # Label on top, value underneath
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        painter.drawText(0, 0, w, label_h, Qt.AlignmentFlag.AlignCenter, self.label)
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(0, h - 14, w, 14, Qt.AlignmentFlag.AlignCenter, f"{self.value}%")


class FingerBarsPanel(QWidget):
    """Row of 5 FingerBars: thumb, pointer, middle, ring, pinkie."""

    NAMES = ["T", "P", "M", "R", "F"]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("finger status bar")
        title.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
        layout.addWidget(title)

        bars_row = QHBoxLayout()
        self.bars = []
        for name in self.NAMES:
            bar = FingerBar(name)
            self.bars.append(bar)
            bars_row.addWidget(bar)
        layout.addLayout(bars_row)

    def update_values(self, values):
        """Display the same normalized values used by the controller."""
        values = normalize_finger_values(values)
        for bar, v in zip(self.bars, values):
            percent = round(min(v, FINGER_RAW_MAX) / FINGER_RAW_MAX * 100) if FINGER_RAW_MAX else 0
            bar.set_value(percent)


# ── Gyro widget ───────────────────────────────────────────────────────────

class GyroWidget(QWidget):
    """
    XYZ axis indicator. The sensor packet the backend receives
    (SENSOR_PACKET_FMT = "<5H") only carries the 5 finger values - there's
    no orientation data on the wire yet, so this renders a static axis
    triad like the sketch. If the firmware/packet format is extended to
    include orientation, feed it in via set_orientation() and hook up a
    rotation transform here.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_orientation(self, *_args, **_kwargs):
        # Placeholder hook for future extension - no-op until the backend
        # actually transmits orientation data.
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor(BORDER), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(2, 2, w - 4, h - 4)

        cx, cy = w * 0.42, h * 0.62
        painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))

        # Y - up
        painter.setPen(QPen(QColor(AXIS_Y), 3))
        painter.drawLine(int(cx), int(cy), int(cx), int(cy - h * 0.32))
        painter.drawText(int(cx) - 6, int(cy - h * 0.32) - 8, "Y")

        # X - right
        painter.setPen(QPen(QColor(AXIS_X), 3))
        painter.drawLine(int(cx), int(cy), int(cx + w * 0.36), int(cy))
        painter.drawText(int(cx + w * 0.36) + 6, int(cy) + 4, "X")

        # Z - down-left
        painter.setPen(QPen(QColor(AXIS_Z), 3))
        painter.drawLine(int(cx), int(cy), int(cx - w * 0.14), int(cy + h * 0.16))
        painter.drawText(int(cx - w * 0.14) - 14, int(cy + h * 0.16) + 16, "Z")

        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        painter.drawText(0, 4, w - 8, 18, Qt.AlignmentFlag.AlignRight, "Gyro")


# ── Connection icons ─────────────────────────────────────────────────────

class ConnIcon(QWidget):
    """
    Small vector icon for a transport (USB plug / WiFi signal), drawn
    instead of a text label so the connection row reads at a glance.
    Lights up (ACCENT) when that transport is the active one, otherwise
    sits dim (DIM).
    """

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind  # "usb" or "wifi"
        self.active = False
        self.setFixedSize(28, 28)
        self.setToolTip("USB" if kind == "usb" else "WiFi")

    def set_active(self, active):
        if self.active != active:
            self.active = active
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT if self.active else DIM)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        if self.kind == "wifi":
            base_y = cy + 8
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(cx, base_y), 1.6, 1.6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for r in (6.0, 10.5, 15.0):
                rect = QRectF(cx - r, base_y - r, r * 2, r * 2)
                painter.drawArc(rect, 45 * 16, 90 * 16)
        else:
            stem_top = QPointF(cx, cy - 2)
            stem_bottom = QPointF(cx, cy + 8)
            painter.drawLine(stem_top, stem_bottom)

            left_end = QPointF(cx - 6, cy - 9)
            right_end = QPointF(cx + 6, cy - 9)
            painter.drawLine(stem_top, left_end)
            painter.drawLine(stem_top, right_end)

            painter.setBrush(QBrush(color))
            painter.drawEllipse(left_end, 1.8, 1.8)
            triangle = QPolygonF([
                QPointF(right_end.x(), right_end.y() - 2.4),
                QPointF(right_end.x() - 2.2, right_end.y() + 1.6),
                QPointF(right_end.x() + 2.2, right_end.y() + 1.6),
            ])
            painter.drawPolygon(triangle)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(cx - 3, stem_bottom.y() - 1, 6, 5))


# ── Colour wheel ─────────────────────────────────────────────────────────

class ColorWheel(QWidget):
    """
    A real polar colour wheel: angle = hue, radius = brightness, at full
    saturation - black at the centre fading out to fully-saturated hues
    at the rim, matching a classic RGB colour wheel. Click or drag to
    pick a colour; colorChanged fires on every pick (including while
    dragging).
    """

    colorChanged = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(240, 240)
        self._hue = 0.0     # 0..1
        self._value = 1.0   # 0..1 (radial brightness, saturation fixed at 1)
        self._wheel_image = self._build_wheel_image(self.width())

    @staticmethod
    def _build_wheel_image(side):
        img = QImage(side, side, QImage.Format.Format_ARGB32)
        img.fill(0)
        cx = cy = side / 2
        radius = side / 2 - 1
        for y in range(side):
            dy = y - cy
            for x in range(side):
                dx = x - cx
                r = math.hypot(dx, dy)
                if r > radius:
                    continue
                angle = math.atan2(-dy, dx)
                if angle < 0:
                    angle += 2 * math.pi
                hue = angle / (2 * math.pi)
                value = min(1.0, r / radius) if radius else 0.0
                img.setPixelColor(x, y, QColor.fromHsvF(hue, 1.0, value))
        return img

    def set_color(self, color: QColor):
        h, _s, v, _a = color.getHsvF()
        self._hue = h if h >= 0 else 0.0
        self._value = v
        self.update()

    def current_color(self):
        return QColor.fromHsvF(self._hue, 1.0, self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, self._wheel_image)

        side = self.width()
        cx = cy = side / 2
        radius = side / 2 - 1
        angle = self._hue * 2 * math.pi
        r = self._value * radius
        mx = cx + math.cos(angle) * r
        my = cy - math.sin(angle) * r

        painter.setPen(QPen(QColor("#000000"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(mx, my), 6, 6)
        painter.setPen(QPen(QColor(TEXT), 1))
        painter.drawEllipse(QPointF(mx, my), 7, 7)

    def _pick_at(self, pos):
        side = self.width()
        cx = cy = side / 2
        radius = side / 2 - 1
        dx = pos.x() - cx
        dy = pos.y() - cy
        r = math.hypot(dx, dy)
        if radius <= 0:
            return
        angle = math.atan2(-dy, dx)
        if angle < 0:
            angle += 2 * math.pi
        self._hue = angle / (2 * math.pi)
        self._value = min(1.0, r / radius)
        self.update()
        self.colorChanged.emit(self.current_color())

    def mousePressEvent(self, event):
        self._pick_at(event.position())
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_at(event.position())
        event.accept()


class ColorWheelDialog(QDialog):
    """LED colour picker: the wheel above, a live preview swatch below."""

    def __init__(self, initial_color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LED Color")

        layout = QVBoxLayout(self)

        self.wheel = ColorWheel()
        self.wheel.set_color(initial_color)
        layout.addWidget(self.wheel, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.preview = QLabel()
        self.preview.setFixedHeight(28)
        self._update_preview(initial_color)
        layout.addWidget(self.preview)

        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.accept)
        layout.addWidget(done_btn)

        self.wheel.colorChanged.connect(self._update_preview)

    def _update_preview(self, color):
        self.preview.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid {BORDER}; border-radius: 4px;"
        )


# ── Worker threads ────────────────────────────────────────────────────────

class ConnectionWorker(QThread):
    """
    Mirrors the original listener(): try a UDP handshake, then scan
    serial ports, repeat until something answers or we're asked to stop.

    Uses its own port-scan loop instead of calling backend.ser_hs()
    directly: that call has no way to be interrupted mid-scan (up to 1s
    per port, blocking), which meant request_stop() during a scan didn't
    actually stop anything until the scan finished on its own - closing
    the window while this was running left the thread (and whatever
    serial port it was mid-probe on) running in the background even
    after the GUI had visibly closed. This version checks self._stop
    roughly every 10ms, from anywhere in the scan.
    """

    connected = pyqtSignal(str, int, object)  # mode ("udp"/"serial"), device_id, ser or None

    def __init__(self, udp_sock, parent=None):
        super().__init__(parent)
        self.udp_sock = udp_sock
        self._stop = False
        # Set right before emitting a successful serial handshake, in
        # case the GUI thread doesn't get to process that queued signal
        # before the window closes (a real race: the thread can finish a
        # handshake and open a port in the same instant the window is
        # closing). closeEvent checks this directly, since by then it
        # can't rely on _on_connected having run.
        self.pending_ser = None

    def request_stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            try:
                device_id = backend.udp_hs(self.udp_sock, timeout=0.3)
            except OSError:
                return  # socket was closed out from under us - shutting down
            if device_id is not None:
                self.connected.emit("udp", device_id, None)
                return

            if self._stop:
                return

            result = self._interruptible_ser_hs()
            if result is not None:
                ser, device_id = result
                self.pending_ser = ser
                self.connected.emit("serial", device_id, ser)
                return

    def _interruptible_ser_hs(self, baudrate=115200):
        """Same handshake logic as backend.ser_hs(), reimplemented with
        frequent self._stop checks so it can actually be interrupted."""
        for port in serial.tools.list_ports.comports():
            if self._stop:
                return None

            try:
                ser = backend.open_serial_no_reset(port.device, baudrate)
            except (serial.SerialException, OSError):
                continue

            start_time = time.monotonic()
            device_id = None
            found = False
            while time.monotonic() - start_time < 1.0:
                if self._stop:
                    ser.close()
                    return None

                if ser.in_waiting >= 2:
                    device_id = ser.read(1)[0]
                    mode = ser.read(1)[0]
                    if mode == backend.SER_MODE:
                        ser.write(b"ser_ok\n")
                        found = True
                        break

                time.sleep(0.01)

            if found:
                return ser, device_id
            ser.close()

        return None


# How long UdpDataWorker will wait for *any* UDP packet (sensor data or
# a udp_ping) before deciding the WiFi link is dead. The firmware pings
# every 500ms, but UDP has no delivery guarantee - a single dropped or
# delayed datagram is routine on real WiFi. The old value here (1.0s,
# barely 2 ping intervals) had no slack for that: losing just one ping
# pushed the gap between received packets right up to the timeout, so
# an ordinary bit of WiFi jitter or packet loss was often enough to
# trigger a false "connection lost" and bounce back to the listening
# state even though the glove was still there. A handful of ping
# intervals of slack fixes that while still catching a genuine drop
# within a few seconds.
UDP_CONNECTION_TIMEOUT = 2.5  # seconds


class UdpDataWorker(QThread):
    """
    Runs the UDP sensor-stream loop (mirrors run_udp/udp_receive). Also
    surfaces anything that's neither a sensor packet nor a watchdog ping
    as text via text_received - the backend's UDP path was designed as a
    one-way sensor stream, so whether the device's command console also
    listens here at all is unconfirmed; this just means we don't
    silently drop a reply if it does.
    """

    sensor_data = pyqtSignal(tuple)
    text_received = pyqtSignal(str)
    connection_lost = pyqtSignal()

    def __init__(self, udp_sock, parent=None):
        super().__init__(parent)
        self.udp_sock = udp_sock
        self.last_addr = None  # tracked here (not in backend.udp_receive)
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        self.udp_sock.settimeout(UDP_CONNECTION_TIMEOUT)
        while not self._stop:
            try:
                data, addr = self.udp_sock.recvfrom(4096)
            except socket.timeout:
                self.connection_lost.emit()
                return
            except OSError:
                if self._stop:
                    return
                self.connection_lost.emit()
                return

            if data == b"udp_ping":
                try:
                    self.udp_sock.sendto(b"ping_ok", addr)
                except OSError:
                    pass
                continue

            self.last_addr = addr  # any packet at all tells us where to reply

            if backend.is_udp_handshake_packet(data):
                # The firmware's own udp_watchdog() gave up (no ping_ok
                # within its ~1s budget) and dropped back into
                # udp_handshake() - it's now re-broadcasting this exact
                # packet every 500ms and will block forever until it
                # gets "udp_ok" back. Answer it so it resumes right away
                # instead of sitting stuck (see the module docstring).
                try:
                    self.udp_sock.sendto(b"udp_ok", addr)
                except OSError:
                    pass
                continue

            if len(data) != backend.SENSOR_PACKET_SIZE:
                text = data.decode("utf-8", errors="replace").strip()
                if text:
                    self.text_received.emit(text)
                continue

            try:
                values = struct.unpack(backend.SENSOR_PACKET_FMT, data)
            except struct.error:
                continue

            self.sensor_data.emit(values)


class SerialWorker(QThread):
    """
    Mirrors serial_console(): reads text lines from the serial port and
    polls the UDP socket (non-blockingly) for a mode-switch handshake.
    Writing to the port is done directly from the GUI thread via
    MainWindow.send_command() - pyserial's write() is safe to call from a
    different thread than the one doing blocking reads.
    """

    line_received = pyqtSignal(str)
    switched_to_udp = pyqtSignal(int)
    serial_lost = pyqtSignal()

    def __init__(self, ser, udp_sock, parent=None):
        super().__init__(parent)
        self.ser = ser
        self.udp_sock = udp_sock
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        line_buf = backend.SerialLineBuffer()
        try:
            while not self._stop:
                data = backend.ser_receive(self.ser)
                if data is not None:
                    line_buf.feed(data)
                    for line in line_buf.lines():
                        self.line_received.emit(line)

                switched_device_id = backend.check_udp_handshake(self.udp_sock, timeout=0.0)
                if switched_device_id is not None:
                    # The device switched transports, but the OS-level
                    # serial port is still open on our end - close it
                    # here, in this thread, before emitting. Leaving it
                    # open (previously: nobody ever closed it) meant the
                    # port stayed locked indefinitely, which is what made
                    # repeated mode switches increasingly unreliable -
                    # eventually a later re-open of the same port would
                    # fail or behave inconsistently while the leaked
                    # handle was still sitting open.
                    self._close_ser()
                    self.switched_to_udp.emit(switched_device_id)
                    return

                self.msleep(1)
        except OSError:
            if not self._stop:
                self._close_ser()
                self.serial_lost.emit()
            return

        if not self._stop:
            self._close_ser()
            self.serial_lost.emit()

    def _close_ser(self):
        try:
            self.ser.close()
        except Exception:
            pass


class WifiScanWorker(QThread):
    """
    Scans for WiFi networks visible to this computer (see wifi_scan.py) -
    off the GUI thread since the OS command it shells out to can take a
    few seconds.
    """

    finished_scan = pyqtSignal(list)

    def run(self):
        self.finished_scan.emit(wifi_scan.scan_wifi_networks())


# ── Debug console dialog ──────────────────────────────────────────────────

class DebugConsoleDialog(QDialog):
    command_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Debug Console")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command and press Enter\u2026")
        self.input.returnPressed.connect(self._submit)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._submit)
        input_row.addWidget(self.input)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

    def _submit(self):
        text = self.input.text().strip()
        if not text:
            return
        self.command_submitted.emit(text)
        self.append_line(f"> {text}")
        self.input.clear()

    def append_line(self, text):
        self.output.appendPlainText(text)

    def closeEvent(self, event):
        # Keep the console (and its history) alive in the background
        # instead of destroying it, so log lines aren't lost while hidden.
        event.ignore()
        self.hide()


# ── Network list row (signal bar / connected label / forget) ───────────

class SignalBarWidget(QWidget):
    """
    Small 5-bar WiFi signal-strength indicator - green bars filled up to
    `percent` (0-100), outline-only for the rest, matching the usual
    phone/laptop WiFi icon.

    `percent=None` means this scan couldn't determine a strength for
    this network - not every OS's scan command reports one (see
    wifi_scan.py) - and is drawn as all-outline/no fill rather than
    guessing a number.
    """

    BAR_COUNT = 5

    def __init__(self, percent=None, parent=None):
        super().__init__(parent)
        self._percent = percent
        self.setFixedSize(38, 20)

    def set_percent(self, percent):
        self._percent = percent
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        n = self.BAR_COUNT
        filled = 0
        if self._percent is not None:
            filled = min(n, max(0, math.ceil(max(0, self._percent) / 100 * n)))

        bar_w = 4.0
        gap = 2.5
        total_w = n * bar_w + (n - 1) * gap
        x0 = (self.width() - total_w) / 2
        base_y = self.height() - 2
        usable_h = self.height() - 5

        for i in range(n):
            bar_h = (i + 1) / n * usable_h
            x = x0 + i * (bar_w + gap)
            rect = QRectF(x, base_y - bar_h, bar_w, bar_h)
            if i < filled:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(CONNECTED_GREEN)))
            else:
                painter.setPen(QPen(QColor(BORDER), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)


class NetworkRow(QWidget):
    """
    One row in the "Choose Network" list: SSID on the left, then either a
    green "Connected" label (the network the glove is on right now) or a
    SignalBarWidget, then a "Forget" button if this GUI has a saved
    WiFi-slot index for it (see NetworkIndexStore).

    Clicking anywhere on the row except the Forget button requests a
    connect; the currently-connected row isn't clickable for that, since
    there's nothing to do. The SSID label and signal bar are marked
    mouse-transparent so clicks on them fall through to this row's own
    mousePressEvent instead of being swallowed by the child widget.
    """

    connect_requested = pyqtSignal(str)
    forget_requested = pyqtSignal(str)

    def __init__(self, ssid, signal_percent, is_connected, is_known, parent=None):
        super().__init__(parent)
        self.ssid = ssid
        self.is_connected = is_connected

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        name_label = QLabel(ssid)
        name_label.setStyleSheet(f"color: {TEXT};")
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(name_label, 1)

        if is_connected:
            status_label = QLabel("Connected")
            status_label.setStyleSheet(f"color: {CONNECTED_GREEN}; font-weight: 600;")
            status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(status_label)
        else:
            signal_widget = SignalBarWidget(signal_percent)
            signal_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(signal_widget)

        if is_known:
            forget_btn = QPushButton("Forget")
            forget_btn.setFixedHeight(24)
            forget_btn.clicked.connect(lambda: self.forget_requested.emit(self.ssid))
            layout.addWidget(forget_btn)

        self.setCursor(
            Qt.CursorShape.ArrowCursor if is_connected else Qt.CursorShape.PointingHandCursor
        )

    def mousePressEvent(self, event):
        if not self.is_connected and event.button() == Qt.MouseButton.LeftButton:
            self.connect_requested.emit(self.ssid)
        super().mousePressEvent(event)


# ── Network dialog ────────────────────────────────────────────────────────

class NetworkDialog(QDialog):
    """
    Pick a network, get asked for its password, done. No SSID/password
    fields to fill in by hand, and nothing is remembered here beyond the
    WiFi-slot index bookkeeping (see NetworkIndexStore) - the glove keeps
    its own WiFi credentials.

    Each row shows the SSID plus either a green "Connected" label (the
    network the glove is on right now) or a signal-strength bar (from
    this computer's own local WiFi scan - see wifi_scan.py), and a
    "Forget" button for any network this GUI has previously saved
    credentials for.
    """

    scan_requested = pyqtSignal()
    network_selected = pyqtSignal(str)   # ssid
    network_forgotten = pyqtSignal(str)  # ssid

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Network")
        self.resize(360, 340)

        self._current_ssid = None
        self._known_ssids = set()
        self._rows = {}             # ssid -> QListWidgetItem
        self._network_signal = {}   # ssid -> signal percent or None

        layout = QVBoxLayout(self)

        note = QLabel(
            "Scan lists WiFi networks visible to this computer. Click\n"
            "one to connect - you'll be asked for its password, which\n"
            "is then sent to the glove over USB. The glove keeps its\n"
            "own credentials; this computer only remembers which WiFi\n"
            "slot each network was saved to, not its password."
        )
        note.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.current_label = QLabel("Currently connected: unknown")
        self.current_label.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
        layout.addWidget(self.current_label)

        layout.addWidget(QLabel("WiFi networks"))
        self.networks_list = QListWidget()
        layout.addWidget(self.networks_list)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self.scan_btn)

    def set_current_network(self, ssid):
        self._current_ssid = ssid
        self.current_label.setText(
            f"Currently connected: {ssid}" if ssid else "Currently connected: not connected"
        )
        for existing_ssid in list(self._rows.keys()):
            self._update_row(existing_ssid)
        if ssid:
            # The device may be connected to a network this computer's own
            # WiFi radio hasn't seen in a scan (out of range, hidden, or we
            # just haven't scanned yet) - add/keep it in the list anyway so
            # "Connected" is always visible somewhere.
            self.add_network(ssid, self._network_signal.get(ssid))

    def set_known_ssids(self, ssids):
        """ssids this GUI has a saved WiFi-slot index for (see
        NetworkIndexStore) - these get a "Forget" button."""
        self._known_ssids = set(ssids)
        for existing_ssid in list(self._rows.keys()):
            self._update_row(existing_ssid)

    def _on_scan_clicked(self):
        self.networks_list.clear()
        self._rows.clear()
        self._network_signal.clear()
        self.scan_requested.emit()
        if self._current_ssid:
            self.add_network(self._current_ssid, None)

    def set_scanning(self, scanning):
        self.scan_btn.setEnabled(not scanning)
        self.scan_btn.setText("Scanning\u2026" if scanning else "Scan")

    def add_network(self, ssid, signal_percent=None):
        if not ssid:
            return
        self._network_signal[ssid] = signal_percent
        if ssid not in self._rows:
            item = QListWidgetItem()
            self.networks_list.addItem(item)
            self._rows[ssid] = item
        self._update_row(ssid)

    def _update_row(self, ssid):
        item = self._rows.get(ssid)
        if item is None:
            return
        row = NetworkRow(
            ssid,
            self._network_signal.get(ssid),
            is_connected=(ssid == self._current_ssid),
            is_known=(ssid in self._known_ssids),
        )
        row.connect_requested.connect(self.network_selected.emit)
        row.forget_requested.connect(self.network_forgotten.emit)
        item.setSizeHint(row.sizeHint())
        self.networks_list.setItemWidget(item, row)

    def closeEvent(self, event):
        event.ignore()
        self.hide()


# ── Controller bindings ------------------------------------------------------

BINDINGS_PATH = Path(__file__).resolve().parent / "bindings.txt"
FINGER_IDS = ("thumb", "pointer", "middle", "ring", "pinkie")
FINGER_NAMES = {
    "thumb": "Thumb",
    "pointer": "Pointer",
    "middle": "Middle",
    "ring": "Ring",
    "pinkie": "Pinkie",
}

# Each finger can drive one analog Xbox/XInput channel. For sticks, the
# +/- variants select the direction while 0% remains the stick center.
CONTROLLER_INPUTS = (
    ("Unassigned", "none"),
    ("Left Stick X +", "lx+"),
    ("Left Stick X -", "lx-"),
    ("Left Stick Y +", "ly+"),
    ("Left Stick Y -", "ly-"),
    ("Right Stick X +", "rx+"),
    ("Right Stick X -", "rx-"),
    ("Right Stick Y +", "ry+"),
    ("Right Stick Y -", "ry-"),
    ("Left Trigger", "lt"),
    ("Right Trigger", "rt"),
)
CONTROLLER_INPUT_IDS = {value for _, value in CONTROLLER_INPUTS}
CONTROLLER_INPUT_NAMES = dict(CONTROLLER_INPUTS)

# Safe, immediately useful defaults. They are only used when no bindings.txt
# exists yet; nothing is sent until a glove produces sensor data.
DEFAULT_CONTROLLER_BINDINGS = {
    "thumb": "rt",
    "pointer": "lt",
    "middle": "lx+",
    "ring": "ly+",
    "pinkie": "rx+",
}


class KeyBindingsStore:
    """Persistent one-controller-input-per-finger bindings."""

    def __init__(self, path=BINDINGS_PATH):
        self.path = path
        self._bindings = {
            finger: {"control": DEFAULT_CONTROLLER_BINDINGS[finger]}
            for finger in FINGER_IDS
        }
        self.load()

    def load(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            print(f"[bindings] could not read {self.path}: {exc}")
            return

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("|", 1)
            if len(parts) != 2:
                # Old versions used finger|key|threshold. There is no safe
                # semantic conversion to an analog controller target, so
                # leave the default for that finger instead of guessing.
                print(f"[bindings] ignoring legacy/malformed line {line_no} in {self.path}: {raw_line!r}")
                continue

            finger, control = (part.strip() for part in parts)
            if finger not in FINGER_IDS:
                print(f"[bindings] ignoring unknown finger on line {line_no} in {self.path}: {raw_line!r}")
                continue
            if control not in CONTROLLER_INPUT_IDS:
                print(f"[bindings] ignoring unknown controller input on line {line_no} in {self.path}: {raw_line!r}")
                continue

            self._bindings[finger] = {"control": control}

    def save(self):
        lines = [
            f"{finger}|{self._bindings[finger]['control']}"
            for finger in FINGER_IDS
        ]
        try:
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[bindings] could not write {self.path}: {exc}")

    def all(self):
        return [
            (finger, self._bindings[finger]["control"])
            for finger in FINGER_IDS
        ]

    def set_all(self, bindings):
        new_bindings = {
            finger: {"control": DEFAULT_CONTROLLER_BINDINGS[finger]}
            for finger in FINGER_IDS
        }
        for finger, control in bindings:
            if finger in FINGER_IDS and control in CONTROLLER_INPUT_IDS:
                new_bindings[finger] = {"control": control}
        self._bindings = new_bindings
        self.save()


class VirtualXboxController:
    """Small adapter that maps glove percentages onto a virtual Xbox 360 pad."""

    def __init__(self):
        self.gamepad = None
        self.error = None
        self._error_reported = False

        if os.name != "nt":
            self.error = "Virtual Xbox controller requires Windows."
            return

        if vg is None:
            self.error = "vgamepad is not installed. Run: pip install vgamepad"
            return

        try:
            self.gamepad = vg.VX360Gamepad()
            self.reset()
        except Exception as exc:
            self.error = f"Could not create virtual Xbox controller: {exc}"

    @property
    def available(self):
        return self.gamepad is not None

    def _report_error_once(self):
        if not self._error_reported and self.error:
            print(f"[controller] {self.error}")
            self._error_reported = True

    @staticmethod
    def _clamp_percent(value):
        return max(0.0, min(100.0, float(value))) / 100.0

    def update(self, bindings, values):
        if not self.available:
            self._report_error_once()
            return

        # Normalize the glove values once here so the virtual controller
        # receives exactly the same corrected percentages shown by the
        # finger bars.
        values = normalize_finger_values(values)
        finger_values = dict(zip(FINGER_IDS, values))

        # 0% = neutral. The two directions can be driven independently and
        # are summed before clipping, which also makes opposing bindings work.
        lx = 0.0
        ly = 0.0
        rx = 0.0
        ry = 0.0
        lt = 0.0
        rt = 0.0

        for finger, control in bindings:
            if control == "none":
                continue

            value = self._clamp_percent(finger_values.get(finger, 0.0))

            if control == "lx+":
                lx += value
            elif control == "lx-":
                lx -= value
            elif control == "ly+":
                ly += value
            elif control == "ly-":
                ly -= value
            elif control == "rx+":
                rx += value
            elif control == "rx-":
                rx -= value
            elif control == "ry+":
                ry += value
            elif control == "ry-":
                ry -= value
            elif control == "lt":
                lt = max(lt, value)
            elif control == "rt":
                rt = max(rt, value)

        lx = max(-1.0, min(1.0, lx))
        ly = max(-1.0, min(1.0, ly))
        rx = max(-1.0, min(1.0, rx))
        ry = max(-1.0, min(1.0, ry))

        try:
            self.gamepad.left_joystick_float(lx, ly)
            self.gamepad.right_joystick_float(rx, ry)
            self.gamepad.left_trigger_float(lt)
            self.gamepad.right_trigger_float(rt)
            self.gamepad.update()
        except Exception as exc:
            self.error = f"Virtual Xbox controller update failed: {exc}"
            self._report_error_once()

    def reset(self):
        if not self.available:
            return
        try:
            self.gamepad.reset()
            self.gamepad.update()
        except Exception as exc:
            self.error = f"Virtual Xbox controller reset failed: {exc}"
            self._report_error_once()


class KeyBindingsDialog(QDialog):
    bindings_changed = pyqtSignal(list)  # list of (finger_id, controller_input)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Controller Bindings")
        self.resize(560, 340)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Map each finger to an Xbox controller analog input. "
            "0% is neutral/released and 100% is full input. "
            "For sticks, choose + or - for the desired direction."
        )
        note.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(len(FINGER_IDS), 2)
        self.table.setHorizontalHeaderLabels(["Finger", "Controller Input"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

        self._build_rows()

    def _build_rows(self):
        by_finger = {
            finger: control
            for finger, control in self.store.all()
        }

        for row, finger in enumerate(FINGER_IDS):
            finger_item = QTableWidgetItem(FINGER_NAMES[finger])
            finger_item.setData(Qt.ItemDataRole.UserRole, finger)
            finger_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, finger_item)

            combo = QComboBox()
            for label, control in CONTROLLER_INPUTS:
                combo.addItem(label, control)
            combo.setCurrentIndex(combo.findData(by_finger.get(finger, "none")))
            self.table.setCellWidget(row, 1, combo)

    def _collect(self):
        bindings = []
        used_controls = {}
        errors = []

        for row, finger in enumerate(FINGER_IDS):
            combo = self.table.cellWidget(row, 1)
            control = combo.currentData() if combo else "none"

            if control not in CONTROLLER_INPUT_IDS:
                errors.append(f"Row {row + 1}: invalid controller input.")
                continue

            if control != "none" and control in used_controls:
                other_finger = used_controls[control]
                errors.append(
                    f"'{CONTROLLER_INPUT_NAMES[control]}' is assigned to both "
                    f"{FINGER_NAMES[other_finger]} and {FINGER_NAMES[finger]}."
                )
                continue

            if control != "none":
                used_controls[control] = finger

            bindings.append((finger, control))

        if errors:
            QMessageBox.warning(self, "Invalid controller bindings", "\n".join(errors))
            return None

        return bindings

    def _save(self):
        bindings = self._collect()
        if bindings is None:
            return
        self.store.set_all(bindings)
        self.bindings_changed.emit(bindings)
        self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide()


# ── Main window ────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cyberkinesis Glove Control")
        self.resize(720, 520)

        self.settings = SettingsStore()
        print(f"[settings] using {self.settings.path}")
        self.led_color = self.settings.led_color or QColor(ACCENT)

        self.network_store = NetworkIndexStore()
        print(f"[settings] using {self.network_store.path}")

        self.keybindings_store = KeyBindingsStore()
        print(f"[bindings] using {self.keybindings_store.path}")
        self.key_bindings = []
        self.virtual_controller = VirtualXboxController()
        if self.virtual_controller.error:
            print(f"[controller] {self.virtual_controller.error}")

        self.udp_sock = backend.open_udp_socket()
        self.mode = None          # None / "udp" / "serial"
        self.ser = None
        self.device_id = None
        self.current_ssid = None  # last SSID we told the device to join

        self.connection_worker = None
        self.udp_worker = None
        self.serial_worker = None
        self.wifi_scan_worker = None

        # State for the "did the WiFi connect actually succeed?" check
        # that follows a credentials write - see _start_wifi_check().
        self._wifi_check_ssid = None
        self._wifi_check_index = None
        self._wifi_check_attempting = None

        self._build_ui()

        self.console_dialog = DebugConsoleDialog(self)
        self.console_dialog.command_submitted.connect(self.send_command)

        self.network_dialog = NetworkDialog(self)
        self.network_dialog.scan_requested.connect(self._on_scan_requested)
        self.network_dialog.network_selected.connect(self._on_network_selected)
        self.network_dialog.network_forgotten.connect(self._on_network_forgotten)
        self._refresh_known_networks()

        self.keybindings_dialog = KeyBindingsDialog(self.keybindings_store, self)
        self.keybindings_dialog.bindings_changed.connect(self._apply_key_bindings)
        self._apply_key_bindings(self.keybindings_store.all())

        self._start_listening()

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        title = QLabel("GUI")
        title.setStyleSheet(f"color: {ACCENT}; font-size: 28px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        panel = QFrame()
        panel.setObjectName("panel")
        root.addWidget(panel)
        panel_layout = QVBoxLayout(panel)

        top_row = QHBoxLayout()
        panel_layout.addLayout(top_row)

        # -- left: connection + LED + network
        left_col = QVBoxLayout()
        top_row.addLayout(left_col, 1)

        conn_row = QHBoxLayout()
        self.usb_icon = ConnIcon("usb")
        self.wifi_icon = ConnIcon("wifi")
        conn_row.addWidget(self.usb_icon)
        conn_row.addWidget(self.wifi_icon)

        self.status_label = QLabel("conn. status: disconnected")
        self.status_label.setStyleSheet(f"color: {WARN}; text-decoration: underline; font-weight: 600;")
        conn_row.addWidget(self.status_label)
        conn_row.addStretch(1)
        left_col.addLayout(conn_row)

        self.device_label = QLabel("device name: \u2014")
        left_col.addWidget(self.device_label)

        led_row = QHBoxLayout()
        self.led_button = QPushButton("LED")
        self.led_button.clicked.connect(self._open_led_dialog)
        led_row.addWidget(self.led_button)
        led_row.addStretch(1)
        left_col.addLayout(led_row)
        self._style_led_button(self.led_color)
        self.led_button.setToolTip(
            f"Settings file: {self.settings.path}\n"
            "Note: the custom colour only visibly lights up once the glove "
            "is in WiFi mode - it stays solid blue while connected over USB."
        )

        self.network_button = QPushButton("Choose network")
        self.network_button.clicked.connect(self._open_network_dialog)
        left_col.addWidget(self.network_button)

        left_col.addStretch(1)

        # -- right: key bindings + debug console
        right_col = QVBoxLayout()
        top_row.addLayout(right_col, 1)

        self.keybindings_button = QPushButton("Key bindings")
        self.keybindings_button.clicked.connect(self._open_keybindings_dialog)
        right_col.addWidget(self.keybindings_button)

        self.console_button = QPushButton("debug console")
        self.console_button.clicked.connect(self._open_console_dialog)
        right_col.addWidget(self.console_button)

        right_col.addStretch(1)

        # -- bottom: finger bars + gyro
        bottom_row = QHBoxLayout()
        panel_layout.addLayout(bottom_row, 1)

        self.finger_panel = FingerBarsPanel()
        bottom_row.addWidget(self.finger_panel, 1)

        gyro_col = QVBoxLayout()
        self.gyro_widget = GyroWidget()
        gyro_col.addWidget(self.gyro_widget)
        bottom_row.addLayout(gyro_col, 1)

        self._update_button_availability(None)

    # -- connection lifecycle -------------------------------------------

    def _start_listening(self):
        self._set_status("listening for device\u2026", connected=False)
        self.connection_worker = ConnectionWorker(self.udp_sock)
        self.connection_worker.connected.connect(self._on_connected)
        self.connection_worker.start()

    def _on_connected(self, mode, device_id, ser):
        self.mode = mode
        self.device_id = device_id
        name = backend.DEVICE_IDS.get(device_id, "Unknown device")
        self.device_label.setText(f"device name: {name}")
        self._highlight_transport(mode)
        self._update_button_availability(mode)

        if mode == "udp":
            self._set_status(self._wifi_status_text(), connected=True)
            self._start_udp_worker()

        elif mode == "serial":
            self._set_status("connected (usb)", connected=True)
            self.ser = ser
            if self.connection_worker is not None:
                self.connection_worker.pending_ser = None  # ownership transferred to self.ser now
            self.serial_worker = SerialWorker(self.ser, self.udp_sock)
            self.serial_worker.line_received.connect(self.console_dialog.append_line)
            self.serial_worker.line_received.connect(self._watch_for_ssid)
            self.serial_worker.switched_to_udp.connect(self._on_switched_to_udp)
            self.serial_worker.serial_lost.connect(self._on_disconnected)
            self.serial_worker.start()

    def _on_switched_to_udp(self, device_id):
        self.console_dialog.append_line("[device switched to UDP mode]")
        # SerialWorker already closed the actual OS-level port before
        # emitting this signal - clear our reference too so nothing here
        # (or closeEvent later) treats it as still open.
        self.ser = None
        self.mode = "udp"
        self.device_id = device_id
        name = backend.DEVICE_IDS.get(device_id, "Unknown device")
        self.device_label.setText(f"device name: {name}")
        self._set_status(self._wifi_status_text(), connected=True)
        self._highlight_transport("udp")
        self._update_button_availability("udp")
        self._start_udp_worker()

    def _start_udp_worker(self):
        self.udp_worker = UdpDataWorker(self.udp_sock)
        self.udp_worker.sensor_data.connect(self._on_sensor_data)
        self.udp_worker.text_received.connect(self._watch_for_ssid)
        self.udp_worker.connection_lost.connect(self._on_disconnected)
        self.udp_worker.start()

    def _on_sensor_data(self, values):
        self.finger_panel.update_values(values)
        self._update_key_bindings(values)
        # Note: there used to be a one-shot "ask wifi_status over UDP to
        # learn the SSID name" probe here for the case where the glove
        # was already on WiFi from a previous session (so we never saw
        # it announced over serial). Confirmed dead per the firmware:
        # loop()'s UDP branch only ever reads incoming UDP packets
        # looking for "ping_ok" (udp_watchdog()) - cmd_exec(), which is
        # what understands "wifi_status", only runs in Serial mode. If
        # current_ssid is still unknown here, it stays generic
        # ("connected (wifi)" - see _wifi_status_text()) until the next
        # USB-mode wifi_status check or boot-time reconnect reveals it.

    def _watch_for_ssid(self, line):
        ssid = _extract_ssid_announcement(line)
        if ssid and ssid != self.current_ssid:
            self.current_ssid = ssid
            if ssid not in self.network_store.known_ssids():
                # Best-effort: we're learning about this network from the
                # device's own confirmed "Connected to:" announcement
                # rather than from writing its credentials ourselves
                # (e.g. it was already saved on the glove from before
                # this GUI tracked WiFi-slot indices, or from an earlier
                # session). NetworkIndexStore will hand it the lowest
                # free slot, which for a fresh/empty store is index 0 -
                # matching the original script's hardcoded "w 0 ...".
                self.network_store.assign_index(ssid)
                self._refresh_known_networks()
            if self.mode == "udp":
                self._set_status(self._wifi_status_text(), connected=True)
            self.network_dialog.set_current_network(ssid)

    def _on_disconnected(self):
        self._reset_controller()
        if self._wifi_check_ssid is not None:
            # Don't leave a WiFi check waiting forever on a link that's
            # already gone - the "unknown" branch handles telling the user.
            self._finish_wifi_check("unknown", self._wifi_check_ssid)

        self.mode = None
        self.ser = None
        self.device_id = None
        self.device_label.setText("device name: \u2014")
        self._set_status("disconnected", connected=False)
        self._highlight_transport(None)
        self._update_button_availability(None)
        self._start_listening()

    def _wifi_status_text(self):
        # current_ssid gets set either because we're the ones who told
        # the device to join it (_on_network_selected/_finish_wifi_check),
        # or because we spotted the device's own confirmed "Connected
        # to: X !" line over serial (_watch_for_ssid).
        return f"connected to {self.current_ssid}" if self.current_ssid else "connected (wifi)"

    def _set_status(self, text, connected):
        color = ACCENT if connected else WARN
        self.status_label.setText(f"conn. status: {text}")
        self.status_label.setStyleSheet(f"color: {color}; text-decoration: underline; font-weight: 600;")

    def _highlight_transport(self, mode):
        self.usb_icon.set_active(mode == "serial")
        self.wifi_icon.set_active(mode == "udp")

    def _update_button_availability(self, mode):
        # LED / network / console need the serial link. Controller bindings
        # are handled locally from sensor data, so they are not affected by
        # the current USB/WiFi transport.
        usb_only = mode == "serial"
        self.led_button.setEnabled(usb_only)
        self.network_button.setEnabled(usb_only)
        self.console_button.setEnabled(usb_only)

    # -- commands ---------------------------------------------------------

    def send_command(self, text):
        """
        General-purpose command send for the LED and debug console over
        the serial transport - the only one the firmware actually reads
        commands from. WiFi scan/connect don't use this at all - see
        _send_serial_only() below, they're USB-only by design too.
        """
        line = (text + "\n").encode("utf-8")

        if self.mode == "serial" and self.ser is not None:
            try:
                self.ser.write(line)
            except Exception as exc:
                self.console_dialog.append_line(f"[serial write failed: {exc}]")

        elif self.mode == "udp":
            # Confirmed dead end, not just unavailable: cmd_exec() only
            # runs in the Serial-mode branch of loop().
            self.console_dialog.append_line(
                "[udp mode - the glove doesn't read commands over WiFi; command not sent]"
            )

        else:
            self.console_dialog.append_line("[no active connection - command not sent]")

    def _send_serial_only(self, text):
        """WiFi scan/connect commands: USB only, never over UDP."""
        if self.mode != "serial" or self.ser is None:
            QMessageBox.information(
                self.network_dialog,
                "USB required",
                "Connect the glove over USB to scan for or configure WiFi networks.",
            )
            return False
        try:
            self.ser.write((text + "\n").encode("utf-8"))
            return True
        except Exception as exc:
            self.console_dialog.append_line(f"[serial write failed: {exc}]")
            return False

    # -- LED dialog ---------------------------------------------------------

    def _style_led_button(self, color):
        text_color = "#0c0c0d" if color.lightness() > 128 else TEXT
        self.led_button.setStyleSheet(
            f"background-color: {color.name()}; color: {text_color};"
            f"border: 2px solid {ACCENT}; border-radius: 5px; padding: 6px 14px; font-weight: 600;"
        )

    def _open_led_dialog(self):
        dialog = ColorWheelDialog(self.led_color, self)
        # Picking/dragging on the wheel only updates the dialog's own
        # live preview swatch (see ColorWheelDialog) - nothing is sent
        # or saved until "Done" is pressed, i.e. the dialog is accepted.
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._on_led_color_picked(dialog.wheel.current_color())

    def _on_led_color_picked(self, color):
        self.led_color = color
        self._style_led_button(color)
        self.settings.led_color = color
        # Confirmed via the device's own help text: "led_set <color in
        # HEX>" wants bare hex digits, no leading '#' (sending the '#'
        # is why every colour was coming back as 0).
        self.send_command(f"led_set {color.name()[1:]}")

    # -- Network dialog -----------------------------------------------------

    def _open_network_dialog(self):
        self.network_dialog.set_current_network(self.current_ssid)
        self.network_dialog.show()
        self.network_dialog.raise_()

    def _on_scan_requested(self):
        # Local scan - lists networks visible to this computer's own
        # WiFi radio, no glove/USB connection needed for this part.
        self.network_dialog.set_scanning(True)
        self.wifi_scan_worker = WifiScanWorker()
        self.wifi_scan_worker.finished_scan.connect(self._on_wifi_scan_finished)
        self.wifi_scan_worker.start()

    def _on_wifi_scan_finished(self, networks):
        self.network_dialog.set_scanning(False)
        for ssid, signal_percent in networks:
            self.network_dialog.add_network(ssid, signal_percent)
        if not networks:
            self.console_dialog.append_line(
                "[wifi scan] no networks found - scanning may need extra "
                "OS permissions, or isn't supported on this platform"
            )

    def _on_network_selected(self, ssid):
        if ssid == self.current_ssid:
            QMessageBox.information(
                self.network_dialog,
                "Already connected",
                f"Already connected to '{ssid}'.",
            )
            return

        if self.mode != "serial" or self.ser is None:
            QMessageBox.information(
                self.network_dialog,
                "USB required",
                "Connect the glove over USB to configure WiFi networks.",
            )
            return

        password, ok = QInputDialog.getText(
            self.network_dialog,
            "Connect to Network",
            f"Password for '{ssid}':",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return

        # Reuses this SSID's existing WiFi-slot index if we've saved it
        # before, otherwise hands out the lowest free one - so a second
        # network no longer overwrites the first (the original script
        # always wrote to slot 0 regardless).
        index = self.network_store.assign_index(ssid)
        if index is None:
            QMessageBox.warning(
                self.network_dialog,
                "No free WiFi slots",
                f"The glove can only remember {MAX_SAVED_NETWORKS} networks at once. "
                "Forget one in the list before adding another.",
            )
            return

        if self._send_serial_only(f"w {index} {ssid} {password}"):
            self.console_dialog.append_line(
                f"[sent WiFi credentials for '{ssid}' (index {index}), checking "
                "connection\u2026 this can take up to a minute if other saved "
                "networks get tried first]"
            )
            self._refresh_known_networks()
            self._start_wifi_check(ssid, index)

    def _on_network_forgotten(self, ssid):
        index = self.network_store.index_for(ssid)
        if index is None:
            return  # nothing saved locally for this ssid - Forget shouldn't be shown for it anyway

        if self._send_serial_only(f"c {index}"):
            self.console_dialog.append_line(f"[forgot '{ssid}' (index {index})]")
            self.network_store.forget(ssid)
            self._refresh_known_networks()
            if ssid == self.current_ssid:
                # The credentials we just cleared were the ones the glove
                # would otherwise try first - it's no longer accurate to
                # show this as the current/preferred network.
                self.current_ssid = None
                self.network_dialog.set_current_network(None)
                if self.mode == "udp":
                    self._set_status(self._wifi_status_text(), connected=True)

    def _refresh_known_networks(self):
        self.network_dialog.set_known_ssids(self.network_store.known_ssids())

    def _start_wifi_check(self, ssid, index):
        """
        After writing credentials, ask the device whether it actually
        connected. "wifi_status" isn't a quick status query - per the
        firmware (wifi_connect()) it scans, then tries the last-known-
        good network and every other saved slot that's visible in the
        scan, up to 5s each, before disconnecting again regardless of
        the outcome (see the module docstring). So it's sent exactly
        ONCE here, never re-polled: the firmware handles one blocking
        serial command at a time, so a second "wifi_status" sent while
        the first is still mid-scan would just queue up another full
        pass behind it instead of refreshing anything. We just listen
        to whatever lines come back.
        """
        self._wifi_check_ssid = ssid
        self._wifi_check_index = index
        self._wifi_check_attempting = None

        if self.serial_worker is not None:
            self.serial_worker.line_received.connect(self._on_wifi_check_line)

        self._send_serial_only("wifi_status")

        # Worst case per the firmware: a WiFi scan (can itself take several
        # seconds), then up to MAX_SAVED_NETWORKS (8) sequential connection
        # attempts at 5s each if several saved networks are in range but
        # unreachable - that's up to 40s of attempts alone, before the scan
        # time is even added in. 50s cut it too close to that; this gives
        # real margin above the documented worst case instead of sitting
        # right on top of it.
        QTimer.singleShot(70000, lambda: self._finish_wifi_check("unknown", ssid))

    def _on_wifi_check_line(self, line):
        target = self._wifi_check_ssid
        if target is None:
            return
        stripped = line.strip()

        attempted = _extract_wifi_attempt_ssid(stripped)
        if attempted:
            # Only an announcement that an attempt is starting, not an
            # outcome - "Trying last network: X" can still fail, so this
            # just tells us which network a later failure/success line
            # belongs to (see the module docstring).
            self._wifi_check_attempting = attempted
            return

        connected_ssid = _extract_ssid_announcement(stripped)
        if connected_ssid is not None:
            if connected_ssid == target:
                self._finish_wifi_check("success", target)
            else:
                # wifi_connect() stops at its first success - a
                # different saved network answering means our target
                # was either skipped entirely or already failed (that
                # would have resolved as "failure" below before this
                # line could arrive), so this run can't confirm it.
                self._finish_wifi_check("other", target, other_ssid=connected_ssid)
            return

        lower = stripped.lower()

        if self._wifi_check_attempting == target and any(
            hint in lower for hint in _WIFI_ATTEMPT_FAILURE_HINTS
        ):
            self._finish_wifi_check("failure", target)
            return

        if any(hint in lower for hint in _WIFI_TOTAL_FAILURE_HINTS):
            self._finish_wifi_check("failure", target)
            return

    def _finish_wifi_check(self, result, ssid, other_ssid=None):
        # Guards against the timeout firing after an already-resolved
        # check, or a check for a network we've since moved past.
        if self._wifi_check_ssid != ssid:
            return
        index = self._wifi_check_index
        self._wifi_check_ssid = None
        self._wifi_check_index = None
        self._wifi_check_attempting = None

        if self.serial_worker is not None:
            try:
                self.serial_worker.line_received.disconnect(self._on_wifi_check_line)
            except TypeError:
                pass  # wasn't connected (serial dropped mid-check)

        if result == "success":
            self.current_ssid = ssid
            self.console_dialog.append_line(f"[wifi_status] connected to '{ssid}'")
            QMessageBox.information(self.network_dialog, "Connected", f"Connected to '{ssid}'.")
            if self.mode == "udp":
                self._set_status(self._wifi_status_text(), connected=True)
            self.network_dialog.set_current_network(ssid)

        elif result == "other":
            # A different, already-saved network answered before (or
            # instead of) our target ever being tried - it's now the
            # glove's own preferred network, so reflect that, but we
            # still don't know whether OUR target's credentials work.
            self.current_ssid = other_ssid
            if other_ssid not in self.network_store.known_ssids():
                # Same bootstrap as _watch_for_ssid: we're learning about
                # this network from its own confirmed success, not from
                # writing its credentials ourselves, so we don't actually
                # know its real slot - lowest free is the best guess.
                self.network_store.assign_index(other_ssid)
                self._refresh_known_networks()
            self.network_dialog.set_current_network(other_ssid)
            if self.mode == "udp":
                self._set_status(self._wifi_status_text(), connected=True)
            self.console_dialog.append_line(
                f"[wifi_status] '{other_ssid}' answered before '{ssid}' could be tried"
            )
            QMessageBox.information(
                self.network_dialog,
                "Couldn't confirm this network",
                f"'{other_ssid}' - another saved network - connected first, so "
                f"'{ssid}' wasn't actually tried this time. Its credentials are "
                "still saved; try again, or forget other saved networks to test "
                "it in isolation.",
            )

        elif result == "failure":
            # wifi_status can only test whatever was just written to this
            # slot (there's no separate "try without saving" command in
            # the firmware), so the write already happened before we
            # could know it was wrong. Clearing it here on a confirmed
            # failure is what actually makes "only keep it if the check
            # passes" true - a bad password doesn't linger saved on the
            # glove, and we drop our own record of the slot too so it's
            # free again for the next attempt.
            self._send_serial_only(f"c {index}")
            self.network_store.forget(ssid)
            self._refresh_known_networks()
            self.console_dialog.append_line(f"[wifi_status] '{ssid}' did not connect - cleared (c {index})")
            QMessageBox.warning(
                self.network_dialog,
                "Connection failed",
                f"Could not connect to '{ssid}' - the password may be incorrect.",
            )

        else:  # "unknown" - timed out with no classifiable response
            self.console_dialog.append_line(f"[wifi_status] no clear response for '{ssid}'")
            QMessageBox.warning(
                self.network_dialog,
                "No response",
                f"No connection status was reported for '{ssid}'.\n"
                "Check the debug console for what the device actually sent back.",
            )

    # -- Key bindings ---------------------------------------------------------

    def _open_keybindings_dialog(self):
        self.keybindings_dialog.show()
        self.keybindings_dialog.raise_()
        self.keybindings_dialog.activateWindow()

    def _apply_key_bindings(self, bindings):
        self._reset_controller()
        self.key_bindings = list(bindings)

    def _update_key_bindings(self, values):
        self.virtual_controller.update(self.key_bindings, values)

    def _reset_controller(self):
        self.virtual_controller.reset()

    # -- Debug console --------------------------------------------------------

    def _open_console_dialog(self):
        self.console_dialog.show()
        self.console_dialog.raise_()
        # Ask the device to print its help/command list as soon as the
        # console is opened.
        self.send_command("h")

    # -- shutdown -------------------------------------------------------------

    def closeEvent(self, event):
        self._reset_controller()
        self._wifi_check_ssid = None
        self._wifi_check_index = None
        self._wifi_check_attempting = None

        # Stop the worker threads FIRST and wait for them to actually
        # exit before touching the serial port or socket they own - this
        # avoids closing a port/socket while another thread is still
        # mid-read on it, and guarantees the serial connection is fully
        # released (not left open) once the window closes.
        for worker in (self.connection_worker, self.udp_worker, self.serial_worker):
            if worker is not None:
                worker.request_stop()

        try:
            self.udp_sock.close()  # unblocks any thread stuck in recvfrom()
        except OSError:
            pass

        for worker in (self.connection_worker, self.udp_worker, self.serial_worker):
            if worker is not None:
                worker.wait(1500)

        # WifiScanWorker has no cancellation hook (the OS command it shells
        # out to can't be interrupted mid-call) - just give it a moment to
        # finish on its own rather than blocking shutdown on it.
        if self.wifi_scan_worker is not None:
            self.wifi_scan_worker.wait(200)

        # Covers the narrow race where ConnectionWorker finished a serial
        # handshake and opened the port at almost the exact moment the
        # window closed - self.ser wouldn't have been set yet (that only
        # happens once _on_connected processes the queued signal, which
        # may never get a chance to run now), so it wouldn't be caught by
        # the self.ser check below without this.
        if self.connection_worker is not None and self.connection_worker.pending_ser is not None:
            try:
                self.connection_worker.pending_ser.close()
            except Exception:
                pass
            self.connection_worker.pending_ser = None

        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        # Safety net: request_stop() + wait() above should always be
        # enough now, but if some worker is still alive anyway (an
        # unexpectedly slow OS call, a platform quirk, etc.), don't let
        # the process linger in the background still holding the port -
        # force it down at the OS level, which guarantees the OS reclaims
        # the serial handle even if Qt/Python cleanup can't finish.
        still_running = [
            w for w in (self.connection_worker, self.udp_worker, self.serial_worker)
            if w is not None and w.isRunning()
        ]
        if still_running:
            print(f"[shutdown] {len(still_running)} worker(s) still running - forcing process exit")
            os._exit(1)

        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
