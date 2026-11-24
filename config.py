"""
Central config. Edit the paths/values here — nothing else in the
codebase should hardcode a model path, port, or API key.
"""
import os
import json

# --- Local LLM (served by llama-server.exe) ---------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))

LLAMA_SERVER_HOST = "127.0.0.1"
# Own port so we never attach to the tiny llama.cpp browser chat on 8080.
LLAMA_SERVER_PORT = 8090
LLAMA_SERVER_URL = f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/v1/chat/completions"

_LOCAL_SERVER = os.path.join(_ROOT, "llama-cuda", "llama-server.exe")
_FALLBACK_SERVER = os.environ.get(
    "AREIS_LLAMA_SERVER",
    r"C:\Users\srikanth\Desktop\AREIS_PP\dist\cuda\llama-b10853-bin-win-cuda-12.4-x64\llama-server.exe",
)
LLAMA_SERVER_EXE = _LOCAL_SERVER if os.path.exists(_LOCAL_SERVER) else _FALLBACK_SERVER

MODEL_PATH = os.environ.get(
    "AREIS_MODEL_PATH",
    os.path.join(_ROOT, "models", "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"),
)

# 2048 keeps the 7B Q4 model on 6GB VRAM.
CONTEXT_SIZE = 2048

MAX_TOKENS = int(os.environ.get("AREIS_MAX_TOKENS", "512"))
TEMPERATURE = float(os.environ.get("AREIS_TEMPERATURE", "0.7"))
REQUEST_TIMEOUT = int(os.environ.get("AREIS_TIMEOUT", "45"))

# Fast cloud models by default; override with env vars if you want larger ones.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# NVIDIA NIM. Super 120B is the working model on this key (fast, strong at code).
# Ultra 550B is listed but generate often 404s; used only as a fallback.
NVIDIA_ULTRA_MODEL = os.environ.get(
    "NVIDIA_ULTRA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"
)
NVIDIA_SUPER_MODEL = os.environ.get(
    "NVIDIA_SUPER_MODEL", "nvidia/nemotron-3-super-120b-a12b"
)
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", NVIDIA_SUPER_MODEL)
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_TIMEOUT = int(os.environ.get("NVIDIA_TIMEOUT", "45"))
NVIDIA_MAX_TOKENS = int(os.environ.get("NVIDIA_MAX_TOKENS", "512"))

# --- Voice -----------------------------------------------------------------
VOICE_RATE = int(os.environ.get("AREIS_VOICE_RATE", "195"))
TTS_VOICE = os.environ.get("AREIS_TTS_VOICE", "en-GB-ThomasNeural")
LISTEN_TIMEOUT = 30
PHRASE_LIMIT = 14
SILENCE_END = 1.15
MIC_GAIN = float(os.environ.get("AREIS_MIC_GAIN", "10"))

# --- API keys -----------------------------------------------------------
# Load from a local, gitignored keys.json so nothing sensitive lives in code.
# Format: { "openai": ["sk-..."], "anthropic": ["sk-ant-..."], "nvidia": ["nvapi-..."] }
_KEYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json")


def model_files_ready() -> tuple[bool, str]:
    if not os.path.exists(MODEL_PATH):
        return False, f"Model not found:\n{MODEL_PATH}"
    if "00001-of-00002" in MODEL_PATH:
        other = MODEL_PATH.replace("00001-of-00002", "00002-of-00002")
        if not os.path.exists(other) or os.path.getsize(other) < 1_000_000:
            return False, (
                "The 7B model is split into two files. Waiting for "
                f"{os.path.basename(other)} to finish downloading."
            )
    return True, MODEL_PATH


def keys_path() -> str:
    return _KEYS_PATH


def load_keys() -> dict:
    data = {}
    if os.path.exists(_KEYS_PATH):
        with open(_KEYS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}

    # Env vars work even without a keys.json (handy for a one-off session).
    env_openai = os.environ.get("OPENAI_API_KEY")
    env_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    env_nvidia = os.environ.get("NVIDIA_API_KEY")
    if env_openai:
        data.setdefault("openai", [])
        if env_openai not in data["openai"]:
            data["openai"].insert(0, env_openai)
    if env_anthropic:
        data.setdefault("anthropic", [])
        if env_anthropic not in data["anthropic"]:
            data["anthropic"].insert(0, env_anthropic)
    if env_nvidia:
        data.setdefault("nvidia", [])
        if env_nvidia not in data["nvidia"]:
            data["nvidia"].insert(0, env_nvidia)
    return data


def save_keys(data: dict) -> None:
    with open(_KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
