from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from config import RasServer
from onec.discovery import DiscoveryService
from onec.models import ClusterSnapshot, ServerSnapshot, Session
from onec.rac import RacClient, RacError
from web.auth import ClusterCredentials
from web.company_store import Company, CompanyStore

log = logging.getLogger(__name__)

# RAC app-id values for sessions created by an interactive 1C client.  The
# allow-list is deliberately conservative: an unknown application is treated
# as technical and is never terminated automatically.
INTERACTIVE_APP_IDS = frozenset({"1cv8", "1cv8c", "webclient", "mobileclient"})
DEFAULT_ADMIN_USERS = frozenset({"admin", "administrator", "администратор"})
APPLICATION_NAMES = {
    "1cv8c": "1С:Предприятие (тонкий клиент)",
    "1cv8": "1С:Предприятие (толстый клиент)",
    "webclient": "Веб-клиент",
    "mobileclient": "Мобильный клиент",
    "designer": "Конфигуратор",
    "backgroundjob": "Фоновое задание",
    "comconnection": "COM-соединение",
    "webservice": "Веб-сервис",
    "httpservice": "HTTP-сервис",
}


def _normalized(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def application_name(value: object) -> str:
    """Return a Russian display name for a RAC application identifier."""
    raw = str(value).strip()
    return APPLICATION_NAMES.get(_normalized(raw), raw or "Не указан")


def is_user_session(
    session: dict[str, Any],
    *,
    cluster_admin: str = "",
    excluded_users: tuple[str, ...] = (),
) -> bool:
    """Return True only for an interactive, non-administrative user session."""
    if not str(session.get("id", "")).strip():
        return False
    if _normalized(session.get("app_id", "")) not in INTERACTIVE_APP_IDS:
        return False
    user = str(session.get("user_name", "")).strip().casefold()
    excluded = set(DEFAULT_ADMIN_USERS)
    excluded.update(value.strip().casefold() for value in excluded_users if value.strip())
    if cluster_admin.strip():
        excluded.add(cluster_admin.strip().casefold())
    return bool(user) and user not in excluded


class MonitorService:
    """Poll RAC in the background and expose an instant in-memory snapshot."""

    def __init__(
        self,
        rac: RacClient,
        servers: tuple[RasServer, ...],
        companies: CompanyStore,
        *,
        poll_seconds: float = 2.0,
        topology_seconds: float = 60.0,
    ) -> None:
        self.rac = rac
        self.servers = servers
        self.companies = companies
        self.poll_seconds = poll_seconds
        self.topology_seconds = topology_seconds
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._credentials: ClusterCredentials | None = None
        self._topology: dict[str, ServerSnapshot] = {}
        self._topology_at = 0.0
        self._snapshots: list[dict[str, Any]] = []
        self._errors: list[dict[str, str]] = []
        self._last_updated: str | None = None
        self._refreshing = False
        self._revision = 0
        self._control_status: dict[int, dict[str, str]] = {}
        self._recent_attempts: dict[str, float] = {}
        self._thread = threading.Thread(target=self._loop, name="rac-monitor", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def set_credentials(self, credentials: ClusterCredentials) -> None:
        with self._lock:
            changed = credentials != self._credentials
            self._credentials = credentials
            if changed:
                self._topology = {}
                self._topology_at = 0.0
        self._wake.set()

    def clear_credentials(self) -> None:
        with self._lock:
            self._credentials = None
            self._snapshots = []
            self._errors = []
            self._last_updated = None
        self._wake.set()

    def refresh_now(self, *, topology: bool = False) -> None:
        if topology:
            with self._lock:
                self._topology_at = 0.0
        self._wake.set()

    def reconfigure(
        self,
        rac: RacClient,
        servers: tuple[RasServer, ...],
        *,
        poll_seconds: float,
        topology_seconds: float,
    ) -> None:
        """Apply connection and polling settings to the running monitor."""
        with self._lock:
            self.rac = rac
            self.servers = servers
            self.poll_seconds = poll_seconds
            self.topology_seconds = topology_seconds
            self._topology = {}
            self._topology_at = 0.0
            self._errors = []
        self._wake.set()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "servers": copy.deepcopy(self._snapshots),
                "errors": copy.deepcopy(self._errors),
                "last_updated": self._last_updated,
                "refreshing": self._refreshing,
                "revision": self._revision,
                "control_status": copy.deepcopy(self._control_status),
            }

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                credentials = self._credentials
            if credentials is None:
                self._wake.wait(30)
                self._wake.clear()
                continue
            try:
                self._refresh(credentials)
            except Exception:
                log.exception("Непредвиденная ошибка фонового мониторинга")
                with self._lock:
                    self._refreshing = False
                    self._errors = [{
                        "server": "monitor",
                        "endpoint": "",
                        "error": "Ошибка фонового опроса; подробности в журнале",
                    }]
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    @staticmethod
    def _server_with_credentials(server: RasServer, credentials: ClusterCredentials) -> RasServer:
        return replace(server, cluster_user=credentials.username, cluster_password=credentials.password)

    def _refresh_topology(self, credentials: ClusterCredentials) -> tuple[dict[str, ServerSnapshot], list[dict[str, str]]]:
        topology: dict[str, ServerSnapshot] = {}
        errors: list[dict[str, str]] = []
        service = DiscoveryService(self.rac)
        for configured in self.servers:
            server = self._server_with_credentials(configured, credentials)
            try:
                topology[server.endpoint] = service.collect(server, include_sessions=False)
            except RacError as exc:
                log.error("Не удалось обновить структуру RAS %s: %s", server.endpoint, exc)
                errors.append({"server": server.name, "endpoint": server.endpoint, "error": str(exc)})
        return topology, errors

    def _refresh(self, credentials: ClusterCredentials) -> None:
        with self._lock:
            self._refreshing = True
            topology = dict(self._topology)
            topology_at = self._topology_at
        errors: list[dict[str, str]] = []
        now = time.monotonic()
        if not topology or now - topology_at >= self.topology_seconds:
            refreshed, topology_errors = self._refresh_topology(credentials)
            errors.extend(topology_errors)
            if refreshed:
                topology = refreshed
                topology_at = now

        snapshots: list[dict[str, Any]] = []
        for configured in self.servers:
            server = self._server_with_credentials(configured, credentials)
            cached = topology.get(server.endpoint)
            if cached is None:
                continue
            clusters: list[ClusterSnapshot] = []
            for cluster_snapshot in cached.clusters:
                cluster_id = cluster_snapshot.cluster.id
                try:
                    records = self.rac.list_sessions(
                        server.endpoint, cluster_id, credentials.username, credentials.password
                    )
                    base_names = {base.id: base.name for base in cluster_snapshot.infobases}
                    sessions = tuple(
                        Session(
                            id=item.get("session", item.get("session-id", "")),
                            cluster_id=cluster_id,
                            infobase_id=item.get("infobase", ""),
                            infobase_name=base_names.get(item.get("infobase", ""), ""),
                            user_name=item.get("user-name", item.get("user", "")),
                            host=item.get("host", item.get("computer", "")),
                            app_id=item.get("app-id", item.get("application", "")),
                            started_at=item.get("started-at", item.get("start-time", "")),
                            raw=item,
                        )
                        for item in records
                    )
                except RacError as exc:
                    log.error("Не удалось обновить сеансы кластера %s: %s", cluster_id, exc)
                    errors.append({"server": server.name, "endpoint": server.endpoint, "error": str(exc)})
                    sessions = ()
                clusters.append(ClusterSnapshot(cluster_snapshot.cluster, cluster_snapshot.infobases, sessions))
            snapshots.append(asdict(ServerSnapshot(cached.server_name, cached.endpoint, tuple(clusters))))

        with self._lock:
            if credentials != self._credentials:
                self._refreshing = False
                return
            self._topology = topology
            self._topology_at = topology_at
            self._snapshots = snapshots
            self._errors = errors
            self._last_updated = datetime.now(timezone.utc).isoformat()
            self._refreshing = False
            self._revision += 1

        self._enforce(snapshots, credentials)

    @staticmethod
    def _sessions_for_company(company: Company, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assigned = {
            (base.server_endpoint, base.cluster_id, base.infobase_id)
            for base in company.bases
        }
        result: list[dict[str, Any]] = []
        for server in snapshots:
            endpoint = str(server["endpoint"])
            for cluster_snapshot in server["clusters"]:
                cluster_id = str(cluster_snapshot["cluster"]["id"])
                for session in cluster_snapshot["sessions"]:
                    if (endpoint, cluster_id, str(session.get("infobase_id", ""))) in assigned:
                        item = dict(session)
                        item["server_endpoint"] = endpoint
                        item["cluster_id"] = cluster_id
                        result.append(item)
        return result

    def _enforce(self, snapshots: list[dict[str, Any]], credentials: ClusterCredentials) -> None:
        companies = self.companies.list_companies()
        now = time.monotonic()
        self._recent_attempts = {
            session_id: attempted_at
            for session_id, attempted_at in self._recent_attempts.items()
            if now - attempted_at < 60
        }
        for company in companies:
            if not company.enforcement_enabled or company.session_limit <= 0:
                continue
            eligible = [
                session
                for session in self._sessions_for_company(company, snapshots)
                if is_user_session(
                    session,
                    cluster_admin=credentials.username,
                    excluded_users=company.excluded_users,
                )
            ]
            eligible.sort(key=lambda item: (str(item.get("started_at", "")), str(item.get("id", ""))))
            excess = eligible[company.session_limit :]
            if not excess:
                continue
            terminated = 0
            failures: list[str] = []
            for session in reversed(excess):
                session_id = str(session["id"])
                if session_id in self._recent_attempts:
                    continue
                self._recent_attempts[session_id] = now
                try:
                    self.rac.terminate_session(
                        str(session["server_endpoint"]),
                        str(session["cluster_id"]),
                        session_id,
                        credentials.username,
                        credentials.password,
                        f"Превышен лимит сеансов компании {company.name}",
                    )
                    terminated += 1
                    log.warning(
                        "Завершён лишний пользовательский сеанс %s для компании «%s»",
                        session_id,
                        company.name,
                    )
                except RacError as exc:
                    failures.append(str(exc))
                    log.error("Не удалось завершить сеанс %s: %s", session_id, exc)
            status = {
                "at": datetime.now(timezone.utc).isoformat(),
                "message": f"Завершено лишних пользовательских сеансов: {terminated}",
            }
            if failures:
                status["error"] = failures[0]
            with self._lock:
                self._control_status[company.id] = status
            if terminated:
                self._wake.set()
