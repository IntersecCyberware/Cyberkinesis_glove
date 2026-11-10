import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


BG = "#0c0c0d"
PANEL_BG = "#161618"
CONTROL_BG = "#1c1c1f"
BORDER = "#3a3a3d"
ACCENT = "#e8e8ea"
TEXT = "#f1f1f2"
DIM = "#77777d"
FILL = "#a6a6ab"
WARN = "#ff5a5a"

FINGER_NAMES = ("Thumb", "Pointer", "Middle", "Ring", "Pinkie")
FINGER_COUNT = len(FINGER_NAMES)

DEVICE_NONE = "none"
DEVICE_KEYBOARD = "keyboard"
DEVICE_XBOX = "xbox"
DEVICE_CHOICES = (
    (DEVICE_NONE, "Not bound"),
    (DEVICE_KEYBOARD, "Keyboard"),
    (DEVICE_XBOX, "Xbox controller"),
)

DEFAULT_THRESHOLD = 50
HYSTERESIS = 3
ANALOG_DEADZONE = 3

APP_DIR_WINDOWS = "CyberkinesisGlove"
APP_DIR_LINUX = "cyberkinesis-glove"
SETTINGS_FILE = "key_bindings.json"
CONFIG_DIR_ENV = "CYBERKINESIS_CONFIG_DIR"

UINPUT_HINT = (
    "Linux needs write access to /dev/uinput: run 'sudo modprobe uinput', add a udev rule "
    "(KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\") and add your user to the 'input' group."
)
XBOX_MISSING_HINT = (
    "Xbox output needs the 'vgamepad' package (pip install vgamepad). "
    "On Windows it installs the ViGEmBus driver; on Linux it needs access to /dev/uinput."
)


def _build_keyboard_keys():
    keys = {}

    for code in range(ord("a"), ord("z") + 1):
        letter = chr(code)
        keys[letter] = (code - 32, "KEY_" + letter.upper())

    for digit in "0123456789":
        keys[digit] = (ord(digit), "KEY_" + digit)

    for number in range(1, 13):
        keys[f"f{number}"] = (0x6F + number, f"KEY_F{number}")

    for number in range(10):
        keys[f"num{number}"] = (0x60 + number, f"KEY_KP{number}")

    specials = (
        ("space", 0x20, "KEY_SPACE"),
        ("enter", 0x0D, "KEY_ENTER"),
        ("esc", 0x1B, "KEY_ESC"),
        ("tab", 0x09, "KEY_TAB"),
        ("backspace", 0x08, "KEY_BACKSPACE"),
        ("left", 0x25, "KEY_LEFT"),
        ("up", 0x26, "KEY_UP"),
        ("right", 0x27, "KEY_RIGHT"),
        ("down", 0x28, "KEY_DOWN"),
        ("shift", 0xA0, "KEY_LEFTSHIFT"),
        ("ctrl", 0xA2, "KEY_LEFTCTRL"),
        ("alt", 0xA4, "KEY_LEFTALT"),
        ("insert", 0x2D, "KEY_INSERT"),
        ("delete", 0x2E, "KEY_DELETE"),
        ("home", 0x24, "KEY_HOME"),
        ("end", 0x23, "KEY_END"),
        ("pageup", 0x21, "KEY_PAGEUP"),
        ("pagedown", 0x22, "KEY_PAGEDOWN"),
        ("-", 0xBD, "KEY_MINUS"),
        ("=", 0xBB, "KEY_EQUAL"),
        ("[", 0xDB, "KEY_LEFTBRACE"),
        ("]", 0xDD, "KEY_RIGHTBRACE"),
        (";", 0xBA, "KEY_SEMICOLON"),
        ("'", 0xDE, "KEY_APOSTROPHE"),
        (",", 0xBC, "KEY_COMMA"),
        (".", 0xBE, "KEY_DOT"),
        ("/", 0xBF, "KEY_SLASH"),
        ("\\", 0xDC, "KEY_BACKSLASH"),
        ("`", 0xC0, "KEY_GRAVE"),
    )
    for name, virtual_key, linux_name in specials:
        keys[name] = (virtual_key, linux_name)

    return keys


KEYBOARD_KEYS = _build_keyboard_keys()
EXTENDED_KEYS = frozenset(
    ("left", "up", "right", "down", "insert", "delete", "home", "end", "pageup", "pagedown")
)

XBOX_INPUTS = {
    "a": "A",
    "b": "B",
    "x": "X",
    "y": "Y",
    "lb": "LB (left bumper)",
    "rb": "RB (right bumper)",
    "lt": "LT (left trigger)",
    "rt": "RT (right trigger)",
    "back": "Back",
    "start": "Start",
    "l3": "L3 (left stick click)",
    "r3": "R3 (right stick click)",
    "dpad_up": "D-pad up",
    "dpad_down": "D-pad down",
    "dpad_left": "D-pad left",
    "dpad_right": "D-pad right",
    "ls_up": "Left stick up",
    "ls_down": "Left stick down",
    "ls_left": "Left stick left",
    "ls_right": "Left stick right",
    "rs_up": "Right stick up",
    "rs_down": "Right stick down",
    "rs_left": "Right stick left",
    "rs_right": "Right stick right",
}

XBOX_BUTTONS = {
    "a": "XUSB_GAMEPAD_A",
    "b": "XUSB_GAMEPAD_B",
    "x": "XUSB_GAMEPAD_X",
    "y": "XUSB_GAMEPAD_Y",
    "lb": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "rb": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "back": "XUSB_GAMEPAD_BACK",
    "start": "XUSB_GAMEPAD_START",
    "l3": "XUSB_GAMEPAD_LEFT_THUMB",
    "r3": "XUSB_GAMEPAD_RIGHT_THUMB",
    "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
    "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
    "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
    "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
}

XBOX_TRIGGERS = {"lt": "left", "rt": "right"}

XBOX_STICKS = {
    "ls_up": ("left", 0, 1),
    "ls_down": ("left", 0, -1),
    "ls_left": ("left", -1, 0),
    "ls_right": ("left", 1, 0),
    "rs_up": ("right", 0, 1),
    "rs_down": ("right", 0, -1),
    "rs_left": ("right", -1, 0),
    "rs_right": ("right", 1, 0),
}

XBOX_ANALOG = frozenset(XBOX_TRIGGERS) | frozenset(XBOX_STICKS)


def key_label(name):
    if len(name) == 1:
        return name.upper()
    if name.startswith("num") and name[3:].isdigit():
        return "Num " + name[3:]
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return name.capitalize()


def _qt_special_keys():
    keys = Qt.Key
    table = {
        keys.Key_Space: "space",
        keys.Key_Return: "enter",
        keys.Key_Enter: "enter",
        keys.Key_Escape: "esc",
        keys.Key_Tab: "tab",
        keys.Key_Backspace: "backspace",
        keys.Key_Left: "left",
        keys.Key_Up: "up",
        keys.Key_Right: "right",
        keys.Key_Down: "down",
        keys.Key_Shift: "shift",
        keys.Key_Control: "ctrl",
        keys.Key_Alt: "alt",
        keys.Key_Insert: "insert",
        keys.Key_Delete: "delete",
        keys.Key_Home: "home",
        keys.Key_End: "end",
        keys.Key_PageUp: "pageup",
        keys.Key_PageDown: "pagedown",
        keys.Key_Minus: "-",
        keys.Key_Equal: "=",
        keys.Key_BracketLeft: "[",
        keys.Key_BracketRight: "]",
        keys.Key_Semicolon: ";",
        keys.Key_Apostrophe: "'",
        keys.Key_Comma: ",",
        keys.Key_Period: ".",
        keys.Key_Slash: "/",
        keys.Key_Backslash: "\\",
        keys.Key_QuoteLeft: "`",
    }
    return {int(key.value): name for key, name in table.items()}


QT_SPECIAL_KEYS = _qt_special_keys()
QT_F1 = int(Qt.Key.Key_F1.value)


def key_name_from_event(event):
    key = event.key()
    keypad = bool(event.modifiers() & Qt.KeyboardModifier.KeypadModifier)

    if keypad and 0x30 <= key <= 0x39:
        return f"num{key - 0x30}"
    if 0x41 <= key <= 0x5A:
        return chr(key).lower()
    if 0x30 <= key <= 0x39:
        return chr(key)
    if QT_F1 <= key <= QT_F1 + 11:
        return f"f{key - QT_F1 + 1}"
    return QT_SPECIAL_KEYS.get(key)


def config_dir():
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return override

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, APP_DIR_WINDOWS)

    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_LINUX)


@dataclass
class Binding:
    device: str = DEVICE_NONE
    key: str = ""
    threshold: int = DEFAULT_THRESHOLD


@dataclass
class Settings:
    enabled: bool = False
    bindings: list = field(default_factory=lambda: [Binding() for _ in range(FINGER_COUNT)])


def is_analog_input(device, key):
    return device == DEVICE_XBOX and key in XBOX_ANALOG


def analog_amount(value):
    if value <= ANALOG_DEADZONE:
        return 0.0
    return round((value - ANALOG_DEADZONE) / (100 - ANALOG_DEADZONE), 2)


def clean_binding(entry):
    device = entry.get("device")
    key = entry.get("key")

    valid = (
        (device == DEVICE_KEYBOARD and key in KEYBOARD_KEYS)
        or (device == DEVICE_XBOX and key in XBOX_INPUTS)
    )
    if not valid:
        device, key = DEVICE_NONE, ""

    try:
        threshold = int(entry.get("threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD

    return Binding(device, key, max(1, min(100, threshold)))


class BindingStore:

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), SETTINGS_FILE)

    def load(self):
        settings = Settings()

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return settings

        if not isinstance(data, dict):
            return settings

        settings.enabled = data.get("enabled") is True

        raw = data.get("bindings")
        if isinstance(raw, dict):
            for index, finger in enumerate(FINGER_NAMES):
                entry = raw.get(finger.lower())
                if isinstance(entry, dict):
                    settings.bindings[index] = clean_binding(entry)

        return settings

    def save(self, settings):
        data = {
            "version": 1,
            "enabled": settings.enabled,
            "bindings": {
                finger.lower(): {
                    "device": binding.device,
                    "key": binding.key,
                    "threshold": binding.threshold,
                }
                for finger, binding in zip(FINGER_NAMES, settings.bindings)
            },
        }

        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temp_path, self.path)


class OutputError(Exception):
    pass


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _InputUnion)]


INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class WindowsKeyboard:

    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int)
        self._user32.SendInput.restype = ctypes.c_uint
        self._user32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
        self._user32.MapVirtualKeyW.restype = ctypes.c_uint

    def send(self, name, down):
        virtual_key = KEYBOARD_KEYS[name][0]
        scan_code = self._user32.MapVirtualKeyW(virtual_key, 0)

        flags = KEYEVENTF_SCANCODE
        if name in EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP

        event = _Input(type=INPUT_KEYBOARD)
        event.u.ki = _KeyboardInput(0, scan_code, flags, 0, 0)

        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        if sent != 1:
            raise OutputError("Windows didn't accept the key press. It is blocked for windows running as administrator.")

    def close(self):
        pass


class LinuxKeyboard:

    def __init__(self):
        try:
            import evdev
        except ImportError as exc:
            raise OutputError("Keyboard output on Linux needs the 'evdev' package (pip install evdev).") from exc

        self._ecodes = evdev.ecodes
        self._codes = {
            name: getattr(evdev.ecodes, spec[1]) for name, spec in KEYBOARD_KEYS.items()
        }

        try:
            self._device = evdev.UInput(
                {evdev.ecodes.EV_KEY: sorted(set(self._codes.values()))},
                name="Cyberkinesis Glove Keyboard",
            )
        except Exception as exc:
            raise OutputError(f"Couldn't create a virtual keyboard: {exc}. {UINPUT_HINT}") from exc

        time.sleep(0.2)

    def send(self, name, down):
        self._device.write(self._ecodes.EV_KEY, self._codes[name], 1 if down else 0)
        self._device.syn()

    def close(self):
        self._device.close()


def create_keyboard():
    if sys.platform.startswith("win"):
        return WindowsKeyboard()
    if sys.platform.startswith("linux"):
        return LinuxKeyboard()
    raise OutputError("Keyboard output is only supported on Windows and Linux.")


class XboxOutput:

    def __init__(self):
        try:
            import vgamepad
        except Exception as exc:
            raise OutputError(XBOX_MISSING_HINT) from exc

        try:
            self._pad = vgamepad.VX360Gamepad()
        except Exception as exc:
            message = f"Couldn't create a virtual Xbox controller: {exc}."
            if sys.platform.startswith("linux"):
                message += " " + UINPUT_HINT
            raise OutputError(message) from exc

        self._buttons = {
            name: getattr(vgamepad.XUSB_BUTTON, attr) for name, attr in XBOX_BUTTONS.items()
        }
        self._levels = {}

    def send(self, name, down):
        if name in self._buttons:
            if down:
                self._pad.press_button(button=self._buttons[name])
            else:
                self._pad.release_button(button=self._buttons[name])
            self._pad.update()
        else:
            self.send_analog(name, 1.0 if down else 0.0)

    def send_analog(self, name, amount):
        amount = max(0.0, min(1.0, amount))

        if name in XBOX_TRIGGERS:
            setter = getattr(self._pad, f"{XBOX_TRIGGERS[name]}_trigger_float")
            setter(value_float=amount)
        elif name in XBOX_STICKS:
            self._levels[name] = amount
            self._set_stick(XBOX_STICKS[name][0])

        self._pad.update()

    def _set_stick(self, side):
        x = y = 0.0
        for name, level in self._levels.items():
            stick = XBOX_STICKS[name]
            if stick[0] == side:
                x += stick[1] * level
                y += stick[2] * level

        setter = getattr(self._pad, f"{side}_joystick_float")
        setter(x_value_float=max(-1.0, min(1.0, x)), y_value_float=max(-1.0, min(1.0, y)))

    def close(self):
        try:
            self._pad.reset()
            self._pad.update()
        except Exception:
            pass
        self._pad = None


class PlatformOutput:

    def __init__(self, keyboard_factory=None, xbox_factory=None):
        self._factories = {
            DEVICE_KEYBOARD: keyboard_factory or create_keyboard,
            DEVICE_XBOX: xbox_factory or XboxOutput,
        }
        self._devices = {}
        self._failures = {}
        self._down = set()
        self._analog = set()

    def _device(self, device):
        if device in self._failures:
            raise self._failures[device]

        if device not in self._devices:
            try:
                self._devices[device] = self._factories[device]()
            except OutputError as exc:
                self._failures[device] = exc
                raise

        return self._devices[device]

    def prepare(self, devices):
        for device in devices:
            self._device(device)

    def press(self, device, key):
        self._device(device).send(key, True)
        self._down.add((device, key))

    def release(self, device, key):
        self._down.discard((device, key))
        backend = self._devices.get(device)
        if backend is not None:
            backend.send(key, False)

    def set_analog(self, device, key, amount):
        self._device(device).send_analog(key, amount)
        if amount > 0:
            self._analog.add((device, key))
        else:
            self._analog.discard((device, key))

    def release_all(self):
        for device, key in list(self._down):
            try:
                self.release(device, key)
            except OutputError:
                pass
        self._down.clear()

        for device, key in list(self._analog):
            try:
                self.set_analog(device, key, 0.0)
            except OutputError:
                pass
        self._analog.clear()

    def close(self):
        self.release_all()
        for backend in self._devices.values():
            try:
                backend.close()
            except Exception:
                pass
        self._devices.clear()
        self._failures.clear()


class BindingEngine(QObject):

    values_updated = pyqtSignal(list)
    state_changed = pyqtSignal()

    def __init__(self, parent=None, store=None, output=None):
        super().__init__(parent)

        self._store = store or BindingStore()
        self._output = output or PlatformOutput()

        settings = self._store.load()
        self._bindings = settings.bindings
        self._enabled = settings.enabled

        self._paused = False
        self._values = [0] * FINGER_COUNT
        self._finger_down = [False] * FINGER_COUNT
        self._held = set()
        self._analog = {}
        self._output_error = ""
        self._save_error = ""
        self._prepared = False

    @property
    def settings_path(self):
        return self._store.path

    @property
    def bindings(self):
        return [Binding(b.device, b.key, b.threshold) for b in self._bindings]

    @property
    def enabled(self):
        return self._enabled

    @property
    def error(self):
        return self._output_error or self._save_error

    def values(self):
        return list(self._values)

    def finger_states(self):
        return list(self._finger_down)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return

        self._enabled = enabled

        if enabled:
            self._prepare()
        else:
            self._release_held()
            self._output.close()
            self._prepared = False
            self._output_error = ""

        self._persist()
        self.state_changed.emit()

    def set_paused(self, paused):
        self._paused = bool(paused)
        if self._paused and self._release_held():
            self.state_changed.emit()

    def set_bindings(self, bindings):
        self._release_held()
        self._bindings = [Binding(b.device, b.key, b.threshold) for b in bindings[:FINGER_COUNT]]
        while len(self._bindings) < FINGER_COUNT:
            self._bindings.append(Binding())

        self._prepared = False
        if self._enabled:
            self._output.close()
            self._prepare()

        saved = self._persist()
        self.state_changed.emit()
        return saved

    @pyqtSlot(list)
    def update(self, values):
        clean = [max(0, min(100, int(v))) for v in list(values)[:FINGER_COUNT]]
        clean += [0] * (FINGER_COUNT - len(clean))
        self._values = clean
        self.values_updated.emit(list(clean))

        if not self._enabled or self._paused:
            return

        if not self._prepared:
            self._prepare()

        self._evaluate()

    def shutdown(self):
        self._release_held()
        self._output.close()
        self._prepared = False

    @pyqtSlot()
    def release_all(self):
        self._release_held()
        self._values = [0] * FINGER_COUNT
        self.values_updated.emit(list(self._values))
        self.state_changed.emit()

    def _prepare(self):
        devices = {b.device for b in self._bindings if b.device != DEVICE_NONE}
        self._prepared = True

        try:
            self._output.prepare(devices)
        except OutputError as exc:
            self._output_error = str(exc)
        else:
            self._output_error = ""

    def _persist(self):
        try:
            self._store.save(Settings(self._enabled, self.bindings))
        except OSError as exc:
            self._save_error = f"Couldn't save the key bindings: {exc}"
            return False

        self._save_error = ""
        return True

    def _evaluate(self):
        changed = False
        levels = {}
        error_before = self.error

        for index, binding in enumerate(self._bindings):
            value = self._values[index]
            was_down = self._finger_down[index]

            if binding.device == DEVICE_NONE or not binding.key:
                down = False
            elif is_analog_input(binding.device, binding.key):
                amount = analog_amount(value)
                down = amount > 0
                if down:
                    key = (binding.device, binding.key)
                    levels[key] = max(levels.get(key, 0.0), amount)
            elif was_down:
                down = value >= max(0, binding.threshold - HYSTERESIS)
            else:
                down = value >= binding.threshold

            if down != was_down:
                self._finger_down[index] = down
                changed = True

        self._apply_analog(levels)

        if changed:
            self._apply()

        if changed or self.error != error_before:
            self.state_changed.emit()

    def _apply_analog(self, levels):
        for device, key in set(self._analog) | set(levels):
            amount = levels.get((device, key), 0.0)
            if amount == self._analog.get((device, key), 0.0):
                continue

            try:
                self._output.set_analog(device, key, amount)
            except OutputError as exc:
                self._output_error = str(exc)
                continue

            if amount > 0:
                self._analog[(device, key)] = amount
            else:
                self._analog.pop((device, key), None)

    def _apply(self):
        wanted = {
            (b.device, b.key)
            for index, b in enumerate(self._bindings)
            if self._finger_down[index] and b.device != DEVICE_NONE and b.key
            and not is_analog_input(b.device, b.key)
        }

        for device, key in self._held - wanted:
            try:
                self._output.release(device, key)
            except OutputError as exc:
                self._output_error = str(exc)

        for device, key in wanted - self._held:
            try:
                self._output.press(device, key)
            except OutputError as exc:
                self._output_error = str(exc)
                wanted.discard((device, key))

        self._held = wanted

    def _release_held(self):
        had_inputs = bool(self._held) or any(self._finger_down)

        for device, key in list(self._held):
            try:
                self._output.release(device, key)
            except OutputError as exc:
                self._output_error = str(exc)

        for device, key in list(self._analog):
            try:
                self._output.set_analog(device, key, 0.0)
            except OutputError as exc:
                self._output_error = str(exc)

        self._analog = {}
        self._held = set()
        self._finger_down = [False] * FINGER_COUNT
        return had_inputs


STYLE = f"""
QDialog {{
    background-color: {PANEL_BG};
}}
QLabel#bindingsStatus {{
    color: {DIM};
}}
QLabel#bindingsStatus[error="true"] {{
    color: {WARN};
}}
QLabel#columnHeader {{
    color: {DIM};
    font-weight: 600;
}}
QLabel#fingerName {{
    font-family: Consolas, monospace;
    font-weight: 700;
}}
QLabel#dimText {{
    color: {DIM};
}}
QLabel#unsavedLabel {{
    color: {ACCENT};
    font-weight: 600;
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {CONTROL_BG};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QComboBox, QSpinBox {{
    background-color: {CONTROL_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox QAbstractItemView {{
    background-color: {CONTROL_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: #2a2a2e;
    selection-color: {TEXT};
    outline: none;
}}
QPushButton:disabled {{
    background-color: {PANEL_BG};
    color: {DIM};
}}
QPushButton[capturing="true"] {{
    border-color: {ACCENT};
    background-color: #232326;
}}
QProgressBar {{
    background-color: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    min-height: 16px;
    max-height: 16px;
}}
QProgressBar::chunk {{
    background-color: {FILL};
    border-radius: 3px;
}}
QProgressBar[pressed="true"]::chunk {{
    background-color: {ACCENT};
}}
"""


class KeyCaptureButton(QPushButton):

    keyCaptured = pyqtSignal(str)
    captureStarted = pyqtSignal()
    captureEnded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key = ""
        self._capturing = False
        self.setAutoDefault(False)
        self.setDefault(False)
        self.clicked.connect(self._begin)
        self._refresh()

    def key(self):
        return self._key

    def set_key(self, name):
        self._key = name
        self._refresh()

    def is_capturing(self):
        return self._capturing

    def _begin(self, _checked=False):
        if self._capturing:
            return
        self._capturing = True
        self.setFocus()
        self._refresh()
        self.captureStarted.emit()

    def _end(self):
        if not self._capturing:
            return
        self._capturing = False
        self._refresh()
        self.captureEnded.emit()

    def _refresh(self):
        if self._capturing:
            self.setText("Press a key...")
        elif self._key:
            self.setText(key_label(self._key))
        else:
            self.setText("Click to set key")

        self.setProperty("capturing", self._capturing)
        self.style().unpolish(self)
        self.style().polish(self)

    def event(self, event):
        if self._capturing and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return

        event.accept()
        if event.isAutoRepeat():
            return

        name = key_name_from_event(event)
        if name is None:
            self.setText("Unsupported key, try another")
            return

        self._key = name
        self._end()
        self.keyCaptured.emit(name)

    def focusOutEvent(self, event):
        self._end()
        super().focusOutEvent(event)

    def focusNextPrevChild(self, next_child):
        if self._capturing:
            return False
        return super().focusNextPrevChild(next_child)


class _FingerRow:

    def __init__(self, finger):
        self.finger = finger

        self.name_label = QLabel(finger.upper())
        self.name_label.setObjectName("fingerName")

        self.type_combo = QComboBox()
        for device, label in DEVICE_CHOICES:
            self.type_combo.addItem(label, device)

        self.stack = QStackedWidget()
        placeholder = QLabel("-")
        placeholder.setObjectName("dimText")
        self.capture = KeyCaptureButton()
        self.xbox_combo = QComboBox()
        for key, label in XBOX_INPUTS.items():
            self.xbox_combo.addItem(label, key)
        self.stack.addWidget(placeholder)
        self.stack.addWidget(self.capture)
        self.stack.addWidget(self.xbox_combo)

        self.spin = QSpinBox()
        self.spin.setRange(1, 100)
        self.spin.setSuffix(" %")
        self.spin.setValue(DEFAULT_THRESHOLD)

        self.analog_label = QLabel("Analog")
        self.analog_label.setObjectName("dimText")
        self.analog_label.setToolTip("Triggers and sticks follow the finger position continuously.")

        self.threshold_stack = QStackedWidget()
        self.threshold_stack.addWidget(self.spin)
        self.threshold_stack.addWidget(self.analog_label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.value_label = QLabel("0%")
        self.value_label.setObjectName("dimText")
        self.value_label.setMinimumWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.live = QWidget()
        live_layout = QHBoxLayout(self.live)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(8)
        live_layout.addWidget(self.bar, 1)
        live_layout.addWidget(self.value_label)

    def device(self):
        return self.type_combo.currentData()

    def is_analog(self):
        return is_analog_input(self.device(), self.xbox_combo.currentData())

    def show_threshold_page(self):
        self.threshold_stack.setCurrentIndex(1 if self.is_analog() else 0)

    def show_device_page(self):
        device = self.device()
        self.stack.setCurrentIndex({DEVICE_NONE: 0, DEVICE_KEYBOARD: 1, DEVICE_XBOX: 2}[device])


class KeyBindingsWindow(QDialog):

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._rows = []
        self._dirty = False
        self._loading = False

        self.setWindowTitle("Key bindings")
        self.setMinimumSize(700, 440)
        self.setStyleSheet(STYLE)

        self._build_ui()
        self._connect_signals()
        self._load_from_engine()
        self._on_state()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.status_label = QLabel()
        self.status_label.setObjectName("bindingsStatus")
        self.status_label.setWordWrap(True)
        self.enable_box = QCheckBox("Send inputs")
        header.addWidget(self.status_label, 1)
        header.addWidget(self.enable_box)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(4, 1)
        grid.setColumnMinimumWidth(0, 80)
        grid.setColumnMinimumWidth(1, 150)
        grid.setColumnMinimumWidth(2, 200)
        grid.setColumnMinimumWidth(3, 90)
        grid.setColumnMinimumWidth(4, 150)

        for column, title in enumerate(("Finger", "Type", "Binding", "Threshold", "Now")):
            label = QLabel(title)
            label.setObjectName("columnHeader")
            grid.addWidget(label, 0, column)

        for index, finger in enumerate(FINGER_NAMES):
            row = _FingerRow(finger)
            self._rows.append(row)
            grid.addWidget(row.name_label, index + 1, 0)
            grid.addWidget(row.type_combo, index + 1, 1)
            grid.addWidget(row.stack, index + 1, 2)
            grid.addWidget(row.threshold_stack, index + 1, 3)
            grid.addWidget(row.live, index + 1, 4)

        layout.addLayout(grid)
        layout.addStretch(1)

        self.path_label = QLabel(f"Settings file: {self.engine.settings_path}")
        self.path_label.setObjectName("dimText")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = QPushButton("Save")
        self.close_button = QPushButton("Close")
        self.unsaved_label = QLabel("")
        self.unsaved_label.setObjectName("unsavedLabel")
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.unsaved_label)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        for button in (self.save_button, self.close_button):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _connect_signals(self):
        for row in self._rows:
            row.type_combo.currentIndexChanged.connect(lambda _i, r=row: self._on_type_changed(r))
            row.capture.keyCaptured.connect(lambda _k: self._mark_dirty())
            row.capture.captureStarted.connect(lambda: self.engine.set_paused(True))
            row.capture.captureEnded.connect(lambda: self.engine.set_paused(False))
            row.xbox_combo.currentIndexChanged.connect(lambda _i, r=row: self._on_xbox_changed(r))
            row.spin.valueChanged.connect(lambda _v: self._mark_dirty())

        self.enable_box.toggled.connect(self.engine.set_enabled)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)

        self.engine.values_updated.connect(self._on_values)
        self.engine.state_changed.connect(self._on_state)

    def show_window(self):
        if not self.isVisible():
            self._load_from_engine()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_type_changed(self, row):
        row.show_device_page()
        row.show_threshold_page()
        self._mark_dirty()

    def _on_xbox_changed(self, row):
        row.show_threshold_page()
        self._mark_dirty()

    def _mark_dirty(self):
        if self._loading:
            return
        self._set_dirty(True)

    def _set_dirty(self, dirty):
        self._dirty = dirty
        self.unsaved_label.setText("Unsaved changes" if dirty else "")

    def _load_from_engine(self):
        self._loading = True

        for row, binding in zip(self._rows, self.engine.bindings):
            row.type_combo.setCurrentIndex(max(0, row.type_combo.findData(binding.device)))
            row.show_device_page()

            row.capture.set_key(binding.key if binding.device == DEVICE_KEYBOARD else "")

            if binding.device == DEVICE_XBOX:
                row.xbox_combo.setCurrentIndex(max(0, row.xbox_combo.findData(binding.key)))
            else:
                row.xbox_combo.setCurrentIndex(0)

            row.spin.setValue(binding.threshold)
            row.show_threshold_page()

        self._loading = False
        self._set_dirty(False)

    def _collect(self):
        bindings = []

        for row in self._rows:
            device = row.device()
            key = ""

            if device == DEVICE_KEYBOARD:
                key = row.capture.key()
            elif device == DEVICE_XBOX:
                key = row.xbox_combo.currentData()

            if device != DEVICE_NONE and not key:
                device = DEVICE_NONE

            bindings.append(Binding(device, key, row.spin.value()))

        return bindings

    def _save(self):
        self.engine.set_bindings(self._collect())
        self._set_dirty(False)

    def _on_values(self, values):
        for row, value in zip(self._rows, values):
            row.bar.setValue(value)
            row.value_label.setText(f"{value}%")

    def _on_state(self):
        self.enable_box.blockSignals(True)
        self.enable_box.setChecked(self.engine.enabled)
        self.enable_box.blockSignals(False)

        error = self.engine.error
        if error:
            text = error
        elif not self.engine.enabled:
            text = 'Inputs are off. Tick "Send inputs" to start sending them.'
        else:
            text = "Inputs are on while the glove streams over WiFi."

        self.status_label.setText(text)
        self.status_label.setProperty("error", bool(error))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        for row, pressed in zip(self._rows, self.engine.finger_states()):
            if bool(row.bar.property("pressed")) != pressed:
                row.bar.setProperty("pressed", pressed)
                row.bar.style().unpolish(row.bar)
                row.bar.style().polish(row.bar)

    def reject(self):
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save the key bindings before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )

            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save:
                self._save()
            else:
                self._load_from_engine()

        super().reject()