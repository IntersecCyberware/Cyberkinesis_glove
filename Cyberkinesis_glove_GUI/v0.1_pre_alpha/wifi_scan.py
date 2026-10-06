"""
wifi_scan.py - best-effort local WiFi network scanning.

This scans for networks visible to *this computer* (not the glove) using
whatever the OS provides, so the network dialog can show real nearby
SSIDs - with a signal-strength reading, where the OS reports one -
without needing the glove connected at all. Parsing is split out from
the subprocess calls so it can be unit-tested with sample output.
"""

import platform
import re
import subprocess


def scan_wifi_networks(timeout=10):
    """Returns a sorted list of (ssid, signal_percent) tuples for
    networks visible to *this computer*, or [] if scanning isn't
    available/permitted on this machine (never raises).

    signal_percent is an int 0-100, or None if this OS's scan command
    didn't report a strength we could parse for that network - callers
    should treat None as "unknown", not as 0%.
    """
    system = platform.system()
    try:
        if system == "Windows":
            return _scan_windows(timeout)
        elif system == "Darwin":
            return _scan_macos(timeout)
        elif system == "Linux":
            return _scan_linux(timeout)
        else:
            print(f"[wifi_scan] unsupported OS: {system}")
    except Exception as exc:
        print(f"[wifi_scan] scan failed: {exc}")
    return []


def _run(cmd, timeout):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True,
    ).stdout


def _merge_best(best, ssid, signal):
    """Keeps the strongest signal seen so far for `ssid` (a network can
    show up more than once - multiple BSSIDs on Windows, multiple scan
    cells elsewhere). A None signal never overwrites a known one."""
    if not ssid:
        return
    if ssid not in best or (signal is not None and (best[ssid] is None or signal > best[ssid])):
        best[ssid] = signal


# -- Windows: netsh -----------------------------------------------------

def parse_windows_networks(output):
    """Parses `netsh wlan show networks mode=Bssid` output. A network can
    list several BSSIDs (access points); we keep the strongest Signal%
    seen for each SSID."""
    best = {}
    current_ssid = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        low = line.lower()
        if low.startswith("ssid") and ":" in line:
            current_ssid = line.split(":", 1)[1].strip()
        elif low.startswith("signal") and ":" in line and current_ssid:
            value = line.split(":", 1)[1].strip().rstrip("%")
            try:
                signal = int(value)
            except ValueError:
                continue
            _merge_best(best, current_ssid, signal)
    return sorted(best.items())


def _scan_windows(timeout):
    output = _run(["netsh", "wlan", "show", "networks", "mode=Bssid"], timeout)
    return parse_windows_networks(output)


# -- macOS: airport -------------------------------------------------------

_AIRPORT_PATH = (
    "/System/Library/PrivateFrameworks/Apple80211.framework"
    "/Versions/Current/Resources/airport"
)


def _rssi_to_percent(rssi):
    """Rough dBm -> percent conversion (common approximation used by a
    lot of WiFi tooling: -100dBm or worse = 0%, -50dBm or better = 100%,
    linear in between)."""
    return max(0, min(100, 2 * (rssi + 100)))


def parse_macos_networks(output):
    lines = output.splitlines()
    if not lines:
        return []
    best = {}
    for line in lines[1:]:  # first line is the header
        if not line.strip():
            continue
        # airport's table is fixed-width with SSID as the first, left
        # padded column; this is the standard (imperfect for SSIDs that
        # contain lots of spaces) way to pull it out. RSSI (dBm) is a
        # small negative number further along the same line.
        ssid = line[:32].strip()
        if not ssid:
            continue
        rssi_match = re.search(r"(-\d{2,3})", line[32:])
        signal = _rssi_to_percent(int(rssi_match.group(1))) if rssi_match else None
        _merge_best(best, ssid, signal)
    return sorted(best.items())


def _scan_macos(timeout):
    output = _run([_AIRPORT_PATH, "-s"], timeout)
    return parse_macos_networks(output)


# -- Linux: nmcli (preferred) or iwlist (fallback) -------------------------

def parse_nmcli_networks(output):
    """Parses `nmcli -t -f SSID,SIGNAL device wifi list` terse output:
    one "SSID:SIGNAL" pair per line, with literal ':' inside the SSID
    escaped as '\\:' by nmcli."""
    best = {}
    for raw_line in output.splitlines():
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        # Split off the trailing numeric SIGNAL field from the right,
        # rather than the (possibly colon-containing) SSID from the left.
        ssid_part, sep, signal_part = line.rpartition(":")
        if not sep:
            continue
        ssid = ssid_part.replace("\\:", ":").strip()
        try:
            signal = int(signal_part.strip())
        except ValueError:
            signal = None
        _merge_best(best, ssid, signal)
    return sorted(best.items())


def parse_iwlist_networks(output):
    """Parses `iwlist scan` output cell-by-cell so each ESSID is paired
    with its own Quality=N/M reading rather than just grabbing every
    ESSID and every Quality value independently."""
    best = {}
    for cell in re.split(r"Cell \d+ - ", output)[1:]:
        essid_match = re.search(r'ESSID:"([^"]*)"', cell)
        if not essid_match or not essid_match.group(1):
            continue
        ssid = essid_match.group(1)
        quality_match = re.search(r"Quality=(\d+)/(\d+)", cell)
        signal = None
        if quality_match:
            num, den = int(quality_match.group(1)), int(quality_match.group(2))
            if den:
                signal = round(num / den * 100)
        _merge_best(best, ssid, signal)
    return sorted(best.items())


def _scan_linux(timeout):
    try:
        _run(["nmcli", "device", "wifi", "rescan"], timeout)
    except Exception:
        pass  # rescan is best-effort; list works off the last scan anyway

    try:
        output = _run(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"], timeout)
        networks = parse_nmcli_networks(output)
        if networks:
            return networks
    except Exception as exc:
        print(f"[wifi_scan] nmcli unavailable ({exc}), trying iwlist")

    try:
        output = _run(["iwlist", "scan"], timeout)
        return parse_iwlist_networks(output)
    except Exception as exc:
        print(f"[wifi_scan] iwlist unavailable ({exc})")

    return []