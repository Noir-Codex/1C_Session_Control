from __future__ import annotations

import logging

from config import RasServer
from onec.models import Cluster, ClusterSnapshot, Infobase, ServerSnapshot, Session
from onec.rac import RacClient, RacError

log = logging.getLogger(__name__)


def _value(record: dict[str, str], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if value is not None:
            return value
    return ""


class DiscoveryService:
    def __init__(self, rac: RacClient) -> None:
        self.rac = rac

    def collect(self, server: RasServer, include_sessions: bool = True) -> ServerSnapshot:
        clusters: list[ClusterSnapshot] = []
        for item in self.rac.list_clusters(server.endpoint):
            cluster_id = _value(item, "cluster")
            if not cluster_id:
                continue
            cluster = Cluster(cluster_id, _value(item, "name"), _value(item, "host"), _value(item, "port"), item)
            try:
                bases_raw = self.rac.list_infobases(
                    server.endpoint, cluster_id, server.cluster_user, server.cluster_password
                )
                sessions_raw = self.rac.list_sessions(
                    server.endpoint, cluster_id, server.cluster_user, server.cluster_password
                ) if include_sessions else []
            except RacError as exc:
                # A broken cluster must not stop discovery on other clusters or
                # on other configured RAS servers.
                log.error("Недоступный кластер %s на %s пропущен: %s", cluster_id, server.endpoint, exc)
                continue
            bases = tuple(
                Infobase(_value(x, "infobase"), _value(x, "name"), cluster_id, x)
                for x in bases_raw if _value(x, "infobase")
            )
            names = {base.id: base.name for base in bases}
            sessions = tuple(
                Session(
                    id=_value(x, "session", "session-id"),
                    cluster_id=cluster_id,
                    infobase_id=_value(x, "infobase"),
                    infobase_name=names.get(_value(x, "infobase"), ""),
                    user_name=_value(x, "user-name", "user"),
                    host=_value(x, "host", "computer"),
                    app_id=_value(x, "app-id", "application"),
                    started_at=_value(x, "started-at", "start-time"),
                    raw=x,
                ) for x in sessions_raw
            )
            clusters.append(ClusterSnapshot(cluster, bases, sessions))
        return ServerSnapshot(server.name, server.endpoint, tuple(clusters))
