from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import ConfigurationError, Settings, load_settings, save_settings


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", socket.gethostname().casefold()}


def port_is_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_this_application(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            data = json.load(response)
        return data.get("mode") == "SELECTIVE_CONTROL"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def ensure_ras(settings: Settings) -> None:
    """Start a missing local RAS, but never prevent the web app from starting."""
    ras_path = settings.rac_path.with_name("ras.exe")
    agent_port = int(os.environ.get("SESSION_CONTROL_AGENT_PORT", "1540"))
    log_path = Path("logs/ras-launch.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for server in settings.servers:
        if port_is_open(server.host, server.port):
            print(f"RAS доступен: {server.endpoint}")
            continue
        if server.host.casefold() not in LOCAL_HOSTS:
            print(f"ВНИМАНИЕ: удалённый RAS {server.endpoint} недоступен; веб-приложение всё равно запустится")
            continue
        if not ras_path.is_file():
            print(f"ВНИМАНИЕ: {ras_path} не найден; веб-приложение всё равно запустится")
            continue

        command = [
            str(ras_path),
            "cluster",
            f"--port={server.port}",
            f"{server.host}:{agent_port}",
        ]
        try:
            log_file = log_path.open("ab")
            subprocess.Popen(
                command,
                cwd=Path.cwd(),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            print(f"ВНИМАНИЕ: не удалось запустить RAS: {exc}; веб-приложение всё равно запустится")
            continue

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not port_is_open(server.host, server.port):
            time.sleep(0.25)
        if port_is_open(server.host, server.port):
            print(f"RAS запущен: {server.endpoint}")
        else:
            print(f"ВНИМАНИЕ: RAS не открыл {server.endpoint}; веб-приложение всё равно запустится")


def choose_web_port(preferred: int) -> int | None:
    if not port_is_open("127.0.0.1", preferred):
        return preferred
    if is_this_application(preferred):
        return None
    for port in range(preferred + 1, preferred + 11):
        if not port_is_open("127.0.0.1", port):
            print(f"ВНИМАНИЕ: порт {preferred} занят; используется порт {port}")
            return port
    raise RuntimeError(f"порты {preferred}-{preferred + 10} заняты")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start RAS when needed and run 1C Session Control")
    parser.add_argument("--check", action="store_true", help="check paths and ports without starting anything")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(os.environ.get("SESSION_CONTROL_CONFIG", "config.toml"))
    if not config_path.is_file():
        example_path = Path("config.example.toml")
        if not example_path.is_file():
            print("ОШИБКА: не найден шаблон config.example.toml", file=sys.stderr)
            return 2
        config_path.write_text(example_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"Создан файл настроек: {config_path}")
    try:
        settings = load_settings(config_path)
        save_settings(config_path, settings)
    except ConfigurationError as exc:
        print(f"ОШИБКА НАСТРОЕК: {exc}", file=sys.stderr)
        return 2

    web_port = int(os.environ.get("SESSION_CONTROL_PORT", "8000"))
    if args.check:
        print(f"RAC: {settings.rac_path}")
        print(f"RAS: {settings.rac_path.with_name('ras.exe')}")
        for server in settings.servers:
            state = "доступен" if port_is_open(server.host, server.port) else "недоступен"
            print(f"RAS {server.endpoint}: {state}")
        print(f"Веб-порт {web_port}: {'занят' if port_is_open('127.0.0.1', web_port) else 'свободен'}")
        return 0

    try:
        ensure_ras(settings)
    except Exception as exc:
        # RAS is optional for process startup.  The login page will show its
        # connectivity error while the web service remains available.
        print(f"ВНИМАНИЕ: подготовка RAS завершилась с ошибкой: {exc}; веб-приложение всё равно запустится")

    port = choose_web_port(web_port)
    if port is None:
        print(f"Контроль сеансов 1С уже запущен: http://127.0.0.1:{web_port}")
        return 0

    import uvicorn

    print(f"Запуск контроля сеансов 1С: http://127.0.0.1:{port}")
    uvicorn.run("app:api", host="0.0.0.0", port=port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
