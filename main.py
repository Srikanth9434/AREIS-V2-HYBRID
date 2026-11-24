"""
AREIS-light CLI (no Tk window).

  python main.py           type
  python main.py --voice   talk

Local llama-server is expected on port 8090 (same as the GUI).
Cloud keys: copy keys.example.json to keys.json.
"""
from __future__ import annotations

import argparse
import sys

from agent import SYSTEM_PROMPT, handle_message
from intent import is_quit
import session


def _handle(history: list[dict], user_text: str) -> str:
    try:
        return (handle_message(history, user_text) or "").strip()
    except Exception as e:
        err = f"That failed: {e}"
        print(f"areis> {err}")
        return err


def text_loop(history: list[dict]) -> None:
    print("AREIS-light (text). Try: connect to chatgpt | use nvidia | use local |")
    print("scrape <url> | open youtube | quit. Ctrl+C to exit.\n")
    while True:
        try:
            user_text = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not user_text:
            continue
        if is_quit(user_text):
            print("areis> Bye.")
            break
        _handle(history, user_text)


def voice_loop(history: list[dict]) -> None:
    from voice import listen, speak

    speak("I'm listening. Talk whenever you're ready.")
    print("AREIS-light (voice). Say quit to stop.\n")
    while True:
        try:
            heard = listen()
        except KeyboardInterrupt:
            print()
            break
        except Exception as e:
            print(f"Mic error: {e}")
            speak("I couldn't reach the speech service. Check the microphone and network.")
            continue
        user_text = (heard or "").strip()
        if not user_text:
            continue
        print(f"you> {user_text}")
        if is_quit(user_text):
            speak("Bye.")
            break
        reply = _handle(history, user_text)
        if reply:
            speak(reply)


def main() -> None:
    parser = argparse.ArgumentParser(description="AREIS-light assistant")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Hands-free: listen and speak instead of typing",
    )
    args = parser.parse_args()
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    session.set_provider("local")
    if args.voice:
        voice_loop(history)
    else:
        text_loop(history)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
