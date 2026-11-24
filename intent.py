"""
Fast path for spoken/typed commands so common actions skip the model.

try_direct() runs first in agent.handle_message. If it returns a string,
the LLM is not called. Keep patterns tight so homework/error talk still
reaches chat via wants_chat().
"""
from __future__ import annotations

import re
import time

from persona import listen_line, open_line, search_line
from tools import (
    browse,
    click_current_video,
    click_on,
    click_result,
    connect_provider,
    get_time,
    list_providers,
    open_app,
    open_anything,
    open_chat,
    open_url,
    pc_action,
    replay_last,
    search_settings,
    search_spotify,
    play_spotify,
    search_web,
    set_api_key,
    type_in,
    type_and_send,
    whats_on_screen,
    draw_circle_paint,
    message_contact,
    scrape_page,
)

_QUIT = {
    "quit areis",
    "exit areis",
    "goodbye areis",
    "good bye areis",
    "goodbye",
    "good bye",
    "shut down areis",
    "stop areis",
}

_PAUSE_LISTEN = {
    "stop listening",
    "pause listening",
    "go to sleep",
    "mute yourself",
}

_SITES = {
    "youtube": "https://youtube.com",
    "you tube": "https://youtube.com",
    "yt": "https://youtube.com",
    "google": "https://google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "reddit": "https://reddit.com",
    "wikipedia": "https://wikipedia.org",
    "wiki": "https://wikipedia.org",
    "netflix": "https://netflix.com",
    "instagram": "https://instagram.com",
    "whatsapp": "https://web.whatsapp.com",
    "leetcode": "https://leetcode.com",
    "leet code": "https://leetcode.com",
    "facebook": "https://facebook.com",
    "linkedin": "https://linkedin.com",
    "amazon": "https://amazon.com",
    "maps": "https://maps.google.com",
    "spotify web": "https://open.spotify.com",
    "discord web": "https://discord.com/app",
}

_APPS = {
    "notepad",
    "calculator",
    "calc",
    "paint",
    "ms paint",
    "mspaint",
    "microsoft paint",
    "brave",
    "brave browser",
    "whatsapp",
    "whats app",
    "explorer",
    "files",
    "file explorer",
    "settings",
    "chrome",
    "edge",
    "spotify",
    "code",
    "cursor",
    "cmd",
    "command prompt",
    "powershell",
    "terminal",
    "task manager",
    "control panel",
    "snipping tool",
    "photos",
    "camera",
    "wordpad",
    "discord",
    "steam",
    "vlc",
}

_FILLER = re.compile(
    r"^(?:hey |hi |hello |okay |ok |alright |all right )?(?:areis|aries|iris|jarvis)\b[, ]*"
    r"|(?:please |can you |could you |would you |will you "
    r"|i want you to |i need you to |i want to |i want |go ahead and "
    r"|stop talking and |shut up and |alright |all right |okay |ok )+",
    re.I,
)
_IGNORE = {
    "",
    "areis",
    "aries",
    "iris",
    "jarvis",
    "hey",
    "ehh",
    "eh",
    "uh",
    "um",
    "hmm",
    "ah",
    "ahh",
    "huh",
    "yeah",
    "yep",
    "ok",
    "okay",
}
_TAIL = re.compile(r"\b(?:for me|please|now|thanks|thank you)\s*$", re.I)
_URL = re.compile(
    r"(https?://[^\s]+|(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?)", re.I
)


def is_quit(text: str) -> bool:
    return text.lower().strip().rstrip(".!") in _QUIT


def is_pause_listen(text: str) -> bool:
    return _clean(text) in _PAUSE_LISTEN


def _clean(text: str) -> str:
    t = text.strip().lower()
    t = t.replace("ms paint", "paint")
    t = t.replace("mspaint", "paint")
    t = t.replace("leet code", "leetcode")
    t = t.replace("whats app", "whatsapp")
    t = t.replace("insurance search", "in settings search")
    t = t.replace("in setting search", "in settings search")
    for a, b in (
        ("school down", "scroll down"),
        ("stroll down", "scroll down"),
        ("scrolled down", "scroll down"),
        ("scrolling down", "scroll down"),
        ("scroll it down", "scroll down"),
        ("skroll down", "scroll down"),
        ("scrawl down", "scroll down"),
        ("scroll it up", "scroll up"),
        ("scrolled up", "scroll up"),
        ("page down", "page down"),
        ("page up", "page up"),
        ("you tube", "youtube"),
        ("open up ", "open "),
    ):
        t = t.replace(a, b)
    t = re.sub(r"[.!?]+$", "", t).strip()
    while True:
        nxt = _FILLER.sub("", t, count=1).strip()
        if nxt == t:
            break
        t = nxt
    t = re.sub(r"^i(?:'?ve| have) reached (?:her|him|them)[, ]*", "", t).strip()
    t = re.sub(r"^now[, ]+", "", t).strip()
    t = _TAIL.sub("", t).strip(" ,")
    return t


def _friendly_open(result: str, spoken: str) -> str:
    return spoken if result.lower().startswith("opened") else result


def _youtube_search(q: str) -> str:
    q = re.sub(r"\s+on youtube$", "", (q or "").strip(), flags=re.I).strip(" ,")
    if not q:
        return open_url(_SITES["youtube"])
    opened = _friendly_open(search_web(q, "youtube"), search_line("YouTube", q))
    time.sleep(2.1)
    from control import play_youtube_first

    played = play_youtube_first()
    if played:
        return f"Playing {q} on YouTube."
    return opened + " Say click the first video if it didn't start."


def _spotify_search(q: str) -> str:
    q = re.sub(r"\s+on\s+spotify$", "", (q or "").strip(), flags=re.I).strip(" ,")
    if not q:
        return open_app("spotify")
    result = play_spotify(q)
    if result.lower().startswith("playing"):
        return result
    return _friendly_open(result, search_line("Spotify", q))


def _strip_place(q: str) -> str:
    q = re.sub(
        r"\s+(?:on|in|with)\s+(?:spotify|youtube|you tube|yt|google)\s*$",
        "",
        q or "",
        flags=re.I,
    )
    return q.strip(" ,")


def _place(t: str, title_hint: str = "") -> str | None:
    if re.search(r"\bspotify\b", t) and not re.search(r"\b(youtube|you tube)\b", t):
        return "spotify"
    if re.search(r"\b(youtube|you tube|\byt\b)\b", t):
        return "youtube"
    import session

    if (
        session.last_app() == "spotify"
        or session.last_site() == "spotify"
        or "spotify" in (title_hint or "")
    ):
        return "spotify"
    if session.last_site() == "youtube" or "youtube" in (title_hint or ""):
        return "youtube"
    return None


def _resolve_reference(t: str) -> str:
    """Rewrite a narrow set of pronoun patterns using recently remembered
    entities, so 'play that on spotify' or 'message him too' don't have to
    be spelled out again. Deliberately narrow: patterns that already have
    dedicated literal handling elsewhere (e.g. 'click it', 'do it again')
    are untouched, since those already mean something specific."""
    import session

    m = re.match(
        r"^(play|watch|search(?:\s+for)?|find|look up)\s+"
        r"(?:that|it|this)(?:\s+song|\s+track|\s+one)?(\s+(?:on|in)\s+(?:spotify|youtube))?$",
        t,
    )
    if m:
        if m.group(1) == "play":
            return t
        query = session.last_entity("query")
        if query:
            return f"{m.group(1)} {query}{m.group(2) or ''}"

    m = re.match(
        r"^(message|text|send)\s+(?:a message\s+)?(?:to\s+)?"
        r"(him|her|them|that person)\b(.*)$",
        t,
    )
    if m:
        contact = session.last_entity("contact")
        if contact:
            return f"{m.group(1)} {contact}{m.group(3) or ''}".strip()

    return t


def try_direct(text: str, _depth: int = 0) -> str | None:
    """Return a reply if this can be handled without the LLM, else None."""
    raw = text.strip()
    t = _clean(raw)
    if t in _IGNORE:
        return listen_line()
    t = _resolve_reference(t)

    m = re.search(
        r"(?:message|dm|text|chat with)\s+(.+?)\s+(?:on|in)\s+"
        r"(discord|instagram|insta|ig|whatsapp)(?:\s+(.*))?$",
        t,
    )
    if m:
        who = m.group(1).strip(" ,")
        app = m.group(2)
        rest = (m.group(3) or "").strip()
        rest = re.sub(r"^(?:and\s+)?(?:then\s+)?(?:type|say|send|tell them)\s+", "", rest)
        rest = re.sub(r"\s+and\s+(?:then\s+)?(?:hit |press |click )?(?:send|enter)$", "", rest)
        rest = rest.strip(" ,")
        if who and who not in ("him", "her", "them"):
            from tools import discord_message, open_chat, type_and_send

            if app in ("insta", "ig"):
                app = "instagram"
            if rest and rest not in ("send", "enter"):
                if app == "discord":
                    return discord_message(who, rest)
                opened = open_chat(who, app)
                return opened + " " + type_and_send(rest)
            return open_chat(who, app)

    m = re.search(
        r"\b(?:type|enter text)\s+(.+?)(?:\s+in\s+(?:her |his |the |their )?"
        r"(?:chat|dm|message(?:\s+box)?|discord|whatsapp|instagram))?"
        r"(?:\s+and\s+(?:then\s+)?(?:hit |press |click )?(?:send|enter))?$",
        t,
    )
    if m and not re.search(r"\b(message|dm|text)\s+.+\s+on\s+discord\b", t):
        msg = m.group(1).strip(" ,")
        msg = re.sub(r"\s+and\s+(?:hit |press |click )?(?:send|enter)$", "", msg).strip()
        if msg and not re.search(r"\b(python|java|code|function|leetcode)\b", msg):
            if re.search(r"\b(send|hit enter|press enter)\b", t):
                return type_and_send(msg)
            return type_in(msg)

    # Solving a LeetCode/code problem is chat, not "open leetcode" / search.
    if re.search(
        r"\b(leetcode|leet code)\b.*\b(\d+|problem|java|python|solve|solution)\b"
        r"|\b(solve|write|implement)\b.*\b(leetcode|code|algorithm|linked list)\b",
        t,
    ) and not re.search(r"\b(open|launch|go to|pull up)\b", t):
        return None

    if _depth < 2:
        m = re.match(
            r"^(maximize|maximise|minimize|minimise)\s+"
            r"(?:and\s+|then\s+)?(?:open|launch|start)\s+(.+)$",
            t,
        )
        if m:
            first = browse("maximize" if "max" in m.group(1) else "minimize")
            second = try_direct("open " + m.group(2), _depth + 1)
            return " ".join(x for x in (first, second) if x)
        if re.search(r"\s+and\s+", t) and not re.search(
            r"youtube.*\bsearch\b|\bsearch\b.*youtube|\btype\b.*\bsend\b", t
        ):
            left, right = re.split(r"\s+and\s+", t, maxsplit=1)
            if re.match(r"^(open|launch|start|maximize|maximise|send|search|draw|call|email)", left) or re.match(
                r"^(open|send|search|type|play|draw|message|call|email)", right
            ):
                a = try_direct(left, _depth + 1)
                b = try_direct(right, _depth + 1)
                joined = " ".join(x for x in (a, b) if x)
                if joined:
                    return joined

    if t in ("what time is it", "what's the time", "whats the time", "time", "date"):
        return get_time()

    if t in ("who are you using", "which model", "which ai", "list providers", "status"):
        return list_providers()

    m = re.search(
        r"(?:set|save|add)\s+(chatgpt|openai|gpt|claude|anthropic|nvidia|nim|nemotron|nvdia)\s+(?:api\s+)?key\s+(.+)",
        raw,
        re.I,
    )
    if m:
        return set_api_key(m.group(1), m.group(2))

    m = re.search(
        r"^(?:connect(?: to)?|switch to|talk to|use)\s+"
        r"(chatgpt|chat gpt|openai|gpt|claude|anthropic|nvidia|nim|nemotron|nvdia|local|llama)\b",
        t,
        re.I,
    )
    if m:
        return connect_provider(m.group(1))

    if t in ("go local", "use local", "disconnect", "back to local", "local model", "use the local model"):
        return connect_provider("local")
    if t in (
        "go nvidia",
        "use nvidia",
        "nvidia mode",
        "use nim",
        "switch to nvidia",
        "talk to nvidia",
        "use the cloud",
        "cloud model",
    ):
        return connect_provider("nvidia")

    m = re.search(
        r"^(?:send\s+(?:an\s+)?)?email(?:\s+to)?\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        t,
    )
    if m:
        target_email = m.group(1)
        open_url(f"mailto:{target_email}")
        return f"Opening your mail client for {target_email}."

    m = re.search(r"(?:call|voice call|message|dm|text)\s+(.+?)\s+on\s+discord$", t)
    if m:
        from tools import open_chat

        return open_chat(m.group(1).strip(), "discord")

    m = re.search(
        r"^(?:open|go to|pull up|bring up|launch|show)\s+"
        r"(.+?)\s+(?:'s\s+)?(?:chat|dm|dms|conversation|thread|channel)\s+"
        r"(?:in|on|from)\s+(discord|instagram|insta|ig|whatsapp)$",
        t,
    )
    if m:
        from tools import open_chat

        return open_chat(m.group(1).strip(), m.group(2))

    m = re.search(
        r"^(?:open|go to|pull up|launch)\s+"
        r"(discord|instagram|insta|ig|whatsapp)\s+"
        r"(?:chat(?:\s+with)?|dm|conversation)?\s+(.+)$",
        t,
    )
    if m:
        from tools import open_chat

        return open_chat(m.group(2).strip(), m.group(1))

    m = re.search(
        r"^(?:message|dm|text|chat with)\s+(.+?)\s+(?:in|on)\s+"
        r"(discord|instagram|insta|ig|whatsapp)$",
        t,
    )
    if m:
        from tools import open_chat

        return open_chat(m.group(1).strip(), m.group(2))

    if re.search(
        r"\b((can you |can )?see|(look at)|(read))\b.*\bscreen\b"
        r"|\bwhat'?s on (my |the )?screen\b"
        r"|\bwhat do you see\b"
        r"|\bsee my screen\b",
        t,
    ):
        return whats_on_screen()

    if t in (
        "what's on screen",
        "whats on screen",
        "what's on my screen",
        "whats on my screen",
        "what do you see",
        "read the screen",
        "read screen",
        "see my screen",
        "see the screen",
        "look at my screen",
        "look at the screen",
    ):
        return whats_on_screen()

    if re.search(r"\bdraw\b.*\b(circle|oval|ellipse)\b|\b(circle|oval)\b.*\bpaint\b", t):
        return _friendly_open(draw_circle_paint(), "Opening Paint and drawing a circle.")

    m = re.search(
        r"^(?:scrape|read|summarize|fetch)\s+(?:the\s+)?(?:page|site|website|article|url)?\s*(.*)$",
        t,
    )
    if m or t in ("scrape this", "read this page", "summarize this page", "scrape"):
        q = (m.group(1).strip() if m else "") or "this"
        if q in ("page", "site", "website", "article", "url"):
            q = "this"
        return scrape_page(q)

    m = re.search(
        r"(?:send|message|text)\s+(?:a\s+)?(?:message\s+)?(?:to\s+)?(.+)$",
        t,
    )
    if m and (
        "whatsapp" in t
        or t.startswith(("send ", "message ", "text "))
    ):
        who = re.sub(r"\s+on\s+whatsapp$", "", m.group(1).strip())
        who = re.sub(r"^message\s+to\s+", "", who).strip()
        if who and who not in ("this", "it", "a message", "him", "her", "them"):
            return message_contact(who)

    if "instagram" in t and "scroll" in t:
        open_url("https://instagram.com")
        return browse("scroll_down")

    if re.search(r"\b(scroll|scrolled|scrolling|skroll|scrawl|stroll)(?:ing|ed)?\b", t):
        return browse("scroll_up" if re.search(r"\bup\b", t) else "scroll_down")
    if t in ("scroll down", "scroll", "page down", "go down", "scroll more", "keep scrolling"):
        return browse("scroll_down")
    if t in ("scroll up", "page up", "go up"):
        return browse("scroll_up")
    if re.search(r"\bpage\s*down\b|\bgo\s+down\b|\bmove\s+down\b|\bdown\s+(a\s+)?(bit|little|more)\b", t):
        if "download" not in t and "shut down" not in t:
            return browse("scroll_down")
    if re.search(r"\bpage\s*up\b|\bgo\s+up\b|\bmove\s+up\b|\bup\s+(a\s+)?(bit|little|more)\b", t):
        if "volume up" not in t:
            return browse("scroll_up")
    if t in ("go back", "back", "previous page"):
        return browse("back")
    if t in ("go forward", "forward"):
        return browse("forward")
    if t in ("press enter", "enter", "select this", "open this", "click this"):
        return browse("enter")
    if t in ("tab", "next item", "next field"):
        return browse("tab")
    if t in ("previous item", "previous field"):
        return browse("prev")
    if t in ("escape", "press escape", "go back out"):
        return browse("escape")
    if t in ("refresh", "reload", "reload page"):
        return browse("refresh")
    if t in ("new tab", "open a new tab"):
        return browse("new_tab")
    if t in ("close tab", "close this tab"):
        return browse("close_tab")
    if t in ("next tab",):
        return browse("next_tab")
    if t in (
        "fullscreen",
        "full screen",
        "make it fullscreen",
        "maximize",
        "maximise",
        "maximize window",
        "maximise window",
        "maximize the window",
        "maximise the window",
        "maximize it",
        "maximise it",
        "make it bigger",
        "make the window bigger",
    ):
        if "full" in t:
            return browse("fullscreen")
        return browse("maximize")
    if t in ("minimize", "minimise", "minimize window", "minimise the window"):
        return browse("minimize")
    if t in ("next video", "skip video", "skip this video"):
        return browse("next_video")
    if t in ("address bar", "url bar"):
        return browse("address")

    if t in (
        "play the video",
        "play this video",
        "play video",
        "click the video",
        "click this video",
        "click on the video",
        "the video on screen",
        "the video that's playing",
        "the video thats playing",
        "resume the video",
        "start the video",
        "play the video that's on screen",
    ):
        return click_current_video()

    if t in (
        "top video",
        "the top video",
        "video at the top",
        "play the top video",
        "click the top video",
    ):
        return click_result(1, "top")
    if t in (
        "bottom video",
        "the bottom video",
        "last video",
        "video at the bottom",
        "play the bottom video",
        "click the bottom video",
        "click the last video",
    ):
        return click_result(1, "bottom")

    if t in (
        "nothing happened",
        "nothing happen",
        "nothing happened.",
        "it didn't work",
        "it didnt work",
        "didn't work",
        "didnt work",
        "try again",
        "do it",
        "do it again",
        "click it",
        "do that again",
    ):
        return replay_last()

    m = re.search(
        r"(?:(?:click|select|play|open|press)\s+(?:on\s+)?)?(?:the\s+)?"
        r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)"
        r"\s+(one|video|result|item|link|thumbnail)"
        r"(?:\s+(?:in|on|from)\s+(?:the\s+)?"
        r"(sidebar|side bar|right|recommended|recommendations))?",
        t,
    )
    if m:
        n = {
            "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3,
            "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
        }[m.group(1)]
        zone = (m.group(3) or "").replace(" ", "")
        return click_result(n, zone)

    m = re.search(r"^(?:click|press|tap)\s+(?:on\s+)?(.+)$", t)
    if m and m.group(1) not in ("this", "it"):
        return click_on(m.group(1).strip())

    m = re.search(r"^(?:type|write|enter text)\s+(.+)$", t)
    if m:
        msg = m.group(1).strip()
        if re.search(r"\b(send|hit enter|press enter)\b", t):
            return type_and_send(re.sub(r"\s+and\s+(?:hit |press )?send$", "", msg).strip())
        return type_in(msg)

    if t in ("volume up", "turn it up", "louder", "increase volume"):
        return pc_action("volume_up")
    if t in ("volume down", "turn it down", "quieter", "decrease volume"):
        return pc_action("volume_down")
    if t in ("mute", "unmute", "mute volume", "unmute volume"):
        return pc_action("mute")
    if t in ("lock", "lock pc", "lock the pc", "lock computer", "lock my computer", "lock the computer"):
        return pc_action("lock")
    if t in ("screenshot", "take a screenshot", "capture screen", "take screenshot"):
        return pc_action("screenshot")
    if t in ("show desktop", "go to desktop", "minimize everything"):
        return pc_action("desktop")
    if t in ("close", "close window", "close this", "close this window"):
        return pc_action("close")
    if t in ("play", "pause", "play pause", "resume"):
        return pc_action("play_pause")
    if t in ("next", "next song", "next track", "skip"):
        return pc_action("next_track")
    if t in ("previous", "previous song", "last song"):
        return pc_action("prev_track")
    if t in ("task manager", "open task manager"):
            return _friendly_open(open_app("task manager"), open_line("Task Manager"))

    m = re.search(
        r"(?:in settings|inside settings|settings)\s+(?:search|find)\s+(?:for\s+)?(.+)$",
        t,
    )
    if m:
        return search_settings(m.group(1).strip())
    m = re.search(r"search(?:\s+for)?\s+(.+)\s+in settings$", t)
    if m:
        return search_settings(m.group(1).strip())

    import session

    title_hint = ""
    try:
        from control import foreground_title

        title_hint = (foreground_title() or "").lower()
    except Exception:
        pass
    in_settings = (
        session.last_app() == "settings"
        or "setting" in title_hint
        or t.startswith("settings ")
    )
    place = _place(t, title_hint)

    if re.match(
        r"^play(?:\s+(?:it|that|this)(?:\s+song|\s+track|\s+one)?|"
        r"\s+the\s+song|\s+now)?(?:\s+on\s+spotify)?$",
        t,
    ) and t not in ("play", "pause"):
        q = session.last_entity("query") or ""
        if (
            place == "spotify"
            or session.last_app() == "spotify"
            or session.last_site() == "spotify"
            or q
        ):
            return _spotify_search(q)

    m = re.search(
        r"^(?:play|watch|search(?:\s+for)?|find|look up)\s+(.+)$",
        t,
    )
    if m:
        q = _strip_place(m.group(1).strip())
        if q and q not in (
            "pause",
            "this",
            "it",
            "that",
            "that song",
            "this song",
            "the song",
            "spotify",
            "youtube",
            "yt",
        ):
            if t.startswith("watch ") or place == "youtube":
                return _youtube_search(q)
            if place == "spotify" or t.startswith("play "):
                return _spotify_search(q)

    if "youtube" in t and re.search(r"\b(search|find|look up|play|watch)\b", t):
        m = re.search(
            r"(?:search(?:\s+for)?|find|look up|play|watch)\s+(.+?)(?:\s+on\s+youtube)?$",
            t,
        )
        if m:
            q = _strip_place(m.group(1).strip())
            q = re.sub(r"^youtube\s+(?:and\s+)?", "", q).strip()
            if q and q not in ("youtube", "yt"):
                return _youtube_search(q)

    m = re.search(
        r"(?:search(?:\s+for)?|find|look up|play|watch)\s+(.+?)\s+on\s+youtube",
        t,
    )
    if m:
        return _youtube_search(m.group(1))

    m = re.search(r"youtube\s+(?:search\s+(?:for\s+)?)(.+)", t)
    if m and m.group(1).strip() not in ("", "dot com"):
        q = _strip_place(m.group(1).strip())
        if q and q not in _SITES:
            return _youtube_search(q)

    m = re.search(r"^(?:search|find|look(?:\s+up)?|look\s+for)\s+(?:for\s+)?(.+)$", t)
    if m:
        q = _strip_place(m.group(1).strip())
        if q:
            if place == "spotify":
                return _spotify_search(q)
            if "youtube" in t or "youtube" in q or place == "youtube":
                return _youtube_search(q.replace("on youtube", "").strip())
            if in_settings:
                return search_settings(q)
            if session.last_site() == "youtube" and session.last_app() not in ("settings", "spotify"):
                return _youtube_search(q)
            return browse("search", q)

    m = re.search(r"^(?:google|look up)\s+(.+)$", t)
    if m:
        q = m.group(1).strip()
        if q and q not in _SITES and q not in _APPS:
            return _friendly_open(search_web(q, "google"), search_line("Google", q))

    m = re.search(
        r"^(?:open|launch|start|pull up|bring up|go to|navigate to|take me to)\s+(?:up\s+)?(.+)$",
        t,
    )
    target = m.group(1).strip() if m else ""
    if not target and t in _SITES:
        target = t
    if not target and t in _APPS:
        target = t

    if target:
        if "youtube" in target and re.search(r"\bsearch\b", target):
            q = re.sub(r".*?(?:search(?:\s+for)?)\s+", "", target).strip()
            if q:
                return _youtube_search(q)
        if target in _APPS or target.replace(" ", "") in {a.replace(" ", "") for a in _APPS}:
            return _friendly_open(open_app(target), open_line(target))
        if target in _SITES:
            return _friendly_open(open_url(_SITES[target]), open_line(target))
        url = _extract_url(target)
        if url:
            browser = None
            bm = re.search(r"\bin\s+(brave|chrome|edge)\b", t)
            if bm:
                browser = bm.group(1)
            return _friendly_open(open_url(url, browser), open_line(url))
        result = open_anything(target)
        if result.lower().startswith("opened"):
            return _friendly_open(result, open_line(target))
        if not result.lower().startswith("i don't"):
            return result

    return None


def wants_chat(text: str) -> bool:
    """True if this should go to the model. False means skip the LLM (stay responsive)."""
    raw = (text or "").strip()
    t = _clean(raw)
    if not t or t in _IGNORE:
        return False
    if "?" in raw:
        return True
    if re.search(
        r"\b(error|exception|bug|wrong|fix|code|java|python|leetcode|leet code|"
        r"compile|compiling|failed|failing|raising|stack trace|solution|algorithm|"
        r"debug|doesn'?t work|not working|write a|solve|how do i|reverse|linked list)\b",
        t,
    ):
        return True
    if re.search(r"\b(open|launch|click|scroll|search|draw|send|message|play|watch|type)\b", t):
        return False
    if re.search(
        r"\b(what|why|how|who|where|when|which|tell me|do you|are you|"
        r"can we talk|i think|i feel|explain|joke|story|remember|"
        r"how are you|what's up|whats up|i love|thank)\b",
        t,
    ):
        if re.search(r"\b(scroll|open|click|search|volume|mute|lock)\b", t):
            return False
        return True
    words = t.split()
    if len(words) >= 6 and not re.search(
        r"\b(open|click|scroll|search|go to|launch|close|type)\b", t
    ):
        return True
    return False


def shutil_which(name: str) -> bool:
    import shutil

    return bool(shutil.which(name) or shutil.which(name + ".exe"))


def _extract_url(text: str) -> str | None:
    m = _URL.search(text)
    return m.group(1) if m else None