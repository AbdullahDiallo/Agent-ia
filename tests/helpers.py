from __future__ import annotations

import time
from typing import Any, Dict, Optional


class FakeRedis:
    def __init__(self) -> None:
        self.store: Dict[str, bytes] = {}
        self.expiry: Dict[str, float] = {}

    def _cleanup(self, key: str) -> None:
        exp = self.expiry.get(key)
        if exp is not None and exp <= time.time():
            self.store.pop(key, None)
            self.expiry.pop(key, None)

    def get(self, key: str) -> Optional[bytes]:
        self._cleanup(key)
        return self.store.get(key)

    def exists(self, key: str) -> int:
        self._cleanup(key)
        return 1 if key in self.store else 0

    def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False):
        self._cleanup(key)
        if nx and key in self.store:
            return False
        self.store[key] = value if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")
        if ex:
            self.expiry[key] = time.time() + int(ex)
        else:
            self.expiry.pop(key, None)
        return True

    def setex(self, key: str, ttl: int, value: Any):
        return self.set(key, value, ex=ttl, nx=False)

    def incr(self, key: str) -> int:
        self._cleanup(key)
        current = int((self.store.get(key) or b"0").decode("utf-8"))
        current += 1
        self.store[key] = str(current).encode("utf-8")
        return current

    def expire(self, key: str, ttl: int) -> bool:
        self._cleanup(key)
        if key not in self.store:
            return False
        self.expiry[key] = time.time() + int(ttl)
        return True

    def ttl(self, key: str) -> int:
        self._cleanup(key)
        if key not in self.store:
            return -2
        exp = self.expiry.get(key)
        if exp is None:
            return -1
        return max(0, int(exp - time.time()))

    def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            self._cleanup(key)
            if key in self.store:
                count += 1
                self.store.pop(key, None)
            self.expiry.pop(key, None)
        return count
