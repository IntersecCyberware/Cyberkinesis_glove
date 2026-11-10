import queue
import socket
import time

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import comm_backend as backend


class GloveLink(QThread):

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    line_received = pyqtSignal(str)
    command_sent = pyqtSignal(str)
    reply_received = pyqtSignal(str, list)

    RETRY_DELAY = 1.0
    STOP_TIMEOUT_MS = 8000
    REHANDSHAKE_QUIET = 1.5
    KEEPALIVE_INTERVAL = 0.25
    INIT_COMMANDS = ("lc",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outbox = queue.Queue()
        self._running = True
        self._is_connected = False

    def is_connected(self):
        return self._is_connected

    def send(self, command):
        if not self._is_connected:
            return False
        self._outbox.put(command)
        return True

    def stop(self):
        self._running = False
        self.wait(self.STOP_TIMEOUT_MS)

    def run(self):
        while self._running:
            ser, device_id = backend.ser_hs()
            if ser is None:
                self._idle(self.RETRY_DELAY)
                continue
            self._run_session(ser, device_id)

    def _idle(self, seconds):
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.05)

    def _clear_outbox(self):
        while True:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                return

    def _flush_outbox(self, ser):
        while True:
            try:
                command = self._outbox.get_nowait()
            except queue.Empty:
                return
            backend.ser_send(ser, command)
            self.command_sent.emit(backend.mask_command(command))

    def _start_session(self, device_id):
        self._clear_outbox()
        for command in self.INIT_COMMANDS:
            self._outbox.put(command)
        self._is_connected = True
        self.connected.emit(backend.DEVICE_IDS.get(device_id, "Unknown device"))

    def _run_session(self, ser, device_id):
        self._start_session(device_id)

        buffer = backend.SerialLineBuffer()
        tail = b""
        quiet_until = 0.0
        next_keepalive = time.monotonic() + self.KEEPALIVE_INTERVAL

        try:
            while self._running:
                self._flush_outbox(ser)

                now = time.monotonic()
                if now >= next_keepalive:
                    backend.ser_send(ser, "ser_ok")
                    next_keepalive = now + self.KEEPALIVE_INTERVAL

                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue

                window = tail + chunk
                tail = window[-1:]

                new_id = backend.find_serial_handshake(window)
                if new_id is not None:
                    if time.monotonic() >= quiet_until:
                        backend.ser_send(ser, "ser_ok")
                        quiet_until = time.monotonic() + self.REHANDSHAKE_QUIET
                        buffer = backend.SerialLineBuffer()
                        self._is_connected = False
                        self.disconnected.emit()
                        self._start_session(new_id)
                    continue

                buffer.feed(chunk)
                for line in buffer.lines():
                    self.line_received.emit(line)
                    reply = backend.parse_reply(line)
                    if reply:
                        self.reply_received.emit(reply[0], reply[1])
        except (serial.SerialException, OSError):
            pass
        finally:
            self._is_connected = False
            try:
                ser.close()
            except (serial.SerialException, OSError):
                pass
            self._clear_outbox()
            self.disconnected.emit()


class UdpLink(QThread):

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    data_received = pyqtSignal(list)

    RECV_TIMEOUT = 0.3
    LINK_TIMEOUT = 2.0
    BIND_RETRY_DELAY = 1.0
    STOP_TIMEOUT_MS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._is_connected = False
        self._last_rx = 0.0

    def is_connected(self):
        return self._is_connected

    def stop(self):
        self._running = False
        self.wait(self.STOP_TIMEOUT_MS)

    def run(self):
        sock = None
        while self._running and sock is None:
            try:
                sock = backend.open_udp_socket()
            except OSError:
                self._idle(self.BIND_RETRY_DELAY)

        if sock is None:
            return

        sock.settimeout(self.RECV_TIMEOUT)

        try:
            while self._running:
                self._poll(sock)
        finally:
            sock.close()
            self._set_connected(False, "")

    def _idle(self, seconds):
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.05)

    def _set_connected(self, connected, name):
        if connected == self._is_connected:
            return
        self._is_connected = connected
        if connected:
            self.connected.emit(name)
        else:
            self.disconnected.emit()

    def _poll(self, sock):
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            data = None
        except OSError:
            self._idle(self.BIND_RETRY_DELAY)
            return

        now = time.monotonic()

        if data is None:
            if self._is_connected and now - self._last_rx > self.LINK_TIMEOUT:
                self._set_connected(False, "")
            return

        if len(data) == 2 and backend.is_udp_handshake_packet(data):
            sock.sendto(b"udp_ok", addr)
            self._last_rx = now
            name = backend.DEVICE_IDS.get(data[0], "Unknown device")
            self._set_connected(True, name)
            return

        if data == b"udp_ping":
            sock.sendto(b"ping_ok", addr)
            self._last_rx = now
            return

        if len(data) == backend.SENSOR_PACKET_SIZE:
            self._last_rx = now
            self.data_received.emit(list(backend.struct.unpack(backend.SENSOR_PACKET_FMT, data)))