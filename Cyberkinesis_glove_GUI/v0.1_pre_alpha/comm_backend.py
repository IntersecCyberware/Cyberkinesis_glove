"""
glove_backend.py

Transport layer for the Cyberkinesis glove, factored out of the original
console script so it can be reused by both a CLI and the PyQt6 GUI.

This file intentionally keeps the low-level handshake / socket / serial
primitives byte-for-byte compatible with the original script - only the
console-specific glue (the print()/input() driven main loop, run_udp(),
serial_console(), listener(), main()) has been removed, since the GUI
replaces that glue with QThread workers (see glove_gui.py).

Protocol summary (unchanged from the original script):
    Ser_hs:  ESP sends ID+SER_MODE every 500ms, expects ser_ok
    UDP_hs:  ESP sends ID+UDP_MODE, expects udp_ok in less than 500ms

    Ser_watchdog: not needed, detects connection automatically.
    UDP_watchdog: ESP sends udp_ping every 500ms, expects ping_ok in
                  less than 1000ms

Confirmed against the firmware source: the ESP re-sends its ID+UDP_MODE
handshake packet (not just at first connect) any time its own UDP
watchdog gives up waiting for a ping_ok reply - see
is_udp_handshake_packet() and glove_gui.py's UdpDataWorker, which needs
to recognise and answer that packet even after the initial handshake is
long done, or the glove is left stuck re-broadcasting it forever.
"""

import serial
import serial.tools.list_ports
import socket
import struct
import time


# ── Constants ─────────────────────────────────────────────────────────────

DEVICE_IDS = {
    0x04: "Cyberkinesis glove v0.4",
    0x05: "Cyberkinesis glove v0.5",
}

UDP_PORT = 2323
SER_MODE = 0x35
UDP_MODE = 0x37

SENSOR_PACKET_FMT = "<5H"           # thumb, pointer, middle, ring, pinkie
SENSOR_PACKET_SIZE = struct.calcsize(SENSOR_PACKET_FMT)  # 10 bytes


# ── UDP ───────────────────────────────────────────────────────────────────

def open_udp_socket(udp_port=UDP_PORT):
    """
    Opens the UDP socket used for handshakes, the sensor stream, and
    watchdog pings. Created ONCE and kept alive for the whole program run,
    not per-connection - see check_udp_handshake() for why.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", udp_port))
    return sock


def is_udp_handshake_packet(data):
    """
    True if `data` looks like the device's ID+UDP_MODE handshake packet
    rather than a sensor packet or a udp_ping/other watchdog message.

    The firmware sends exactly this 2-byte packet (device ID, then
    UDP_MODE) both for the very first handshake AND every time it
    re-enters udp_handshake() after its own watchdog times out - so
    anything reading the UDP socket after the initial connect (not just
    check_udp_handshake() below) needs to recognise and answer it too.
    """
    return len(data) >= 2 and data[1] == UDP_MODE


def check_udp_handshake(sock, timeout=0.0):
    """
    Checks for a UDP handshake packet (ID + UDP_MODE). If one arrives,
    replies "udp_ok" and returns the device_id; otherwise returns None
    after `timeout` seconds.

    timeout=0.0 makes this non-blocking, which is what lets this be
    polled from inside another loop (like the serial console) without
    stalling it.
    """
    sock.settimeout(timeout)
    try:
        data, addr = sock.recvfrom(32)
    except (socket.timeout, BlockingIOError):
        return None

    if is_udp_handshake_packet(data):
        sock.sendto(b"udp_ok", addr)
        return data[0]

    return None


def udp_hs(sock, timeout=1.0):
    """Blocks for up to `timeout` seconds waiting for a UDP handshake."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        device_id = check_udp_handshake(sock, timeout=remaining)
        if device_id is not None:
            return device_id


def udp_receive(sock):
    """
    Returns a (thumb, pointer, middle, ring, pinkie) tuple, or None if the
    packet was a watchdog ping (or malformed and safely ignored).
    """
    data, addr = sock.recvfrom(4096)

    if data == b"udp_ping":
        sock.sendto(b"ping_ok", addr)
        return None

    if len(data) != SENSOR_PACKET_SIZE:
        # Unexpected/short/garbled datagram - ignore instead of crashing.
        return None

    try:
        return struct.unpack(SENSOR_PACKET_FMT, data)
    except struct.error:
        return None


# ── Serial ────────────────────────────────────────────────────────────────

def open_serial_no_reset(port_name, baudrate):
    """
    Open a serial port without pulsing DTR/RTS.

    Opening a pyserial port normally asserts DTR/RTS as part of the open()
    sequence and only then lets you set them low. That transition is what
    triggers the auto-reset circuit on most ESP32 boards. To avoid it, we
    configure DTR/RTS *before* the port is actually opened.
    """
    ser = serial.Serial()
    ser.port = port_name
    ser.baudrate = baudrate
    ser.timeout = 0.05
    ser.dsrdtr = False
    ser.rtscts = False
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def ser_hs(baudrate=115200):
    for port in serial.tools.list_ports.comports():

        try:
            ser = open_serial_no_reset(port.device, baudrate)

            start_time = time.monotonic()

            while time.monotonic() - start_time < 1.0:
                if ser.in_waiting >= 2:
                    device_id = ser.read(1)[0]
                    mode = ser.read(1)[0]

                    if mode == SER_MODE:
                        # Trailing newline matters: the firmware reads the
                        # reply with Serial.readStringUntil('\n'). Without
                        # it, the ESP32 blocks for its full Stream timeout
                        # (~1s) before it gives up waiting and accepts
                        # what it has.
                        ser.write(b"ser_ok\n")
                        print(ser)
                        print(DEVICE_IDS.get(device_id, "Unknown device"))
                        return ser, device_id

            ser.close()

        except (serial.SerialException, OSError):
            pass

    return None, None


def ser_receive(ser):
    try:
        data = ser.read(ser.in_waiting or 1)
        return data if data else None
    except (serial.SerialException, OSError):
        return None


class SerialLineBuffer:
    """
    Buffers raw serial bytes and yields complete decoded text lines.

    Serial mode on this firmware is a text console (cmd_exec), not a
    binary sensor stream - all sensor data goes over UDP.
    """

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        if data:
            self.buf.extend(data)

    def lines(self):
        while b"\n" in self.buf:
            line, _, rest = self.buf.partition(b"\n")
            self.buf = bytearray(rest)
            yield line.decode("utf-8", errors="replace").rstrip("\r")