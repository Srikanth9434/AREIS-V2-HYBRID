"""
Local retrieval memory for chats, code solutions, and scraped pages.

SQLite + FTS5 — no extra ML download. Works offline. Local 7B and NVIDIA
can both use retrieve() if a caller attaches the notes (the GUI does).

Paths live here so this module does not depend on optional config keys.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.abspath(__file__))
MEMORY_DB = os.path.join(_ROOT, "data", "areis_memory.sqlite")
MEMORY_RECENT = int(os.environ.get("AREIS_MEMORY_RECENT", "4"))
MEMORY_RETRIEVE = int(os.environ.get("AREIS_MEMORY_HITS", "4"))

_CODE_BLOCK = re.compile(r"```[\w+-]*\n(.*?)```", re.S)
_STOP = {
    "the", "a", "an", "is", "it", "to", "of", "and", "or", "in", "on", "for",
    "you", "i", "me", "my", "we", "that", "this", "with", "be", "are", "was",
}


def _connect() -> sqlite3.Connection:
    Path(MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(MEMORY_DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            created REAL NOT NULL,
            kind TEXT NOT NULL,
            title TEXT,
            body TEXT NOT NULL
        )
        """
    )
    try:
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
            "title, body, content='notes', content_rowid='id')"
        )
        con.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
              INSERT INTO notes_fts(rowid, title, body)
              VALUES (new.id, new.title, new.body);
            END
            """
        )
        con.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
              INSERT INTO notes_fts(notes_fts, rowid, title, body)
              VALUES('delete', old.id, old.title, old.body);
            END
            """
        )
    except sqlite3.OperationalError:
        pass
    con.commit()
    return con


def remember_turn(user_text: str, assistant_text: str) -> None:
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    if not user_text or not assistant_text:
        return
    low = assistant_text.lower()
    if low.startswith(("sorry?", "i'm here", "lost that", "that took too long")):
        return
    body = f"User: {user_text}\nAREIS: {assistant_text}"
    title = user_text[:120]
    _insert("chat", title, body[:4000])
    for block in _CODE_BLOCK.findall(assistant_text):
        code = block.strip()
        if len(code) < 20:
            continue
        _insert("solution", title, f"For: {user_text}\n{code}"[:4000])


def remember_page(url: str, text: str) -> None:
    text = (text or "").strip()
    if not text or text.lower().startswith("could not fetch"):
        return
    title = url
    match = re.search(r"^Title:\s*(.+)$", text, re.M)
    if match:
        title = match.group(1).strip()[:120]
    _insert("page", title, f"{url}\n{text}"[:4000])


def _insert(kind: str, title: str, body: str) -> None:
    con = _connect()
    try:
        con.execute(
            "INSERT INTO notes(created, kind, title, body) VALUES (?, ?, ?, ?)",
            (time.time(), kind, title, body),
        )
        con.commit()
    finally:
        con.close()


def _fts_query(text: str) -> str:
    words = re.findall(r"[a-zA-Z0-9#+]+", (text or "").lower())
    keep = [w for w in words if w not in _STOP and len(w) > 1][:12]
    if not keep:
        return ""
    return " OR ".join(keep)


def retrieve(query: str, limit: int | None = None) -> str:
    """Return notes to paste into a system prompt: hits + recent chats."""
    limit = MEMORY_RETRIEVE if limit is None else limit
    con = _connect()
    chunks: list[str] = []
    seen: set[int] = set()
    try:
        q = _fts_query(query)
        rows: list[tuple] = []
        if q:
            try:
                rows = con.execute(
                    """
                    SELECT notes.id, notes.kind, notes.title, notes.body
                    FROM notes_fts
                    JOIN notes ON notes.id = notes_fts.rowid
                    WHERE notes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                tokens = q.replace(" OR ", " ").split()[:4]
                like = "%" + "%".join(tokens) + "%"
                rows = con.execute(
                    """
                    SELECT id, kind, title, body FROM notes
                    WHERE body LIKE ? OR title LIKE ?
                    ORDER BY created DESC LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        for nid, kind, title, body in rows:
            if nid in seen:
                continue
            seen.add(nid)
            chunks.append(f"[{kind}] {title}\n{body[:700]}")

        recent = con.execute(
            """
            SELECT id, kind, title, body FROM notes
            WHERE kind = 'chat'
            ORDER BY created DESC LIMIT ?
            """,
            (MEMORY_RECENT,),
        ).fetchall()
        rec_bits = []
        for nid, _kind, title, body in recent:
            if nid in seen:
                continue
            seen.add(nid)
            rec_bits.append(f"{title}: {body[:400]}")
        if rec_bits:
            chunks.append("Recent conversation:\n" + "\n---\n".join(rec_bits))
    finally:
        con.close()

    text = "\n\n".join(chunks).strip()
    if len(text) > 1600:
        text = text[:1600] + "…"
    return text


# --- App/site sequence memory --------------------------------------------
#
# A separate, un-FTS'd table: "what got opened, and when" — not chat text.
# This is what lets AREIS notice "you always open Spotify right after VS
# Code" and offer it, instead of guessing after seeing it once.

def _ensure_opens_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS opens ("
        "id INTEGER PRIMARY KEY, ts REAL NOT NULL, name TEXT NOT NULL)"
    )


def log_app_open(name: str) -> None:
    """Record that an app/site was just opened. Cheap append-only log —
    called once per action, not per word, so this stays tiny for years."""
    name = (name or "").strip().lower()
    if not name:
        return
    con = _connect()
    try:
        _ensure_opens_table(con)
        con.execute("INSERT INTO opens(ts, name) VALUES (?, ?)", (time.time(), name))
        con.commit()
    finally:
        con.close()


def suggest_followup(name: str, window_seconds: int = 240, min_days: int = 3) -> str | None:
    """If `name` has historically been followed, within window_seconds, by
    the same other app/site — on at least min_days distinct calendar days —
    return that app/site's name. Returns None otherwise: a suggestion
    should be earned by repetition across real days, never guessed from a
    single coincidence."""
    name = (name or "").strip().lower()
    if not name:
        return None
    con = _connect()
    try:
        _ensure_opens_table(con)
        rows = con.execute("SELECT ts, name FROM opens ORDER BY ts ASC").fetchall()
    finally:
        con.close()

    day_hits: dict[str, set[str]] = {}
    for i, (ts, n) in enumerate(rows):
        if n != name:
            continue
        for ts2, n2 in rows[i + 1:]:
            if ts2 - ts > window_seconds:
                break
            if n2 == name:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            day_hits.setdefault(n2, set()).add(day)

    best, best_days = None, 0
    for followup, days in day_hits.items():
        if len(days) > best_days:
            best, best_days = followup, len(days)
    return best if best_days >= min_days else None