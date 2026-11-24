"""
In-memory session state for the current AREIS process.

Tracks which chat backend is active and which app/site last received
an action, so follow-up voice commands (search, play, scrape) stay in
the right place. Nothing here is persisted to disk.

_ENTITIES generalizes that idea: any recent "thing" (contact, search
query, video title, url...) can be remembered by type and resolved
later so pronouns ("it", "that", "him") work across command kinds,
not just the click/search flows that had hand-rolled tracking before.
"""
from __future__ import annotations

import time

_PROVIDER = "local"
_LAST_SITE = ""
_LAST_APP = ""
_LAST_ACTION: tuple | None = None

# entity_type -> (value, remembered_at, ttl_seconds_or_None)
_ENTITIES: dict[str, tuple[str, float, float | None]] = {}
_DEFAULT_ENTITY_TTL = 600.0  # 10 minutes: stale enough that "it" stops guessing

_SUGGESTED: set[str] = set()  # follow-up suggestions already made this run

_ALIASES = {
    "local": "local",
    "llama": "local",
    "offline": "local",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "chat gpt": "openai",
    "chat": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "nvidia": "nvidia",
    "nim": "nvidia",
    "nemotron": "nvidia",
    "nvdia": "nvidia",
}


def normalize_provider(name: str | None) -> str | None:
    """Map spoken names (chatgpt, nim, llama) to canonical ids."""
    if not name or not isinstance(name, str):
        return None
    key = " ".join(name.lower().strip().replace("_", " ").split())
    return _ALIASES.get(key)


def get_provider() -> str:
    return _PROVIDER


def set_provider(name: str) -> str:
    """Switch the live backend. Unknown names are stored lowercased as-is."""
    global _PROVIDER
    resolved = normalize_provider(name)
    if resolved:
        _PROVIDER = resolved
    elif isinstance(name, str) and name.strip():
        _PROVIDER = name.strip().lower()
    return _PROVIDER


def remember_entity(entity_type: str, value: str, ttl: float | None = None) -> None:
    """Remember the most recent thing of a given type — contact, query,
    video, url, file, app, site... — so a later pronoun can resolve to it.
    ttl overrides the default 10-minute staleness window for this entry."""
    value = (value or "").strip()
    entity_type = (entity_type or "").strip().lower()
    if not value or not entity_type:
        return
    _ENTITIES[entity_type] = (value, time.time(), ttl)


def last_entity(entity_type: str, max_age: float = _DEFAULT_ENTITY_TTL) -> str | None:
    """Most recent value remembered for entity_type, or None if there isn't
    one or it's older than max_age (or its own ttl) seconds."""
    hit = _ENTITIES.get((entity_type or "").strip().lower())
    if not hit:
        return None
    value, remembered_at, ttl = hit
    limit = ttl if ttl is not None else max_age
    if time.time() - remembered_at > limit:
        return None
    return value


def set_last_site(site: str) -> None:
    global _LAST_SITE
    _LAST_SITE = (site or "").strip().lower()
    if _LAST_SITE:
        remember_entity("site", _LAST_SITE)


def last_site() -> str:
    return _LAST_SITE


def set_last_app(name: str) -> None:
    global _LAST_APP
    _LAST_APP = (name or "").strip().lower()
    if _LAST_APP:
        remember_entity("app", _LAST_APP)


def last_app() -> str:
    return _LAST_APP


def remember_action(kind: str, **kwargs) -> None:
    """Store the last click/search so 'try again' can replay it."""
    global _LAST_ACTION
    _LAST_ACTION = (kind, kwargs)


def last_action() -> tuple | None:
    return _LAST_ACTION


def was_suggested(key: str) -> bool:
    """True if a follow-up suggestion for `key` was already made this run —
    so we nudge once, not on every single matching command."""
    return key in _SUGGESTED


def mark_suggested(key: str) -> None:
    _SUGGESTED.add(key)