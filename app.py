"""Desktop AREIS window. Type and talk here — not the llama.cpp browser."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import requests

from agent import SYSTEM_PROMPT, handle_message
from config import (
    CONTEXT_SIZE,
    LLAMA_SERVER_EXE,
    LLAMA_SERVER_HOST,
    LLAMA_SERVER_PORT,
    MODEL_PATH,
    model_files_ready,
)
from intent import is_pause_listen, is_quit
from key_manager import key_manager
import session
from tools import connect_provider, list_providers
from voice import current_labels, list_devices, save_devices, speak, stop_speaking
from control import restore_pointer

BG = "#101218"
PANEL = "#1b1f2a"
FG = "#e8eaed"
MUTED = "#9aa3b2"
ACCENT = "#5eead4"
USER = "#93c5fd"
BTN = "#2a3142"
BTN_ON = "#134e4a"


class AreisApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AREIS")
        self.root.geometry("980x720")
        self.root.minsize(760, 560)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.busy = False
        self.hands_free = True
        self.speak_replies = True
        self.server_proc = None
        self._listen_started = False
        self.gen = 0
        self.speaking = False

        local_ok, _ = model_files_ready()
        if local_ok:
            session.set_provider("local")
        elif key_manager.has_usable_key("nvidia"):
            session.set_provider("nvidia")
        else:
            session.set_provider("local")
        self._build()
        self._cursor_ticks = 0
        self.root.after(80, self._keep_cursor)
        self.root.after(200, self._boot_server)

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=PANEL, padx=16, pady=10)
        top.pack(side="top", fill="x")
        tk.Label(top, text="AREIS", bg=PANEL, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(
            side="left"
        )
        self.status = tk.Label(top, text="Starting…", bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        self.status.pack(side="right")

        # Composer first so it never gets crushed off-screen.
        composer = tk.Frame(self.root, bg=PANEL, padx=16, pady=12)
        composer.pack(side="bottom", fill="x")
        tk.Label(
            composer,
            text="Type here, or use Talk. Talking ON speaks every reply.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        self.entry = tk.Text(
            composer,
            height=3,
            wrap="word",
            bg=BG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=("Segoe UI", 12),
            padx=10,
            pady=8,
        )
        self.entry.pack(fill="x", pady=(6, 8))
        self.entry.bind("<Return>", self._on_return)
        row = tk.Frame(composer, bg=PANEL)
        row.pack(fill="x")
        tk.Button(
            row,
            text="Send",
            command=self._send_typed,
            bg=ACCENT,
            fg="#042f2e",
            relief="flat",
            padx=18,
            pady=8,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        self.mic_btn = tk.Button(
            row,
            text="Talk",
            command=self._once_listen,
            bg=BTN,
            fg=FG,
            relief="flat",
            padx=14,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.mic_btn.pack(side="left", padx=(8, 0))
        self.hf_btn = tk.Button(
            row,
            text="Keep listening: ON",
            command=self._toggle_hands_free,
            bg=BTN_ON,
            fg=FG,
            relief="flat",
            padx=12,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.hf_btn.pack(side="left", padx=(8, 0))
        self.talk_btn = tk.Button(
            row,
            text="Talking: ON",
            command=self._toggle_talking,
            bg=BTN_ON,
            fg=FG,
            relief="flat",
            padx=12,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.talk_btn.pack(side="left", padx=(8, 0))
        tk.Button(
            row,
            text="API keys",
            command=self._keys_dialog,
            bg=BTN,
            fg=FG,
            relief="flat",
            padx=12,
            pady=8,
            font=("Segoe UI", 10),
        ).pack(side="right")

        bar = tk.Frame(self.root, bg=BG, padx=16, pady=8)
        bar.pack(side="top", fill="x")
        self.provider_btns = {}
        for key, label in (
            ("local", "Local 7B"),
            ("openai", "ChatGPT"),
            ("anthropic", "Claude"),
            ("nvidia", "NVIDIA"),
        ):
            b = tk.Button(
                bar,
                text=label,
                command=lambda k=key: self._set_provider(k),
                bg=BTN,
                fg=FG,
                relief="flat",
                padx=12,
                pady=4,
                font=("Segoe UI", 9),
            )
            b.pack(side="left", padx=(0, 8))
            self.provider_btns[key] = b
        self._paint_providers()

        audio = tk.Frame(self.root, bg=BG, padx=16, pady=4)
        audio.pack(side="top", fill="x")
        tk.Label(audio, text="Mic", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.mic_var = tk.StringVar()
        self.spk_var = tk.StringVar()
        self.mic_menu = ttk.Combobox(audio, textvariable=self.mic_var, width=36, state="readonly")
        self.mic_menu.pack(side="left", padx=(6, 16))
        tk.Label(audio, text="Earbuds / speakers", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(
            side="left"
        )
        self.spk_menu = ttk.Combobox(audio, textvariable=self.spk_var, width=36, state="readonly")
        self.spk_menu.pack(side="left", padx=(6, 0))
        self._fill_audio_menus()
        self.mic_menu.bind("<<ComboboxSelected>>", lambda _e: self._save_audio())
        self.spk_menu.bind("<<ComboboxSelected>>", lambda _e: self._save_audio())

        wrap = tk.Frame(self.root, bg=BG, padx=16, pady=8)
        wrap.pack(fill="both", expand=True)
        self.chat = tk.Text(
            wrap,
            wrap="word",
            bg=PANEL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=("Segoe UI", 11),
            padx=12,
            pady=12,
            state="disabled",
        )
        scroll = tk.Scrollbar(wrap, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        self.chat.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.chat.tag_configure("you", foreground=USER, font=("Segoe UI", 11, "bold"))
        self.chat.tag_configure("areis", foreground=ACCENT, font=("Segoe UI", 11, "bold"))
        self.chat.tag_configure("body", foreground=FG)
        self.chat.tag_configure("sys", foreground=MUTED, font=("Segoe UI", 9, "italic"))
        self.entry.focus_set()
        self._append_sys(
            "AREIS is your butler. Talk like you would to a person. "
            "I open apps, search, and scroll. Say play this video for the one "
            "already on screen, or click the first video for a thumbnail. "
            "Play that / message him reuse the last search or contact. "
            "Close with X or say quit AREIS."
        )

    def _keep_cursor(self) -> None:
        restore_pointer()
        self._cursor_ticks += 1
        if self._cursor_ticks < 20:
            self.root.after(2000, self._keep_cursor)

    def _fill_audio_menus(self) -> None:
        mics = [d["name"] for d in list_devices("input")]
        spks = [d["name"] for d in list_devices("output")]
        self.mic_menu["values"] = mics
        self.spk_menu["values"] = spks
        cur_in, cur_out = current_labels()
        if cur_in in mics:
            self.mic_var.set(cur_in)
        elif mics:
            self.mic_var.set(mics[0])
        if cur_out in spks:
            self.spk_var.set(cur_out)
        elif spks:
            self.spk_var.set(spks[0])

    def _save_audio(self) -> None:
        save_devices(self.mic_var.get(), self.spk_var.get())
        self._append_sys(f"Audio: {self.mic_var.get()} → {self.spk_var.get()}")

    def _paint_providers(self) -> None:
        current = session.get_provider()
        for key, btn in self.provider_btns.items():
            btn.configure(bg=BTN_ON if key == current else BTN)

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _append(self, who: str, text: str) -> None:
        self.chat.configure(state="normal")
        tag = "you" if who == "you" else "areis"
        self.chat.insert("end", f"{who}\n", tag)
        self.chat.insert("end", f"{text}\n\n", "body")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _append_sys(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{text}\n\n", "sys")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _begin_assistant(self) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", "areis\n", "areis")
        self.chat.configure(state="disabled")

    def _stream_token(self, token: str) -> None:
        def _do():
            self.chat.configure(state="normal")
            self.chat.insert("end", token, "body")
            self.chat.see("end")
            self.chat.configure(state="disabled")

        self.root.after(0, _do)

    def _finish_assistant(self) -> None:
        def _do():
            self.chat.configure(state="normal")
            self.chat.insert("end", "\n\n", "body")
            self.chat.see("end")
            self.chat.configure(state="disabled")

        self.root.after(0, _do)

    def _on_return(self, event):
        if event.state & 0x1:
            return None
        self._send_typed()
        return "break"

    def _server_up(self) -> bool:
        try:
            r = requests.get(
                f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/health",
                timeout=1.5,
            )
            return r.status_code < 500
        except Exception:
            try:
                r = requests.get(
                    f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/v1/models",
                    timeout=1.5,
                )
                return r.status_code < 500
            except Exception:
                return False

    def _boot_server(self) -> None:
        self._start_listening_if_needed()
        self._set_status("Listening…")

        def boot():
            try:
                speak("Good to see you. AREIS, at your service. Talk whenever you like.")
            except Exception:
                pass
            if session.get_provider() == "local":
                self._ensure_server()

        threading.Thread(target=boot, daemon=True).start()

    def _stop_local_server(self) -> None:
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            self.server_proc = None

    def _ensure_server(self) -> None:
        if session.get_provider() != "local":
            return
        if self._server_up():
            self.root.after(0, lambda: self._set_status("Local 7B ready — listening"))
            return
        ready, detail = model_files_ready()
        while not ready and session.get_provider() == "local":
            self.root.after(0, lambda d=detail: self._set_status(d[:60]))
            time.sleep(2)
            ready, detail = model_files_ready()
        if session.get_provider() != "local":
            return
        if not os.path.exists(LLAMA_SERVER_EXE):
            self.root.after(0, lambda: self._set_status("llama-server.exe missing"))
            return
        self.root.after(0, lambda: self._set_status("Loading Qwen2.5-7B in background…"))
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            off = getattr(subprocess, "STARTF_FORCEOFFFEEDBACK", 0x80)
            startupinfo.dwFlags |= off | subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        cmd = [
            LLAMA_SERVER_EXE,
            "-m",
            MODEL_PATH,
            "-ngl",
            "99",
            "-c",
            str(CONTEXT_SIZE),
            "--port",
            str(LLAMA_SERVER_PORT),
            "--host",
            LLAMA_SERVER_HOST,
            "--no-webui",
        ]
        self.server_proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(LLAMA_SERVER_EXE),
            creationflags=flags,
            startupinfo=startupinfo,
        )
        restore_pointer()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if session.get_provider() != "local":
                self._stop_local_server()
                return
            if self._server_up():
                restore_pointer()
                self.root.after(0, lambda: self._set_status("Local 7B ready — listening"))
                return
            if self.server_proc.poll() is not None:
                break
            time.sleep(0.6)
        if session.get_provider() == "local":
            self.root.after(0, lambda: self._set_status("Local model failed — voice commands still work"))
            self.root.after(
                0,
                lambda: self._append_sys(
                    "Could not load the 7B model. Voice commands like open YouTube still work."
                ),
            )

    def _start_listening_if_needed(self) -> None:
        if self.hands_free and not self._listen_started:
            self._listen_started = True
            threading.Thread(target=self._hands_free_loop, daemon=True).start()

    def _set_provider(self, name: str) -> None:
        msg = connect_provider(name)
        self._paint_providers()
        self._append_sys(msg)
        if name == "local":
            threading.Thread(target=self._ensure_server, daemon=True).start()
        else:
            self._stop_local_server()
            self._set_status("Listening…")

    def _keys_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("API keys")
        win.configure(bg=BG)
        win.transient(self.root)
        tk.Label(win, text="ChatGPT (OpenAI)", bg=BG, fg=FG).pack(anchor="w", padx=12, pady=(12, 4))
        openai_var = tk.Entry(win, width=48, show="*", bg=PANEL, fg=FG, insertbackground=FG)
        openai_var.pack(padx=12, fill="x")
        tk.Label(win, text="Claude (Anthropic)", bg=BG, fg=FG).pack(anchor="w", padx=12, pady=(12, 4))
        claude_var = tk.Entry(win, width=48, show="*", bg=PANEL, fg=FG, insertbackground=FG)
        claude_var.pack(padx=12, fill="x")

        tk.Label(win, text="NVIDIA NIM (nvapi-)", bg=BG, fg=FG).pack(anchor="w", padx=12, pady=(12, 4))
        nvidia_var = tk.Entry(win, width=48, show="*", bg=PANEL, fg=FG, insertbackground=FG)
        nvidia_var.pack(padx=12, fill="x")

        def save():
            o, c, n = openai_var.get().strip(), claude_var.get().strip(), nvidia_var.get().strip()
            notes = []

            def store(provider: str, key: str, label: str) -> None:
                if key_manager.add_key(provider, key):
                    notes.append(f"Saved {label} key.")
                else:
                    notes.append(f"{label} key already saved (or not accepted).")

            if o:
                if o.startswith("nvapi-"):
                    store("nvidia", o, "NVIDIA")
                else:
                    store("openai", o, "ChatGPT")
            if c:
                store("anthropic", c, "Claude")
            if n:
                if n.startswith("nvapi-") or "nvapi" in n.lower():
                    store("nvidia", n, "NVIDIA")
                else:
                    self._append_sys("NVIDIA keys start with nvapi-. Paste it in the NVIDIA box only.")
                    return
            win.destroy()
            for line in notes:
                self._append_sys(line)
            self._append_sys(list_providers())
            self._append_sys(
                "Stay on Local 7B for speed; tap NVIDIA only when you want the cloud model."
            )

        tk.Button(win, text="Save", command=save, bg=ACCENT, fg="#042f2e", relief="flat").pack(
            pady=16
        )

    def _send_typed(self) -> None:
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        self._run_turn(text, interrupt=True)

    def _once_listen(self) -> None:
        if self.busy:
            return
        threading.Thread(target=self._listen_once_worker, daemon=True).start()

    def _listen_once_worker(self) -> None:
        from voice import listen

        self.root.after(0, lambda: self._set_status("Listening…"))
        try:
            heard = listen()
        except Exception as e:
            self.root.after(0, lambda: self._append_sys(str(e)))
            return
        text = (heard or "").strip()
        if not text:
            self.root.after(0, lambda: self._set_status("Didn't catch that"))
            return
        self.root.after(0, lambda: self._run_turn(text))

    def _toggle_talking(self) -> None:
        self.speak_replies = not self.speak_replies
        self.talk_btn.configure(
            bg=BTN_ON if self.speak_replies else BTN,
            text="Talking: ON" if self.speak_replies else "Talking: OFF",
        )
        if not self.speak_replies:
            stop_speaking()
            self.speaking = False
            self._set_status("Talking off")
        else:
            self._set_status("Talking on")

    def _toggle_hands_free(self) -> None:
        self.hands_free = not self.hands_free
        self.hf_btn.configure(
            bg=BTN_ON if self.hands_free else BTN,
            text="Keep listening: ON" if self.hands_free else "Keep listening: OFF",
        )
        if self.hands_free:
            self._listen_started = True
            threading.Thread(target=self._hands_free_loop, daemon=True).start()
        else:
            self._listen_started = False
            self._set_status("Listening paused")

    def _hands_free_loop(self) -> None:
        from voice import listen
        from llama_client import abort_chat

        while self.hands_free:
            while self.speaking and self.hands_free:
                time.sleep(0.05)
            self.root.after(0, lambda: self._set_status("Listening…"))
            try:
                heard = listen()
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: self._append_sys(m))
                time.sleep(1)
                continue
            text = (heard or "").strip()
            if not text or not self.hands_free:
                continue
            if is_pause_listen(text):
                self.hands_free = False
                self._listen_started = False
                self.root.after(
                    0,
                    lambda: self.hf_btn.configure(bg=BTN, text="Keep listening: OFF"),
                )
                self.root.after(0, lambda: self._set_status("Listening paused"))
                continue
            if is_quit(text):
                self.root.after(0, self._close)
                break
            if self.busy:
                abort_chat()
            self.root.after(0, lambda t=text: self._run_turn(t, interrupt=True))

    def _run_turn(self, text: str, on_done=None, interrupt: bool = False) -> None:
        if self.busy and not interrupt:
            if on_done:
                on_done()
            return
        if is_quit(text):
            self._close()
            return
        try:
            from llama_client import abort_chat

            abort_chat()
        except Exception:
            pass
        self.gen += 1
        gen = self.gen
        self.busy = True
        self._append("you", text)
        self._begin_assistant()
        self._set_status("On it…")

        def work():
            buf = []

            def on_token(tok: str):
                if gen != self.gen:
                    return
                buf.append(tok)
                self._stream_token(tok)

            try:
                reply = handle_message(self.history, text, on_token=on_token)
            except Exception as e:
                reply = f"That failed: {e}"
                if not buf and gen == self.gen:
                    self._stream_token(reply)

            self._finish_assistant()
            if gen != self.gen:
                if on_done:
                    on_done()
                return

            said = (reply or "").strip()

            def release():
                if gen != self.gen:
                    return
                self.busy = False
                restore_pointer()
                self._paint_providers()
                self._set_status("Listening…" if self.hands_free else "Ready")
                if on_done:
                    on_done()
                try:
                    from control import restore_focus

                    restore_focus()
                except Exception:
                    pass

            if self.speak_replies and said and gen == self.gen:
                self.speaking = True
                self.root.after(0, lambda: self._set_status("Talking…"))
                try:
                    speak(said)
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self._append_sys("Could not speak: " + err))
                finally:
                    self.speaking = False
            self.root.after(0, release)

        threading.Thread(target=work, daemon=True).start()

    def _close(self) -> None:
        self.hands_free = False
        self._listen_started = False
        self.gen += 1
        try:
            from llama_client import abort_chat

            abort_chat()
        except Exception:
            pass
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=3)
            except Exception:
                try:
                    self.server_proc.kill()
                except Exception:
                    pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main() -> None:
    try:
        root = tk.Tk()
        AreisApp(root)
        root.mainloop()
    except Exception:
        import traceback

        err = traceback.format_exc()
        try:
            with open(os.path.join(os.path.dirname(__file__), "areis_boot.log"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, err[-1800:], "AREIS failed to start", 0x10)
        except Exception:
            print(err)
        raise


if __name__ == "__main__":
    main()
