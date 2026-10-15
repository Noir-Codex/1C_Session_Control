from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import ConfigurationError, RasServer, Settings, save_settings
from onec.rac import RacClient, RacError
from web.auth import ClusterCredentials
from web.company_store import AssignedInfobase, Company, CompanyStoreError
from web.monitor import application_name, is_user_session

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
AUTH_COOKIE = "session_control_auth"
AUTH_MAX_AGE = 8 * 60 * 60


def _validate_credentials(request: Request, credentials: ClusterCredentials) -> None:
    checked = 0
    for server in request.app.state.settings.servers:
        for item in request.app.state.rac.list_clusters(server.endpoint):
            cluster_id = item.get("cluster", "")
            if not cluster_id:
                continue
            request.app.state.rac.list_infobases(
                server.endpoint, cluster_id, credentials.username, credentials.password
            )
            checked += 1
    if checked == 0:
        raise RacError("RAS не вернул ни одного доступного кластера")


def _base_key(server_endpoint: str, cluster_id: str, infobase_id: str) -> str:
    raw = "\0".join((server_endpoint, cluster_id, infobase_id)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _available_bases(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for server in snapshots:
        endpoint = str(server["endpoint"])
        server_name = str(server["server_name"])
        for cluster_snapshot in server["clusters"]:  # type: ignore[index]
            cluster = cluster_snapshot["cluster"]
            cluster_id = str(cluster["id"])
            cluster_name = str(cluster["name"] or cluster_id)
            sessions_by_base: dict[str, list[dict[str, object]]] = {}
            for session in cluster_snapshot["sessions"]:
                infobase_id = str(session["infobase_id"])
                if not infobase_id:
                    continue
                item = dict(session)
                item.pop("raw", None)
                item["app_name"] = application_name(item.get("app_id", ""))
                item["server_endpoint"] = endpoint
                item["cluster_id"] = cluster_id
                sessions_by_base.setdefault(infobase_id, []).append(item)
            for infobase in cluster_snapshot["infobases"]:
                infobase_id = str(infobase["id"])
                sessions = sessions_by_base.get(infobase_id, [])
                result.append(
                    {
                        "key": _base_key(endpoint, cluster_id, infobase_id),
                        "server_endpoint": endpoint,
                        "server_name": server_name,
                        "cluster_id": cluster_id,
                        "cluster_name": cluster_name,
                        "infobase_id": infobase_id,
                        "infobase_name": str(infobase["name"] or infobase_id),
                        "sessions": sessions,
                        "session_count": len(sessions),
                        "online": True,
                    }
                )
    return sorted(result, key=lambda item: str(item["infobase_name"]).casefold())


def _company_views(
    companies: tuple[Company, ...],
    available_bases: list[dict[str, object]],
    cluster_admin: str = "",
) -> list[dict[str, object]]:
    available_by_key = {str(base["key"]): base for base in available_bases}
    views: list[dict[str, object]] = []
    for company in companies:
        assigned_keys: list[str] = []
        assigned_bases: list[dict[str, object]] = []
        all_sessions: list[dict[str, object]] = []
        for stored_base in company.bases:
            key = _base_key(stored_base.server_endpoint, stored_base.cluster_id, stored_base.infobase_id)
            assigned_keys.append(key)
            current = available_by_key.get(key)
            if current is None:
                assigned_bases.append(
                    {
                        "key": key,
                        "server_endpoint": stored_base.server_endpoint,
                        "cluster_id": stored_base.cluster_id,
                        "infobase_id": stored_base.infobase_id,
                        "infobase_name": stored_base.infobase_name,
                        "sessions": [],
                        "session_count": 0,
                        "online": False,
                    }
                )
            else:
                assigned_bases.append(current)
                all_sessions.extend(current["sessions"])  # type: ignore[arg-type]
        user_sessions = [
            session
            for session in all_sessions
            if is_user_session(
                session,
                cluster_admin=cluster_admin,
                excluded_users=company.excluded_users,
            )
        ]
        limit = company.session_limit
        views.append(
            {
                "id": company.id,
                "name": company.name,
                "session_limit": limit,
                "enforcement_enabled": company.enforcement_enabled,
                "excluded_users": company.excluded_users,
                "remaining_sessions": max(limit - len(user_sessions), 0) if limit > 0 else None,
                "over_limit": limit > 0 and len(user_sessions) > limit,
                "assigned_keys": assigned_keys,
                "bases": assigned_bases,
                "sessions": user_sessions,
                "user_session_count": len(user_sessions),
                "ignored_session_count": len(all_sessions) - len(user_sessions),
            }
        )
    return views


def _current_credentials(request: Request) -> ClusterCredentials | None:
    return request.app.state.credentials.get(request.cookies.get(AUTH_COOKIE))


def _redirect(
    *,
    message: str | None = None,
    error: str | None = None,
    path: str = "/",
) -> RedirectResponse:
    query = urlencode({key: value for key, value in {"message": message, "error": error}.items() if value})
    return RedirectResponse(url=f"{path}{'?' + query if query else ''}", status_code=303)


async def _form_fields(request: Request) -> dict[str, list[str]]:
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
        return {}
    return parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)


def _login_page(request: Request, error: str | None = None, username: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login_v2.html",
        context={"error": error, "username": username},
        status_code=401 if error else 200,
    )


def _dashboard_data(request: Request, credentials: ClusterCredentials) -> dict[str, object]:
    state = request.app.state.monitor.state()
    available = _available_bases(state["servers"])
    companies = request.app.state.companies.list_companies()
    views = _company_views(companies, available, credentials.username)
    owners = {key: company["name"] for company in views for key in company["assigned_keys"]}
    for base in available:
        base["company_name"] = owners.get(base["key"], "")
    for company in views:
        company["control_status"] = state["control_status"].get(company["id"], {})
        limit = int(company["session_limit"])
        count = int(company["user_session_count"])
        enabled = bool(company["enforcement_enabled"]) and limit > 0
        company["usage_percent"] = round(count / limit * 100) if limit > 0 else 0
        if not enabled:
            company["status_key"] = "disabled"
            company["status_label"] = "Выключен"
        elif count > limit:
            company["status_key"] = "danger"
            company["status_label"] = "Превышение"
        elif count == limit:
            company["status_key"] = "warning"
            company["status_label"] = "Лимит"
        else:
            company["status_key"] = "success"
            company["status_label"] = "Норма"

    company_by_base: dict[str, dict[str, object]] = {
        str(key): company
        for company in views
        for key in company["assigned_keys"]
    }
    all_sessions: list[dict[str, object]] = []
    for base in available:
        company = company_by_base.get(str(base["key"]))
        base["company_id"] = company["id"] if company else None
        base["company_name"] = company["name"] if company else ""
        excluded = tuple(company["excluded_users"]) if company else ()
        user_sessions = [
            session
            for session in base["sessions"]
            if is_user_session(session, cluster_admin=credentials.username, excluded_users=excluded)
        ]
        base["user_session_count"] = len(user_sessions)
        base["ignored_session_count"] = len(base["sessions"]) - len(user_sessions)
        for session in base["sessions"]:
            item = dict(session)
            item["company_id"] = company["id"] if company else None
            item["company_name"] = company["name"] if company else ""
            item["counted"] = is_user_session(
                session,
                cluster_admin=credentials.username,
                excluded_users=excluded,
            )
            all_sessions.append(item)

    server_views: list[dict[str, object]] = []
    for server in state["servers"]:
        clusters = server["clusters"]
        server_views.append(
            {
                "name": server["server_name"],
                "endpoint": server["endpoint"],
                "cluster_count": len(clusters),
                "base_count": sum(len(cluster["infobases"]) for cluster in clusters),
                "session_count": sum(len(cluster["sessions"]) for cluster in clusters),
                "online": True,
            }
        )
    known_endpoints = {str(server["endpoint"]) for server in server_views}
    for configured in request.app.state.settings.servers:
        if configured.endpoint not in known_endpoints:
            server_views.append(
                {
                    "name": configured.name,
                    "endpoint": configured.endpoint,
                    "cluster_count": 0,
                    "base_count": 0,
                    "session_count": 0,
                    "online": False,
                }
            )
    return {
        **state,
        "available_bases": available,
        "companies": views,
        "total_user_sessions": sum(int(company["user_session_count"]) for company in views),
        "total_ignored_sessions": sum(int(company["ignored_session_count"]) for company in views),
        "active_controls": sum(
            1
            for company in views
            if company["enforcement_enabled"] and int(company["session_limit"]) > 0
        ),
        "problem_count": sum(1 for company in views if company["status_key"] in {"warning", "danger"}),
        "all_sessions": all_sessions,
        "server_views": server_views,
    }


@router.get("/health", tags=["service"])
async def health(request: Request) -> dict[str, object]:
    state = request.app.state.monitor.state()
    return {
        "status": "ok",
        "mode": "SELECTIVE_CONTROL",
        "servers_configured": len(request.app.state.settings.servers),
        "last_updated": state["last_updated"],
    }


@router.get("/api/snapshots", tags=["monitoring"])
async def snapshots(request: Request) -> dict[str, object]:
    if _current_credentials(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход администратора кластера")
    return request.app.state.monitor.state()


@router.get("/api/dashboard", tags=["monitoring"])
async def dashboard_api(request: Request) -> dict[str, object]:
    credentials = _current_credentials(request)
    if credentials is None:
        raise HTTPException(status_code=401, detail="Требуется вход администратора кластера")
    return await run_in_threadpool(_dashboard_data, request, credentials)


@router.post("/api/refresh", tags=["monitoring"])
async def refresh_api(request: Request) -> dict[str, str]:
    if _current_credentials(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход администратора кластера")
    request.app.state.monitor.refresh_now(topology=True)
    return {"status": "accepted"}


@router.post("/login", include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
        return _login_page(request, "Неверный формат запроса")
    fields = await _form_fields(request)
    username = fields.get("username", [""])[0].strip()
    password = fields.get("password", [""])[0]
    if not username:
        return _login_page(request, "Введите имя администратора", username)
    credentials = ClusterCredentials(username=username, password=password)
    try:
        await run_in_threadpool(_validate_credentials, request, credentials)
    except RacError as exc:
        log.warning("Не удалось выполнить вход администратора кластера: %s", exc)
        return _login_page(request, f"Не удалось войти: {exc}", username)
    token = request.app.state.credentials.create(credentials)
    request.app.state.monitor.set_credentials(credentials)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    request.app.state.credentials.delete(request.cookies.get(AUTH_COOKIE))
    request.app.state.monitor.clear_credentials()
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


@router.post("/companies", include_in_schema=False)
async def create_company(request: Request) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    try:
        await run_in_threadpool(request.app.state.companies.create_company, fields.get("name", [""])[0])
    except CompanyStoreError as exc:
        return _redirect(error=str(exc))
    return _redirect(message="Компания создана")


@router.post("/companies/{company_id}/bases", include_in_schema=False)
async def update_company_bases(request: Request, company_id: int) -> RedirectResponse:
    credentials = _current_credentials(request)
    if credentials is None:
        return _redirect(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    selected_keys = set(fields.get("base", []))
    data = await run_in_threadpool(_dashboard_data, request, credentials)
    available = data["available_bases"]
    available_by_key = {str(base["key"]): base for base in available}
    if selected_keys.difference(available_by_key):
        return _redirect(error="Список баз изменился. Обновите страницу и повторите")
    stored = request.app.state.companies.list_companies()
    company = next((item for item in stored if item.id == company_id), None)
    if company is None:
        return _redirect(error="Компания не найдена")
    assignments = [
        AssignedInfobase(
            server_endpoint=str(available_by_key[key]["server_endpoint"]),
            cluster_id=str(available_by_key[key]["cluster_id"]),
            infobase_id=str(available_by_key[key]["infobase_id"]),
            infobase_name=str(available_by_key[key]["infobase_name"]),
        )
        for key in selected_keys
    ]
    for old in company.bases:
        key = _base_key(old.server_endpoint, old.cluster_id, old.infobase_id)
        if key not in available_by_key:
            assignments.append(old)
    try:
        await run_in_threadpool(request.app.state.companies.replace_bases, company_id, assignments)
    except CompanyStoreError as exc:
        return _redirect(error=str(exc))
    return _redirect(message=f"Базы компании «{company.name}» сохранены")


@router.post("/companies/{company_id}/control", include_in_schema=False)
async def update_control(request: Request, company_id: int) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    try:
        limit = int(fields.get("session_limit", [""])[0].strip())
    except ValueError:
        return _redirect(error="Введите целое число сеансов")
    enabled = fields.get("enforcement_enabled", [""])[0] == "on"
    excluded = fields.get("excluded_users", [""])[0]
    try:
        await run_in_threadpool(
            request.app.state.companies.update_control_settings,
            company_id,
            limit,
            enabled,
            excluded,
        )
    except CompanyStoreError as exc:
        return _redirect(error=str(exc))
    request.app.state.monitor.refresh_now()
    state = "включён" if enabled and limit > 0 else "выключен"
    return _redirect(message=f"Настройки сохранены. Контроль {state}")


@router.post("/companies/{company_id}/session-limit", include_in_schema=False)
async def update_legacy_limit(request: Request, company_id: int) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect(error="Сначала войдите как администратор кластера")
    fields = await _form_fields(request)
    fields.setdefault("excluded_users", [""])
    try:
        limit = int(fields.get("session_limit", [""])[0].strip())
        await run_in_threadpool(request.app.state.companies.update_session_limit, company_id, limit)
    except (ValueError, CompanyStoreError) as exc:
        return _redirect(error=str(exc) or "Введите целое число сеансов")
    return _redirect(message="Лимит сеансов сохранён")


@router.post("/companies/{company_id}/delete", include_in_schema=False)
async def delete_company(request: Request, company_id: int) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect(error="Сначала войдите как администратор кластера")
    try:
        await run_in_threadpool(request.app.state.companies.delete_company, company_id)
    except CompanyStoreError as exc:
        return _redirect(error=str(exc))
    return _redirect(message="Компания удалена")


async def _render_admin_page(
    request: Request,
    template_name: str,
    *,
    active_page: str,
    page_title: str,
    page_description: str,
    extra: dict[str, object] | None = None,
) -> HTMLResponse:
    credentials = _current_credentials(request)
    if credentials is None:
        response = _login_page(request)
        if request.cookies.get(AUTH_COOKIE):
            response.delete_cookie(AUTH_COOKIE)
        return response
    data = await run_in_threadpool(_dashboard_data, request, credentials)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            **data,
            "cluster_user": credentials.username,
            "active_page": active_page,
            "page_title": page_title,
            "page_description": page_description,
            "message": request.query_params.get("message"),
            "page_error": request.query_params.get("error"),
            **(extra or {}),
        },
    )


def _read_log_entries(path: Path, limit: int = 250) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    entries: list[dict[str, str]] = []
    for line in reversed(lines):
        parts = line.split(" ", 3)
        if len(parts) == 4:
            date, clock, level, message = parts
        else:
            date, clock, level, message = "", "", "INFO", line
        source, separator, event = message.partition(": ")
        source = source if separator else "application"
        event = event if separator else message
        legacy_termination = re.fullmatch(
            r"Terminated excess user session (\S+) for company (.+)", event
        )
        if legacy_termination:
            event = (
                f"Завершён лишний пользовательский сеанс {legacy_termination.group(1)} "
                f"для компании «{legacy_termination.group(2)}»"
            )
        legacy_prefixes = {
            "Cluster administrator authentication failed: ": "Не удалось выполнить вход администратора кластера: ",
            "Cannot refresh sessions for cluster ": "Не удалось обновить сеансы кластера ",
            "Cannot refresh RAS topology ": "Не удалось обновить структуру RAS ",
            "Cannot terminate session ": "Не удалось завершить сеанс ",
            "Unexpected background monitor failure": "Непредвиденная ошибка фонового мониторинга",
            "rac.exe failed (code ": "Ошибка rac.exe (код ",
            "Skipping unavailable cluster ": "Пропущен недоступный кластер ",
        }
        for old_prefix, new_prefix in legacy_prefixes.items():
            if event.startswith(old_prefix):
                event = new_prefix + event[len(old_prefix):]
                break
        source = {
            "web.monitor": "Контроль сеансов",
            "web.routes_v2": "Веб-интерфейс",
            "onec.rac": "RAC",
            "onec.discovery": "Опрос 1С",
            "application": "Приложение",
        }.get(source, source)
        entries.append(
            {
                "date": date,
                "time": clock,
                "level": level.upper(),
                "source": source,
                "event": event,
            }
        )
    return entries


@router.get("/companies", response_class=HTMLResponse, include_in_schema=False)
async def companies_page(request: Request) -> HTMLResponse:
    return await _render_admin_page(
        request,
        "companies.html",
        active_page="companies",
        page_title="Компании",
        page_description="Группировка информационных баз и общие лимиты пользовательских сеансов.",
    )


@router.get("/companies/{company_id}", response_class=HTMLResponse, include_in_schema=False)
async def company_page(request: Request, company_id: int) -> HTMLResponse:
    credentials = _current_credentials(request)
    if credentials is None:
        return _login_page(request)
    data = await run_in_threadpool(_dashboard_data, request, credentials)
    company = next((item for item in data["companies"] if item["id"] == company_id), None)
    if company is None:
        return _redirect(error="Компания не найдена")
    return templates.TemplateResponse(
        request=request,
        name="company_detail.html",
        context={
            **data,
            "company": company,
            "cluster_user": credentials.username,
            "active_page": "companies",
            "page_title": str(company["name"]),
            "page_description": "Общий лимит, базы и активные пользовательские сеансы компании.",
            "message": request.query_params.get("message"),
            "page_error": request.query_params.get("error"),
        },
    )


@router.get("/infobases", response_class=HTMLResponse, include_in_schema=False)
async def infobases_page(request: Request) -> HTMLResponse:
    return await _render_admin_page(
        request,
        "infobases.html",
        active_page="infobases",
        page_title="Информационные базы",
        page_description="Все базы, обнаруженные на подключённых серверах RAS.",
    )


@router.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
async def sessions_page(request: Request) -> HTMLResponse:
    return await _render_admin_page(
        request,
        "sessions.html",
        active_page="sessions",
        page_title="Активные сеансы",
        page_description="Пользовательские и технические подключения к базам 1С.",
    )


@router.get("/servers", response_class=HTMLResponse, include_in_schema=False)
async def servers_page(request: Request) -> HTMLResponse:
    return await _render_admin_page(
        request,
        "servers.html",
        active_page="servers",
        page_title="Серверы 1С",
        page_description="Состояние серверов администрирования, кластеров и информационных баз.",
    )


@router.get("/logs", response_class=HTMLResponse, include_in_schema=False)
async def logs_page(request: Request) -> HTMLResponse:
    entries = await run_in_threadpool(_read_log_entries, request.app.state.settings.log_file)
    return await _render_admin_page(
        request,
        "logs.html",
        active_page="logs",
        page_title="Журнал",
        page_description="Последние события мониторинга и управления сеансами.",
        extra={"log_entries": entries},
    )


def _positive_number(fields: dict[str, list[str]], name: str, label: str) -> float:
    raw = fields.get(name, [""])[0].strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{label}: введите положительное число") from exc
    if value <= 0:
        raise ConfigurationError(f"{label}: значение должно быть больше нуля")
    return value


def _settings_from_form(request: Request, fields: dict[str, list[str]]) -> Settings:
    rac_value = os.path.expandvars(fields.get("rac_path", [""])[0].strip())
    rac_path = Path(rac_value).expanduser()
    if not rac_value or not rac_path.is_file():
        raise ConfigurationError(f"rac.exe не найден: {rac_path if rac_value else 'путь не указан'}")

    log_value = fields.get("log_file", [""])[0].strip()
    if not log_value:
        raise ConfigurationError("Укажите путь к файлу журнала")

    names = fields.get("server_name", [])
    hosts = fields.get("server_host", [])
    ports = fields.get("server_port", [])
    count = max(len(names), len(hosts), len(ports))
    current_by_endpoint = {server.endpoint: server for server in request.app.state.settings.servers}
    servers: list[RasServer] = []
    for index in range(count):
        name = names[index].strip() if index < len(names) else ""
        host = hosts[index].strip() if index < len(hosts) else ""
        port_text = ports[index].strip() if index < len(ports) else ""
        if not name and not host and not port_text:
            continue
        if not host:
            raise ConfigurationError(f"RAS-сервер #{index + 1}: укажите адрес")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ConfigurationError(f"RAS-сервер #{index + 1}: порт должен быть целым числом") from exc
        if not 1 <= port <= 65535:
            raise ConfigurationError(f"RAS-сервер #{index + 1}: порт должен быть от 1 до 65535")
        previous = current_by_endpoint.get(f"{host}:{port}")
        servers.append(RasServer(
            name=name or host,
            host=host,
            port=port,
            cluster_user=previous.cluster_user if previous else "",
            cluster_password=previous.cluster_password if previous else "",
            cluster_user_env=previous.cluster_user_env if previous else "",
            cluster_password_env=previous.cluster_password_env if previous else "",
        ))
    if not servers:
        raise ConfigurationError("Добавьте хотя бы один RAS-сервер")

    return Settings(
        rac_path=rac_path.resolve(),
        command_timeout_seconds=_positive_number(
            fields, "command_timeout_seconds", "Тайм-аут команды"
        ),
        log_file=Path(os.path.expandvars(log_value)).expanduser(),
        servers=tuple(servers),
        poll_interval_seconds=_positive_number(
            fields, "poll_interval_seconds", "Интервал обновления сеансов"
        ),
        topology_refresh_seconds=_positive_number(
            fields, "topology_refresh_seconds", "Интервал обновления топологии"
        ),
    )


def _replace_log_handler(log_file: Path) -> None:
    target = log_file.resolve()
    root = logging.getLogger()
    current_handlers = [handler for handler in root.handlers if isinstance(handler, logging.FileHandler)]
    if any(Path(handler.baseFilename).resolve() == target for handler in current_handlers):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    new_handler = logging.FileHandler(target, encoding="utf-8")
    new_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S",
    ))
    root.addHandler(new_handler)
    for handler in current_handlers:
        root.removeHandler(handler)
        handler.close()


@router.post("/settings", include_in_schema=False)
async def update_settings(request: Request) -> RedirectResponse:
    if _current_credentials(request) is None:
        return _redirect(error="Сначала войдите как администратор кластера", path="/settings")
    fields = await _form_fields(request)
    try:
        settings = _settings_from_form(request, fields)
        config_path = request.app.state.config_path
        await run_in_threadpool(save_settings, config_path, settings)
        await run_in_threadpool(_replace_log_handler, settings.log_file)
    except (ConfigurationError, OSError) as exc:
        return _redirect(error=str(exc), path="/settings")

    rac = RacClient(settings.rac_path, settings.command_timeout_seconds)
    request.app.state.settings = settings
    request.app.state.rac = rac
    request.app.state.monitor.reconfigure(
        rac,
        settings.servers,
        poll_seconds=settings.poll_interval_seconds,
        topology_seconds=settings.topology_refresh_seconds,
    )
    log.info("Настройки сохранены через веб-интерфейс")
    return _redirect(message="Настройки сохранены и применены", path="/settings")


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return await _render_admin_page(
        request,
        "settings.html",
        active_page="settings",
        page_title="Настройки",
        page_description="Текущая конфигурация подключения и параметры обновления.",
        extra={
            "rac_path": str(settings.rac_path),
            "command_timeout": settings.command_timeout_seconds,
            "log_file": str(settings.log_file),
            "settings_servers": settings.servers,
            "poll_interval": settings.poll_interval_seconds,
            "topology_refresh": settings.topology_refresh_seconds,
            "config_path": str(request.app.state.config_path),
        },
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    return await _render_admin_page(
        request,
        "overview.html",
        active_page="overview",
        page_title="Контроль сеансов 1С",
        page_description="Мониторинг и ограничение пользовательских подключений к информационным базам 1С.",
    )
