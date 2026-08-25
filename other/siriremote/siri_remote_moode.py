#!/usr/bin/env python3
"""Control moOde Audio with a first-generation Apple Siri Remote.

This daemon speaks ATT directly over the Bluetooth LE fixed L2CAP channel.
It deliberately does not use gatttool, BlueZ D-Bus GATT, bleak, or bluepy.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import logging
import os
import queue
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


LOG = logging.getLogger("siri-remote-moode")

AF_BLUETOOTH = 31
SOCK_SEQPACKET = 5
BTPROTO_L2CAP = 0
SOL_BLUETOOTH = 274
BT_SECURITY = 4
ATT_CID = 4
BDADDR_LE_PUBLIC = 0x01
BDADDR_LE_RANDOM = 0x02

ATT_OP_ERROR_RSP = 0x01
ATT_OP_MTU_REQ = 0x02
ATT_OP_MTU_RSP = 0x03
ATT_OP_READ_REQ = 0x0A
ATT_OP_READ_RSP = 0x0B
ATT_OP_WRITE_REQ = 0x12
ATT_OP_WRITE_RSP = 0x13
ATT_OP_HANDLE_NOTIFY = 0x1B
ATT_OP_HANDLE_IND = 0x1D
ATT_OP_HANDLE_CONFIRM = 0x1E

HANDLE_INPUT_ENABLE = 0x001D
HANDLE_INPUT_VALUE = 0x0023
HANDLE_INPUT_CCCD = 0x0024
HANDLE_BATTERY_VALUE = 0x0028

BUTTON_AIRPLAY = 0x01
BUTTON_VOLUME_UP = 0x02
BUTTON_VOLUME_DOWN = 0x04
BUTTON_PLAY_PAUSE = 0x08
BUTTON_SIRI = 0x10
BUTTON_MENU = 0x20
BUTTON_TOUCHPAD = 0x80

TOUCH_EVENT_MARKER = 0x32
GEN1_TOUCH_X_MIN = 2278
GEN1_TOUCH_X_MAX = 3914
GEN1_TOUCH_X_MID = (GEN1_TOUCH_X_MIN + GEN1_TOUCH_X_MAX) // 2

ATT_ERRORS = {
    0x01: "invalid handle",
    0x02: "read not permitted",
    0x03: "write not permitted",
    0x05: "insufficient authentication",
    0x08: "insufficient authorization",
    0x0C: "insufficient encryption key size",
    0x0F: "insufficient encryption",
}


class BdAddr(ctypes.Structure):
    _fields_ = [("b", ctypes.c_ubyte * 6)]


class SockAddrL2(ctypes.Structure):
    _fields_ = [
        ("family", ctypes.c_ushort),
        ("psm", ctypes.c_ushort),
        ("bdaddr", BdAddr),
        ("cid", ctypes.c_ushort),
        ("bdaddr_type", ctypes.c_ubyte),
    ]


class BtSecurity(ctypes.Structure):
    _fields_ = [("level", ctypes.c_ubyte), ("key_size", ctypes.c_ubyte)]


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def parse_mac(value: str) -> bytes:
    parts = value.split(":")
    if len(parts) != 6:
        raise ValueError(f"invalid Bluetooth address: {value!r}")
    try:
        raw = bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise ValueError(f"invalid Bluetooth address: {value!r}") from exc
    if any(len(part) != 2 for part in parts):
        raise ValueError(f"invalid Bluetooth address: {value!r}")
    return raw


def sockaddr(mac: str | None, address_type: int) -> SockAddrL2:
    address = SockAddrL2()
    address.family = AF_BLUETOOTH
    address.psm = 0
    # Linux stores bdaddr_t least-significant byte first.
    raw = bytes(6) if mac is None else parse_mac(mac)[::-1]
    address.bdaddr = BdAddr((ctypes.c_ubyte * 6)(*raw))
    address.cid = ATT_CID
    address.bdaddr_type = address_type
    return address


def checked_call(libc: ctypes.CDLL, name: str, *args: object) -> int:
    result = getattr(libc, name)(*args)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(result)


class RawAttClient:
    def __init__(self, mac: str, address_type: str, security: str) -> None:
        self.mac = mac
        self.address_type = {
            "public": BDADDR_LE_PUBLIC,
            "random": BDADDR_LE_RANDOM,
        }[address_type]
        self.security_level = {"low": 1, "medium": 2, "high": 3, "fips": 4}[security]
        self.sock: socket.socket | None = None
        self.notification_handler = None

    def connect(self) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = checked_call(libc, "socket", AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)
        try:
            security = BtSecurity(self.security_level, 0)
            checked_call(
                libc,
                "setsockopt",
                fd,
                SOL_BLUETOOTH,
                BT_SECURITY,
                ctypes.byref(security),
                ctypes.sizeof(security),
            )
            local = sockaddr(None, BDADDR_LE_PUBLIC)
            checked_call(libc, "bind", fd, ctypes.byref(local), ctypes.sizeof(local))
            remote = sockaddr(self.mac, self.address_type)
            checked_call(libc, "connect", fd, ctypes.byref(remote), ctypes.sizeof(remote))
            self.sock = socket.socket(fileno=fd)
            fd = -1
            # Keep Bluetooth SOCK_SEQPACKET blocking. On some recent kernels,
            # Python's settimeout() non-blocking emulation returns immediately
            # for this socket type and creates a 100% CPU spin loop.
            self.sock.setblocking(True)
        finally:
            if fd >= 0:
                libc.close(fd)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, packet: bytes) -> None:
        if self.sock is None:
            raise ConnectionError("ATT socket is not connected")
        self.sock.sendall(packet)

    def _receive(self, timeout: float = 10.0) -> bytes:
        if self.sock is None:
            raise ConnectionError("ATT socket is not connected")
        readable, _, exceptional = select.select([self.sock], [], [self.sock], timeout)
        if exceptional:
            raise ConnectionError("ATT socket entered an exceptional state")
        if not readable:
            raise socket.timeout("timed out waiting for an ATT packet")
        packet = self.sock.recv(512)
        if not packet:
            raise ConnectionError("remote closed the ATT connection")
        return packet

    def _dispatch_async(self, packet: bytes) -> bool:
        if not packet:
            return True
        opcode = packet[0]
        if opcode == ATT_OP_HANDLE_NOTIFY and len(packet) >= 3:
            handle = struct.unpack_from("<H", packet, 1)[0]
            if self.notification_handler is not None:
                self.notification_handler(handle, packet[3:])
            return True
        if opcode == ATT_OP_HANDLE_IND and len(packet) >= 3:
            handle = struct.unpack_from("<H", packet, 1)[0]
            if self.notification_handler is not None:
                self.notification_handler(handle, packet[3:])
            self._send(bytes([ATT_OP_HANDLE_CONFIRM]))
            return True
        return False

    def write_request(self, handle: int, value: bytes) -> None:
        packet = bytes([ATT_OP_WRITE_REQ]) + struct.pack("<H", handle) + value
        self._send(packet)
        while True:
            response = self._receive()
            if self._dispatch_async(response):
                continue
            if response[0] == ATT_OP_WRITE_RSP:
                return
            if response[0] == ATT_OP_ERROR_RSP and len(response) >= 5:
                failed_opcode = response[1]
                failed_handle = struct.unpack_from("<H", response, 2)[0]
                code = response[4]
                detail = ATT_ERRORS.get(code, "unknown ATT error")
                raise PermissionError(
                    f"ATT error 0x{code:02x} ({detail}) for opcode "
                    f"0x{failed_opcode:02x}, handle 0x{failed_handle:04x}"
                )
            raise ConnectionError(f"unexpected ATT response: {response.hex(' ')}")

    def read_request(self, handle: int) -> bytes:
        self._send(bytes([ATT_OP_READ_REQ]) + struct.pack("<H", handle))
        while True:
            response = self._receive()
            if self._dispatch_async(response):
                continue
            if response[0] == ATT_OP_READ_RSP:
                return response[1:]
            if response[0] == ATT_OP_ERROR_RSP and len(response) >= 5:
                code = response[4]
                detail = ATT_ERRORS.get(code, "unknown ATT error")
                raise ConnectionError(
                    f"ATT read failed for handle 0x{handle:04x}: "
                    f"0x{code:02x} ({detail})"
                )
            raise ConnectionError(f"unexpected ATT read response: {response.hex(' ')}")

    def exchange_mtu(self, requested_mtu: int) -> int:
        self._send(bytes([ATT_OP_MTU_REQ]) + struct.pack("<H", requested_mtu))
        while True:
            response = self._receive()
            if self._dispatch_async(response):
                continue
            if response[0] == ATT_OP_MTU_RSP and len(response) >= 3:
                remote_mtu = struct.unpack_from("<H", response, 1)[0]
                return max(23, min(requested_mtu, remote_mtu))
            if response[0] == ATT_OP_ERROR_RSP and len(response) >= 5:
                code = response[4]
                detail = ATT_ERRORS.get(code, "unknown ATT error")
                raise ConnectionError(f"ATT MTU exchange failed: 0x{code:02x} ({detail})")
            raise ConnectionError(f"unexpected ATT MTU response: {response.hex(' ')}")

    def enable_input(self) -> None:
        # Register the notification path before sending the HID feature report.
        # This mirrors BlueZ HoG + hid-siriremote: HID I/O is started first and
        # only then is feature report F0/AF sent. BlueZ strips report ID F0, so
        # the actual ATT value for handle 0x001d remains the single byte AF.
        # Both writes use ATT Write Requests and must be acknowledged.
        LOG.info("Enabling notifications via 0x0024")
        self.write_request(HANDLE_INPUT_CCCD, b"\x01\x00")
        LOG.info("Activating input: writing AF to 0x001d")
        self.write_request(HANDLE_INPUT_ENABLE, b"\xAF")

    def receive_forever(self, stop_event: threading.Event, keepalive_seconds: float) -> None:
        next_keepalive = (
            time.monotonic() + keepalive_seconds if keepalive_seconds > 0 else None
        )
        while not stop_event.is_set():
            wait_seconds = (
                max(0.05, min(1.0, next_keepalive - time.monotonic()))
                if next_keepalive is not None else 1.0
            )
            try:
                packet = self._receive(wait_seconds)
            except socket.timeout:
                packet = None
            if packet is not None and not self._dispatch_async(packet):
                LOG.debug("Ignoring ATT packet: %s", packet.hex(" "))
            if next_keepalive is not None and time.monotonic() >= next_keepalive:
                battery = self.read_request(HANDLE_BATTERY_VALUE)
                if battery:
                    LOG.debug("Keepalive; battery=%d%%", battery[0])
                next_keepalive = time.monotonic() + keepalive_seconds


class MoodeWorker:
    def __init__(self, base_url: str, timeout: float, dry_run: bool = False) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.dry_run = dry_run
        self.commands: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=32)
        self.thread = threading.Thread(target=self._run, name="moode-http", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        try:
            self.commands.put_nowait(None)
        except queue.Full:
            pass

    def submit(self, button_name: str, command: str) -> None:
        if not command:
            LOG.info("Button %s has no configured command", button_name)
            return
        try:
            self.commands.put_nowait((button_name, command))
        except queue.Full:
            LOG.error("moOde command queue full; dropping %s", button_name)

    def _run(self) -> None:
        while True:
            item = self.commands.get()
            if item is None:
                return
            button_name, command = item
            if self.dry_run:
                LOG.info("DRY RUN: %s -> %s", button_name, command)
                continue
            query = urllib.parse.urlencode({"cmd": command}, quote_via=urllib.parse.quote)
            url = f"{self.base_url}?{query}"
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "siri-remote-moode/1.0", "Connection": "close"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response.read(4096)
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"HTTP {response.status}")
                LOG.info("%s -> %s", button_name, command)
            except (OSError, urllib.error.URLError, RuntimeError) as exc:
                # Do not retry toggle commands: if the response was lost after
                # execution, a retry could immediately undo the first command.
                LOG.error("moOde request failed for %s: %s", button_name, exc)


class X11ClickWorker:
    """Generate a click on moOde's local X11 display without extra packages."""

    def __init__(self) -> None:
        self.enabled = env("SIRI_MENU_SCREEN_CLICK", "no").lower() in (
            "1", "yes", "true", "on"
        )
        self.display_name = env("SIRI_X_DISPLAY", ":0")
        self.xauthority = env("SIRI_XAUTHORITY", "/home/mischa/.Xauthority")
        self.playback_x = int(env("SIRI_PLAYBACK_CLICK_X", "220"), 0)
        self.playback_y = int(env("SIRI_PLAYBACK_CLICK_Y", "1135"), 0)
        self.ready_timeout = float(env("SIRI_X_READY_TIMEOUT", "60"))
        command_url = env("MOODE_URL", "http://localhost/command/")
        self.view_url = urllib.parse.urljoin(
            command_url,
            "cfg-table.php?cmd=get_cfg_system_value&param=current_view",
        )
        self.http_timeout = float(env("MOODE_HTTP_TIMEOUT", "4"))
        self.commands: queue.Queue[bool | None] = queue.Queue(maxsize=4)
        self.thread = threading.Thread(target=self._run, name="moode-x11-click", daemon=True)

    def start(self) -> None:
        if not self.enabled:
            return
        self.thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        try:
            self.commands.put_nowait(None)
        except queue.Full:
            pass

    def submit(self) -> None:
        if not self.enabled:
            return
        try:
            self.commands.put_nowait(True)
        except queue.Full:
            LOG.error("X11 click queue full; dropping Menu/Back")

    @staticmethod
    def _load_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
        x11 = ctypes.CDLL("libX11.so.6")
        xtst = ctypes.CDLL("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayHeight.restype = ctypes.c_int
        xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong
        ]
        xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong
        ]
        return x11, xtst

    def _open_display(self, x11: ctypes.CDLL) -> int:
        os.environ["DISPLAY"] = self.display_name
        os.environ["XAUTHORITY"] = self.xauthority
        display = x11.XOpenDisplay(self.display_name.encode())
        if not display:
            raise RuntimeError(
                f"cannot open X display {self.display_name} using {self.xauthority}"
            )
        return display

    def _emit_click(self, x: int, y: int) -> None:
        x11, xtst = self._load_libraries()
        display = self._open_display(x11)
        try:
            xtst.XTestFakeMotionEvent(display, 0, x, y, 0)
            xtst.XTestFakeButtonEvent(display, 1, True, 0)
            xtst.XTestFakeButtonEvent(display, 1, False, 50)
            x11.XFlush(display)
        finally:
            x11.XCloseDisplay(display)

    def _find_coverart(self) -> tuple[int, int]:
        """Return a source-independent point inside moOde's cover-art link."""
        x11, _xtst = self._load_libraries()
        display = self._open_display(x11)
        try:
            screen = x11.XDefaultScreen(display)
            width = x11.XDisplayWidth(display, screen)
            height = x11.XDisplayHeight(display, screen)
            return self._coverart_target(width, height)
        finally:
            x11.XCloseDisplay(display)

    @staticmethod
    def _coverart_target(width: int, height: int) -> tuple[int, int]:
        if width <= 0 or height <= 0:
            raise RuntimeError("invalid X11 display dimensions")
        # In moOde's portrait Playback layout, the upper quarter of the
        # viewport is safely inside #coverart-url for square and tall covers.
        return width // 2, height // 4

    def _get_current_view(self) -> str:
        """Read the WebUI's persisted view instead of guessing local state."""
        request = urllib.request.Request(
            self.view_url,
            headers={"User-Agent": "siri-remote-moode/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise RuntimeError(f"could not read moOde current_view: {exc}") from exc
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"invalid moOde current_view response: {value!r}")
        return value

    def _ensure_playback(self) -> None:
        """Wait for X11 after boot and put the WebUI in a known Playback state."""
        deadline = time.monotonic() + self.ready_timeout
        while True:
            try:
                # This hits the playbar in Library and harmless whitespace in Playback.
                self._emit_click(self.playback_x, self.playback_y)
                LOG.info(
                    "Menu/Back X11 ready; synchronized to Playback at %d,%d",
                    self.playback_x,
                    self.playback_y,
                )
                return
            except (OSError, RuntimeError) as exc:
                if time.monotonic() >= deadline:
                    LOG.error("Menu/Back X11 not ready after %.1fs: %s", self.ready_timeout, exc)
                    return
                time.sleep(1)

    def _click(self) -> None:
        current_view = self._get_current_view()
        if current_view.startswith("playback"):
            x, y = self._find_coverart()
            destination = current_view.partition(",")[2] or "Library"
        else:
            x, y = self.playback_x, self.playback_y
            destination = "Playback"
        self._emit_click(x, y)
        LOG.info(
            "Menu/Back: current_view=%s -> %s via local display click at %d,%d",
            current_view,
            destination,
            x,
            y,
        )

    def _run(self) -> None:
        self._ensure_playback()
        while True:
            item = self.commands.get()
            if item is None:
                return
            try:
                self._click()
            except (OSError, RuntimeError) as exc:
                LOG.error("Menu/Back X11 click failed: %s", exc)


class ButtonMapper:
    def __init__(
        self,
        worker: MoodeWorker,
        shutdown_action=None,
        screen_clicker: X11ClickWorker | None = None,
    ) -> None:
        self.worker = worker
        self.screen_clicker = screen_clicker
        self.previous = 0
        self.lock = threading.Lock()
        self.last_touch_x: int | None = None
        self.last_touch_time = 0.0
        self.touch_click_handled = False
        self.touch_x_split = int(env("SIRI_TOUCH_X_SPLIT", str(GEN1_TOUCH_X_MID)), 0)
        self.touch_dead_zone = int(env("SIRI_TOUCH_DEAD_ZONE", "60"), 0)
        self.touch_max_age = float(env("SIRI_TOUCH_MAX_AGE_SECONDS", "1.5"))
        if not GEN1_TOUCH_X_MIN <= self.touch_x_split <= GEN1_TOUCH_X_MAX:
            raise ValueError(
                f"SIRI_TOUCH_X_SPLIT must be between {GEN1_TOUCH_X_MIN} "
                f"and {GEN1_TOUCH_X_MAX}"
            )
        if self.touch_dead_zone < 0:
            raise ValueError("SIRI_TOUCH_DEAD_ZONE cannot be negative")
        if self.touch_max_age <= 0:
            raise ValueError("SIRI_TOUCH_MAX_AGE_SECONDS must be greater than zero")
        self.previous_command = env("MOODE_PREVIOUS_CMD", "previous")
        self.next_command = env("MOODE_NEXT_CMD", "next")
        self.home_mask = int(env("SIRI_HOME_BUTTON_MASK", "0x01"), 0)
        if self.home_mask <= 0 or self.home_mask > 0xFF:
            raise ValueError("SIRI_HOME_BUTTON_MASK must be between 0x01 and 0xff")
        self.home_hold_seconds = float(env("SIRI_HOME_HOLD_SECONDS", "3"))
        if self.home_hold_seconds <= 0:
            raise ValueError("SIRI_HOME_HOLD_SECONDS must be greater than zero")
        self.shutdown_command = shlex.split(
            env("SIRI_HOME_COMMAND", "/usr/bin/systemctl poweroff")
        )
        if not self.shutdown_command:
            raise ValueError("SIRI_HOME_COMMAND must not be empty")
        self.shutdown_action = shutdown_action or self._run_shutdown
        self.home_timer: threading.Timer | None = None
        self.home_fired = False
        self.mapping = {
            BUTTON_AIRPLAY: ("AirPlay", env("MOODE_AIRPLAY_CMD", "")),
            BUTTON_VOLUME_UP: ("Volume +", env("MOODE_VOLUME_UP_CMD", "set_volume -up 5")),
            BUTTON_VOLUME_DOWN: ("Volume -", env("MOODE_VOLUME_DOWN_CMD", "set_volume -dn 5")),
            BUTTON_PLAY_PAUSE: ("Play/Pause", env("MOODE_PLAY_PAUSE_CMD", "toggle_play_pause")),
            BUTTON_SIRI: ("Siri", env("MOODE_SIRI_CMD", "")),
            BUTTON_MENU: ("Menu/Back", env("MOODE_MENU_CMD", "")),
        }

    def reset(self) -> None:
        with self.lock:
            self.previous = 0
            self.last_touch_x = None
            self.last_touch_time = 0.0
            self.touch_click_handled = False
            self.home_fired = False
            if self.home_timer is not None:
                self.home_timer.cancel()
                self.home_timer = None

    def _run_shutdown(self) -> None:
        LOG.warning("Home held for %.1f seconds; shutting down the Raspberry Pi", self.home_hold_seconds)
        try:
            subprocess.run(self.shutdown_command, check=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.error("Shutdown command failed: %s", exc)

    def _home_hold_elapsed(self) -> None:
        with self.lock:
            self.home_timer = None
            if not self.previous & self.home_mask or self.home_fired:
                return
            self.home_fired = True
        self.shutdown_action()

    def _update_home_locked(self, previous: int, buttons: int) -> None:
        was_pressed = bool(previous & self.home_mask)
        is_pressed = bool(buttons & self.home_mask)
        if is_pressed and not was_pressed:
            self.home_fired = False
            timer = threading.Timer(self.home_hold_seconds, self._home_hold_elapsed)
            timer.daemon = True
            self.home_timer = timer
            LOG.info(
                "Home pressed; hold for %.1f seconds to shut down",
                self.home_hold_seconds,
            )
            timer.start()
        elif was_pressed and not is_pressed:
            if self.home_timer is not None:
                self.home_timer.cancel()
                self.home_timer = None
            self.home_fired = False

    @staticmethod
    def decode_touch_x(payload: bytes) -> int | None:
        """Decode the first-finger X coordinate from a Gen-1 ATT report."""
        if len(payload) < 13 or payload[2] != TOUCH_EVENT_MARKER:
            return None
        x = payload[6] | ((payload[7] & 0x0F) << 8)
        # The remote sends a wrapping signed 12-bit value. Normalizing the
        # lower half produces the 2278..3914 range used by hid-siriremote.
        if x < 0x800:
            x += 0x1000
        return x

    def _touch_click_action_locked(
        self, buttons: int, now: float
    ) -> tuple[str, str] | None:
        if not buttons & BUTTON_TOUCHPAD:
            self.touch_click_handled = False
            return None
        if self.touch_click_handled:
            return None

        age = now - self.last_touch_time
        if self.last_touch_x is None or age > self.touch_max_age:
            LOG.debug("Touchpad click waiting for a recent touch position")
            return None

        self.touch_click_handled = True
        offset = self.last_touch_x - self.touch_x_split
        if abs(offset) <= self.touch_dead_zone:
            LOG.info(
                "Touchpad center click ignored: x=%d, split=%d, dead-zone=%d",
                self.last_touch_x,
                self.touch_x_split,
                self.touch_dead_zone,
            )
            return None
        if offset < 0:
            return "Touchpad left / Previous", self.previous_command
        return "Touchpad right / Next", self.next_command

    def notification(self, handle: int, payload: bytes) -> None:
        if handle != HANDLE_INPUT_VALUE:
            LOG.debug("Notification 0x%04x: %s", handle, payload.hex(" "))
            return
        if len(payload) < 2:
            LOG.warning("Short input notification: %s", payload.hex(" "))
            return
        buttons = payload[1]
        now = time.monotonic()
        touch_x = self.decode_touch_x(payload)
        with self.lock:
            if touch_x is not None:
                self.last_touch_x = touch_x
                self.last_touch_time = now
                LOG.debug(
                    "Touch report: x=%d split=%d buttons=0x%02x raw=%s",
                    touch_x,
                    self.touch_x_split,
                    buttons,
                    payload.hex(" "),
                )
            elif buttons == self.previous:
                return

            previous = self.previous
            newly_pressed = buttons & ~previous
            if buttons != previous:
                LOG.debug("Input notification: %s", payload.hex(" "))
                self.previous = buttons
                self._update_home_locked(previous, buttons)
            touch_action = self._touch_click_action_locked(buttons, now)

        if touch_action is not None:
            self.worker.submit(*touch_action)
        if newly_pressed & BUTTON_MENU and self.screen_clicker is not None:
            self.screen_clicker.submit()
        for mask, (name, command) in self.mapping.items():
            # The configured Home button is reserved for the long-press action
            # and must not also execute its normal short-press mapping.
            if (
                mask != self.home_mask
                and not (mask == BUTTON_MENU and self.screen_clicker is not None)
                and newly_pressed & mask
            ):
                self.worker.submit(name, command)


def reclaim_att_channel(mac: str) -> None:
    """Ask BlueZ to release a connection it acquired before the raw client."""
    try:
        result = subprocess.run(
            ["/usr/bin/bluetoothctl", "disconnect", mac],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        interesting = [
            line for line in lines
            if any(word in line.lower() for word in ("disconnect", "failed", "not available"))
        ]
        output = (interesting or lines or [f"exit {result.returncode}"])[-1]
        LOG.warning("BlueZ disconnect after busy ATT channel: %s", output[:500])
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.error("Could not ask BlueZ to release the ATT channel: %s", exc)


def run(args: argparse.Namespace) -> int:
    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    worker = MoodeWorker(args.moode_url, args.http_timeout, args.dry_run)
    screen_clicker = X11ClickWorker()
    mapper = ButtonMapper(
        worker,
        screen_clicker=screen_clicker if screen_clicker.enabled else None,
    )
    worker.start()
    screen_clicker.start()

    delay = args.reconnect_min
    try:
        while not stop_event.is_set():
            client = RawAttClient(args.mac, args.address_type, args.security)
            att_busy = False
            client.notification_handler = mapper.notification
            mapper.reset()
            try:
                LOG.info("Connecting to Siri Remote %s", args.mac)
                client.connect()
                if args.mtu > 23:
                    LOG.info("Connected; negotiating ATT MTU %d", args.mtu)
                    negotiated_mtu = client.exchange_mtu(args.mtu)
                    LOG.info("ATT MTU negotiated: %d", negotiated_mtu)
                else:
                    LOG.info("Connected; using default ATT MTU 23")
                client.enable_input()
                LOG.info("Ready; listening for notifications on 0x0023")
                delay = args.reconnect_min
                client.receive_forever(stop_event, args.keepalive)
            except (ConnectionError, OSError, PermissionError) as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, OSError) and exc.errno in (errno.EBUSY, errno.EADDRINUSE):
                    att_busy = True
                    LOG.error(
                        "Bluetooth ATT channel busy (%s). Stop gatttool/other GATT clients; retrying.",
                        exc,
                    )
                else:
                    LOG.warning("Bluetooth connection lost/failed: %s", exc)
            finally:
                client.close()
                mapper.reset()
            if att_busy and args.reclaim_busy:
                reclaim_att_channel(args.mac)
                delay = args.reconnect_min
            if not stop_event.wait(delay):
                LOG.info("Reconnecting (next backoff %.1fs)", min(delay * 2, args.reconnect_max))
                delay = min(delay * 2, args.reconnect_max)
    finally:
        screen_clicker.stop()
        worker.stop()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", default=env("SIRI_REMOTE_MAC", "70:48:0F:F2:65:99"))
    parser.add_argument(
        "--address-type",
        choices=("public", "random"),
        default=env("SIRI_ADDR_TYPE", "public").lower(),
    )
    parser.add_argument(
        "--security",
        choices=("low", "medium", "high", "fips"),
        default=env("SIRI_SECURITY", "medium").lower(),
    )
    parser.add_argument("--moode-url", default=env("MOODE_URL", "http://localhost/command/"))
    parser.add_argument("--http-timeout", type=float, default=float(env("MOODE_HTTP_TIMEOUT", "4")))
    parser.add_argument("--mtu", type=int, default=int(env("SIRI_ATT_MTU", "23")))
    parser.add_argument(
        "--keepalive",
        type=float,
        default=float(env("SIRI_KEEPALIVE_SECONDS", "0")),
    )
    parser.add_argument(
        "--reclaim-busy",
        action=argparse.BooleanOptionalAction,
        default=env("SIRI_RECLAIM_BUSY", "yes").lower() in ("1", "yes", "true", "on"),
    )
    parser.add_argument("--reconnect-min", type=float, default=float(env("RECONNECT_MIN", "0.2")))
    parser.add_argument("--reconnect-max", type=float, default=float(env("RECONNECT_MAX", "1")))
    parser.add_argument("--dry-run", action="store_true", help="log button commands without calling moOde")
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=env("SIRI_DEBUG", "no").lower() in ("1", "yes", "true", "on"),
    )
    args = parser.parse_args()
    if args.reconnect_min <= 0 or args.reconnect_max < args.reconnect_min:
        parser.error("reconnect delays must satisfy 0 < min <= max")
    if not 23 <= args.mtu <= 517:
        parser.error("MTU must be between 23 and 517")
    if args.keepalive < 0:
        parser.error("keepalive interval cannot be negative")
    parse_mac(args.mac)
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if sys.platform != "linux":
        LOG.error("raw Bluetooth ATT mode requires Linux")
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
