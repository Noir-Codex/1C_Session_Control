from __future__ import annotations

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

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    rac_path: Path
    command_timeout_seconds: float
    log_file: Path
    servers: tuple[RasServer, ...]


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
        raise ConfigurationError(f"rac.exe not found at configured path: {candidate}")
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
    raise ConfigurationError("rac.exe was not found; set rac_path in config.toml")


def load_settings(path: Path) -> Settings:
    if not path.is_file():
        raise ConfigurationError(f"file not found: {path} (copy config.example.toml)")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    raw_servers = data.get("servers")
    if not isinstance(raw_servers, list) or not raw_servers:
        raise ConfigurationError("at least one [[servers]] entry is required")
    servers: list[RasServer] = []
    for index, item in enumerate(raw_servers, 1):
        if not isinstance(item, dict) or not str(item.get("host", "")).strip():
            raise ConfigurationError(f"servers entry #{index} requires host")
        servers.append(RasServer(
            name=str(item.get("name") or item["host"]),
            host=str(item["host"]),
            port=int(item.get("port", 1545)),
            cluster_user=_resolve_secret(item, "cluster_user", "cluster_user_env"),
            cluster_password=_resolve_secret(item, "cluster_password", "cluster_password_env"),
        ))
    timeout = float(data.get("command_timeout_seconds", 30))
    if timeout <= 0:
        raise ConfigurationError("command_timeout_seconds must be positive")
    return Settings(
        rac_path=_find_rac(str(data.get("rac_path", ""))),
        command_timeout_seconds=timeout,
        log_file=Path(str(data.get("log_file", "logs/session-control.log"))),
        servers=tuple(servers),
    )
