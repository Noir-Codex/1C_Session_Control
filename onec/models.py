from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cluster:
    id: str
    name: str = ""
    host: str = ""
    port: str = ""
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Infobase:
    id: str
    name: str
    cluster_id: str
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    id: str
    cluster_id: str
    infobase_id: str = ""
    infobase_name: str = ""
    user_name: str = ""
    host: str = ""
    app_id: str = ""
    started_at: str = ""
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClusterSnapshot:
    cluster: Cluster
    infobases: tuple[Infobase, ...]
    sessions: tuple[Session, ...]


@dataclass(frozen=True)
class ServerSnapshot:
    server_name: str
    endpoint: str
    clusters: tuple[ClusterSnapshot, ...]
