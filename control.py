"""
Keyboard, mouse, focus, and on-screen UI for the frontmost Windows app.

Clicks use SendInput left-button (not UIA click_input). restore_pointer()
clears a stuck wait/appstarting cursor after llama-server or pywinauto.
"""
from __future__ import annotations

import ctypes
import os
import re
import tempfile
import time
from ctypes import wintypes

from persona import click_line, search_line, scroll_line

user32 = ctypes.windll.user32

SPI_SETCURSORS = 0x0057
SPIF_SENDCHANGE = 0x02
IDC_ARROW = 32512
OCR_NORMAL = 32512
OCR_WAIT = 32514
OCR_APPSTARTING = 32650
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEUP = 0x0040


def restore_pointer() -> None:
    """Stop a stuck loading spinner: release buttons, restore scheme, hide wait cursor."""
    try:
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
        user32.ClipCursor(None)
    except Exception:
        pass
    try:
        user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)
    except Exception:
        pass
    try:
        arrow = user32.LoadCursorW(None, IDC_ARROW)
        if not arrow:
            return
        user32.SetCursor(arrow)
        copy_icon = user32.CopyIcon
        copy_icon.restype = ctypes.c_void_p
        copy_icon.argtypes = [ctypes.c_void_p]
        # Replace the wait / "app starting" pointers with the normal arrow.
        for ocr in (OCR_WAIT, OCR_APPSTARTING):
            copied = copy_icon(arrow)
            if copied:
                user32.SetSystemCursor(ctypes.c_void_p(copied), ocr)
        user32.SetCursor(arrow)
    except Exception:
        pass

VK = {
    "back": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "delete": 0x2E,
    "e": 0x45,
    "a": 0x41,
    "f": 0x46,
    "k": 0x4B,
    "l": 0x4C,
    "n": 0x4E,
    "r": 0x52,
    "t": 0x54,
    "w": 0x57,
    "f4": 0x73,
    "f11": 0x7A,
    "ctrl": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "slash": 0xBF,
}
for _i, _ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    VK.setdefault(_ch, 0x41 + _i)

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

_saved_hwnd = None


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))


def _send(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _vk_down(vk: int) -> INPUT:
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.union.ki = KEYBDINPUT(vk, 0, 0, 0, 0)
    return i


def _vk_up(vk: int) -> INPUT:
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)
    return i


def _title_of(hwnd) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value or ""


def snapshot_focus() -> None:
    global _saved_hwnd
    hwnd = user32.GetForegroundWindow()
    title = _title_of(hwnd).lower()
    if hwnd and "areis" not in title:
        _saved_hwnd = hwnd


def restore_focus() -> None:
    current = user32.GetForegroundWindow()
    if "areis" not in _title_of(current).lower():
        snapshot_focus()
        return
    hwnd = _saved_hwnd
    if not hwnd:
        return
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if hwnd and "areis" not in _title_of(hwnd).lower():
        return _title_of(hwnd)
    return _title_of(_saved_hwnd)


def press(*names: str, restore: bool = True) -> None:
    if restore:
        restore_focus()
    downs = [_vk_down(VK[n] if isinstance(n, str) else n) for n in names]
    ups = [_vk_up(VK[n] if isinstance(n, str) else n) for n in reversed(names)]
    _send(downs + ups)
    time.sleep(0.05)


def type_text(text: str, restore: bool = True) -> str:
    if restore:
        restore_focus()
    text = text or ""
    inputs = []
    for ch in text:
        code = ord(ch)
        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        inputs.extend([down, up])
    if inputs:
        _send(inputs)
    return f"Typed {text}" if text else "Nothing to type."


def _point_at_page(hwnd) -> None:
    left, top, right, bottom = _window_rect(hwnd)
    cx = (left + right) // 2
    cy = top + int((bottom - top) * 0.58)
    user32.SetCursorPos(int(cx), int(cy))
    time.sleep(0.05)


def _key_tap(name: str) -> None:
    vk = VK[name]
    _send([_vk_down(vk), _vk_up(vk)])
    time.sleep(0.04)


def scroll(direction: str, amount: int = 8) -> str:
    restore_focus()
    hwnd = _target_hwnd()
    _point_at_page(hwnd)
    going_down = direction not in ("up", "upward")
    ticks = max(3, amount // 2)
    delta = -240 if going_down else 240
    wheel = ctypes.c_uint32(ctypes.c_int32(delta).value).value
    for _ in range(ticks):
        i = INPUT()
        i.type = INPUT_MOUSE
        i.union.mi = MOUSEINPUT(0, 0, wheel, MOUSEEVENTF_WHEEL, 0, 0)
        _send([i])
        time.sleep(0.03)
    _key_tap("pagedown" if going_down else "pageup")
    arrow = "down" if going_down else "up"
    for _ in range(6):
        _key_tap(arrow)
    restore_pointer()
    return scroll_line("down" if going_down else "up")


def maximize_window() -> str:
    restore_focus()
    hwnd = _target_hwnd()
    if hwnd:
        user32.ShowWindow(hwnd, 3)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        return "Maximized the window."
    press("win", "up")
    return "Maximized the window."


def minimize_window() -> str:
    restore_focus()
    hwnd = _target_hwnd()
    if hwnd:
        user32.ShowWindow(hwnd, 6)
        return "Minimized the window."
    press("win", "down")
    return "Minimized the window."


def _click_xy(x: int, y: int) -> None:
    """Left-click at screen coordinates (never a right-click)."""
    x, y = int(x), int(y)
    user32.SetCursorPos(x, y)
    time.sleep(0.08)
    down = INPUT()
    down.type = INPUT_MOUSE
    down.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
    up = INPUT()
    up.type = INPUT_MOUSE
    up.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
    _send([down, up])
    time.sleep(0.05)


def _drag_xy(x1: int, y1: int, x2: int, y2: int) -> None:
    user32.SetCursorPos(int(x1), int(y1))
    time.sleep(0.08)
    down = INPUT()
    down.type = INPUT_MOUSE
    down.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
    _send([down])
    time.sleep(0.05)
    user32.SetCursorPos(int(x2), int(y2))
    time.sleep(0.12)
    up = INPUT()
    up.type = INPUT_MOUSE
    up.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
    _send([up])


def draw_circle() -> str:
    restore_focus()
    time.sleep(0.25)
    for label in ("Oval", "Circle", "Ellipse"):
        msg = click_label(label)
        if "couldn't find" not in msg.lower():
            break
    hwnd = _target_hwnd()
    left, top, right, bottom = _window_rect(hwnd)
    w = max(right - left, 1)
    h = max(bottom - top, 1)
    x1 = left + int(w * 0.32)
    y1 = top + int(h * 0.38)
    x2 = left + int(w * 0.58)
    y2 = top + int(h * 0.62)
    _drag_xy(x1, y1, x2, y2)
    return "Drew a circle in Paint."


def _target_hwnd():
    hwnd = user32.GetForegroundWindow()
    if hwnd and "areis" not in _title_of(hwnd).lower():
        return hwnd
    return _saved_hwnd or hwnd


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        return 0, 0, sw, sh
    return rect.left, rect.top, rect.right, rect.bottom


_CHROME_NOISE = {
    "home",
    "shorts",
    "subscriptions",
    "you",
    "history",
    "watch later",
    "liked videos",
    "show more",
    "sign in",
    "search",
    "skip",
    "skip ad",
    "subscribe",
    "subscribed",
    "share",
    "download",
    "clip",
    "thanks",
    "join",
    "play",
    "pause",
    "mute",
    "settings",
    "youtube",
    "google",
    "chrome",
    "new tab",
}


def _is_video_name(name: str) -> bool:
    n = (name or "").strip()
    if len(n) < 8 or len(n) > 160:
        return False
    low = n.lower()
    if low in _CHROME_NOISE:
        return False
    if low.startswith(("http", "www.", "javascript")):
        return False
    if re.search(r"youtube\s*-\s*(brave|chrome|edge|msedge)", low):
        return False
    if low.endswith(" chrome") or "msedge" in low:
        return False
    if re.match(r"^\(\d+\)", n):
        return False
    if low in ("filters", "search", "mixes", "shorts", "all", "unwatched", "watched"):
        return False
    title = foreground_title().lower()
    if title and low == title:
        return False
    return True


def _video_hits(zone: str):
    import session

    hwnd = _target_hwnd()
    if session.last_site() == "youtube":
        hwnd = _youtube_hwnd() or hwnd
    left, top, right, bottom = _window_rect(hwnd)
    width = max(right - left, 1)
    hits = []
    try:
        for name, el in _uia_elements(700, hwnd=hwnd):
            if not _is_video_name(name):
                continue
            try:
                rec = el.rectangle()
                cx = (rec.left + rec.right) // 2
                cy = (rec.top + rec.bottom) // 2
            except Exception:
                continue
            if cy < top + 150 or cy > bottom - 50:
                continue
            if rec.bottom - rec.top < 12:
                continue
            if cx < left + 40 or cx > right - 20:
                continue
            if zone in ("sidebar", "right", "recommended", "recommendations"):
                if cx < left + int(width * 0.60):
                    continue
            elif zone in ("feed", "main", "results", "search", "", "top", "bottom", "last"):
                if cx > left + int(width * 0.62):
                    continue
            hits.append((cy, cx, name, el))
    except Exception:
        return []
    hits.sort()
    uniq = []
    for item in hits:
        if uniq and abs(item[0] - uniq[-1][0]) < 40 and abs(item[1] - uniq[-1][1]) < 90:
            continue
        uniq.append(item)
    return uniq


def click_nth(n: int, zone: str = "") -> str:
    """Click the nth video/result in the front window."""
    import session

    if session.last_site() == "youtube":
        hwnd = _youtube_hwnd()
        if hwnd:
            global _saved_hwnd
            _saved_hwnd = hwnd
            user32.ShowWindow(hwnd, 9)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
    else:
        restore_focus()
        time.sleep(0.12)
    n = max(1, min(int(n), 10))
    zone = (zone or "feed").lower()
    session.remember_action("click_nth", n=n, zone=zone)

    hits = _video_hits("feed" if zone in ("bottom", "last", "top", "feed", "") else zone)
    if hits:
        if zone in ("bottom", "last"):
            idx = len(hits) - 1
        elif zone in ("top",):
            idx = 0
        else:
            idx = min(n, len(hits)) - 1
        _cy, cx, name, el = hits[idx]
        _click_xy(cx, _cy)
        session.remember_entity("video", name)
        return click_line(name)

    hwnd = _target_hwnd()
    left, top, right, bottom = _window_rect(hwnd)
    w = max(right - left, 1)
    h = max(bottom - top, 1)
    if zone in ("sidebar", "right", "recommended", "recommendations"):
        x = left + int(w * 0.82)
        y = top + int(h * 0.24) + (n - 1) * int(h * 0.13)
    else:
        x = left + int(w * 0.36)
        y = top + int(h * 0.32) + (n - 1) * int(h * 0.15)
    _click_xy(x, y)
    where = "sidebar" if zone in ("sidebar", "right", "recommended") else "results"
    return click_line()


def _youtube_hwnd():
    hwnd = _find_hwnd("youtube")
    if hwnd:
        return hwnd
    for needle in ("brave", "chrome", "msedge", "edge"):
        hwnd = _find_hwnd(needle)
        if hwnd:
            return hwnd
    return _target_hwnd()


def play_youtube_first() -> bool:
    """Click the first main-column search result so the video actually plays."""
    hwnd = _youtube_hwnd()
    if not hwnd:
        return False
    global _saved_hwnd
    _saved_hwnd = hwnd
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.45)
    left, top, right, bottom = _window_rect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    main_left = left + int(width * 0.18)
    main_right = left + int(width * 0.72)
    main_top = top + int(height * 0.22)
    main_bot = bottom - int(height * 0.08)
    hits = []
    try:
        for name, el in _uia_elements(700, hwnd=hwnd):
            if not _is_video_name(name):
                continue
            try:
                rec = el.rectangle()
                cx = (rec.left + rec.right) // 2
                cy = (rec.top + rec.bottom) // 2
            except Exception:
                continue
            if cy < main_top or cy > main_bot:
                continue
            if cx < main_left or cx > main_right:
                continue
            if rec.bottom - rec.top < 14:
                continue
            hits.append((cy, max(main_left + 20, rec.left + 36), name))
    except Exception:
        hits = []
    if hits:
        hits.sort()
        cy, cx, _name = hits[0]
        _click_xy(cx, cy)
        time.sleep(0.25)
        return True
    _click_xy(left + int(width * 0.38), top + int(height * 0.40))
    time.sleep(0.2)
    return True


def screenshot_path() -> str:
    path = os.path.join(tempfile.gettempdir(), "areis-screen.png")
    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(path)
        return path
    except Exception as e:
        raise RuntimeError(f"Could not capture the screen: {e}") from e


def _uia_elements(limit: int = 80, hwnd=None) -> list[tuple[str, object]]:
    from pywinauto import Desktop
    from pywinauto import timings

    timings.Timings.fast()
    hwnd = hwnd or _saved_hwnd or user32.GetForegroundWindow()
    found = []
    try:
        win = Desktop(backend="uia").window(handle=hwnd)
        for el in win.descendants():
            try:
                name = (el.window_text() or "").strip()
            except Exception:
                continue
            if not name or len(name) > 180:
                continue
            found.append((name, el))
            if len(found) >= limit:
                break
        return found
    finally:
        restore_pointer()


def read_screen() -> str:
    restore_focus()
    title = foreground_title()
    lines = [f"Front window: {title or '(unknown)'}"]
    try:
        names = []
        seen = set()
        for name, _el in _uia_elements():
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
            if len(names) >= 24:
                break
        if names:
            lines.append("On screen: " + " | ".join(names))
        else:
            lines.append("I can see the window but no labeled buttons.")
    except Exception:
        lines.append("UI read failed; keyboard navigation still works.")
    return "\n".join(lines)


def click_label(label: str) -> str:
    restore_focus()
    want = (label or "").strip().lower()
    if not want:
        return "What should I click?"
    try:
        best = None
        best_score = 0
        for name, el in _uia_elements(120):
            n = name.lower()
            if want == n:
                best = (name, el)
                break
            if want in n:
                score = 1000 - len(n)
                if score > best_score:
                    best_score = score
                    best = (name, el)
        if best:
            name, el = best
            try:
                rec = el.rectangle()
                _click_xy((rec.left + rec.right) // 2, (rec.top + rec.bottom) // 2)
            except Exception:
                pass
            import session

            session.remember_entity("label", name)
            return click_line(name)
    except Exception:
        pass
    return f"I couldn't find '{label}' on screen. Try scroll, or say what's on screen."


def _find_hwnd(substr: str):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            title = _title_of(hwnd).lower()
            if substr.lower() in title and "areis" not in title:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def focus_app(substr: str) -> bool:
    """Bring a visible window whose title contains substr to the front."""
    global _saved_hwnd
    needle = (substr or "").strip().lower()
    if not needle:
        return False
    hwnd = _find_hwnd(needle)
    if not hwnd:
        return False
    _saved_hwnd = hwnd
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.18)
    return True


def _discord_hwnd():
    hwnd = _find_hwnd("discord")
    if hwnd and "discord" in _title_of(hwnd).lower():
        return hwnd
    return None


def _discord_escape() -> None:
    for _ in range(2):
        press("escape", restore=False)
        time.sleep(0.08)


def discord_prepare_compose() -> bool:
    """Close Discord's top search and click the bottom Message box."""
    hwnd = _discord_hwnd()
    if not hwnd:
        return False
    global _saved_hwnd
    _saved_hwnd = hwnd
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.12)
    _discord_escape()
    left, top, right, bottom = _window_rect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    band_top = bottom - int(height * 0.22)
    clicked = False
    try:
        from pywinauto import Desktop
        from pywinauto import timings

        timings.Timings.fast()
        win = Desktop(backend="uia").window(handle=hwnd)
        best = None
        best_y = -1
        for el in win.descendants():
            try:
                ctype = (getattr(el.element_info, "control_type", None) or "").lower()
                name = (el.window_text() or "").strip().lower()
                rec = el.rectangle()
            except Exception:
                continue
            cx = (rec.left + rec.right) // 2
            cy = (rec.top + rec.bottom) // 2
            w = rec.right - rec.left
            h = rec.bottom - rec.top
            if cy < band_top or cy > bottom - 6:
                continue
            if cx < left + int(width * 0.20):
                continue
            if "search" in name or "filter" in name:
                continue
            looks_box = (
                ctype in ("edit", "document")
                or name.startswith("message @")
                or name.startswith("message #")
                or name.startswith("message ")
            )
            if looks_box and w > 100 and h < 180 and cy > best_y:
                best_y = cy
                best = (cx, cy)
        if best:
            _click_xy(*best)
            clicked = True
    except Exception:
        clicked = False
    finally:
        restore_pointer()
    if not clicked:
        _click_xy(left + int(width * 0.40), bottom - int(height * 0.09))
    time.sleep(0.12)
    return True


def discord_jump_to(who: str) -> bool:
    """Search Direct Messages for this username, then open that chat.

    Always types into the left Find box. Do not reuse whoever is already open.
    """
    who = (who or "").strip()
    if not who:
        return False
    hwnd = _discord_hwnd()
    if not hwnd:
        return False
    global _saved_hwnd
    _saved_hwnd = hwnd
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    _discord_escape()
    left, top, right, bottom = _window_rect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    clicked_find = False
    try:
        from pywinauto import Desktop
        from pywinauto import timings

        timings.Timings.fast()
        win = Desktop(backend="uia").window(handle=hwnd)
        for el in win.descendants():
            try:
                name = (el.window_text() or "").strip().lower()
                ctype = (getattr(el.element_info, "control_type", None) or "").lower()
                rec = el.rectangle()
            except Exception:
                continue
            cx = (rec.left + rec.right) // 2
            cy = (rec.top + rec.bottom) // 2
            if cx > left + int(width * 0.34):
                continue
            if cy < top + 36 or cy > top + int(height * 0.28):
                continue
            useful = (
                "find or start" in name
                or "search" in name
                or "conversation" in name
                or ctype == "edit"
            )
            if useful:
                _click_xy(cx, cy)
                clicked_find = True
                break
    except Exception:
        clicked_find = False
    finally:
        restore_pointer()
    if not clicked_find:
        _click_xy(left + int(width * 0.14), top + int(height * 0.12))
    time.sleep(0.28)
    press("ctrl", "a", restore=False)
    time.sleep(0.05)
    type_text(who, restore=False)
    time.sleep(0.7)
    press("down", restore=False)
    time.sleep(0.1)
    press("enter", restore=False)
    time.sleep(0.45)
    return True


def navigate_url_in_tab(url: str) -> bool:
    """Point the already-open browser tab at url via the address bar, instead
    of opening a new tab/window. Returns False if there's no browser tab to
    reuse yet, so the caller should fall back to opening a fresh one."""
    import session

    if session.last_app() != "browser":
        return False
    hwnd = _target_hwnd()
    if not hwnd:
        return False
    restore_focus()
    time.sleep(0.1)
    press("ctrl", "l")  # focus the address bar of the current tab
    time.sleep(0.15)
    press("ctrl", "a")  # select whatever's already typed there
    type_text(url)
    time.sleep(0.05)
    press("enter")
    time.sleep(0.3)
    return True


def click_spotify_first_result() -> str:
    """Click the first playable row in Spotify's search-results view so a
    search actually starts audio. Tries a UIA 'Play' button first (Spotify
    desktop shows a hidden Play button per row that UIA can still see even
    though it's only visually revealed on hover); falls back to a coordinate
    guess at the top result row, with a second click since a single click on
    a track row sometimes only selects it rather than starting playback."""
    restore_focus()
    time.sleep(0.35)
    try:
        for name, el in _uia_elements(150):
            n = name.strip().lower()
            if n in ("play", "play song", "play track"):
                rec = el.rectangle()
                _click_xy((rec.left + rec.right) // 2, (rec.top + rec.bottom) // 2)
                time.sleep(0.15)
                return "Playing."
    except Exception:
        pass
    hwnd = _target_hwnd()
    left, top, right, bottom = _window_rect(hwnd)
    w = max(right - left, 1)
    h = max(bottom - top, 1)
    x = left + int(w * 0.30)
    y = top + int(h * 0.34)
    _click_xy(x, y)
    time.sleep(0.15)
    _click_xy(x, y)  # some rows need a second click/double-click to start playback
    return "Playing."


def click_current_video() -> str:
    """Play/pause the video already loaded in the player — not a thumbnail
    in a results list. Prefers the site's own hotkey (reliable) over trying
    to hit-test the player element with UIA (fragile across layouts)."""
    restore_focus()
    title = foreground_title().lower()
    if "youtube" in title:
        press("k")  # YouTube's native play/pause shortcut for the loaded video
        return "Playing the video."
    press("space")  # works for most native/web players (Spotify, VLC, etc.)
    return "Playing the video."


def search_in_settings(query: str) -> str:
    import session

    q = (query or "").strip()
    if not q:
        return "What should I search for in Settings?"
    hwnd = _find_hwnd("settings")
    global _saved_hwnd
    if hwnd:
        _saved_hwnd = hwnd
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.4)
    else:
        restore_focus()
        time.sleep(0.2)
    press("ctrl", "e")
    time.sleep(0.2)
    press("ctrl", "a")
    type_text(q)
    time.sleep(0.12)
    press("enter")
    session.set_last_app("settings")
    session.set_last_site("")
    return search_line("Settings", q)


def in_app_search(query: str) -> str:
    from urllib.parse import quote_plus
    import webbrowser
    import session

    q = (query or "").strip()
    if not q:
        return "What should I search for?"
    title = foreground_title().lower()
    raw = q.lower()
    app = session.last_app()
    if ("setting" in title or app == "settings") and "youtube" not in raw:
        return search_in_settings(q)
    if app == "spotify" or "spotify" in title:
        encoded = quote_plus(q)
        try:
            os.startfile("spotify:search:" + encoded)
        except OSError:
            webbrowser.open("https://open.spotify.com/search/" + encoded)
        session.set_last_site("spotify")
        session.set_last_app("spotify")
        return search_line("Spotify", q)
    use_yt = (
        "youtube" in title
        or "youtube" in raw
        or (session.last_site() == "youtube" and app not in ("settings", "spotify"))
        or raw.endswith(" on youtube")
    )
    if raw.endswith(" on youtube"):
        q = q[: -len(" on youtube")].strip()
        use_yt = True
    session.remember_entity("query", q)
    encoded = quote_plus(q)
    if use_yt:
        url = f"https://www.youtube.com/results?search_query={encoded}"
        label = "YouTube"
        site = "youtube"
    else:
        url = f"https://www.google.com/search?q={encoded}"
        label = "Google"
        site = "google"

    if navigate_url_in_tab(url):
        session.set_last_site(site)
        session.set_last_app("browser")
        return search_line(label, q)

    restore_focus()
    webbrowser.open(url)
    session.set_last_site(site)
    time.sleep(0.8)
    snapshot_focus()
    return search_line(label, q)


def navigate(action: str, extra: str = "") -> str:
    act = (action or "").strip().lower().replace(" ", "_")
    extra = (extra or "").strip()
    restore_focus()
    if act in ("scroll_down", "down"):
        return scroll("down")
    if act in ("scroll_up", "up"):
        return scroll("up")
    if act in ("enter", "select", "open_this", "click"):
        press("enter")
        return "Pressed Enter."
    if act in ("back", "go_back"):
        press("alt", "left")
        return "Went back."
    if act in ("forward", "go_forward"):
        press("alt", "right")
        return "Went forward."
    if act in ("tab", "next"):
        press("tab")
        return "Next item."
    if act in ("prev", "previous"):
        press("shift", "tab")
        return "Previous item."
    if act in ("escape", "esc", "back_out"):
        press("escape")
        return "Pressed Escape."
    if act in ("space",):
        press("space")
        return "Pressed Space."
    if act in ("refresh", "reload"):
        press("ctrl", "r")
        return "Refreshed."
    if act in ("new_tab",):
        press("ctrl", "t")
        return "Opened a new tab."
    if act in ("close_tab",):
        press("ctrl", "w")
        return "Closed the tab."
    if act in ("next_tab",):
        press("ctrl", "tab")
        return "Next tab."
    if act in ("address", "url_bar"):
        press("ctrl", "l")
        return "Address bar."
    if act in ("fullscreen",):
        press("f")
        return "Toggled fullscreen."
    if act in ("maximize", "maximise"):
        return maximize_window()
    if act in ("minimize", "minimise"):
        return minimize_window()
    if act in ("next_video",):
        press("shift", "n")
        return "Next video."
    if act in ("search",):
        return in_app_search(extra)
    if act.startswith("result_"):
        try:
            n = int(act.split("_")[1])
        except (IndexError, ValueError):
            n = 1
        return click_nth(n, extra)
    if act in ("click_label", "click_on"):
        return click_label(extra)
    if act in ("type",):
        return type_text(extra)
    if act in ("read", "whats_on_screen"):
        return read_screen()
    return f"I don't know the navigation action {action}."