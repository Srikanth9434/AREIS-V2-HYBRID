"""
AREIS API Key Manager

Maintains pools of API keys for each provider and rotates between them.
Keys marked as bad (for example, after HTTP 401/429 errors) are skipped
until the manager is reloaded.

Supported providers are whatever providers are present in keys.json.
"""

from itertools import cycle
from typing import Iterator

from config import load_keys, save_keys


class KeyManager:
    """
    Manages API-key pools for multiple providers.

    Features:
    - Multiple API keys per provider
    - Round-robin key rotation
    - Skips keys marked as bad
    - Prevents duplicate keys
    - Runtime key addition
    - Persistent storage through config.load_keys/save_keys
    - Safe reload/reset of key state
    """

    def __init__(self) -> None:
        self._pools: dict[str, Iterator[str]] = {}
        self._keys: dict[str, list[str]] = {}
        self._dead: dict[str, set[str]] = {}
        self.reload()

    def reload(self) -> None:
        """
        Reload all API keys from persistent storage.

        Reloading also clears the current dead-key state, because the
        underlying keys.json may have been changed since the manager
        was initialized.
        """
        raw = load_keys()

        self._pools.clear()
        self._keys.clear()
        self._dead.clear()

        if not isinstance(raw, dict):
            return

        for provider, keys in raw.items():
            provider = str(provider).strip().lower()

            if not provider:
                continue

            if not isinstance(keys, list):
                continue

            cleaned_keys = self._clean_keys(keys)

            if not cleaned_keys:
                continue

            self._keys[provider] = cleaned_keys
            self._dead[provider] = set()
            self._pools[provider] = cycle(cleaned_keys)

    def get(self, provider: str) -> str | None:
        """
        Return the next usable API key for a provider.

        Keys previously marked as bad are skipped.

        Returns:
            A usable API key, or None if no usable key exists.
        """
        provider = self._normalize_provider(provider)

        if not provider:
            return None

        pool = self._pools.get(provider)
        keys = self._keys.get(provider)

        if pool is None or not keys:
            return None

        dead = self._dead.setdefault(provider, set())

        # Check at most one complete rotation through the pool.
        for _ in range(len(keys)):
            key = next(pool)

            if key not in dead:
                return key

    def mark_bad(self, provider: str, key: str) -> None:
        """
        Mark an API key as unusable for the current manager session.

        Typically called after authentication/rate-limit failures such
        as HTTP 401 or HTTP 429.
        """
        provider = self._normalize_provider(provider)
        key = self._normalize_key(key)

        if not provider or not key:
            return

        if provider not in self._keys:
            return

        if key not in self._keys[provider]:
            return

        self._dead.setdefault(provider, set()).add(key)

    def has_provider(self, provider: str) -> bool:
        """
        Return True if the provider has at least one configured key.
        """
        provider = self._normalize_provider(provider)

        if not provider:
            return False

        return bool(self._keys.get(provider))

    def has_usable_key(self, provider: str) -> bool:
        """
        Return True if the provider currently has at least one key that
        has not been marked bad.
        """
        provider = self._normalize_provider(provider)

        if not provider:
            return False

        keys = self._keys.get(provider, [])
        dead = self._dead.get(provider, set())

        return any(key not in dead for key in keys)


    def add_key(self, provider: str, key: str) -> bool:
        """
        Add a new API key to a provider and persist it.

        Returns:
            True if the key was added.
            False if the input was invalid or already existed.
        """
        provider = self._normalize_provider(provider)
        key = self._normalize_key(key)

        if not provider or not key:
            return False

        data = load_keys()

        if not isinstance(data, dict):
            data = {}

        keys = data.setdefault(provider, [])

        if not isinstance(keys, list):
            keys = []
            data[provider] = keys

        # Prevent duplicate keys.
        if key in keys:
            return False

        keys.insert(0, key)

        save_keys(data)
        self.reload()

        return True

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        """
        Normalize provider names so 'OpenAI', ' openai ', etc. resolve
        consistently.
        """
        if not isinstance(provider, str):
            return ""

        return provider.strip().lower()

    @staticmethod
    def _normalize_key(key: str) -> str:
        """
        Normalize an API key without modifying its internal characters.
        """
        if not isinstance(key, str):
            return ""

        return key.strip()

    @classmethod
    def _clean_keys(cls, keys: list) -> list[str]:
        """
        Remove invalid, empty, and duplicate keys while preserving order.
        """
        cleaned: list[str] = []
        seen: set[str] = set()

        for key in keys:
            normalized = cls._normalize_key(key)

            if not normalized:
                continue

            if normalized in seen:
                continue

            seen.add(normalized)
            cleaned.append(normalized)

        return cleaned

key_manager = KeyManager()