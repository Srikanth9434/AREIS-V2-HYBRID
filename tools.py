"""
Desktop tools AREIS can run: open apps/sites, search, scrape, switch backends.

Each action is a Python function. TOOL_SCHEMAS / TOOL_REGISTRY exist for
models that use tools; the live agent path may skip tools and use intent.py.
Keys go through KeyManager: add_key returns False on duplicates, and
has_usable_key ignores keys marked bad this session.
"""
from __future__ import annotations

import ctypes
import datetime
import os
import re
import shutil
import subprocess
import time
import webbrowser
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import session
from control import (
    click_label,
    click_nth,
    focus_app,
    in_app_search,
    navigate,
    read_screen,
    search_in_settings,
    type_text,
)
from key_manager import key_manager

# --- Tool implementations -----------------------------------------------

def get_time() -> str:
    return datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")


def open_url(url: str, browser: str | None = None) -> str:
    """Opens a URL in the default browser, or Brave/Chrome/Edge if asked."""
    if not url.startswith(("http://", "https://", "mailto:")):
        url = "https://" + url
    try:
        browser = (browser or "").strip().lower()
        launched = False
        if browser in ("brave", "brave browser"):
            exe = shutil.which("brave") or shutil.which("brave.exe")
            if not exe:
                exe = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
            if os.path.exists(exe) or shutil.which("brave"):
                subprocess.Popen([exe if os.path.exists(exe) else "brave", url], shell=False)
                launched = True
        elif browser in ("chrome",):
            exe = shutil.which("chrome") or shutil.which("chrome.exe")
            if exe:
                subprocess.Popen([exe, url], shell=False)
                launched = True
        elif browser in ("edge", "msedge"):
            subprocess.Popen(["msedge", url], shell=False)
            launched = True
        if not launched and not browser:
            try:
                from control import navigate_url_in_tab

                if navigate_url_in_tab(url):
                    launched = True
            except Exception:
                pass
        if not launched:
            webbrowser.open(url)
        time.sleep(0.9)
        try:
            from control import snapshot_focus

            snapshot_focus()
        except Exception:
            pass
        session.remember_entity("url", url)
        low = url.lower()
        if low.startswith("mailto:"):
            pass  # opened the mail client, not a browser tab — leave session as-is
        elif "youtube" in low:
            session.set_last_site("youtube")
            session.set_last_app("browser")
        elif "google" in low:
            session.set_last_site("google")
            session.set_last_app("browser")
        elif "whatsapp" in low:
            session.set_last_app("whatsapp")
            session.set_last_site("whatsapp")
        else:
            session.set_last_app("browser")
        return f"Opened {url}"
    except Exception as e:
        return f"Failed to open {url}: {e}"


def search_web(query: str, engine: str = "google") -> str:
    raw_query = (query or "").strip()
    q = quote_plus(raw_query)
    if not q:
        return "What should I search for?"
    engine = (engine or "google").lower()
    if engine in ("spotify",):
        return search_spotify(query)
    if engine in ("youtube", "yt"):
        url = f"https://www.youtube.com/results?search_query={q}"
        site = "youtube"
    else:
        url = f"https://www.google.com/search?q={q}"
        site = "google"
    session.remember_entity("query", raw_query)

    # If a browser tab is already open, reuse it via the address bar instead
    # of spawning another tab every time the person says "search X".
    from control import navigate_url_in_tab

    if navigate_url_in_tab(url):
        session.set_last_site(site)
        session.set_last_app("browser")
        return f"Opened {url}"
    return open_url(url)


def search_spotify(query: str) -> str:
    """Open Spotify's search for a song/artist — not YouTube."""
    q = (query or "").strip()
    if not q:
        return "What should I play on Spotify?"
    return play_spotify(q)


_VAGUE_SONG = {
    "any",
    "any song",
    "a song",
    "some song",
    "some music",
    "music",
    "a random song",
    "random",
    "random song",
    "something",
    "whatever",
    "whatever song",
    "anything",
    "anything song",
    "a track",
    "any track",
}

# Public editorial playlist so "play any song" actually starts audio.
_SPOTIFY_MIX = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"


def _spotify_click_result() -> None:
    from control import click_spotify_first_result, snapshot_focus

    click_spotify_first_result()
    try:
        snapshot_focus()
    except Exception:
        pass


def play_spotify(query: str = "") -> str:
    """Search (or open a mix) on the Spotify app and start the first result track."""
    q = (query or "").strip()
    low = q.lower()
    vague = (not q) or low in _VAGUE_SONG or bool(
        re.match(r"^(any|some|a|random)\s+(songs?|tracks?|music|tunes?)$", low)
    )
    session.set_last_app("spotify")
    session.set_last_site("spotify")
    try:
        if vague:
            os.startfile(_SPOTIFY_MIX)
            spoken = "Playing a mix on Spotify."
        else:
            session.remember_entity("query", q)
            os.startfile("spotify:search:" + quote_plus(q))
            spoken = f"Playing {q} on Spotify."
    except OSError:
        try:
            os.startfile("spotify:")
            time.sleep(1.2)
            if vague:
                os.startfile(_SPOTIFY_MIX)
                spoken = "Playing a mix on Spotify."
            else:
                session.remember_entity("query", q)
                os.startfile("spotify:search:" + quote_plus(q))
                spoken = f"Playing {q} on Spotify."
        except OSError:
            webbrowser.open("https://open.spotify.com")
            return "Opened Spotify in the browser. Pick a song there — the app isn't installed."
    time.sleep(2.8)
    try:
        _spotify_click_result()
    except Exception:
        pass
    return spoken


_APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "ms paint": "mspaint",
    "mspaint": "mspaint",
    "microsoft paint": "mspaint",
    "explorer": "explorer",
    "files": "explorer",
    "file explorer": "explorer",
    "settings": "ms-settings:",
    "chrome": "chrome",
    "edge": "msedge",
    "brave": "brave",
    "brave browser": "brave",
    "spotify": "spotify",
    "code": "code",
    "cursor": "cursor",
    "cmd": "cmd",
    "command prompt": "cmd",
    "powershell": "powershell",
    "terminal": "wt",
    "task manager": "taskmgr",
    "taskmgr": "taskmgr",
    "control panel": "control",
    "snipping tool": "snippingtool",
    "photos": "ms-photos:",
    "camera": "microsoft.windows.camera:",
    "wordpad": "wordpad",
    "discord": "discord",
    "steam": "steam",
    "vlc": "vlc",
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
}

_KNOWN_PATHS = {
    "discord": os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe"),
}


def _launch_known(key: str) -> bool:
    path = _KNOWN_PATHS.get(key)
    if path and os.path.exists(path):
        try:
            if key == "discord":
                subprocess.Popen([path, "--processStart", "Discord.exe"], shell=False)
            else:
                subprocess.Popen([path], shell=False)
            return True
        except Exception:
            pass
    if key == "discord":
        try:
            os.startfile("discord:")
            return True
        except OSError:
            return False
    return False


def open_app(name: str) -> str:
    """Launch a local Windows app by friendly name."""
    key = (name or "").strip().lower()
    if key in ("whatsapp", "whats app"):
        try:
            os.startfile("whatsapp:")
            session.set_last_app("whatsapp")
            session.set_last_site("whatsapp")
            return f"Opened {name}"
        except OSError:
            return open_url("https://web.whatsapp.com")
    if key == "discord" and _launch_known("discord"):
        session.set_last_app("discord")
        session.set_last_site("discord")
        return f"Opened {name}"
    target = _APPS.get(key) or shutil.which(key) or shutil.which(key + ".exe")
    if not target:
        return (
            f"I don't have {name} on the app list. Try Chrome, Notepad, "
            "Calculator, Explorer, Spotify, Paint, Brave, WhatsApp, or Settings."
        )
    try:
        os.startfile(target)
        if "paint" in key:
            session.set_last_app("paint")
        elif "whatsapp" in key:
            session.set_last_app("whatsapp")
            session.set_last_site("whatsapp")
        elif key == "spotify":
            session.set_last_app("spotify")
            session.set_last_site("spotify")
        elif key in ("settings",):
            session.set_last_app("settings")
            session.set_last_site("")
        else:
            session.set_last_app(key)
        return f"Opened {name}"
    except OSError:
        if os.path.exists(target) or shutil.which(target):
            try:
                subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
                session.set_last_app(key)
                return f"Opened {name}"
            except Exception as e:
                return f"Couldn't open {name}: {e}"
        return (
            f"{name} isn't installed where I expected. "
            "Tell me its exe path and I'll remember it."
        )


def open_anything(target: str, browser: str | None = None) -> str:
    """Open an app, a known site, or guess https://name.com."""
    t = (target or "").strip().lower()
    t = re.sub(r"\b(website|site|app|application|page)\b", "", t)
    t = " ".join(t.split())
    browser = (browser or "").strip().lower() or None
    m = re.search(r"\s+in\s+(brave|chrome|edge|msedge)$", t)
    if m:
        browser = m.group(1)
        t = t[: m.start()].strip()
    if t in _APPS or t.replace(" ", "") in {a.replace(" ", "") for a in _APPS}:
        return open_app(t)
    sites = {
        "youtube": "https://youtube.com",
        "google": "https://google.com",
        "github": "https://github.com",
        "gmail": "https://mail.google.com",
        "leetcode": "https://leetcode.com",
        "leet code": "https://leetcode.com",
        "whatsapp": "https://web.whatsapp.com",
        "whats app": "https://web.whatsapp.com",
        "spotify": "https://open.spotify.com",
        "twitter": "https://x.com",
        "reddit": "https://reddit.com",
        "linkedin": "https://linkedin.com",
        "netflix": "https://netflix.com",
        "instagram": "https://instagram.com",
        "facebook": "https://facebook.com",
        "amazon": "https://amazon.com",
        "maps": "https://maps.google.com",
        "wikipedia": "https://wikipedia.org",
    }
    if t in sites:
        return open_url(sites[t], browser)
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}", t):
        return open_url(t, browser)
    token = t.replace(" ", "")
    if token and " " not in t:
        return open_url(token + ".com", browser)
    if token:
        return open_url("https://www.google.com/search?q=" + quote_plus(t), browser)
    return f"I don't know how to open {target}."


def message_contact(who: str, app: str = "whatsapp") -> str:
    who = (who or "").strip()
    if not who:
        return "Who should I message?"
    session.remember_entity("contact", who)
    if app in ("whatsapp", "whats app"):
        result = open_app("whatsapp")
        time.sleep(1.4)
        try:
            from control import snapshot_focus, press, type_text

            snapshot_focus()
            press("ctrl", "f")
            time.sleep(0.25)
            type_text(who)
        except Exception:
            pass
        return f"Opened WhatsApp and searched for {who}. Say the message and I'll type it."
    return open_anything(app)


def draw_circle_paint() -> str:
    msg = open_app("paint")
    time.sleep(1.3)
    try:
        from control import draw_circle

        return draw_circle()
    except Exception:
        return msg if msg.lower().startswith("opened") else "Opened Paint. Draw with the mouse."


def _key(*vk_codes: int) -> None:
    user32 = ctypes.windll.user32
    for code in vk_codes:
        user32.keybd_event(code, 0, 0, 0)
    for code in reversed(vk_codes):
        user32.keybd_event(code, 0, 2, 0)


def pc_action(action: str) -> str:
    """Windows OS shortcuts: volume, mute, lock, screenshot, desktop, close."""
    act = (action or "").strip().lower().replace(" ", "_")
    if act in ("volume_up", "vol_up", "louder"):
        for _ in range(6):
            _key(0xAF)
        return "Turned the volume up."
    if act in ("volume_down", "vol_down", "quieter"):
        for _ in range(6):
            _key(0xAE)
        return "Turned the volume down."
    if act in ("mute", "unmute"):
        _key(0xAD)
        return "Toggled mute."
    if act in ("lock", "lock_pc", "lock_computer"):
        ctypes.windll.user32.LockWorkStation()
        return "Locked the PC."
    if act in ("screenshot", "screen_shot", "capture"):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        path = os.path.join(desktop, time.strftime("AREIS-%Y%m%d-%H%M%S.png"))
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(path)
            return f"Saved a screenshot on your Desktop as {os.path.basename(path)}."
        except Exception:
            _key(0x2C)
            return "Copied a screenshot. Paste it with Control V."
    if act in ("task_manager", "taskmgr"):
        return open_app("task manager")
    if act in ("desktop", "show_desktop", "minimize_all"):
        _key(0x5B, 0x44)
        return "Showing the desktop."
    if act in ("close", "close_window"):
        _key(0x12, 0x73)
        return "Closed the front window."
    if act in ("next_track", "next"):
        _key(0xB0)
        return "Next track."
    if act in ("prev_track", "previous"):
        _key(0xB1)
        return "Previous track."
    if act in ("play", "pause", "play_pause"):
        _key(0xB3)
        return "Toggled play and pause."
    return f"I don't have a PC action named {action}."


def browse(action: str, extra: str = "") -> str:
    return navigate(action, extra)


def type_in(text: str) -> str:
    app = session.last_app()
    if app == "discord" or not app:
        try:
            from control import discord_prepare_compose

            if discord_prepare_compose():
                return type_text(text, restore=False)
        except Exception:
            pass
    if app:
        try:
            focus_app(app)
        except Exception:
            pass
    return type_text(text, restore=False)


def type_and_send(text: str) -> str:
    """Jump to the remembered contact if needed, then type in compose and Enter."""
    msg = (text or "").strip().strip("\"'")
    if not msg:
        return "What should I type?"
    from control import discord_prepare_compose, discord_jump_to, press, snapshot_focus

    app = (session.last_app() or "discord").strip().lower()
    who = session.last_entity("contact")
    if app in ("discord", "") or "discord" in app or who:
        if who:
            focus_app("discord")
            discord_jump_to(who)
        discord_prepare_compose()
        app = "discord"
    else:
        needle = "whatsapp" if "whatsapp" in app else app
        if not focus_app(needle):
            discord_prepare_compose()
        else:
            time.sleep(0.15)
    type_text(msg, restore=False)
    time.sleep(0.12)
    press("enter", restore=False)
    try:
        snapshot_focus()
    except Exception:
        pass
    return f"Sent: {msg}"


def discord_message(who: str, text: str) -> str:
    """Open Discord, search DMs for who, type the message, send."""
    who = (who or "").strip()
    msg = (text or "").strip().strip("\"'")
    if not who:
        return "Who should I message?"
    opened = open_chat(who, "discord")
    if not msg:
        return opened
    from control import discord_prepare_compose, press, snapshot_focus

    discord_prepare_compose()
    type_text(msg, restore=False)
    time.sleep(0.12)
    press("enter", restore=False)
    try:
        snapshot_focus()
    except Exception:
        pass
    return f"Sent to {who}: {msg}"


def search_settings(query: str) -> str:
    return search_in_settings(query)


def click_on(label: str) -> str:
    return click_label(label)


def click_result(n: int, zone: str = "") -> str:
    return click_nth(n, zone)


def click_current_video() -> str:
    """Play/pause whatever video is already loaded on screen (not a thumbnail)."""
    from control import click_current_video as _click_current_video

    return _click_current_video()


def replay_last() -> str:
    import session

    act = session.last_action()
    if not act:
        return "Tell me exactly what to click, like click the third video in the sidebar."
    kind, kwargs = act
    if kind == "click_nth":
        return click_nth(**kwargs)
    query = session.last_entity("query")
    if query:
        return search_web(query)
    return "Tell me what to do again."


def open_chat(who: str, app: str = "discord") -> str:
    """Jump to a person/channel in Discord (Ctrl+K) or Instagram DMs."""
    who = re.sub(r"\s+(chat|dm|dms|conversation|channel)$", "", (who or "").strip(), flags=re.I)
    who = who.strip(" .")
    if not who:
        return "Who should I open a chat with?"
    app = (app or "discord").strip().lower()
    if app in ("insta", "ig"):
        app = "instagram"
    session.remember_entity("contact", who)

    from control import discord_jump_to, discord_prepare_compose, press, snapshot_focus, restore_focus

    if app == "discord":
        opened = open_app("discord")
        time.sleep(2.0)
        if not focus_app("discord"):
            return opened if opened.lower().startswith("isn't") else (
                "Discord is open but I couldn't focus its window. Click Discord, then say it again."
            )
        discord_jump_to(who)
        discord_prepare_compose()
        session.set_last_app("discord")
        session.set_last_site("discord")
        try:
            snapshot_focus()
        except Exception:
            pass
        if opened.lower().startswith("isn't installed"):
            return opened
        return f"Opening {who} in Discord."

    if app == "instagram":
        open_url("https://www.instagram.com/direct/inbox/")
        time.sleep(2.4)
        focus_app("instagram") or restore_focus()
        time.sleep(0.3)
        try:
            click_label("Search")
            time.sleep(0.25)
        except Exception:
            press("ctrl", "k")
            time.sleep(0.3)
        type_text(who)
        time.sleep(0.45)
        press("enter")
        session.set_last_app("instagram")
        session.set_last_site("instagram")
        return f"Opening Instagram messages for {who}."

    if app in ("whatsapp", "whats app"):
        return message_contact(who, "whatsapp")
    return f"I can open chats in Discord, Instagram, or WhatsApp — not {app}."


def whats_on_screen() -> str:
    return read_screen()


def scrape_website(url: str, max_chars: int = 4000) -> str:
    """Fetch a page and return readable text (title, headings, body, links)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return f"Could not fetch {url}: {e}"

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""
    headings = [
        h.get_text(" ", strip=True)
        for h in soup.find_all(["h1", "h2", "h3"])
        if h.get_text(strip=True)
    ][:12]
    paragraphs = [
        p.get_text(" ", strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if text and href.startswith("http"):
            links.append(f"{text} -> {href}")
        if len(links) >= 8:
            break

    body = " ".join(paragraphs) or soup.get_text(" ", strip=True)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "…"

    parts = [f"URL: {url}"]
    if title:
        parts.append(f"Title: {title}")
    if headings:
        parts.append("Headings: " + " | ".join(headings))
    parts.append("Content: " + body)
    if links:
        parts.append("Links:\n" + "\n".join(links))
    out = "\n".join(parts)
    try:
        from memory import remember_page

        remember_page(url, out)
    except Exception:
        pass
    return out


def scrape_page(target: str = "") -> str:
    """Scrape a URL, a site name, or the last opened site. Saves into RAG memory."""
    import session

    raw = (target or "").strip()
    if not raw or raw.lower() in ("this", "this page", "the page", "it", "here"):
        site = session.last_site()
        fallback = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "leetcode": "https://leetcode.com",
            "github": "https://github.com",
            "whatsapp": "https://web.whatsapp.com",
            "spotify": "https://open.spotify.com",
        }
        raw = fallback.get(site, "")
        if not raw:
            return "Tell me a URL to scrape, like scrape leetcode.com"
    text = scrape_website(raw, max_chars=3500)
    if text.lower().startswith("could not"):
        return text
    if len(text) > 1200:
        return text[:1200] + "\n…saved the rest to memory. Ask me about this page anytime."
    return text


def connect_provider(provider: str) -> str:
    """Switch the live chat backend. local | openai | anthropic | nvidia."""
    name = session.normalize_provider(provider)
    if name is None:
        return (
            "Unknown provider. Say local, chatgpt/openai, claude/anthropic, "
            "or nvidia/nim."
        )
    if name != "local" and not key_manager.has_usable_key(name):
        if key_manager.has_provider(name):
            return (
                f"Every saved {name} key is marked unusable this session. "
                f"Paste a new key, or restart AREIS to try the old ones again."
            )
        return (
            f"No {name} API key saved yet. Add one in keys.json "
            f"(copy keys.example.json) or say 'set {name} key' followed by the key."
        )
    session.set_provider(name)
    if name == "nvidia":
        return (
            "Connected to NVIDIA NIM. It is slower than Local 7B — "
            "voice commands still run instantly. Tap Local 7B for faster chat."
        )
    labels = {
        "local": "local model",
        "openai": "ChatGPT",
        "anthropic": "Claude",
        "nvidia": "NVIDIA NIM",
    }
    return f"Connected to {labels[name]}. Talk normally — I'll use it until you switch."


def list_providers() -> str:
    current = session.get_provider()
    rows = []
    for name, label in (
        ("local", "local model"),
        ("openai", "ChatGPT"),
        ("anthropic", "Claude"),
        ("nvidia", "NVIDIA NIM"),
    ):
        if name == "local":
            ready = "ready"
        elif key_manager.has_usable_key(name):
            ready = "ready"
        elif key_manager.has_provider(name):
            ready = "keys marked bad"
        else:
            ready = "no key"
        mark = " (active)" if name == current else ""
        rows.append(f"{label}: {ready}{mark}")
    return "\n".join(rows)


def set_api_key(provider: str, key: str) -> str:
    """Save a cloud key and connect immediately."""
    key = (key or "").strip().strip('"').strip("'")
    if key.startswith("nvapi-"):
        name = "nvidia"
    else:
        name = session.normalize_provider(provider)
    if name not in ("openai", "anthropic", "nvidia"):
        return "I can store keys for ChatGPT, Claude, or NVIDIA (nvapi-)."
    if len(key) < 12:
        return "That doesn't look like a full API key. Try pasting it in text mode."
    labels = {"openai": "ChatGPT", "anthropic": "Claude", "nvidia": "NVIDIA NIM"}
    added = key_manager.add_key(name, key)
    if not added:
        return (
            f"That {labels[name]} key was not added — it may already be saved. "
            "I won't repeat the key."
        )
    extra = ""
    if name == "nvidia":
        extra = " Tap NVIDIA when you want the cloud model — Local 7B stays faster for talk."
    else:
        session.set_provider(name)
        extra = f" Connected to {labels[name]}."
    return f"Saved the {labels[name]} key.{extra} I won't repeat the key."


# --- Schemas the model sees ----------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current date and time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the default web browser. Use this for "
                            "requests like 'open YouTube' or 'open github.com'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to open, e.g. https://youtube.com",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open a Windows app on this PC: notepad, calculator, "
                            "explorer, chrome, edge, spotify, settings, cursor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "App name, e.g. notepad, chrome, calculator",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search Google, YouTube, or Spotify and open the results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text"},
                    "engine": {
                        "type": "string",
                        "description": "google, youtube, or spotify",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pc_action",
            "description": "Control this Windows PC: volume_up, volume_down, mute, "
                            "lock, screenshot, task_manager, desktop, close, "
                            "play_pause, next_track, prev_track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "PC action name"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": "Control the frontmost app without a mouse. Use this for "
                            "navigation/UI actions that are not covered by a more "
                            "specific tool (open_url, click_result, click_on, "
                            "click_current_video, type_in).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "scroll_down", "scroll_up", "enter", "back", "forward",
                            "tab", "prev", "escape", "space", "refresh", "new_tab",
                            "close_tab", "next_tab", "address", "fullscreen",
                            "maximize", "minimize", "next_video", "search",
                            "result_1", "result_2", "result_3", "result_4",
                            "result_5", "click_label", "type", "read",
                        ],
                        "description": (
                            "search: search the frontmost app/site (needs extra=query). "
                            "result_N: click the Nth video/result on screen. "
                            "click_label: click a control by visible name (needs extra=label). "
                            "type: type text into the focused field (needs extra=text)."
                        ),
                    },
                    "extra": {
                        "type": "string",
                        "description": "Search query, click label, or text to type — "
                                        "only needed for search/click_label/type/result_N.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_in",
            "description": "Type text into the focused field of the front app.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_and_send",
            "description": "Type a chat message into Discord's bottom compose box "
                            "and press Enter. Not the top search bar.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_chat",
            "description": "Open a person's chat in Discord, Instagram, or WhatsApp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "Person or channel name"},
                    "app": {
                        "type": "string",
                        "description": "discord, instagram, or whatsapp",
                    },
                },
                "required": ["who"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_result",
            "description": "Click the nth video/result. zone=sidebar for YouTube "
                            "recommended list on the right, zone=feed for main results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "1 is the first item"},
                    "zone": {
                        "type": "string",
                        "description": "sidebar, recommended, feed, or empty",
                    },
                },
                "required": ["n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_current_video",
            "description": "Play or pause the video already loaded/paused on screen "
                            "right now — not a thumbnail from a list of search results. "
                            "Use for commands like 'play the video', 'play this'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_on",
            "description": "Click a visible control whose name matches label.",
            "parameters": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whats_on_screen",
            "description": "Read the front window title and visible UI names.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_website",
            "description": "Download a web page and extract readable text, "
                            "headings, and links. Use when the user wants you "
                            "to read, summarize, or scrape a site.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Page URL, e.g. https://example.com",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_page",
            "description": "Scrape a URL or the last opened site and save text to memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "URL, site name, or empty for the last site",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "message_contact",
            "description": "Open WhatsApp and search for a person to message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "Contact name"},
                    "app": {"type": "string", "description": "whatsapp"},
                },
                "required": ["who"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_provider",
            "description": "Switch which AI answers: local, openai (ChatGPT), "
                            "anthropic (Claude), or nvidia (NIM/Nemotron). "
                            "Use when the user says connect to ChatGPT/Claude/"
                            "NVIDIA or go back to local.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "local, openai, anthropic, or nvidia",
                    }
                },
                "required": ["provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_providers",
            "description": "Show which AIs have keys and which one is active.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_api_key",
            "description": "Save a ChatGPT (openai), Claude (anthropic), or "
                            "NVIDIA (nvidia / nvapi-) API key and connect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "openai, anthropic, or nvidia",
                    },
                    "key": {
                        "type": "string",
                        "description": "The API key to store",
                    },
                },
                "required": ["provider", "key"],
            },
        },
    },
]

TOOL_REGISTRY = {
    "get_time": get_time,
    "open_url": open_url,
    "open_app": open_app,
    "search_web": search_web,
    "pc_action": pc_action,
    "browse": browse,
    "type_in": type_in,
    "type_and_send": type_and_send,
    "discord_message": discord_message,
    "open_chat": open_chat,
    "click_on": click_on,
    "click_result": click_result,
    "click_current_video": click_current_video,
    "replay_last": replay_last,
    "whats_on_screen": whats_on_screen,
    "scrape_website": scrape_website,
    "scrape_page": scrape_page,
    "message_contact": message_contact,
    "search_spotify": search_spotify,
    "play_spotify": play_spotify,
    "connect_provider": connect_provider,
    "list_providers": list_providers,
    "set_api_key": set_api_key,
}