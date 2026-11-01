"""Windows' displays and windows, for moving windows around by voice.

Ported from Atticus for Windows (FindlayHi/atticus-windows, itself a port of
SaarthurR/atticus-mac; both MIT): every display with its work area and whether
it is the laptop's own panel, windows matched by process, app name or title,
and moves that land the visible frame exactly where asked. Pure ctypes; every
call returns quickly or gives up quietly.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes as W
from typing import Any

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    version = ctypes.WinDLL("version")
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user32.SetWindowPos.argtypes = [
        W.HWND,
        W.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]

# Where a window can go: Atticus's halves, full and center, plus Friday's
# quarters, maximize and minimize.
PLACES = (
    "left",
    "right",
    "full",
    "center",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "maximize",
    "minimize",
)
SCREENS = ("current", "laptop", "monitor")


class RECT(ctypes.Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", W.DWORD),
        ("szDevice", W.WCHAR * 32),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", W.DWORD), ("HighPart", W.LONG)]


class PATH_INFO(ctypes.Structure):  # DISPLAYCONFIG_PATH_INFO, flattened (72 bytes)
    _fields_ = [
        ("srcAdapter", LUID),
        ("srcId", ctypes.c_uint32),
        ("srcMode", ctypes.c_uint32),
        ("srcFlags", ctypes.c_uint32),
        ("tgtAdapter", LUID),
        ("tgtId", ctypes.c_uint32),
        ("tgtMode", ctypes.c_uint32),
        ("tech", ctypes.c_uint32),
        ("rot", ctypes.c_uint32),
        ("scale", ctypes.c_uint32),
        ("rate", ctypes.c_uint32 * 2),
        ("order", ctypes.c_uint32),
        ("avail", W.BOOL),
        ("tgtFlags", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("adapter", LUID),
        ("id", ctypes.c_uint32),
    ]


class SOURCE_NAME(ctypes.Structure):
    _fields_ = [("h", HEADER), ("gdi", W.WCHAR * 32)]


class TARGET_NAME(ctypes.Structure):
    _fields_ = [
        ("h", HEADER),
        ("flags", ctypes.c_uint32),
        ("tech", ctypes.c_uint32),
        ("mfr", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("connector", ctypes.c_uint32),
        ("friendly", W.WCHAR * 64),
        ("path", W.WCHAR * 128),
    ]


# LVDS, embedded DisplayPort, UDI embedded, "internal": a laptop's own panel.
INTERNAL = {6, 11, 13, 0x80000000}


def _panels() -> dict[str, tuple[str, bool]]:
    """GDI device name (\\\\.\\DISPLAY1) -> (friendly monitor name, built in?)."""
    out: dict[str, tuple[str, bool]] = {}
    n_path, n_mode = ctypes.c_uint32(), ctypes.c_uint32()
    try:
        if user32.GetDisplayConfigBufferSizes(2, ctypes.byref(n_path), ctypes.byref(n_mode)):
            return out
        paths = (PATH_INFO * n_path.value)()
        modes = (ctypes.c_byte * (64 * max(1, n_mode.value)))()
        if user32.QueryDisplayConfig(
            2, ctypes.byref(n_path), paths, ctypes.byref(n_mode), modes, None
        ):
            return out
        for p in paths[: n_path.value]:
            s = SOURCE_NAME(HEADER(1, ctypes.sizeof(SOURCE_NAME), p.srcAdapter, p.srcId))
            t = TARGET_NAME(HEADER(2, ctypes.sizeof(TARGET_NAME), p.tgtAdapter, p.tgtId))
            if user32.DisplayConfigGetDeviceInfo(ctypes.byref(s)) == 0:
                user32.DisplayConfigGetDeviceInfo(ctypes.byref(t))
                out[s.gdi] = (t.friendly or "", p.tech in INTERNAL)
    except (AttributeError, OSError):
        pass
    return out


def has_battery() -> bool:
    class SPS(ctypes.Structure):
        _fields_ = [
            ("ac", ctypes.c_byte),
            ("flag", ctypes.c_byte),
            ("pct", ctypes.c_byte),
            ("saver", ctypes.c_byte),
            ("life", W.DWORD),
            ("full", W.DWORD),
        ]

    s = SPS()
    # 128 = no system battery; -128 = unknown.
    return (
        bool(kernel32.GetSystemPowerStatus(ctypes.byref(s)))
        and s.flag != -128
        and not (s.flag & 128)
    )


def monitors() -> list[dict[str, Any]]:
    """[{name, x, y, w, h, work: (x, y, w, h), built_in, primary}], the primary
    one first, in physical pixels."""
    found = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(RECT), W.LPARAM)

    def each(h, _dc, _r, _l):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(h, ctypes.byref(mi)):
            found.append(mi)
        return True

    user32.EnumDisplayMonitors(None, None, proc(each), 0)
    names, out, seen = _panels(), [], {}
    for mi in found:
        friendly, built_in = names.get(mi.szDevice, ("", False))
        name = friendly or ("Built-in Display" if built_in else "Display")
        seen[name] = seen.get(name, 0) + 1
        m, w = mi.rcMonitor, mi.rcWork
        out.append(
            {
                "name": name if seen[name] == 1 else f"{name} {seen[name]}",
                "x": m.left,
                "y": m.top,
                "w": m.right - m.left,
                "h": m.bottom - m.top,
                "work": (w.left, w.top, w.right - w.left, w.bottom - w.top),
                "built_in": built_in,
                "primary": bool(mi.dwFlags & 1),
            }
        )
    out.sort(key=lambda d: not d["primary"])
    if out and len(out) > 1 and not any(d["built_in"] for d in out) and has_battery():
        out[0]["built_in"] = True  # no EDID answer: a laptop's panel is usually the primary
    return out


# ---------- processes and windows ----------

_desc: dict[str, str] = {}


def exe_of(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def describe_exe(path: str) -> str:
    """The app's own name from its version info ("Google Chrome" for chrome.exe)."""
    if path in _desc:
        return _desc[path]
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if size:
            data = ctypes.create_string_buffer(size)
            version.GetFileVersionInfoW(path, 0, size, data)
            ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
            if (
                version.VerQueryValueW(
                    data, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(ln)
                )
                and ln.value >= 4
            ):
                lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16))[0:2]
                if (
                    version.VerQueryValueW(
                        data,
                        f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription",
                        ctypes.byref(ptr),
                        ctypes.byref(ln),
                    )
                    and ln.value > 1
                ):
                    d = ctypes.wstring_at(ptr, ln.value - 1).strip()
                    if (
                        d
                        and len(d) < 40
                        and not re.search(r"\b(host|launcher|helper|runtime|setup)\b", d, re.I)
                    ):
                        name = d
    except OSError:
        pass
    _desc[path] = name
    return name


def title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def pid_of(hwnd) -> int:
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def real_pid(hwnd) -> int:
    """UWP apps (Calculator, Settings) draw inside ApplicationFrameHost: the app
    is the child window's process."""
    pid = pid_of(hwnd)
    if not exe_of(pid).lower().endswith("applicationframehost.exe"):
        return pid
    hit: list[int] = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user32.EnumChildWindows(
        hwnd,
        proc(lambda c, _: (hit.append(pid_of(c)) if pid_of(c) != pid else None) or True),
        0,
    )
    return hit[0] if hit else pid


def cloaked(hwnd) -> bool:
    v = ctypes.c_int()
    dwmapi.DwmGetWindowAttribute(W.HWND(hwnd), 14, ctypes.byref(v), ctypes.sizeof(v))
    return bool(v.value)


SKIP_EXES = {
    "textinputhost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
}


def windows() -> list[dict[str, Any]]:
    """Top-level app windows a person can see, front to back:
    [{hwnd, title, exe, proc, app}]."""
    hs: list[int] = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user32.EnumWindows(proc(lambda h, _: hs.append(h) or True), 0)
    out = []
    for h in hs:
        # 4 = GW_OWNER: dialogs and popups belong to another window.
        if not user32.IsWindowVisible(h) or user32.GetWindow(h, 4) or cloaked(h):
            continue
        ex = user32.GetWindowLongW(h, -20)
        t = title(h)
        if not t or ex & 0x80 and not ex & 0x40000:  # tool window without WS_EX_APPWINDOW
            continue
        exe = exe_of(real_pid(h))
        if not exe or os.path.basename(exe).lower() in SKIP_EXES:
            continue
        out.append(
            {
                "hwnd": h,
                "title": t,
                "exe": exe,
                "proc": os.path.splitext(os.path.basename(exe))[0],
                "app": describe_exe(exe),
            }
        )
    return out


def key(s: str) -> str:
    """Comparable form of a name: "Trading View" == "TradingView" == "tradingview.exe"."""
    return re.sub(r"[^a-z0-9]", "", s.lower().removesuffix(".exe"))


def matches(w: dict[str, Any], name: str) -> bool:
    """A window belongs to `name` when it names the process ("chrome"), the app
    ("Google Chrome"), the end of its title ("… - Notepad"), or its title."""
    k = key(name)
    if not k:
        return False
    if k in (key(w["proc"]), key(w["app"])):
        return True
    if key(w["title"].rsplit(" - ", 1)[-1]) == k:
        return True
    words = [key(part) for part in w["app"].split()]
    return k in words or name.casefold().strip() in w["title"].casefold()


def find(name: str, words: str = "") -> list[dict[str, Any]]:
    words = words.lower()
    return [w for w in windows() if matches(w, name) and (not words or words in w["title"].lower())]


def front() -> tuple[str, str, Any]:
    """(app name, window title, hwnd) of the window in front."""
    h = user32.GetForegroundWindow()
    if not h:
        return "", "", None
    exe = exe_of(real_pid(h))
    return (describe_exe(exe) if exe else ""), title(h), h


def frame(hwnd) -> tuple[int, int, int, int]:
    """The visible frame, without the invisible resize borders: (x, y, w, h)."""
    r = RECT()
    if dwmapi.DwmGetWindowAttribute(W.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)):
        user32.GetWindowRect(W.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def set_frame(hwnd, x: int, y: int, w: int, h: int) -> None:
    """Move so the visible frame lands exactly there: the window rect is a few
    pixels bigger on each side."""
    r = RECT()
    user32.GetWindowRect(W.HWND(hwnd), ctypes.byref(r))
    fx, fy, fw, fh = frame(hwnd)
    dl, dt, dr, db = fx - r.left, fy - r.top, r.right - (fx + fw), r.bottom - (fy + fh)
    user32.SetWindowPos(hwnd, None, x - dl, y - dt, w + dl + dr, h + dt + db, 0x0014)


def bring(hwnd) -> bool:
    """Bring a window forward. Windows lets only the app with the last input do
    that, so borrow the front window's input queue."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    them = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = them and them != me and user32.AttachThreadInput(me, them, True)
    user32.keybd_event(0xE8, 0, 0, 0)  # an unassigned key: counts as input, does nothing
    user32.keybd_event(0xE8, 0, 2, 0)
    user32.BringWindowToTop(hwnd)
    ok = user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(me, them, False)
    return bool(ok)


def rect_for(
    where: str, area: tuple[int, int, int, int], size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Where a window goes in a work area (x, y, w, h)."""
    x, y, w, h = area
    cw, ch = min(size[0], w), min(size[1], h)
    hw, hh = w // 2, h // 2
    return {
        "left": (x, y, hw, h),
        "right": (x + hw, y, w - hw, h),
        "full": (x, y, w, h),
        "center": (x + (w - cw) // 2, y + (h - ch) // 2, cw, ch),
        "top-left": (x, y, hw, hh),
        "top-right": (x + hw, y, w - hw, hh),
        "bottom-left": (x, y + hh, hw, h - hh),
        "bottom-right": (x + hw, y + hh, w - hw, h - hh),
    }[where]


def pick_monitor(
    mons: list[dict[str, Any]], screen: str, center: tuple[int, int]
) -> dict[str, Any]:
    """The display a window goes to: laptop, monitor, one by name, or the one it
    is on now."""
    cx, cy = center
    if screen == "laptop":
        hit = next((m for m in mons if m["built_in"]), None)
    elif screen == "monitor":
        hit = next((m for m in mons if not m["built_in"]), None)
    elif screen != "current":
        hit = next((m for m in mons if m["name"].lower() == screen.lower()), None)
    else:
        hit = next(
            (m for m in mons if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]),
            None,
        )
    return hit or mons[0]


def place(name: str, where: str = "full", screen: str = "current", words: str = "") -> dict:
    """Move a window. where: one of PLACES; screen: laptop, monitor, current or a
    display's name. -> {window, app, position, screen}; raises LookupError when
    no window matches."""
    if where == "maximize":
        where = "full"
    if where not in PLACES:
        raise ValueError(f"where must be one of {', '.join(PLACES)}")
    words = re.sub(
        r"\.[a-z0-9]{2,4}$", "", words, flags=re.I
    )  # "notes.txt" finds "notes - Notepad"
    for _ in range(8):  # a window just opened can take a moment to appear
        ws = find(name, words)
        if ws:
            break
        time.sleep(0.25)
    else:
        raise LookupError(f"No open window called {name!r}.")
    hwnd = ws[0]["hwnd"]
    if where == "minimize":
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return {"window": ws[0]["title"], "app": ws[0]["app"], "position": where, "screen": ""}
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # maximized windows can't be moved: restore first
    fx, fy, fw, fh = frame(hwnd)
    mons = monitors()
    m = pick_monitor(mons, screen, (fx + fw // 2, fy + fh // 2))
    x, y, w, h = rect_for(where, m["work"], (fw, fh))
    set_frame(hwnd, x, y, w, h)
    if where == "full":
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE, on the display it now sits on
    bring(hwnd)
    return {
        "window": ws[0]["title"],
        "app": ws[0]["app"],
        "position": where,
        "screen": m["name"] if len(mons) > 1 else "",
    }
