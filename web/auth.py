from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class ClusterCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class _CredentialSession:
    credentials: ClusterCredentials
    expires_at: float


class CredentialStore:
    """Keep cluster credentials in process memory, indexed by an opaque cookie."""

    def __init__(self, ttl_seconds: int = 8 * 60 * 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, _CredentialSession] = {}
        self._lock = Lock()

    def create(self, credentials: ClusterCredentials) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_expired()
            self._sessions[token] = _CredentialSession(
                credentials=credentials,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
        return token

    def get(self, token: str | None) -> ClusterCredentials | None:
        if not token:
            return None
        with self._lock:
            self._purge_expired()
            session = self._sessions.get(token)
            if session is None:
                return None
            self._sessions[token] = _CredentialSession(
                credentials=session.credentials,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
            return session.credentials

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            del self._sessions[token]
