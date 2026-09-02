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

RENDERER_NAMES = {
    "btactive": "Bluetooth",
    "aplactive": "AirPlay",
    "spotactive": "Spotify",
    "deezactive": "Deezer",
    "slactive": "Squeezelite",
    "paactive": "Plexamp",
    "rbactive": "RoonBridge",
    "inpactive": "Audio Input",
    "rxactive": "Multiroom Receiver",
}


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong),
        ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong),
        ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]


class CairoTextExtents(ctypes.Structure):
    _fields_ = [
        ("x_bearing", ctypes.c_double),
        ("y_bearing", ctypes.c_double),
        ("width", ctypes.c_double),
        ("height", ctypes.c_double),
        ("x_advance", ctypes.c_double),
        ("y_advance", ctypes.c_double),
    ]


class XImage(ctypes.Structure):
    """Initial, stable portion of Xlib's XImage structure."""

    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]

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
        self.battery_handler = None
        self.battery_level: int | None = None

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

    def read_battery(self, source: str = "Battery") -> int | None:
        value = self.read_request(HANDLE_BATTERY_VALUE)
        if not value:
            return None
        level = value[0]
        self.battery_level = level
        LOG.debug("%s; battery=%d%%", source, level)
        if self.battery_handler is not None:
            self.battery_handler(level)
        return level

    def battery_interval(
        self,
        normal_seconds: float,
        low_seconds: float,
        critical_seconds: float,
        low_threshold: int,
        critical_threshold: int,
    ) -> float:
        if (
            self.battery_level is not None
            and self.battery_level < critical_threshold
            and critical_seconds > 0
        ):
            return critical_seconds
        if (
            self.battery_level is not None
            and self.battery_level < low_threshold
            and low_seconds > 0
        ):
            return low_seconds
        return normal_seconds

    def receive_forever(
        self,
        stop_event: threading.Event,
        keepalive_seconds: float,
        battery_check_seconds: float = 0,
        battery_low_check_seconds: float = 0,
        battery_critical_check_seconds: float = 0,
        battery_low_threshold: int = 10,
        battery_critical_threshold: int = 5,
    ) -> None:
        next_keepalive = (
            time.monotonic() + keepalive_seconds if keepalive_seconds > 0 else None
        )
        initial_battery_interval = self.battery_interval(
            battery_check_seconds,
            battery_low_check_seconds,
            battery_critical_check_seconds,
            battery_low_threshold,
            battery_critical_threshold,
        )
        next_battery_check = (
            time.monotonic() + initial_battery_interval
            if battery_check_seconds > 0 else None
        )
        while not stop_event.is_set():
            deadlines = [
                deadline for deadline in (next_keepalive, next_battery_check)
                if deadline is not None
            ]
            wait_seconds = max(
                0.05,
                min(1.0, min(deadlines) - time.monotonic()),
            ) if deadlines else 1.0
            try:
                packet = self._receive(wait_seconds)
            except socket.timeout:
                packet = None
            if packet is not None and not self._dispatch_async(packet):
                LOG.debug("Ignoring ATT packet: %s", packet.hex(" "))
            if next_keepalive is not None and time.monotonic() >= next_keepalive:
                self.read_battery("Keepalive")
                next_keepalive = time.monotonic() + keepalive_seconds
            if (
                next_battery_check is not None
                and time.monotonic() >= next_battery_check
            ):
                self.read_battery("Periodic check")
                interval = self.battery_interval(
                    battery_check_seconds,
                    battery_low_check_seconds,
                    battery_critical_check_seconds,
                    battery_low_threshold,
                    battery_critical_threshold,
                )
                next_battery_check = time.monotonic() + interval


class RendererGuard:
    """Read moOde's renderer-active flags before ordinary remote actions."""

    ACTIVE_FLAGS = tuple(RENDERER_NAMES)

    def __init__(self, command_url: str, timeout: float, blocked_handler=None) -> None:
        self.blocked_handler = blocked_handler
        self.enabled = env("SIRI_IGNORE_DURING_RENDERER", "yes").lower() in (
            "1", "yes", "true", "on",
        )
        self.cache_seconds = float(env("SIRI_RENDERER_CACHE_SECONDS", "0.25"))
        if self.cache_seconds < 0:
            raise ValueError("SIRI_RENDERER_CACHE_SECONDS cannot be negative")
        self.timeout = min(2.0, timeout)
        self.url = urllib.parse.urljoin(
            command_url,
            "cfg-table.php?cmd=get_cfg_system",
        )
        self.lock = threading.Lock()
        self.last_check = 0.0
        self.last_result: tuple[str, ...] | None = None

    @classmethod
    def active_renderers(cls, data: object) -> tuple[str, ...]:
        if not isinstance(data, dict):
            raise ValueError("invalid get_cfg_system response")
        return tuple(flag for flag in cls.ACTIVE_FLAGS if str(data.get(flag, "0")) == "1")

    def check(self) -> tuple[str, ...] | None:
        """Return active flags, empty when inactive, or None on status failure."""
        if not self.enabled:
            return ()
        with self.lock:
            now = time.monotonic()
            if now - self.last_check <= self.cache_seconds and self.last_result is not None:
                return self.last_result
            request = urllib.request.Request(
                self.url,
                headers={"User-Agent": "siri-remote-moode/1", "Connection": "close"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read(262144).decode("utf-8"))
                result = self.active_renderers(data)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                LOG.error("Cannot determine renderer state; ignoring remote action: %s", exc)
                self.last_check = now
                self.last_result = None
                return None
            self.last_check = now
            self.last_result = result
            return result

    def allows(self, action_name: str) -> bool:
        active = self.check()
        if active is None:
            if self.blocked_handler is not None:
                self.blocked_handler("Renderer")
            return False
        if active:
            LOG.info(
                "Ignoring %s while external renderer is active: %s",
                action_name,
                ", ".join(active),
            )
            if self.blocked_handler is not None:
                self.blocked_handler(RENDERER_NAMES.get(active[0], "Renderer"))
            return False
        return True


class MoodeWorker:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        dry_run: bool = False,
        result_handler=None,
        renderer_guard: RendererGuard | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.dry_run = dry_run
        self.result_handler = result_handler
        self.renderer_guard = renderer_guard
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
            if (
                self.renderer_guard is not None
                and not self.renderer_guard.allows(button_name)
            ):
                continue
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
                    body = response.read(4096)
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"HTTP {response.status}")
                LOG.info("%s -> %s", button_name, command)
                if self.result_handler is not None:
                    self.result_handler(button_name, command, body)
            except (OSError, urllib.error.URLError, RuntimeError) as exc:
                # Do not retry toggle commands: if the response was lost after
                # execution, a retry could immediately undo the first command.
                LOG.error("moOde request failed for %s: %s", button_name, exc)


class X11ClickWorker:
    """Generate a click on moOde's local X11 display without extra packages."""

    def __init__(self, renderer_guard: RendererGuard | None = None) -> None:
        self.renderer_guard = renderer_guard
        self.enabled = env("SIRI_MENU_SCREEN_CLICK", "no").lower() in (
            "1", "yes", "true", "on"
        )
        self.display_name = env("SIRI_X_DISPLAY", ":0")
        self.xauthority = env("SIRI_XAUTHORITY", "/home/pi/.Xauthority")
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
            if (
                self.renderer_guard is not None
                and not self.renderer_guard.allows("Menu/Back")
            ):
                continue
            try:
                self._click()
            except (OSError, RuntimeError) as exc:
                LOG.error("Menu/Back X11 click failed: %s", exc)


class X11Overlay:
    """Temporary fake-alpha X11 OSD that does not require a compositor."""

    CW_OVERRIDE_REDIRECT = 1 << 9
    ZPIXMAP = 2
    MOODE_GREY = 0x303030
    MOODE_TEXT = (240 / 255.0, 240 / 255.0, 240 / 255.0)
    OVERLAY_OPACITY = 0.60
    OVERLAY_BORDER_OPACITY = 0.38
    OVERLAY_BORDER_WIDTH = 0.004
    REPAINT_SETTLE_SECONDS = 0.05
    SHUTDOWN_LABEL_CENTER_Y = 0.30
    SHUTDOWN_RING_CENTER_Y = 0.64
    SYMBOLS = {"PLAY", "PAUSE", "NEXT", "PREVIOUS", "BATTERY"}
    FONT_5X7 = {
        " ": ("00000",) * 7,
        "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
        "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
        "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
        "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
        "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
        "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
        "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
        "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
        "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
        "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
        "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
        "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
        "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
        "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
        "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
        "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
        "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
        "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
        "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
        "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
        "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
        "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
        "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
        "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
        "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
        "%": ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
        "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    }
    def __init__(self) -> None:
        self.display_name = env("SIRI_X_DISPLAY", ":0")
        self.xauthority = env("SIRI_XAUTHORITY", "/home/pi/.Xauthority")

    @staticmethod
    def _load_x11() -> ctypes.CDLL:
        x11 = ctypes.CDLL("libX11.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayHeight.restype = ctypes.c_int
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultVisual.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDefaultVisual.restype = ctypes.c_void_p
        x11.XGetImage.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
        ]
        x11.XGetImage.restype = ctypes.c_void_p
        x11.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        x11.XGetPixel.restype = ctypes.c_ulong
        x11.XPutPixel.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
        ]
        x11.XCreateSimpleWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_ulong, ctypes.c_ulong,
        ]
        x11.XCreateSimpleWindow.restype = ctypes.c_ulong
        x11.XChangeWindowAttributes.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(XSetWindowAttributes),
        ]
        x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XCreateGC.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ]
        x11.XCreateGC.restype = ctypes.c_void_p
        x11.XPutImage.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint,
        ]
        x11.XSetForeground.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
        ]
        x11.XFillRectangle.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
        ]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XFreeGC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x11.XDestroyImage.argtypes = [ctypes.c_void_p]
        x11.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        return x11

    @staticmethod
    def _load_cairo() -> ctypes.CDLL:
        cairo = ctypes.CDLL("libcairo.so.2")
        cairo.cairo_xlib_surface_create.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
        ]
        cairo.cairo_xlib_surface_create.restype = ctypes.c_void_p
        cairo.cairo_surface_destroy.argtypes = [ctypes.c_void_p]
        cairo.cairo_surface_flush.argtypes = [ctypes.c_void_p]
        cairo.cairo_image_surface_create_for_data.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
        ]
        cairo.cairo_image_surface_create_for_data.restype = ctypes.c_void_p
        cairo.cairo_create.argtypes = [ctypes.c_void_p]
        cairo.cairo_create.restype = ctypes.c_void_p
        cairo.cairo_destroy.argtypes = [ctypes.c_void_p]
        cairo.cairo_select_font_face.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
        ]
        cairo.cairo_set_font_size.argtypes = [ctypes.c_void_p, ctypes.c_double]
        cairo.cairo_text_extents.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(CairoTextExtents),
        ]
        cairo.cairo_set_source_rgb.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ]
        cairo.cairo_set_source_rgba.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.c_double,
        ]
        cairo.cairo_move_to.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_double,
        ]
        cairo.cairo_show_text.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cairo.cairo_new_path.argtypes = [ctypes.c_void_p]
        cairo.cairo_line_to.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_double,
        ]
        cairo.cairo_close_path.argtypes = [ctypes.c_void_p]
        cairo.cairo_fill.argtypes = [ctypes.c_void_p]
        cairo.cairo_arc.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ]
        cairo.cairo_set_line_width.argtypes = [ctypes.c_void_p, ctypes.c_double]
        cairo.cairo_stroke.argtypes = [ctypes.c_void_p]
        return cairo

    @staticmethod
    def _make_input_transparent(display: int, window: int) -> None:
        """Give the overlay an empty X11 input shape so clicks pass through."""
        try:
            xext = ctypes.CDLL("libXext.so.6")
            xext.XShapeCombineRectangles.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]
            # ShapeInput=2, ShapeSet=0, Unsorted=0, zero rectangles.
            xext.XShapeCombineRectangles(
                display, window, 2, 0, 0, None, 0, 0, 0,
            )
        except OSError as exc:
            LOG.warning("Cannot make X11 overlay click-through: %s", exc)

    @staticmethod
    def _darken_pixel(pixel: int, retained: float = 0.34) -> int:
        blue = int((pixel & 0xFF) * retained)
        green = int(((pixel >> 8) & 0xFF) * retained)
        red = int(((pixel >> 16) & 0xFF) * retained)
        return (red << 16) | (green << 8) | blue

    @staticmethod
    def _blend_pixel(pixel: int, tint: int, opacity: float) -> int:
        retained = 1.0 - opacity
        blue = int((pixel & 0xFF) * retained + (tint & 0xFF) * opacity)
        green = int(
            ((pixel >> 8) & 0xFF) * retained
            + ((tint >> 8) & 0xFF) * opacity
        )
        red = int(
            ((pixel >> 16) & 0xFF) * retained
            + ((tint >> 16) & 0xFF) * opacity
        )
        return (red << 16) | (green << 8) | blue

    @classmethod
    def _text_layout(cls, text: str, size: int) -> tuple[str, int, int, int]:
        clean = "".join(ch if ch in cls.FONT_5X7 else "?" for ch in text.upper())[:12]
        scale = max(5, min(24, (size - 48) // max(1, len(clean) * 6 - 1)))
        width = max(0, (len(clean) * 6 - 1) * scale)
        height = 7 * scale
        return clean, scale, width, height

    @staticmethod
    def _overlay_geometry(width: int, height: int) -> tuple[int, int, int]:
        if width <= 0 or height <= 0:
            raise RuntimeError("invalid X11 display dimensions")
        # moOde's portrait cover is centered horizontally near the top and is
        # about 80% of the 720-pixel viewport width. Keep the circle just
        # inside that artwork and align both centers.
        size = max(280, min(536, int(width * 0.745), height - 24))
        left = (width - size) // 2
        cover_center_y = int(height * 0.295)
        top = max(12, min(height - size - 12, cover_center_y - size // 2))
        return size, left, top

    @classmethod
    def _draw_text(
        cls, x11: ctypes.CDLL, display: int, window: int, gc: int,
        text: str, size: int,
    ) -> None:
        clean, scale, text_width, text_height = cls._text_layout(text, size)
        start_x = (size - text_width) // 2
        start_y = (size - text_height) // 2
        for char_index, char in enumerate(clean):
            glyph = cls.FONT_5X7[char]
            char_x = start_x + char_index * 6 * scale
            for row, pattern in enumerate(glyph):
                for column, bit in enumerate(pattern):
                    if bit == "1":
                        x11.XFillRectangle(
                            display, window, gc,
                            char_x + column * scale,
                            start_y + row * scale,
                            scale, scale,
                        )

    @classmethod
    def _draw_lato_text(
        cls, x11: ctypes.CDLL, display: int, screen: int,
        window: int, text: str, size: int,
    ) -> None:
        cairo = cls._load_cairo()
        visual = x11.XDefaultVisual(display, screen)
        surface = cairo.cairo_xlib_surface_create(
            display, window, visual, size, size,
        )
        if not surface:
            raise RuntimeError("could not create Cairo Xlib surface")
        context = cairo.cairo_create(surface)
        if not context:
            cairo.cairo_surface_destroy(surface)
            raise RuntimeError("could not create Cairo drawing context")

        def centered(
            label: str,
            font_size: float,
            center_y: float,
            bold: bool = False,
            center_x: float | None = None,
        ) -> None:
            encoded = label.encode("ascii", "replace")
            cairo.cairo_select_font_face(context, b"Lato", 0, 1 if bold else 0)
            cairo.cairo_set_font_size(context, font_size)
            extents = CairoTextExtents()
            cairo.cairo_text_extents(context, encoded, ctypes.byref(extents))
            horizontal_center = size / 2.0 if center_x is None else center_x
            text_x = horizontal_center - extents.width / 2.0 - extents.x_bearing
            text_y = center_y - extents.height / 2.0 - extents.y_bearing
            cairo.cairo_move_to(context, text_x, text_y)
            cairo.cairo_show_text(context, encoded)

        def text_width(label: str, font_size: float, bold: bool = False) -> float:
            encoded = label.encode("ascii", "replace")
            cairo.cairo_select_font_face(context, b"Lato", 0, 1 if bold else 0)
            cairo.cairo_set_font_size(context, font_size)
            extents = CairoTextExtents()
            cairo.cairo_text_extents(context, encoded, ctypes.byref(extents))
            return extents.width

        def polygon(points: list[tuple[float, float]]) -> None:
            cairo.cairo_new_path(context)
            cairo.cairo_move_to(context, *points[0])
            for point in points[1:]:
                cairo.cairo_line_to(context, *point)
            cairo.cairo_close_path(context)
            cairo.cairo_fill(context)

        def power_ring(center_y: float, content: str | None = None) -> None:
            cairo.cairo_set_line_width(context, size * 0.026)
            cairo.cairo_new_path(context)
            cairo.cairo_arc(
                context, size * 0.50, size * center_y, size * 0.19,
                5.45, 10.55,
            )
            cairo.cairo_stroke(context)
            cairo.cairo_new_path(context)
            cairo.cairo_move_to(context, size * 0.50, size * (center_y - 0.24))
            cairo.cairo_line_to(context, size * 0.50, size * (center_y - 0.11))
            cairo.cairo_stroke(context)
            if content is not None:
                content_size = size * (0.285 if len(content) == 1 else 0.14)
                centered(
                    content, content_size,
                    size * (center_y + 0.035), bold=True,
                )

        def shutdown_label(label: str) -> None:
            reference_size = size * 0.285
            reference_width = text_width("60%", reference_size, bold=True)
            label_width = text_width(label, reference_size, bold=False)
            label_size = reference_size * reference_width / label_width
            centered(
                label, label_size * 0.92,
                size * cls.SHUTDOWN_LABEL_CENTER_Y, bold=False,
            )

        try:
            cairo.cairo_set_source_rgba(
                context, *cls.MOODE_TEXT, cls.OVERLAY_BORDER_OPACITY,
            )
            cairo.cairo_set_line_width(context, size * cls.OVERLAY_BORDER_WIDTH)
            cairo.cairo_new_path(context)
            cairo.cairo_arc(
                context, size * 0.50, size * 0.50, size * 0.478,
                0.0, 6.283185307179586,
            )
            cairo.cairo_stroke(context)
            cairo.cairo_set_source_rgb(context, *cls.MOODE_TEXT)
            if text == "PLAY":
                polygon([
                    (size * 0.39, size * 0.31),
                    (size * 0.70, size * 0.50),
                    (size * 0.39, size * 0.69),
                ])
            elif text == "PAUSE":
                polygon([
                    (size * 0.36, size * 0.31),
                    (size * 0.45, size * 0.31),
                    (size * 0.45, size * 0.69),
                    (size * 0.36, size * 0.69),
                ])
                polygon([
                    (size * 0.55, size * 0.31),
                    (size * 0.64, size * 0.31),
                    (size * 0.64, size * 0.69),
                    (size * 0.55, size * 0.69),
                ])
            elif text == "NEXT":
                polygon([
                    (size * 0.32, size * 0.31),
                    (size * 0.63, size * 0.50),
                    (size * 0.32, size * 0.69),
                ])
                polygon([
                    (size * 0.64, size * 0.31),
                    (size * 0.70, size * 0.31),
                    (size * 0.70, size * 0.69),
                    (size * 0.64, size * 0.69),
                ])
            elif text == "PREVIOUS":
                polygon([
                    (size * 0.68, size * 0.31),
                    (size * 0.37, size * 0.50),
                    (size * 0.68, size * 0.69),
                ])
                polygon([
                    (size * 0.30, size * 0.31),
                    (size * 0.36, size * 0.31),
                    (size * 0.36, size * 0.69),
                    (size * 0.30, size * 0.69),
                ])
            elif text.startswith("BATTERY:"):
                percentage = text.partition(":")[2]
                cairo.cairo_set_line_width(context, size * 0.026)
                cairo.cairo_new_path(context)
                cairo.cairo_move_to(context, size * 0.27, size * 0.37)
                cairo.cairo_line_to(context, size * 0.68, size * 0.37)
                cairo.cairo_line_to(context, size * 0.68, size * 0.63)
                cairo.cairo_line_to(context, size * 0.27, size * 0.63)
                cairo.cairo_close_path(context)
                cairo.cairo_stroke(context)
                polygon([
                    (size * 0.69, size * 0.44),
                    (size * 0.75, size * 0.44),
                    (size * 0.75, size * 0.56),
                    (size * 0.69, size * 0.56),
                ])
                centered(
                    percentage,
                    size * 0.14,
                    size * 0.50,
                    bold=True,
                    center_x=size * 0.475,
                )
            elif text.startswith("VOLUME:"):
                percentage = text.partition(":")[2]
                percentage_size = size * 0.285
                target_width = text_width(percentage, percentage_size, bold=True)
                label_width = text_width("Volume:", percentage_size, bold=False)
                label_size = percentage_size * target_width / label_width
                centered("Volume:", label_size * 0.92, size * 0.37, bold=False)
                centered(percentage, percentage_size, size * 0.57, bold=True)
            elif text.startswith("DISABLED:"):
                renderer_key = text.partition(":")[2]
                renderer = {
                    name.upper(): name for name in RENDERER_NAMES.values()
                }.get(renderer_key, "Renderer")
                reference_size = size * 0.285
                reference_width = text_width("60%", reference_size, bold=True)
                volume_width = text_width("Volume:", reference_size, bold=True)
                label_size = reference_size * reference_width / volume_width
                if renderer == "Multiroom Receiver":
                    renderer_size = size * 0.175
                    centered("Disabled:", label_size, size * 0.29, bold=True)
                    centered("Multiroom", renderer_size, size * 0.51, bold=True)
                    centered("Receiver", renderer_size, size * 0.68, bold=True)
                else:
                    renderer_size = size * 0.22
                    renderer_width = text_width(renderer, renderer_size, bold=True)
                    if renderer_width > size * 0.76:
                        renderer_size *= size * 0.76 / renderer_width
                    centered("Disabled:", label_size, size * 0.37, bold=True)
                    centered(renderer, renderer_size, size * 0.57, bold=True)
            elif text.startswith("SHUTDOWN:"):
                countdown = text.partition(":")[2]
                shutdown_label("Shutdown:")
                power_ring(cls.SHUTDOWN_RING_CENTER_Y, countdown)
            elif text == "SHUTTING DOWN":
                shutdown_label("Shutdown")
                power_ring(cls.SHUTDOWN_RING_CENTER_Y, "0")
            else:
                font_size = size * 0.27
                max_width = size * 0.78
                width = text_width(text, font_size)
                if width > max_width:
                    font_size *= max_width / width
                centered(text, font_size, size * 0.50)
            cairo.cairo_surface_flush(surface)
        finally:
            cairo.cairo_destroy(context)
            cairo.cairo_surface_destroy(surface)

    @classmethod
    def _tint_image(cls, image: int, size: int) -> None:
        """Blend the fake-alpha circle in native Cairo instead of a slow pixel loop."""
        ximage = ctypes.cast(image, ctypes.POINTER(XImage)).contents
        native_rgb24 = (
            ximage.data
            and ximage.bits_per_pixel == 32
            and ximage.red_mask == 0xFF0000
            and ximage.green_mask == 0x00FF00
            and ximage.blue_mask == 0x0000FF
        )
        if native_rgb24:
            cairo = cls._load_cairo()
            surface = cairo.cairo_image_surface_create_for_data(
                ximage.data, 1, size, size, ximage.bytes_per_line,
            )
            if surface:
                context = cairo.cairo_create(surface)
                if context:
                    try:
                        channel = ((cls.MOODE_GREY >> 16) & 0xFF) / 255.0
                        cairo.cairo_set_source_rgba(
                            context, channel, channel, channel,
                            cls.OVERLAY_OPACITY,
                        )
                        cairo.cairo_new_path(context)
                        cairo.cairo_arc(
                            context, (size - 1) / 2.0, (size - 1) / 2.0,
                            size * 0.485, 0.0, 6.283185307179586,
                        )
                        cairo.cairo_fill(context)
                        cairo.cairo_surface_flush(surface)
                        return
                    finally:
                        cairo.cairo_destroy(context)
                        cairo.cairo_surface_destroy(surface)
                cairo.cairo_surface_destroy(surface)

        # Portable fallback for unusual X visuals. This is slower, but keeps
        # the daemon functional instead of assuming a 32-bit RGB framebuffer.
        x11 = cls._load_x11()
        center = (size - 1) / 2.0
        radius_sq = (size * 0.485) ** 2
        for y in range(size):
            dy_sq = (y - center) ** 2
            for x in range(size):
                if (x - center) ** 2 + dy_sq <= radius_sq:
                    pixel = x11.XGetPixel(image, x, y)
                    x11.XPutPixel(
                        image, x, y,
                        cls._blend_pixel(
                            pixel, cls.MOODE_GREY, cls.OVERLAY_OPACITY,
                        ),
                    )

    def show_sequence(
        self,
        frames: list[tuple[str, float]],
        cancel_event: threading.Event | None = None,
    ) -> None:
        clean_frames = [
            (text.strip().upper()[:32], max(0.1, duration))
            for text, duration in frames
            if text.strip()
        ]
        if not clean_frames:
            return
        os.environ["DISPLAY"] = self.display_name
        os.environ["XAUTHORITY"] = self.xauthority
        x11 = self._load_x11()
        display = x11.XOpenDisplay(self.display_name.encode())
        if not display:
            raise RuntimeError(
                f"cannot open X display {self.display_name} using {self.xauthority}"
            )

        image = None
        window = 0
        gc = None
        try:
            screen = x11.XDefaultScreen(display)
            width = x11.XDisplayWidth(display, screen)
            height = x11.XDisplayHeight(display, screen)
            size, left, top = self._overlay_geometry(width, height)
            root = x11.XDefaultRootWindow(display)
            image = x11.XGetImage(
                display, root, left, top, size, size,
                ctypes.c_ulong(-1).value, self.ZPIXMAP,
            )
            if not image:
                raise RuntimeError("could not capture pixels below X11 overlay")
            self._tint_image(image, size)

            window = x11.XCreateSimpleWindow(
                display, root, left, top, size, size, 0, 0, 0,
            )
            attributes = XSetWindowAttributes()
            attributes.override_redirect = 1
            x11.XChangeWindowAttributes(
                display, window, self.CW_OVERRIDE_REDIRECT, ctypes.byref(attributes)
            )
            self._make_input_transparent(display, window)
            gc = x11.XCreateGC(display, window, 0, None)
            x11.XMapRaised(display, window)
            mapped = True
            for clean_text, duration in clean_frames:
                if clean_text == "HIDE":
                    if mapped:
                        x11.XUnmapWindow(display, window)
                        mapped = False
                    x11.XSync(display, False)
                    if cancel_event is None:
                        time.sleep(duration)
                    elif cancel_event.wait(duration):
                        break
                    continue
                if not mapped:
                    x11.XMapRaised(display, window)
                    mapped = True
                x11.XPutImage(
                    display, window, gc, image, 0, 0, 0, 0, size, size,
                )
                try:
                    self._draw_lato_text(
                        x11, display, screen, window, clean_text, size,
                    )
                except (OSError, RuntimeError) as exc:
                    LOG.warning(
                        "Lato/Cairo overlay text unavailable; using bitmap: %s", exc,
                    )
                    x11.XSetForeground(display, gc, 0xF0F0F0)
                    self._draw_text(x11, display, window, gc, clean_text, size)
                x11.XSync(display, False)
                if cancel_event is None:
                    time.sleep(duration)
                elif cancel_event.wait(duration):
                    break
        finally:
            if window:
                x11.XUnmapWindow(display, window)
                x11.XSync(display, False)
                time.sleep(self.REPAINT_SETTLE_SECONDS)
                x11.XDestroyWindow(display, window)
            if gc:
                x11.XFreeGC(display, gc)
            if image:
                x11.XDestroyImage(image)
            x11.XFlush(display)
            x11.XCloseDisplay(display)

    def show(
        self,
        text: str,
        duration: float = 2.5,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.show_sequence([(text, duration)], cancel_event)


class OverlayWorker:
    """Latest-wins, interruptible X11 overlay worker."""

    def __init__(self, overlay: X11Overlay | None = None) -> None:
        self.enabled = env("SIRI_OVERLAY", "yes").lower() in (
            "1", "yes", "true", "on",
        )
        self.duration = float(env("SIRI_OVERLAY_SECONDS", "1"))
        if self.duration <= 0:
            raise ValueError("SIRI_OVERLAY_SECONDS must be greater than zero")
        self.overlay = overlay or X11Overlay()
        self.condition = threading.Condition()
        self.latest: tuple[str, list[tuple[str, float]]] | None = None
        self.active_kind: str | None = None
        self.interrupt = threading.Event()
        self.stopping = False
        self.thread = threading.Thread(
            target=self._run, name="moode-x11-overlay", daemon=True,
        )

    def start(self) -> None:
        if self.enabled:
            self.thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        with self.condition:
            self.stopping = True
            self.latest = None
            self.interrupt.set()
            self.condition.notify()
        self.thread.join(timeout=2)

    def submit(
        self, text: str, duration: float | None = None, kind: str = "command",
    ) -> None:
        self.submit_sequence(
            [(text, self.duration if duration is None else duration)], kind,
        )

    def submit_sequence(
        self, frames: list[tuple[str, float]], kind: str = "command",
    ) -> None:
        if not self.enabled or not frames:
            return
        with self.condition:
            # There is deliberately no FIFO. The newest remote event replaces
            # both queued and visible content, which keeps rapid volume clicks
            # responsive instead of replaying stale percentages.
            self.latest = (kind, frames)
            self.interrupt.set()
            self.condition.notify()

    def cancel(self, kind: str | None = None) -> None:
        if not self.enabled:
            return
        with self.condition:
            if self.latest is not None and (
                kind is None or self.latest[0] == kind
            ):
                self.latest = None
            if kind is None or self.active_kind == kind:
                self.interrupt.set()

    def start_shutdown(self, seconds: float) -> None:
        remaining = seconds
        number = max(1, int(seconds + 0.999999))
        frames: list[tuple[str, float]] = []
        while remaining > 0:
            frame_seconds = min(1.0, remaining)
            frames.append((f"SHUTDOWN:{number}", frame_seconds))
            remaining -= frame_seconds
            number -= 1
        self.submit_sequence(frames, kind="shutdown")

    def moode_result(self, button_name: str, command: str, body: bytes) -> None:
        """Translate a successful moOde response into its resulting OSD."""
        # Previous/Next responses are not consistently JSON. Their overlay is
        # tied to successful HTTP completion so a renderer-blocked action does
        # not misleadingly show a navigation symbol.
        if button_name == "Touchpad left / Previous":
            self.submit("PREVIOUS")
            return
        if button_name == "Touchpad right / Next":
            self.submit("NEXT")
            return
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            LOG.warning("Cannot parse moOde response for overlay: %s", button_name)
            return
        command_name = command.partition(" ")[0]
        if command_name == "set_volume" and isinstance(result, dict):
            volume = result.get("volume")
            if str(volume).isdigit():
                self.submit(f"VOLUME:{volume}%")
        elif command_name == "toggle_play_pause" and isinstance(result, dict):
            state = str(result.get("state", "")).lower()
            if state == "play":
                self.submit("PLAY")
            elif state in ("pause", "stop"):
                self.submit("PAUSE")

    def _run(self) -> None:
        while True:
            with self.condition:
                while self.latest is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                kind, frames = self.latest
                self.latest = None
                self.active_kind = kind
                self.interrupt.clear()
            try:
                self.overlay.show_sequence(frames, self.interrupt)
            except (OSError, RuntimeError) as exc:
                LOG.error("X11 overlay failed: %s", exc)
            finally:
                with self.condition:
                    if self.active_kind == kind:
                        self.active_kind = None


class BatteryMonitor:
    """Schedule short white battery warnings from periodic ATT readings."""

    def __init__(
        self,
        overlay_worker: OverlayWorker,
        threshold: int = 10,
        critical_threshold: int = 5,
    ) -> None:
        if not 1 <= threshold <= 100:
            raise ValueError("battery threshold must be between 1 and 100")
        if not 1 <= critical_threshold < threshold:
            raise ValueError("critical threshold must be below the low threshold")
        self.overlay_worker = overlay_worker
        self.threshold = threshold
        self.critical_threshold = critical_threshold
        self.is_low = False
        self.is_critical = False
        self.last_level: int | None = None
        self.pending_show = False

    def update(self, level: int) -> bool:
        if not 0 <= level <= 100:
            LOG.warning("Ignoring invalid Siri Remote battery level: %d", level)
            return self.is_low
        LOG.info("Siri Remote battery: %d%%", level)
        low = level < self.threshold
        critical = level < self.critical_threshold
        if critical:
            if not self.is_critical:
                LOG.warning(
                    "Siri Remote battery below %d%%; warning flashes three times",
                    self.critical_threshold,
                )
            self.overlay_worker.submit_sequence(
                [
                    (f"BATTERY:{level}%", 0.45),
                    ("HIDE", 0.25),
                    (f"BATTERY:{level}%", 0.45),
                    ("HIDE", 0.25),
                    (f"BATTERY:{level}%", 0.45),
                ],
                kind="battery-critical",
            )
        elif low:
            if not self.is_low:
                LOG.warning(
                    "Siri Remote battery below %d%%; showing one-second warning",
                    self.threshold,
                )
            self.overlay_worker.submit(
                f"BATTERY:{level}%", duration=1.0, kind="battery-low",
            )
        elif self.is_low:
            LOG.info("Siri Remote battery recovered; periodic warning stopped")
        self.is_low = low
        self.is_critical = critical
        self.last_level = level
        if self.pending_show:
            self.pending_show = False
            self.overlay_worker.submit(f"BATTERY:{level}%")
        return low

    def show_current(self) -> None:
        if self.last_level is None:
            LOG.info("Battery button clicked; waiting for initial battery reading")
            self.pending_show = True
            return
        LOG.info("Battery button clicked; showing %d%%", self.last_level)
        self.overlay_worker.submit(f"BATTERY:{self.last_level}%")


class ButtonMapper:
    def __init__(
        self,
        worker: MoodeWorker,
        shutdown_action=None,
        screen_clicker: X11ClickWorker | None = None,
        overlay_worker: OverlayWorker | None = None,
        battery_display_action=None,
    ) -> None:
        self.worker = worker
        self.screen_clicker = screen_clicker
        self.overlay_worker = overlay_worker
        self.battery_display_action = battery_display_action
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
        self.mic_mask = int(env("SIRI_MIC_BUTTON_MASK", "0x10"), 0)
        if self.mic_mask <= 0 or self.mic_mask > 0xFF:
            raise ValueError("SIRI_MIC_BUTTON_MASK must be between 0x01 and 0xff")
        if self.mic_mask == self.home_mask:
            raise ValueError("SIRI_MIC_BUTTON_MASK must differ from the Home mask")
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
        if self.overlay_worker is not None:
            self.overlay_worker.cancel("shutdown")

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
        if self.overlay_worker is not None:
            self.overlay_worker.submit(
                "SHUTTING DOWN", duration=1.0, kind="shutdown",
            )
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
            if self.overlay_worker is not None:
                self.overlay_worker.start_shutdown(self.home_hold_seconds)
            timer.start()
        elif was_pressed and not is_pressed:
            if self.home_timer is not None:
                self.home_timer.cancel()
                self.home_timer = None
            self.home_fired = False
            if self.overlay_worker is not None:
                self.overlay_worker.cancel("shutdown")

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
        if newly_pressed & self.mic_mask and self.battery_display_action is not None:
            self.battery_display_action()
        if newly_pressed & BUTTON_MENU and self.screen_clicker is not None:
            self.screen_clicker.submit()
        for mask, (name, command) in self.mapping.items():
            # The configured Home button is reserved for the long-press action
            # and must not also execute its normal short-press mapping.
            if (
                mask != self.home_mask
                and not (mask == self.mic_mask and self.battery_display_action is not None)
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

    overlay_worker = OverlayWorker()
    battery_monitor = BatteryMonitor(
        overlay_worker,
        args.battery_threshold,
        args.battery_critical_threshold,
    )
    renderer_guard = RendererGuard(
        args.moode_url,
        args.http_timeout,
        blocked_handler=(
            lambda renderer_name: overlay_worker.submit(
                f"DISABLED:{renderer_name}"
            )
            if overlay_worker.enabled
            else None
        ),
    )
    worker = MoodeWorker(
        args.moode_url,
        args.http_timeout,
        args.dry_run,
        result_handler=overlay_worker.moode_result,
        renderer_guard=renderer_guard,
    )
    screen_clicker = X11ClickWorker(renderer_guard)
    mapper = ButtonMapper(
        worker,
        screen_clicker=screen_clicker if screen_clicker.enabled else None,
        overlay_worker=overlay_worker if overlay_worker.enabled else None,
        battery_display_action=battery_monitor.show_current,
    )
    overlay_worker.start()
    worker.start()
    screen_clicker.start()

    delay = args.reconnect_min
    try:
        while not stop_event.is_set():
            client = RawAttClient(args.mac, args.address_type, args.security)
            att_busy = False
            client.notification_handler = mapper.notification
            client.battery_handler = battery_monitor.update
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
                if args.battery_check > 0:
                    client.read_battery("Initial check")
                delay = args.reconnect_min
                client.receive_forever(
                    stop_event,
                    args.keepalive,
                    args.battery_check,
                    args.battery_low_check,
                    args.battery_critical_check,
                    args.battery_threshold,
                    args.battery_critical_threshold,
                )
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
        overlay_worker.stop()
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
        "--battery-check",
        type=float,
        default=float(env("SIRI_BATTERY_CHECK_SECONDS", "900")),
        help="seconds between Siri Remote battery checks; 0 disables checks",
    )
    parser.add_argument(
        "--battery-low-check",
        type=float,
        default=float(env("SIRI_BATTERY_LOW_CHECK_SECONDS", "300")),
        help="seconds between battery checks while below the low threshold",
    )
    parser.add_argument(
        "--battery-critical-check",
        type=float,
        default=float(env("SIRI_BATTERY_CRITICAL_CHECK_SECONDS", "60")),
        help="seconds between battery checks while below the critical threshold",
    )
    parser.add_argument(
        "--battery-threshold",
        type=int,
        default=int(env("SIRI_BATTERY_LOW_PERCENT", "10")),
        help="show a one-second battery warning below this percentage",
    )
    parser.add_argument(
        "--battery-critical-threshold",
        type=int,
        default=int(env("SIRI_BATTERY_CRITICAL_PERCENT", "5")),
        help="flash the battery warning three times below this percentage",
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
    if args.battery_check < 0:
        parser.error("battery check interval cannot be negative")
    if args.battery_low_check < 0:
        parser.error("low-battery check interval cannot be negative")
    if args.battery_critical_check < 0:
        parser.error("critical-battery check interval cannot be negative")
    if not 1 <= args.battery_threshold <= 100:
        parser.error("battery threshold must be between 1 and 100")
    if not 1 <= args.battery_critical_threshold < args.battery_threshold:
        parser.error("critical battery threshold must be below the low threshold")
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
