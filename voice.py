"""
Microphone in, speaker out.

Prefers Bluetooth Hands-Free for the mic and Stereo/A2DP for speakers.
Skips WDM-KS devices that fail with PortAudio -9999. STT is Google at 8 kHz
on BT; TTS is local SAPI/pyttsx3 (no cloud TTS).
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import tempfile
import threading
import time
import wave

import numpy as np
import pyttsx3
import sounddevice as sd
import speech_recognition as sr

from config import LISTEN_TIMEOUT, MIC_GAIN, PHRASE_LIMIT, SILENCE_END, TTS_VOICE, VOICE_RATE

_SAMPLE_RATE = 16000
_engine = None
_tts_lock = threading.Lock()
_recognizer = None
_noise_floor = None
_ok_input = None
_AUDIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio.json")

_BT_WORDS = (
    "earbud",
    "earbuds",
    "airpod",
    "headset",
    "headphone",
    "bluetooth",
    "hands-free",
    "handsfree",
    "a2dp",
    "hfp",
    "stereo",
)


def _com_init() -> None:
    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass


def _host_name(device_index: int) -> str:
    try:
        dev = sd.query_devices(device_index)
        api = sd.query_hostapis(int(dev["hostapi"]))
        return str(api.get("name") or "")
    except Exception:
        return ""


def _is_wdmks(device_index: int) -> bool:
    n = _host_name(device_index).lower()
    return "wdm" in n or "kernel" in n


def _host_rank(device_index: int) -> int:
    n = _host_name(device_index).lower()
    if "wasapi" in n:
        return 0
    if n == "mme" or "windows mme" in n:
        return 1
    if "directsound" in n:
        return 2
    if _is_wdmks(device_index):
        return 9
    return 5


def list_devices(kind: str) -> list[dict]:
    out = []
    for i, dev in enumerate(sd.query_devices()):
        if kind == "input" and dev["max_input_channels"] < 1:
            continue
        if kind == "output" and dev["max_output_channels"] < 1:
            continue
        if _is_wdmks(i):
            continue
        host = _host_name(i)
        out.append({"index": i, "name": dev["name"], "host": host})
    out.sort(key=lambda d: _host_rank(d["index"]))
    return out


def _load_saved() -> dict:
    if os.path.exists(_AUDIO_PATH):
        with open(_AUDIO_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    return {}


def save_devices(input_name: str | None, output_name: str | None) -> None:
    data = _load_saved()
    if input_name:
        data["input_name"] = input_name
    if output_name:
        data["output_name"] = output_name
    with open(_AUDIO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _score(name: str, kind: str = "input") -> int:
    n = name.lower()
    score = 0
    if kind == "input":
        for w in ("hands-free", "handsfree", "headset", "hfp", "earbud", "earbuds"):
            if w in n:
                score += 6
        if "stereo" in n or "a2dp" in n:
            score -= 8
        if "speaker" in n or "realtek" in n:
            score -= 3
    else:
        for w in ("stereo", "a2dp", "headphone", "headphones", "earbud", "earbuds"):
            if w in n:
                score += 6
        if "hands-free" in n or "handsfree" in n:
            score -= 5
        if "speaker" in n or "realtek" in n:
            score -= 1
    return score


def _match_name(kind: str, wanted: str | None) -> int | None:
    devices = list_devices(kind)
    if wanted:
        wanted_l = wanted.lower()
        hits = [d for d in devices if d["name"].lower() == wanted_l]
        if not hits:
            hits = [d for d in devices if wanted_l in d["name"].lower()]
        if hits:
            hits.sort(key=lambda d: (_host_rank(d["index"]), -_score(d["name"], kind)))
            return hits[0]["index"]
    ranked = sorted(
        devices,
        key=lambda d: (_host_rank(d["index"]), -_score(d["name"], kind)),
    )
    if ranked and _score(ranked[0]["name"], kind) > 0:
        return ranked[0]["index"]
    try:
        default = sd.default.device[0 if kind == "input" else 1]
        if isinstance(default, int) and default >= 0 and not _is_wdmks(default):
            return default
    except Exception:
        pass
    return ranked[0]["index"] if ranked else None


def input_device_index() -> int | None:
    return _match_name("input", _load_saved().get("input_name"))


def output_device_index() -> int | None:
    return _match_name("output", _load_saved().get("output_name"))


def current_labels() -> tuple[str, str]:
    ins = {d["index"]: d["name"] for d in list_devices("input")}
    outs = {d["index"]: d["name"] for d in list_devices("output")}
    i, o = input_device_index(), output_device_index()
    return ins.get(i, "Default mic"), outs.get(o, "Default speakers")


def _input_candidates() -> list[tuple[int | None, int]]:
    seen = set()
    out: list[tuple[int | None, int]] = []

    def add(idx: int | None, rate: int) -> None:
        key = (idx, rate)
        if key in seen:
            return
        if idx is not None and _is_wdmks(idx):
            return
        seen.add(key)
        out.append((idx, rate))

    primary = input_device_index()
    for rate in (8000, 16000, 44100, 48000):
        add(primary, rate)
    if primary is not None:
        try:
            add(primary, int(sd.query_devices(primary)["default_samplerate"]))
        except Exception:
            pass
    headsets = [
        d
        for d in list_devices("input")
        if _score(d["name"], "input") > 0 and d["index"] != primary
    ][:3]
    for d in headsets:
        add(d["index"], 8000)
        add(d["index"], 16000)
    add(None, 16000)
    return out


def _tts():
    global _engine
    _com_init()
    if _engine is None:
        _engine = pyttsx3.init()
        _engine.setProperty("rate", VOICE_RATE)
        _engine.setProperty("volume", 1.0)
        _pick_sapi_voice(_engine)
    return _engine


def stop_speaking() -> None:
    global _engine
    try:
        if _engine is not None:
            _engine.stop()
    except Exception:
        pass


def _pick_sapi_voice(engine) -> None:
    prefer = ("george", "hazel", "james", "mark", "david", "zira", "english")
    voices = engine.getProperty("voices") or []
    ranked = []
    for voice in voices:
        name = (voice.name or "").lower()
        for i, key in enumerate(prefer):
            if key in name:
                ranked.append((i, voice))
                break
    if ranked:
        ranked.sort(key=lambda x: x[0])
        engine.setProperty("voice", ranked[0][1].id)


def _edge_speak(text: str, path: str) -> None:
    import asyncio
    import edge_tts

    async def _run():
        comm = edge_tts.Communicate(text, TTS_VOICE, rate="-4%", pitch="-2Hz")
        await comm.save(path)

    asyncio.run(_run())


def _play_mp3(path: str) -> None:
    winmm = ctypes.windll.winmm
    alias = "areistts"
    full = os.path.abspath(path)
    buf = ctypes.create_unicode_buffer(255)

    def mci(cmd: str) -> None:
        err = winmm.mciSendStringW(cmd, buf, 254, None)
        if err:
            raise OSError(f"mci error {err}")

    mci(f'open "{full}" type mpegvideo alias {alias}')
    try:
        mci(f"play {alias} wait")
    finally:
        try:
            mci(f"close {alias}")
        except OSError:
            pass


def speak(text: str) -> None:
    text = _spoken(text)
    if not text:
        return
    _com_init()
    with _tts_lock:
        global _engine
        for _attempt in range(2):
            try:
                engine = _tts()
                engine.say(text)
                engine.runAndWait()
                return
            except Exception:
                _engine = None
        fd, wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            engine = _tts()
            engine.save_to_file(text, wav)
            engine.runAndWait()
            import winsound

            winsound.PlaySound(wav, winsound.SND_FILENAME)
        except Exception:
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass


def _spoken(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= 900:
        return text
    parts = []
    length = 0
    for sentence in text.replace("!", ".").replace("?", ".").split("."):
        sentence = sentence.strip()
        if not sentence:
            continue
        if length + len(sentence) > 900 and parts:
            break
        parts.append(sentence)
        length += len(sentence)
    return ". ".join(parts) + "."


def _rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


def _record_with(device: int | None, samplerate: int) -> np.ndarray | None:
    global _noise_floor
    block_ms = 0.1
    frames = int(samplerate * block_ms)
    spoken = []
    heard = False
    silent_blocks = 0
    start = time.monotonic()
    chunks: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, _frames, _time_info, status):
        if status:
            return
        chunks.put(indata.copy())

    kwargs = {
        "samplerate": samplerate,
        "channels": 1,
        "dtype": "int16",
        "blocksize": frames,
        "callback": callback,
        "latency": "high",
    }
    if device is not None:
        kwargs["device"] = device

    with sd.InputStream(**kwargs):
        if _noise_floor is None:
            samples = []
            deadline = time.monotonic() + 1.0
            while len(samples) < 5 and time.monotonic() < deadline:
                try:
                    data = chunks.get(timeout=0.3)
                except queue.Empty:
                    continue
                samples.append(_rms(data.reshape(-1)))
            if samples:
                _noise_floor = max(28.0, min(280.0, (sum(samples) / len(samples)) * 1.12))
            else:
                _noise_floor = 40.0

        threshold = _noise_floor
        while True:
            elapsed = time.monotonic() - start
            if not heard and elapsed > LISTEN_TIMEOUT:
                return None
            if heard and elapsed > PHRASE_LIMIT:
                break
            try:
                data = chunks.get(timeout=0.35)
            except queue.Empty:
                if heard:
                    silent_blocks += 1
                    if silent_blocks * block_ms >= SILENCE_END:
                        break
                continue
            chunk = data.reshape(-1).astype(np.float32) * MIC_GAIN
            level = _rms(chunk)
            stored = np.clip(chunk, -32768, 32767).astype(np.int16)
            if level >= threshold:
                if not heard:
                    try:
                        from control import snapshot_focus

                        snapshot_focus()
                    except Exception:
                        pass
                heard = True
                silent_blocks = 0
                spoken.append(stored)
            elif heard:
                spoken.append(stored)
                silent_blocks += 1
                if silent_blocks * block_ms >= SILENCE_END:
                    break

    if not spoken:
        return None
    audio = np.concatenate(spoken)
    if samplerate != _SAMPLE_RATE and audio.size:
        duration = audio.size / float(samplerate)
        target = max(1, int(duration * _SAMPLE_RATE))
        x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, target, endpoint=False)
        audio = np.interp(x_new, x_old, audio.astype(np.float32)).astype(np.int16)
    return audio


def _record() -> np.ndarray | None:
    global _ok_input
    _com_init()
    last_error = None
    ordered = []
    if _ok_input:
        ordered.append(_ok_input)
    ordered.extend(_input_candidates())
    tried = set()
    for device, rate in ordered:
        key = (device, rate)
        if key in tried:
            continue
        tried.add(key)
        try:
            result = _record_with(device, rate)
            _ok_input = (device, rate)
            return result
        except Exception as e:
            last_error = e
            if _ok_input == key:
                _ok_input = None
            continue
    if last_error:
        raise last_error
    return None


def reset_noise() -> None:
    global _noise_floor
    _noise_floor = None


def listen(prompt: str = "Listening…") -> str:
    global _recognizer, _noise_floor
    if _recognizer is None:
        _recognizer = sr.Recognizer()

    print(prompt)
    try:
        samples = _record()
    except Exception:
        _noise_floor = None
        samples = _record()
    if samples is None:
        return ""
    max_len = int(_SAMPLE_RATE * 12)
    if samples.size > max_len:
        samples = samples[-max_len:]

    audio = sr.AudioData(samples.tobytes(), _SAMPLE_RATE, 2)
    try:
        return _recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        raise RuntimeError(f"Speech recognition failed: {e}") from e
