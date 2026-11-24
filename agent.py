"""Shared agent turn: tools, streaming, optional spoken reply.

This is the hub: intent → local tool pick → chat backends, plus RAG
notes and habit follow-ups. GUI and CLI should only call handle_message.
"""
from llama_client import chat, classify_tool, parse_tool_calls, was_aborted
from persona import SYSTEM_PROMPT, listen_line, miss_line
from tools import TOOL_SCHEMAS, TOOL_REGISTRY


def run_tool(name: str, arguments: dict) -> str:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return str(fn(**arguments))
    except Exception as e:
        return f"Tool '{name}' failed: {e}"


def _print_token(token: str) -> None:
    print(token, end="", flush=True)


def _apply_notes(history: list[dict], user_text: str) -> str | None:
    orig = None
    if history and history[0].get("role") == "system":
        orig = history[0].get("content")
        try:
            from memory import retrieve

            notes = retrieve(user_text)
            if notes:
                history[0]["content"] = orig + "\n\nNotes from earlier on this PC:\n" + notes
        except Exception:
            pass
    return orig


def _restore_notes(history: list[dict], orig: str | None) -> None:
    if orig is not None and history:
        history[0]["content"] = orig


def _save_turn(user_text: str, reply: str) -> None:
    try:
        from memory import remember_turn

        remember_turn(user_text, reply)
    except Exception:
        pass


def try_tool_pick(history: list[dict], user_text: str, on_token=None) -> str | None:
    """Tier 2: ask the local model to pick one tool for this utterance."""
    try:
        message = classify_tool(user_text, TOOL_SCHEMAS)
    except Exception:
        return None

    calls = parse_tool_calls(message)
    if not calls:
        return None

    history.append({"role": "user", "content": user_text})
    result = run_tool(calls[0]["name"], calls[0]["arguments"])
    content = result or "Done."
    if on_token:
        on_token(content)
    else:
        print(f"areis> {content}")
    history.append({"role": "assistant", "content": content})
    return content


def handle_message(history: list[dict], user_text: str, on_token=None) -> str:
    from intent import _clean, _resolve_reference, try_direct, wants_chat

    direct = try_direct(user_text)
    if direct is not None:
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": direct})
        if on_token:
            on_token(direct)
        else:
            print(f"areis> {direct}")
        reply = _with_followup(direct, on_token=on_token)
        _save_turn(user_text, reply)
        return reply
    low = user_text.lower().strip()
    if low in ("areis", "aries", "iris", "ehh", "eh", "uh", "um", "ok", "okay"):
        msg = listen_line()
        if on_token:
            on_token(msg)
        return msg

    picked = try_tool_pick(
        history, _resolve_reference(_clean(user_text)), on_token=on_token
    )
    if picked is not None:
        reply = _with_followup(picked, on_token=on_token)
        _save_turn(user_text, reply)
        return reply

    if not wants_chat(user_text):
        msg = miss_line()
        if on_token:
            on_token(msg)
        else:
            print(f"areis> {msg}")
        return msg

    reply = agent_turn(history, user_text, on_token=on_token)
    _save_turn(user_text, reply)
    return reply


def _with_followup(result: str, on_token=None) -> str:
    import session

    after_app = session.last_app()
    if not after_app:
        return result
    try:
        from memory import log_app_open, suggest_followup

        log_app_open(after_app)
        suggestion = suggest_followup(after_app)
    except Exception:
        return result

    if not suggestion or suggestion == after_app:
        return result
    key = f"{after_app}->{suggestion}"
    if session.was_suggested(key):
        return result
    session.mark_suggested(key)

    nudge = f" Want me to open {suggestion} too, like usual?"
    if on_token:
        on_token(nudge)
    else:
        print(nudge)
    return (result or "") + nudge


def agent_turn(history: list[dict], user_text: str, stream: bool = True, on_token=None) -> str:
    orig = _apply_notes(history, user_text)
    history.append({"role": "user", "content": user_text})

    if on_token is None and stream:
        on_token = _print_token
        print("areis> ", end="", flush=True)

    try:
        try:
            message = chat(history, tools=None, on_token=on_token)
        except Exception as e:
            if was_aborted():
                history.pop()
                return ""
            content = "Lost that thought. Say it once more."
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                content = "That took too long. I'm listening again."
            history.append({"role": "assistant", "content": content})
            if on_token:
                on_token(content)
            return content

        tool_calls = parse_tool_calls(message)

        cli = on_token is _print_token
        if not tool_calls:
            content = (message.get("content") or "").strip()
            if not content:
                content = "I'm right here. What shall we do next?"
                if on_token:
                    on_token(content)
            elif cli:
                print("")
            history.append({"role": "assistant", "content": content})
            return content

        if cli:
            print("[working…]", flush=True)

        history.append(message)
        for call in tool_calls:
            result = run_tool(call["name"], call["arguments"])
            history.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

        if cli:
            print("areis> ", end="", flush=True)
        followup = chat(history, tools=TOOL_SCHEMAS, on_token=on_token)
        content = followup.get("content") or ""
        if cli:
            print("(no reply)" if not content else "")
        history.append({"role": "assistant", "content": content})
        return content
    finally:
        _restore_notes(history, orig)
