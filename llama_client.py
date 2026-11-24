"""
Chat backends: local llama-server, OpenAI, Anthropic, and NVIDIA NIM.
Internal history stays in OpenAI chat-completions shape so tools stay shared.
"""
import json
import os
import requests
import session
from config import (
    ANTHROPIC_MODEL,
    ANTHROPIC_URL,
    ANTHROPIC_VERSION,
    LLAMA_SERVER_URL,
    MAX_TOKENS,
    NVIDIA_MAX_TOKENS,
    NVIDIA_MODEL,
    NVIDIA_SUPER_MODEL,
    NVIDIA_TIMEOUT,
    NVIDIA_ULTRA_MODEL,
    NVIDIA_URL,
    OPENAI_MODEL,
    OPENAI_URL,
    REQUEST_TIMEOUT,
    TEMPERATURE,
)
from key_manager import key_manager

_active_resp = None
_aborted = False
_nvidia_model = None


def abort_chat() -> None:
    """User interrupt: drop the in-flight HTTP stream."""
    global _active_resp, _aborted
    _aborted = True
    _close_active()


def _close_active() -> None:
    global _active_resp
    resp = _active_resp
    _active_resp = None
    if resp is not None:
        try:
            resp.close()
        except Exception:
            pass

def was_aborted() -> bool:
    global _aborted
    was = _aborted
    _aborted = False
    return was


def parse_tool_calls(message: dict) -> list[dict]:
    """Normalizes tool_calls into [{'name': ..., 'arguments': {...}, 'id': ...}, ...]"""
    calls = message.get("tool_calls") or []
    parsed = []
    for c in calls:
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        parsed.append({"id": c.get("id"), "name": fn.get("name"), "arguments": args})
    return parsed


def _session_context() -> str:
    """One line of live PC state for the model: last search, contact, video, app."""
    bits = []
    for kind, label in (
        ("query", "last search"),
        ("contact", "last contact"),
        ("video", "last video"),
        ("app", "front app"),
        ("site", "last site"),
    ):
        val = session.last_entity(kind)
        if val:
            bits.append(f"{label}: {val}")
    return "; ".join(bits)


def _with_session_context(messages: list[dict]) -> list[dict]:
    ctx = _session_context()
    if not ctx or not messages:
        return messages
    out = list(messages)
    first = dict(out[0])
    if first.get("role") == "system":
        first["content"] = (
            (first.get("content") or "")
            + "\n\nLive context on this PC (use for it/that/him/her): "
            + ctx
        )
        out[0] = first
    return out


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = TEMPERATURE,
    on_token=None,
) -> dict:
    """One completion. If on_token is set, streams text tokens as they arrive."""
    provider = session.get_provider()
    messages = _with_session_context(_trim_messages(messages))
    if provider == "openai":
        return _openai_chat(messages, tools, temperature, on_token)
    if provider == "anthropic":
        return _anthropic_chat(messages, tools, temperature, on_token)
    if provider == "nvidia":
        return _nvidia_chat(messages, None, temperature, on_token)
    return _local_chat(messages, tools, temperature, on_token)

def _trim_messages(messages: list[dict]) -> list[dict]:
    system = [m for m in messages if m.get("role") == "system"][:1]
    rest = [m for m in messages if m.get("role") != "system"][-6:]
    while rest and rest[0].get("role") == "tool":
        rest.pop(0)
    return system + rest

def _sse_payloads(resp):
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            if line == "[DONE]":
                break
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _openai_style_request(
    url,
    headers,
    messages,
    tools,
    temperature,
    on_token,
    model=None,
    extra=None,
    timeout=REQUEST_TIMEOUT,
    max_tokens=None,
):
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": MAX_TOKENS if max_tokens is None else max_tokens,
        "stream": bool(on_token),
    }
    if model:
        payload["model"] = model
    elif url == OPENAI_URL:
        payload["model"] = OPENAI_MODEL
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if extra:
        payload.update(extra)

    global _active_resp
    _close_active()
    resp = requests.post(
        url, json=payload, headers=headers, timeout=(4, timeout), stream=bool(on_token)
    )
    _active_resp = resp
    try:
        if resp.status_code in (401, 429):
            raise PermissionError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if url == NVIDIA_URL and resp.status_code in (403, 404, 410):
            raise LookupError(f"NVIDIA model gone HTTP {resp.status_code}")
        resp.raise_for_status()

        if not on_token:
            return resp.json()["choices"][0]["message"]

        content = []
        tool_acc = {}
        for chunk in _sse_payloads(resp):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                content.append(piece)
                on_token(piece)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_acc.setdefault(
                    idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
        message = {"role": "assistant", "content": "".join(content)}
        if tool_acc:
            message["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
            if not message["content"]:
                message["content"] = None
        return message
    finally:
        if _active_resp is resp:
            _active_resp = None


def _local_chat(messages, tools, temperature, on_token):
    return _openai_style_request(
        LLAMA_SERVER_URL, {}, messages, tools, temperature, on_token
    )


_CLASSIFY_SYSTEM = {
    "role": "system",
    "content": (
        "You turn one spoken command into at most one tool call. Pick the "
        "single tool that matches what the person wants and fill in its "
        "arguments from what they said. If no tool clearly fits — for "
        "example the person is chatting, asking a question, or talking "
        "about code/homework — call nothing and reply with exactly: NONE. "
        "Never call more than one tool. Never explain your choice. "
        "If they ask to see/read the screen, use whats_on_screen. "
        "If they ask to solve a problem, write code, LeetCode, homework, "
        "or debug an error, reply NONE — that is chat, not a tool."
    ),
}


def classify_tool(user_text: str, tools: list[dict]) -> dict:
    """Fast, local-only completion whose only job is picking a tool for one
    utterance (or nothing). Always uses the local llama-server regardless of
    the active provider, and is capped to a small max_tokens, since it never
    needs to produce prose — it either emits a tool call or the word NONE."""
    ctx = _session_context()
    user = user_text
    if ctx:
        user = (
            f"Live context: {ctx}\n"
            f"If they say that/it/this/him/her, use the matching live context.\n"
            f"Command: {user_text}"
        )
    messages = [_CLASSIFY_SYSTEM, {"role": "user", "content": user}]
    return _openai_style_request(
        LLAMA_SERVER_URL, {}, messages, tools, 0.0, None, max_tokens=150
    )


def _openai_chat(messages, tools, temperature, on_token):
    key = key_manager.get("openai")
    if not key:
        raise RuntimeError("No OpenAI key. Add one in keys.json or say set chatgpt key.")
    try:
        return _openai_style_request(
            OPENAI_URL,
            {"Authorization": f"Bearer {key}"},
            messages,
            tools,
            temperature,
            on_token,
            model=OPENAI_MODEL,
        )
    except PermissionError:
        key_manager.mark_bad("openai", key)
        raise


def _nvidia_candidates() -> list[str]:
    if os.environ.get("NVIDIA_MODEL"):
        return [NVIDIA_MODEL]
    # Super answers hard coding questions on this key; Ultra is catalogued
    # but its generate endpoint often 404s (that is why LeetCode 203 went silent).
    return [NVIDIA_SUPER_MODEL, NVIDIA_ULTRA_MODEL]


def _nvidia_chat(messages, tools, temperature, on_token):
    global _nvidia_model
    key = key_manager.get("nvidia")
    if not key:
        raise RuntimeError(
            "No NVIDIA key. Add an nvapi- key in keys.json under nvidia, "
            "or say set nvidia key followed by the key."
        )
    extra = {
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_effort": "low",
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    models = [_nvidia_model] if _nvidia_model else _nvidia_candidates()
    last_err = None
    for model in models:
        try:
            message = _openai_style_request(
                NVIDIA_URL,
                headers,
                messages,
                None,
                temperature,
                on_token,
                model=model,
                extra=extra,
                timeout=NVIDIA_TIMEOUT,
                max_tokens=NVIDIA_MAX_TOKENS,
            )
            _nvidia_model = model
            return message
        except PermissionError:
            key_manager.mark_bad("nvidia", key)
            raise
        except LookupError as e:
            last_err = e
            _nvidia_model = None
            continue
        except requests.HTTPError as e:
            last_err = e
            continue
    raise RuntimeError(
        "NVIDIA chat is unavailable right now. Voice commands still work: "
        "say search for cats, scroll down, or play this video."
    ) from last_err


def _anthropic_tools(tools):
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return out


def _split_system(messages):
    system_parts = []
    rest = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content") or "")
        else:
            rest.append(m)
    return "\n".join(p for p in system_parts if p), rest


def _to_anthropic_messages(messages):
    converted = []
    pending_tools = []

    def flush_tools():
        nonlocal pending_tools
        if pending_tools:
            converted.append({"role": "user", "content": pending_tools})
            pending_tools = []

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending_tools.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id"),
                    "content": m.get("content") or "",
                }
            )
            continue
        flush_tools()
        if role == "user":
            converted.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            content = []
            text = m.get("content")
            if text:
                content.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": fn.get("name"),
                        "input": args,
                    }
                )
            converted.append({"role": "assistant", "content": content or ""})
    flush_tools()
    return converted


def _anthropic_chat(messages, tools, temperature, on_token):
    key = key_manager.get("anthropic")
    if not key:
        raise RuntimeError("No Claude key. Add one in keys.json or say set claude key.")

    system, rest = _split_system(messages)
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "messages": _to_anthropic_messages(rest),
        "stream": bool(on_token),
    }
    if system:
        payload["system"] = system
    a_tools = _anthropic_tools(tools)
    if a_tools:
        payload["tools"] = a_tools

    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            stream=bool(on_token),
        )
        if resp.status_code in (401, 429):
            key_manager.mark_bad("anthropic", key)
            raise PermissionError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
    except PermissionError:
        raise

    if not on_token:
        return _anthropic_to_openai_message(resp.json())

    text_parts = []
    tool_calls = []
    current_tool = None
    for event in _sse_payloads(resp):
        etype = event.get("type")
        if etype == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                current_tool = {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": "",
                }
        elif etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                piece = delta.get("text") or ""
                if piece:
                    text_parts.append(piece)
                    on_token(piece)
            elif delta.get("type") == "input_json_delta" and current_tool:
                current_tool["arguments"] += delta.get("partial_json") or ""
        elif etype == "content_block_stop" and current_tool:
            tool_calls.append(current_tool)
            current_tool = None

    message = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": t["id"],
                "type": "function",
                "function": {
                    "name": t["name"],
                    "arguments": t["arguments"] or "{}",
                },
            }
            for t in tool_calls
        ]
    return message


def _anthropic_to_openai_message(data: dict) -> dict:
    text = []
    tool_calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    message = {"role": "assistant", "content": "".join(text) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message