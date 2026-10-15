from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class RasServer:
    name: str
    host: str
    port: int = 1545
    cluster_user: str = ""
    cluster_password: str = ""
    cluster_user_env: str = ""
    cluster_password_env: str = ""

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    rac_path: Path
    command_timeout_seconds: float
    log_file: Path
    servers: tuple[RasServer, ...]
    poll_interval_seconds: float = 2.0
    topology_refresh_seconds: float = 60.0


def _resolve_secret(item: dict[str, object], direct_key: str, env_key: str) -> str:
    env_name = str(item.get(env_key, "")).strip()
    if env_name:
        return os.environ.get(env_name, "")
    return str(item.get(direct_key, ""))


def _find_rac(configured: str) -> Path:
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which("rac.exe") or shutil.which("rac")
    if found:
        return Path(found).resolve()
    roots = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "1cv8",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "1cv8",
    )
    candidates: list[Path] = []
    for root in roots:
        if root.is_dir():
            candidates.extend(root.glob("*/bin/rac.exe"))
    if candidates:
        return sorted(candidates, reverse=True)[0].resolve()
    if configured:
        raise ConfigurationError(
            f"rac.exe не найден по сохранённому пути {candidate} и не обнаружен в установленных версиях 1С"
        )
    raise ConfigurationError(
        "rac.exe не найден. Установите платформу 1С с компонентами администрирования сервера"
    )


def load_settings(path: Path) -> Settings:
    if not path.is_file():
        raise ConfigurationError(f"файл настроек не найден: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"не удалось прочитать {path}: {exc}") from exc
    raw_servers = data.get("servers")
    if not isinstance(raw_servers, list) or not raw_servers:
        raise ConfigurationError("нужно указать хотя бы один сервер RAS")
    servers: list[RasServer] = []
    for index, item in enumerate(raw_servers, 1):
        if not isinstance(item, dict) or not str(item.get("host", "")).strip():
            raise ConfigurationError(f"для RAS-сервера №{index} не указан адрес")
        servers.append(RasServer(
            name=str(item.get("name") or item["host"]),
            host=str(item["host"]),
            port=int(item.get("port", 1545)),
            cluster_user=_resolve_secret(item, "cluster_user", "cluster_user_env"),
            cluster_password=_resolve_secret(item, "cluster_password", "cluster_password_env"),
            cluster_user_env=str(item.get("cluster_user_env", "")).strip(),
            cluster_password_env=str(item.get("cluster_password_env", "")).strip(),
        ))
    timeout = float(data.get("command_timeout_seconds", 30))
    if timeout <= 0:
        raise ConfigurationError("тайм-аут команды должен быть больше нуля")
    poll_interval = float(data.get("poll_interval_seconds", 2))
    if poll_interval <= 0:
        raise ConfigurationError("интервал обновления сеансов должен быть больше нуля")
    topology_refresh = float(data.get("topology_refresh_seconds", 60))
    if topology_refresh <= 0:
        raise ConfigurationError("интервал обновления структуры серверов должен быть больше нуля")
    return Settings(
        rac_path=_find_rac(str(data.get("rac_path", ""))),
        command_timeout_seconds=timeout,
        log_file=Path(str(data.get("log_file", "logs/session-control.log"))),
        servers=tuple(servers),
        poll_interval_seconds=poll_interval,
        topology_refresh_seconds=topology_refresh,
    )


def save_settings(path: Path, settings: Settings) -> None:
    """Atomically persist settings without writing cluster credentials."""

    def quoted(value: object) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    lines = [
        "# This file is managed by the web settings page.",
        f"rac_path = {quoted(settings.rac_path)}",
        f"command_timeout_seconds = {settings.command_timeout_seconds:g}",
        f"log_file = {quoted(settings.log_file)}",
        f"poll_interval_seconds = {settings.poll_interval_seconds:g}",
        f"topology_refresh_seconds = {settings.topology_refresh_seconds:g}",
    ]
    for server in settings.servers:
        lines.extend((
            "",
            "[[servers]]",
            f"name = {quoted(server.name)}",
            f"host = {quoted(server.host)}",
            f"port = {server.port}",
        ))
        if server.cluster_user_env:
            lines.append(f"cluster_user_env = {quoted(server.cluster_user_env)}")
        if server.cluster_password_env:
            lines.append(f"cluster_password_env = {quoted(server.cluster_password_env)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigurationError(f"не удалось записать {path}: {exc}") from exc
