from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import ConfigurationError, load_settings
from onec.discovery import DiscoveryService
from onec.rac import RacClient, RacError
from reporting.console import print_report
from web.auth import CredentialStore
from web.company_store import CompanyStore
from web.monitor import MonitorService
from web.routes_v2 import router


def configure_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError as exc:
        print(f"Внимание: не удалось открыть файл журнала {log_file}: {exc}", file=sys.stderr)
    logging.basicConfig(level=level, format=formatter._fmt, datefmt=formatter.datefmt, handlers=handlers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only 1C cluster session monitor")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--check", action="store_true", help="only check rac and RAS connectivity")
    return parser.parse_args()


@asynccontextmanager
async def lifespan(application: FastAPI):
    config_path = Path(os.environ.get("SESSION_CONTROL_CONFIG", "config.toml"))
    settings = load_settings(config_path)
    configure_logging(settings.log_file, False)
    application.state.settings = settings
    application.state.config_path = config_path
    application.state.rac = RacClient(settings.rac_path, settings.command_timeout_seconds)
    application.state.credentials = CredentialStore()
    data_directory = Path(os.environ.get("SESSION_CONTROL_DATA_DIR", "data"))
    application.state.companies = CompanyStore(data_directory / "session-control.db")
    application.state.monitor = MonitorService(
        application.state.rac,
        settings.servers,
        application.state.companies,
        poll_seconds=settings.poll_interval_seconds,
        topology_seconds=settings.topology_refresh_seconds,
    )
    application.state.monitor.start()
    try:
        yield
    finally:
        application.state.monitor.stop()


def create_app() -> FastAPI:
    application = FastAPI(
        title="1C Session Control",
        description="Real-time monitoring and selective control of 1C user sessions through RAS/RAC",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "web" / "static"),
        name="static",
    )
    application.include_router(router)
    return application


api = create_app()


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        print(f"Ошибка настроек: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_file, args.verbose)
    rac = RacClient(settings.rac_path, settings.command_timeout_seconds)
    try:
        version = rac.version()
    except RacError as exc:
        logging.error("Проверка rac.exe завершилась с ошибкой: %s", exc)
        return 3

    print(f"RAC: {settings.rac_path}")
    print(f"Version: {version or 'available (version not reported)'}")
    failures = 0
    for server in settings.servers:
        try:
            report = DiscoveryService(rac).collect(server, include_sessions=not args.check)
            print_report(report, connectivity_only=args.check)
        except RacError as exc:
            failures += 1
            logging.error("RAS %s недоступен: %s", server.endpoint, exc)
            print(f"\nRAS {server.name} ({server.endpoint}): ERROR — {exc}")
    return 1 if failures == len(settings.servers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
